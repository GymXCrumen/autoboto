"""
Three responsibilities, kept separate from the strategy code:

1. Position sizing — from risk_per_trade%, the ATR stop distance, and
   MT5's own tick value (so no hand-rolled currency conversion).
2. Account-level kill switches — daily loss (auto-resets) and max
   drawdown from peak equity (manual resume). Unchanged from before.
3. The Agno-style layer — after too many losses in a row, pause new
   entries for a cooldown period, then come back in at reduced size
   and a stricter regime threshold ("more selective") until a win
   resets it.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Tuple

import config
import notify
import state_store

if TYPE_CHECKING:
    from metaapi_client import MetaApiClient


# --- position sizing -----------------------------------------------------------

def position_size(client: "MetaApiClient", symbol: str, account_equity: float, atr: float,
                   reduced: bool = False) -> float:
    info = client.get_symbol_trading_info(symbol)
    stop_distance_price = atr * config.STOP_LOSS_ATR_MULTIPLIER
    if stop_distance_price <= 0 or info["tick_size"] <= 0:
        return info["volume_min"]

    value_per_price_unit_per_lot = info["tick_value"] / info["tick_size"]
    risk_amount = account_equity * (config.RISK_PER_TRADE_PCT / 100)
    if reduced:
        risk_amount *= config.SIZE_REDUCTION_FACTOR

    lot = risk_amount / (stop_distance_price * value_per_price_unit_per_lot)

    # Round to the broker's lot step, then clamp to [broker min, our safety ceiling].
    step = info["volume_step"] or 0.01
    lot = round(lot / step) * step
    lot = max(info["volume_min"], min(lot, min(config.MAX_LOT_SIZE, info["volume_max"])))
    return round(lot, 2)


def pyramid_add_size(client: "MetaApiClient", symbol: str, account_equity: float,
                      atr: float, current_volume: float) -> float:
    """Lot size for an extra leg on a position that's ALREADY open and
    already at/beyond breakeven — never called on a losing position.
    Same risk-based sizing as a base entry, capped so total volume never
    exceeds PYRAMID_MAX_TOTAL_LOT. Returns 0.0 if there's no room left."""
    room = round(config.PYRAMID_MAX_TOTAL_LOT - current_volume, 2)
    if room <= 0:
        return 0.0
    base_size = position_size(client, symbol, account_equity, atr, reduced=False)
    return round(min(base_size, room), 2)


# --- account-level kill switches (unchanged logic from the martingale version) ---

def _today_str() -> str:
    return datetime.now(config.WAT).strftime("%Y-%m-%d")


def check_kill_switches(current_equity: float) -> Tuple[bool, Optional[str]]:
    state = state_store.get_kill_switch_state()
    today = _today_str()

    if state["daily_date"] != today:
        state = {
            "daily_date": today,
            "daily_start_equity": current_equity,
            "peak_equity": state["peak_equity"] or current_equity,
            "hard_halted": state["hard_halted"],
            "daily_halted": False,
        }

    if state["hard_halted"]:
        _persist_kill_switch(state)
        return True, "max drawdown hard halt (run `python risk_manager.py` to resume)"

    if state["daily_halted"]:
        _persist_kill_switch(state)
        return True, "daily loss limit hit today (auto-resumes tomorrow)"

    state["peak_equity"] = max(state["peak_equity"], current_equity)

    daily_loss_pct = 100 * (state["daily_start_equity"] - current_equity) / state["daily_start_equity"]
    if daily_loss_pct >= config.DAILY_LOSS_LIMIT_PCT:
        state["daily_halted"] = True
        _persist_kill_switch(state)
        notify.kill_switch(f"Daily loss limit hit ({daily_loss_pct:.1f}%) — halted for the rest of today.")
        return True, f"daily loss limit hit ({daily_loss_pct:.1f}% today)"

    drawdown_pct = 100 * (state["peak_equity"] - current_equity) / state["peak_equity"]
    if drawdown_pct >= config.MAX_DRAWDOWN_PCT:
        state["hard_halted"] = True
        _persist_kill_switch(state)
        notify.kill_switch(f"Max drawdown hit ({drawdown_pct:.1f}% below peak) — halted until manually resumed.")
        return True, f"max drawdown hard halt ({drawdown_pct:.1f}% below peak equity)"

    _persist_kill_switch(state)
    return False, None


def _persist_kill_switch(state: dict) -> None:
    state_store.set_kill_switch_state(
        daily_date=state["daily_date"], daily_start_equity=state["daily_start_equity"],
        peak_equity=state["peak_equity"], hard_halted=state["hard_halted"], daily_halted=state["daily_halted"],
    )


def clear_hard_halt() -> None:
    state = state_store.get_kill_switch_state()
    state["hard_halted"] = False
    _persist_kill_switch(state)
    print("[risk_manager] Hard halt cleared.")


# --- Agno-style consecutive-loss cooldown / selectivity -----------------------------

def in_cooldown() -> Tuple[bool, dict]:
    """Returns (in_cooldown, regime_state). Also clears an expired cooldown."""
    state = state_store.get_regime_state()
    if state["cooldown_until"]:
        until = datetime.fromisoformat(state["cooldown_until"])
        if datetime.now(config.WAT) < until:
            return True, state
        # Cooldown expired — trading resumes, but at reduced size / higher
        # selectivity until a win clears it (see record_trade_outcome()).
        state["cooldown_until"] = None
        state_store.set_regime_state(state["consecutive_losses"], None, state["reduced_size_active"])
    return False, state


def current_efficiency_threshold(regime_state: dict) -> float:
    base = config.REGIME_EFFICIENCY_THRESHOLD
    return base + config.SELECTIVITY_EFFICIENCY_BOOST if regime_state["reduced_size_active"] else base


def record_trade_outcome(won: bool) -> None:
    state = state_store.get_regime_state()
    if won:
        state_store.set_regime_state(consecutive_losses=0, cooldown_until=None, reduced_size_active=False)
        return

    consecutive_losses = state["consecutive_losses"] + 1
    if consecutive_losses >= config.CONSECUTIVE_LOSS_LIMIT:
        cooldown_until = (datetime.now(config.WAT) + timedelta(minutes=config.COOLDOWN_MINUTES)).isoformat()
        state_store.set_regime_state(consecutive_losses=0, cooldown_until=cooldown_until, reduced_size_active=True)
        notify.kill_switch(
            f"{config.CONSECUTIVE_LOSS_LIMIT} losses in a row — pausing new entries for "
            f"{config.COOLDOWN_MINUTES} min, then resuming at reduced size and stricter selectivity."
        )
    else:
        state_store.set_regime_state(
            consecutive_losses=consecutive_losses,
            cooldown_until=None,
            reduced_size_active=state["reduced_size_active"],
        )


if __name__ == "__main__":
    state_store.init()
    clear_hard_halt()
