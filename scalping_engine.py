"""
Donchian breakout scalping engine.

Each tick:
  1. Check account-level kill switches. Halted -> do nothing else.
  2. If a position is open:
       a. If MT5 has already closed it (TP or SL — both are real
          broker-side orders), detect that and update the Agno
          consecutive-loss/cooldown state.
       b. Otherwise, check breakeven (move SL to entry once price has
          moved BREAKEVEN_TRIGGER_ATR_MULTIPLE in favor) and, only
          once breakeven is set, check for a pyramid add-on (a fresh
          same-direction breakout while regime is still favorable).
  3. If no position is open and we're not in an Agno-style cooldown:
     ask strategy.get_entry_signal() for a breakout (regime-filtered
     already); if given, size the position from risk_per_trade% and
     the ATR stop distance, and open it with real SL/TP.

Pyramiding only ever adds to a position that has already proven
itself (past breakeven) — it never adds to a losing position, which
is what would make this martingale again. That distinction is the
whole point of replacing the martingale design.
"""

import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

import config
import notify
import risk_manager
import state_store

if TYPE_CHECKING:
    from metaapi_client import MetaApiClient


class ScalpingEngine:
    def __init__(self, client: "MetaApiClient"):
        self.client = client
        self.symbol = config.SYMBOL
        self.client.ensure_symbol(self.symbol)

    def run_forever(self, get_entry_signal: Callable):
        print(f"Starting Donchian scalping engine on {self.symbol} ({config.TIMEFRAME}). Ctrl+C to stop.")
        while True:
            try:
                self._tick(get_entry_signal)
            except Exception as exc:
                print(f"[error] {exc!r}")
                notify.error(repr(exc))
            time.sleep(config.POLL_INTERVAL_SECONDS)

    def _tick(self, get_entry_signal: Callable):
        account = self.client.get_account_info()
        halted, reason = risk_manager.check_kill_switches(account["equity"])
        if halted:
            print(f"[halted] {reason}")
            return

        saved = state_store.load_open_position()
        broker_position = self.client.get_position(self.symbol)

        if broker_position is None:
            if saved:
                self._handle_position_closed(saved)
                return  # re-evaluate entries fresh next tick
            self._maybe_open(get_entry_signal, account["equity"])
            return

        self._manage_open_position(saved, broker_position, get_entry_signal, account["equity"])

    def _maybe_open(self, get_entry_signal: Callable, account_equity: float):
        cooling_down, regime_state = risk_manager.in_cooldown()
        if cooling_down:
            return

        min_efficiency = risk_manager.current_efficiency_threshold(regime_state)
        signal = get_entry_signal(self.client, self.symbol, min_efficiency)
        if signal:
            self._open_position(signal, account_equity, regime_state["reduced_size_active"])

    def _open_position(self, signal: dict, account_equity: float, reduced: bool):
        direction = signal["direction"]
        atr = signal["atr"]
        lot = risk_manager.position_size(self.client, self.symbol, account_equity, atr, reduced=reduced)

        price = self.client.get_price(self.symbol)
        entry_price = price["ask"] if direction == "long" else price["bid"]
        sign = 1 if direction == "long" else -1
        sl = entry_price - sign * atr * config.STOP_LOSS_ATR_MULTIPLIER
        tp = entry_price + sign * atr * config.TAKE_PROFIT_ATR_MULTIPLIER

        fill = self.client.market_order(self.symbol, direction, lot, sl=sl, tp=tp)
        position = self.client.get_position(self.symbol)
        if position is None:
            raise RuntimeError("market_order succeeded but no position found immediately after.")

        state_store.save_open_position(position["ticket"], direction, lot, sl, tp, atr, breakeven_set=False)
        print(f"[open] {direction} {lot} lot @ {fill['price']} — SL {sl:.5f} / TP {tp:.5f} "
              f"(efficiency {signal['efficiency']:.2f}, lookback {signal['lookback']})")
        notify.position_opened(direction, self.symbol, lot, fill["price"], sl, tp, signal["efficiency"])

    def _manage_open_position(self, saved: Optional[dict], broker_position: dict,
                               get_entry_signal: Callable, account_equity: float):
        if saved is None:
            print("[warn] Open position found with no local record — leaving it untouched.")
            return

        direction = saved["direction"]
        avg_price = broker_position["price_open"]
        atr = saved["atr"]
        price = self.client.get_price(self.symbol)
        current_price = price["bid"] if direction == "long" else price["ask"]
        sign = 1 if direction == "long" else -1
        favorable_price_move = (current_price - avg_price) * sign

        if not saved["breakeven_set"] and favorable_price_move >= atr * config.BREAKEVEN_TRIGGER_ATR_MULTIPLE:
            self._set_breakeven(saved, broker_position, direction, avg_price, atr)
            return  # re-evaluate pyramiding next tick against the fresh state

        if saved["breakeven_set"] and broker_position["volume"] < config.PYRAMID_MAX_TOTAL_LOT:
            self._maybe_pyramid(saved, broker_position, get_entry_signal, account_equity, direction)

    def _set_breakeven(self, saved: dict, broker_position: dict, direction: str, avg_price: float, atr: float):
        sign = 1 if direction == "long" else -1
        breakeven_price = avg_price + sign * atr * config.BREAKEVEN_BUFFER_ATR_MULTIPLE
        self.client.modify_sl_tp(self.symbol, broker_position["ticket"], sl=breakeven_price)
        state_store.save_open_position(
            saved["ticket"], direction, broker_position["volume"], breakeven_price, saved["tp"],
            atr, breakeven_set=True,
        )
        print(f"[breakeven] SL moved to {breakeven_price:.5f}")
        notify.breakeven_set(direction, self.symbol, breakeven_price)

    def _maybe_pyramid(self, saved: dict, broker_position: dict, get_entry_signal: Callable,
                        account_equity: float, direction: str):
        cooling_down, regime_state = risk_manager.in_cooldown()
        if cooling_down:
            return
        min_efficiency = risk_manager.current_efficiency_threshold(regime_state)
        signal = get_entry_signal(self.client, self.symbol, min_efficiency)
        if not signal or signal["direction"] != direction:
            return  # only add to strength in the SAME direction, never fade it

        add_lot = risk_manager.pyramid_add_size(
            self.client, self.symbol, account_equity, signal["atr"], broker_position["volume"],
        )
        if add_lot <= 0:
            return

        fill = self.client.market_order(self.symbol, direction, add_lot)
        position = self.client.get_position(self.symbol)
        atr = signal["atr"]
        sign = 1 if direction == "long" else -1
        new_tp = position["price_open"] + sign * atr * config.TAKE_PROFIT_ATR_MULTIPLIER

        # SL is left as-is (the breakeven level already set) — a pyramid
        # add should never loosen protection on the legs already secured.
        self.client.modify_sl_tp(self.symbol, position["ticket"], sl=position["sl"], tp=new_tp)

        state_store.save_open_position(
            position["ticket"], direction, position["volume"], position["sl"], new_tp, atr, breakeven_set=True,
        )
        print(f"[pyramid] added {add_lot} lot @ {fill['price']} (total {position['volume']} lot)")
        notify.pyramid_added(direction, self.symbol, add_lot, fill["price"], position["volume"])

    def _handle_position_closed(self, saved: dict):
        profit = self.client.get_last_deal_profit(saved["ticket"])
        won = (profit or 0) > 0
        outcome = "win" if won else "loss"

        print(f"[close] Ticket {saved['ticket']} closed — {outcome} (profit {profit}).")
        notify.position_closed(saved["direction"], self.symbol, outcome, profit or 0.0)
        state_store.log_closed_trade(
            direction=saved["direction"], lot=saved["lot"], profit=profit, outcome=outcome,
            closed_at=datetime.now(config.WAT).strftime("%Y-%m-%d %H:%M WAT"),
        )
        state_store.clear_open_position()
        risk_manager.record_trade_outcome(won)
