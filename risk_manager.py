"""
Two responsibilities, kept separate from the trading logic on purpose
so the risk rules can't get quietly bypassed by a change to the
strategy code:

1. Lot sizing — clamp every lot value into the configured limits.
2. Kill switches — daily loss limit (auto-resets next day) and max
   drawdown from peak equity (does not auto-reset; needs
   clear_hard_halt()). Notifications fire once, on the transition
   into a halt, not on every tick while halted.
"""

from datetime import datetime
from typing import Optional, Tuple

import config
import notify
import state_store


def clamp_base_lot(lot: float) -> float:
    return max(config.BASE_LOT_MIN, min(lot, config.BASE_LOT_MAX))


def next_scale_lot(previous_lot: float) -> float:
    return round(min(previous_lot * config.SCALE_MULTIPLIER, config.MAX_LOT_SIZE), 2)


def _today_str() -> str:
    return datetime.now(config.WAT).strftime("%Y-%m-%d")


def check_kill_switches(current_equity: float) -> Tuple[bool, Optional[str]]:
    """Call before doing anything else each tick. Returns (halted, reason)."""
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
        _persist(state)
        return True, "max drawdown hard halt (run `python risk_manager.py` to resume)"

    if state["daily_halted"]:
        _persist(state)
        return True, "daily loss limit hit today (auto-resumes tomorrow)"

    state["peak_equity"] = max(state["peak_equity"], current_equity)

    daily_loss_pct = 100 * (state["daily_start_equity"] - current_equity) / state["daily_start_equity"]
    if daily_loss_pct >= config.DAILY_LOSS_LIMIT_PCT:
        state["daily_halted"] = True
        _persist(state)
        notify.kill_switch(f"Daily loss limit hit ({daily_loss_pct:.1f}%) — halted for the rest of today.")
        return True, f"daily loss limit hit ({daily_loss_pct:.1f}% today)"

    drawdown_pct = 100 * (state["peak_equity"] - current_equity) / state["peak_equity"]
    if drawdown_pct >= config.MAX_DRAWDOWN_PCT:
        state["hard_halted"] = True
        _persist(state)
        notify.kill_switch(f"Max drawdown hit ({drawdown_pct:.1f}% below peak) — halted until manually resumed.")
        return True, f"max drawdown hard halt ({drawdown_pct:.1f}% below peak equity)"

    _persist(state)
    return False, None


def _persist(state: dict) -> None:
    state_store.set_kill_switch_state(
        daily_date=state["daily_date"],
        daily_start_equity=state["daily_start_equity"],
        peak_equity=state["peak_equity"],
        hard_halted=state["hard_halted"],
        daily_halted=state["daily_halted"],
    )


def clear_hard_halt() -> None:
    """Run this yourself (`python risk_manager.py`) after you've reviewed
    what happened and decided to let the bot trade again. Only clears
    the hard (drawdown) halt — the daily loss halt always auto-resets
    at the next calendar day regardless."""
    state = state_store.get_kill_switch_state()
    state["hard_halted"] = False
    _persist(state)
    print("[risk_manager] Hard halt cleared.")


if __name__ == "__main__":
    state_store.init()
    clear_hard_halt()
