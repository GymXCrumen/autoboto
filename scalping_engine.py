"""
Donchian breakout engine (5-minute timeframe) — scans every symbol in
config.MAJOR_PAIRS each tick, independently, sharing one account-level
risk state across all of them.

Each tick, per symbol:
  1. (Once per tick, not per symbol) Check account-level kill switches
     and the inactivity warning. Halted -> skip every symbol this tick.
  2. If that symbol has an open position:
       a. If MT5 has already closed it (TP or SL — both are real
          broker-side orders), detect that, log it, and update the
          Agno consecutive-loss/cooldown state (shared across symbols).
       b. Otherwise, check breakeven and, only once breakeven is set,
          check for a pyramid add-on.
  3. If that symbol has no open position, we're not in an Agno-style
     cooldown, AND we're under MAX_CONCURRENT_POSITIONS across all
     symbols combined: ask for a breakout signal and open it.

Pyramiding only ever adds to a position that has already proven
itself (past breakeven) — it never adds to a losing position.
"""

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Optional

import config
import notify
import risk_manager
import state_store
import trade_memory

if TYPE_CHECKING:
    from metaapi_client import MetaApiClient

UTC = timezone.utc


class ScalpingEngine:
    def __init__(self, client: "MetaApiClient"):
        self.client = client
        self.symbols = config.MAJOR_PAIRS
        for symbol in self.symbols:
            self.client.ensure_symbol(symbol)

    def run_forever(self, get_entry_signal: Callable):
        print(
            f"Starting Donchian breakout engine on {', '.join(self.symbols)} "
            f"({config.TIMEFRAME}), max {config.MAX_CONCURRENT_POSITIONS} concurrent. Ctrl+C to stop."
        )
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

        inactivity_warning = risk_manager.check_inactivity()
        if inactivity_warning:
            notify.inactivity_warning(inactivity_warning)

        for symbol in self.symbols:
            self._tick_symbol(symbol, get_entry_signal, account["equity"])

    def _tick_symbol(self, symbol: str, get_entry_signal: Callable, account_equity: float):
        saved = state_store.load_open_position(symbol)
        broker_position = self.client.get_position(symbol)

        if broker_position is None:
            if saved:
                self._handle_position_closed(symbol, saved)
                return  # re-evaluate this symbol fresh next tick
            self._maybe_open(symbol, get_entry_signal, account_equity)
            return

        self._manage_open_position(symbol, saved, broker_position, get_entry_signal, account_equity)

    def _maybe_open(self, symbol: str, get_entry_signal: Callable, account_equity: float):
        if state_store.count_open_positions() >= config.MAX_CONCURRENT_POSITIONS:
            return  # at the cap across all symbols combined — wait for one to close

        cooling_down, regime_state = risk_manager.in_cooldown()
        if cooling_down:
            return

        min_efficiency = risk_manager.current_efficiency_threshold(regime_state)
        signal = get_entry_signal(self.client, symbol, min_efficiency)
        if not signal:
            return

        features = trade_memory.extract_features(symbol, signal)
        confidence = trade_memory.compute_confidence(features)
        if trade_memory.should_skip(confidence):
            print(f"[skip] {symbol} {signal['direction']} — confidence {confidence:.2f} below "
                  f"{trade_memory.MIN_CONFIDENCE_TO_TRADE} for this setup's history: {features}")
            return

        self._open_position(symbol, signal, account_equity, regime_state["reduced_size_active"],
                             confidence, features)

    def _open_position(self, symbol: str, signal: dict, account_equity: float, reduced: bool,
                        confidence: float, features: dict):
        direction = signal["direction"]
        atr = signal["atr"]
        lot = risk_manager.position_size(self.client, symbol, account_equity, atr,
                                          reduced=reduced, confidence=confidence)
        risk_amount = risk_manager.risk_amount_for(account_equity, reduced=reduced, confidence=confidence)

        price = self.client.get_price(symbol)
        entry_price = price["ask"] if direction == "long" else price["bid"]
        sign = 1 if direction == "long" else -1
        sl = entry_price - sign * atr * config.STOP_LOSS_ATR_MULTIPLIER
        tp = entry_price + sign * atr * config.TAKE_PROFIT_ATR_MULTIPLIER

        fill = self.client.market_order(symbol, direction, lot, sl=sl, tp=tp)
        position = self.client.get_position(symbol)
        if position is None:
            raise RuntimeError(f"market_order succeeded but no {symbol} position found immediately after.")

        state_store.save_open_position(
            symbol, position["ticket"], direction, lot, sl, tp, atr, breakeven_set=False,
            features_json=trade_memory.features_to_json(features), risk_amount=risk_amount,
        )
        print(f"[open] {symbol} {direction} {lot} lot @ {fill['price']} — SL {sl:.5f} / TP {tp:.5f} "
              f"(efficiency {signal['efficiency']:.2f}, lookback {signal['lookback']}, "
              f"confidence {confidence:.2f})")
        notify.position_opened(direction, symbol, lot, fill["price"], sl, tp, signal["efficiency"], confidence)

    def _manage_open_position(self, symbol: str, saved: Optional[dict], broker_position: dict,
                               get_entry_signal: Callable, account_equity: float):
        if saved is None:
            print(f"[warn] Open {symbol} position found with no local record — leaving it untouched.")
            return

        direction = saved["direction"]
        avg_price = broker_position["price_open"]
        atr = saved["atr"]
        price = self.client.get_price(symbol)
        current_price = price["bid"] if direction == "long" else price["ask"]
        sign = 1 if direction == "long" else -1
        favorable_price_move = (current_price - avg_price) * sign

        if not saved["breakeven_set"] and favorable_price_move >= atr * config.BREAKEVEN_TRIGGER_ATR_MULTIPLE:
            self._set_breakeven(symbol, saved, broker_position, direction, avg_price, atr)
            return  # re-evaluate pyramiding next tick against the fresh state

        if saved["breakeven_set"] and broker_position["volume"] < config.PYRAMID_MAX_TOTAL_LOT:
            self._maybe_pyramid(symbol, saved, broker_position, get_entry_signal, account_equity, direction)

    def _set_breakeven(self, symbol: str, saved: dict, broker_position: dict,
                        direction: str, avg_price: float, atr: float):
        sign = 1 if direction == "long" else -1
        breakeven_price = avg_price + sign * atr * config.BREAKEVEN_BUFFER_ATR_MULTIPLE
        self.client.modify_sl_tp(symbol, broker_position["ticket"], sl=breakeven_price)
        state_store.save_open_position(
            symbol, saved["ticket"], direction, broker_position["volume"], breakeven_price, saved["tp"],
            atr, breakeven_set=True,
        )
        print(f"[breakeven] {symbol} SL moved to {breakeven_price:.5f}")
        notify.breakeven_set(direction, symbol, breakeven_price)

    def _maybe_pyramid(self, symbol: str, saved: dict, broker_position: dict, get_entry_signal: Callable,
                        account_equity: float, direction: str):
        cooling_down, regime_state = risk_manager.in_cooldown()
        if cooling_down:
            return
        min_efficiency = risk_manager.current_efficiency_threshold(regime_state)
        signal = get_entry_signal(self.client, symbol, min_efficiency)
        if not signal or signal["direction"] != direction:
            return  # only add to strength in the SAME direction, never fade it

        add_lot = risk_manager.pyramid_add_size(
            self.client, symbol, account_equity, signal["atr"], broker_position["volume"],
        )
        if add_lot <= 0:
            return

        fill = self.client.market_order(symbol, direction, add_lot)
        position = self.client.get_position(symbol)
        atr = signal["atr"]
        sign = 1 if direction == "long" else -1
        new_tp = position["price_open"] + sign * atr * config.TAKE_PROFIT_ATR_MULTIPLIER

        # SL is left as-is (the breakeven level already set) — a pyramid
        # add should never loosen protection on the legs already secured.
        self.client.modify_sl_tp(symbol, position["ticket"], sl=position["sl"], tp=new_tp)

        state_store.save_open_position(
            symbol, position["ticket"], direction, position["volume"], position["sl"], new_tp,
            atr, breakeven_set=True,
        )
        print(f"[pyramid] {symbol} added {add_lot} lot @ {fill['price']} (total {position['volume']} lot)")
        notify.pyramid_added(direction, symbol, add_lot, fill["price"], position["volume"])

    def _handle_position_closed(self, symbol: str, saved: dict):
        profit = self.client.get_last_deal_profit(saved["ticket"])
        won = (profit or 0) > 0
        outcome = "win" if won else "loss"
        closed_at_utc = datetime.now(UTC).isoformat()
        r_multiple = (profit / saved["risk_amount"]) if (profit is not None and saved.get("risk_amount")) else None

        print(f"[close] {symbol} ticket {saved['ticket']} closed — {outcome} "
              f"(profit {profit}, {f'{r_multiple:.2f}R' if r_multiple is not None else 'R unknown'}).")
        notify.position_closed(saved["direction"], symbol, outcome, profit or 0.0)
        state_store.log_closed_trade(
            symbol=symbol, direction=saved["direction"], lot=saved["lot"], profit=profit, outcome=outcome,
            closed_at=datetime.now(config.WAT).strftime("%Y-%m-%d %H:%M WAT"),
            features_json=trade_memory.features_to_json(saved["features"]) if saved.get("features") else None,
            r_multiple=r_multiple,
        )
        state_store.set_last_trade_closed_at(closed_at_utc)  # UTC, isoformat — feeds the inactivity check
        state_store.clear_open_position(symbol)
        risk_manager.record_trade_outcome(won)

        # Learning layer: only base entries carry a feature snapshot (see
        # trade_memory.log_trade_result's docstring for why pyramid legs
        # don't feed this).
        if saved.get("features"):
            trade_memory.log_trade_result(saved["features"], won)

        # Push a learning summary every 5 closed trades rather than after
        # every single one — frequent enough to notice drift, not so
        # frequent it buries the open/close alerts.
        if state_store.count_closed_trades() % 5 == 0:
            notify.learning_summary(trade_memory.summary_text())
