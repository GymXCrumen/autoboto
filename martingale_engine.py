"""
Core martingale engine for MT5.

Each tick:
  1. Check account-level kill switches (daily loss / max drawdown).
     If halted, do nothing else this tick.
  2. If no position is open: ask entry_signal for a direction; if
     given, open the base leg with a real broker-side SL (the
     precomputed worst-case hard stop) and TP.
  3. If a position is open: check for breakeven-trend confirmation,
     check for a scale-in trigger, keep the moving TP current.

The hard stop is intentionally set ONCE at base-entry time as an
absolute price (anchored to the base entry, sized for the fully
scaled worst case) and never moved — see config.py for the reasoning.
Because it's a real SL on the position, it protects the account even
if this process crashes or loses network — it doesn't depend on this
loop running.
"""

import time
from datetime import datetime
from typing import Callable, Optional

import config
import notify
import risk_manager
import state_store
from mt5_client import MT5Client


class MartingaleEngine:
    def __init__(self, client: MT5Client):
        self.client = client
        self.symbol = config.SYMBOL
        self.client.ensure_symbol(self.symbol)
        self.pip = client.pip_size(self.symbol)

    def run_forever(self, get_entry_signal: Callable, is_trend_confirmed: Callable):
        print(f"Starting martingale engine on {self.symbol}. Ctrl+C to stop.")
        while True:
            try:
                self._tick(get_entry_signal, is_trend_confirmed)
            except Exception as exc:
                # Never let a transient error (network blip, API hiccup)
                # kill the process silently — log and notify, keep going.
                print(f"[error] {exc!r}")
                notify.error(repr(exc))
            time.sleep(config.POLL_INTERVAL_SECONDS)

    def _tick(self, get_entry_signal: Callable, is_trend_confirmed: Callable):
        account = self.client.get_account_info()
        halted, reason = risk_manager.check_kill_switches(account["equity"])
        if halted:
            print(f"[halted] {reason}")
            return

        saved = state_store.load_open_position()
        broker_position = self.client.get_position(self.symbol)

        if broker_position is None:
            if saved:
                # We had a position on record but MT5 shows none now — it
                # closed (TP, hard stop, or breakeven SL) since last tick.
                self._handle_position_closed(saved)
            direction = get_entry_signal(self.client, self.symbol)
            if direction:
                self._open_base(direction)
            return

        self._manage_open_position(saved, broker_position, is_trend_confirmed)

    def _open_base(self, direction: str):
        lot = risk_manager.clamp_base_lot(config.BASE_LOT)
        price = self.client.get_price(self.symbol)
        entry_price = price["ask"] if direction == "long" else price["bid"]

        # Precompute the worst-case fully-scaled backstop price ONCE, as an
        # absolute level anchored to the base entry (see config.py).
        worst_case_pips = (
            config.SCALE_TRIGGER_PIPS * config.MAX_SCALES + config.HARD_STOP_PIPS_BEYOND_LAST_SCALE
        )
        sign = 1 if direction == "long" else -1
        hard_stop_price = entry_price - sign * worst_case_pips * self.pip

        fill = self.client.market_order(self.symbol, direction, lot, sl=hard_stop_price)
        position = self.client.get_position(self.symbol)
        if position is None:
            raise RuntimeError(
                "market_order succeeded but no position found immediately after — check MT5 terminal logs."
            )

        tp_price = self._tp_price(direction, fill["price"])
        self.client.modify_sl_tp(self.symbol, position["ticket"], sl=hard_stop_price, tp=tp_price)

        state_store.save_open_position(
            ticket=position["ticket"], direction=direction, scales_done=0,
            last_leg_lot=lot, last_avg_price=fill["price"],
            hard_stop_price=hard_stop_price, breakeven_set=False,
        )
        print(f"[open] {direction} {lot} lot @ {fill['price']} — hard stop @ {hard_stop_price:.5f}")
        notify.position_opened(direction, self.symbol, lot, fill["price"])

    def _manage_open_position(self, saved: Optional[dict], broker_position: dict, is_trend_confirmed: Callable):
        if saved is None:
            # A position exists at the broker with our magic number but we
            # have no local record (e.g. the DB was wiped). Don't guess at
            # its history — leave it alone rather than risk scaling into
            # something we don't understand.
            print("[warn] Open position found with no local record — leaving it untouched.")
            return

        direction = saved["direction"]
        avg_price = broker_position["price_open"]
        price = self.client.get_price(self.symbol)
        current_price = price["bid"] if direction == "long" else price["ask"]

        move_pips = (current_price - avg_price) / self.pip
        if direction == "short":
            move_pips = -move_pips
        adverse_pips = -move_pips

        if not saved["breakeven_set"] and is_trend_confirmed(self.client, self.symbol, direction):
            self._set_breakeven(saved, broker_position, direction, avg_price)
            return  # re-evaluate scaling next tick against the fresh state

        if saved["scales_done"] < config.MAX_SCALES and adverse_pips >= config.SCALE_TRIGGER_PIPS:
            self._scale_in(saved, direction)

    def _set_breakeven(self, saved: dict, broker_position: dict, direction: str, avg_price: float):
        sign = 1 if direction == "long" else -1
        breakeven_price = avg_price + sign * config.BREAKEVEN_BUFFER_PIPS * self.pip
        self.client.modify_sl_tp(self.symbol, broker_position["ticket"], sl=breakeven_price)
        state_store.save_open_position(
            ticket=saved["ticket"], direction=direction, scales_done=saved["scales_done"],
            last_leg_lot=saved["last_leg_lot"], last_avg_price=avg_price,
            hard_stop_price=breakeven_price, breakeven_set=True,
        )
        print(f"[breakeven] SL moved to {breakeven_price:.5f}")
        notify.breakeven_set(direction, self.symbol, breakeven_price)

    def _scale_in(self, saved: dict, direction: str):
        new_lot = risk_manager.next_scale_lot(saved["last_leg_lot"])
        fill = self.client.market_order(self.symbol, direction, new_lot)
        position = self.client.get_position(self.symbol)

        new_tp = self._tp_price(direction, position["price_open"])
        # SL is untouched here — either the original fixed backstop or an
        # already-set breakeven stop stands as-is.
        self.client.modify_sl_tp(self.symbol, position["ticket"], tp=new_tp)

        scales_done = saved["scales_done"] + 1
        state_store.save_open_position(
            ticket=position["ticket"], direction=direction, scales_done=scales_done,
            last_leg_lot=new_lot, last_avg_price=position["price_open"],
            hard_stop_price=saved["hard_stop_price"], breakeven_set=saved["breakeven_set"],
        )
        print(
            f"[scale {scales_done}] added {new_lot} lot @ {fill['price']} "
            f"(new average {position['price_open']:.5f})"
        )
        notify.scaled_in(direction, self.symbol, scales_done, new_lot, fill["price"], position["price_open"])

    def _tp_price(self, direction: str, avg_price: float) -> float:
        sign = 1 if direction == "long" else -1
        return avg_price + sign * config.TAKE_PROFIT_PIPS * self.pip

    def _handle_position_closed(self, saved: dict):
        print(
            f"[close] Position (ticket {saved['ticket']}) no longer open at the broker — "
            f"presuming TP, hard stop, or breakeven SL fired."
        )
        notify.position_closed(
            saved["direction"], self.symbol,
            "closed at broker (check MT5 history for the exact fill)",
            saved["last_avg_price"],
        )
        state_store.log_closed_trade(
            direction=saved["direction"], scales_used=saved["scales_done"],
            average_price=saved["last_avg_price"], reason="closed at broker (see MT5 history)",
            closed_at=datetime.now(config.WAT).strftime("%Y-%m-%d %H:%M WAT"),
        )
        state_store.clear_open_position()
