"""
Three responsibilities, kept separate from the strategy code:

1. Position sizing — from risk_per_trade%, the ATR stop distance, and
   MT5's own tick value (so no hand-rolled currency conversion).
2. Account-level kill switches — a daily line (auto-resets at the next
   UTC day) and a max-loss fail level FIXED off the account's starting
   balance (manual resume only, and only if the breach turns out to
   have been a false trigger from the safety buffer). Deliberately NOT
   a trailing/peak-based drawdown check — see check_kill_switches()
   below for why.
3. The Agno-style layer — after too many losses in a row, pause new
   entries for a cooldown period, then come back in at reduced size
   and a stricter regime threshold ("more selective") until a win
   resets it.
"""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional, Tuple

UTC = timezone.utc

import config
import notify
import state_store

if TYPE_CHECKING:
    from metaapi_client import MetaApiClient


# --- position sizing -----------------------------------------------------------

def risk_amount_for(account_equity: float, reduced: bool = False, confidence: float = 1.0) -> float:
    """Single source of truth for how much is actually being risked on a
    base entry, so scalping_engine.py can store the SAME number it used
    for sizing rather than recomputing it (and risking drift) when it
    later needs it to compute r_multiple at close.

    confidence is hard-clamped to <= 1.0 here regardless of what's
    passed in — the learning layer (trade_memory.py) is only ever
    allowed to shrink risk below the configured RISK_PER_TRADE_PCT,
    never raise it above. config.py's own exposure-math validation at
    the bottom of that file assumes RISK_PER_TRADE_PCT as the ceiling;
    letting confidence exceed 1.0 would silently invalidate that."""
    confidence = max(0.0, min(confidence, 1.0))
    risk_amount = account_equity * (config.RISK_PER_TRADE_PCT / 100) * confidence
    if reduced:
        risk_amount *= config.SIZE_REDUCTION_FACTOR
    return risk_amount


def position_size(client: "MetaApiClient", symbol: str, account_equity: float, atr: float,
                   reduced: bool = False, confidence: float = 1.0) -> float:
    info = client.get_symbol_trading_info(symbol)
    stop_distance_price = atr * config.STOP_LOSS_ATR_MULTIPLIER
    if stop_distance_price <= 0 or info["tick_size"] <= 0:
        return info["volume_min"]

    value_per_price_unit_per_lot = info["tick_value"] / info["tick_size"]
    risk_amount = risk_amount_for(account_equity, reduced=reduced, confidence=confidence)

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


# --- account-level kill switches, matching the evaluation's actual rules ------------
#
# Fail level: FIXED at MAX_LOSS_PCT below the account's starting balance,
# captured once and never moved. Daily line: a FIXED amount (a % of
# STARTING balance) below TODAY's opening equity, on a UTC trading day.
# Both get an internal safety buffer since the evaluation judges live,
# every tick, and this bot only polls every few seconds.

def _today_utc_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def ensure_starting_balance(current_balance: float) -> float:
    """Call once at startup. Captures the account's starting balance the
    very first time this ever runs; every later call is a no-op that just
    returns the already-captured value, even if the account has since made
    or lost money — this number must never move."""
    existing = state_store.get_account_state()
    if existing is None:
        state_store.set_starting_balance(current_balance)
        notify.kill_switch(
            f"Starting balance captured: {current_balance:,.2f}. Fail level is fixed at "
            f"{current_balance * (1 - config.MAX_LOSS_PCT / 100):,.2f} for the life of this account."
        )
        return current_balance
    return existing["starting_balance"]


def check_kill_switches(current_equity: float) -> Tuple[bool, Optional[str]]:
    starting_balance = ensure_starting_balance(current_equity)
    state = state_store.get_kill_switch_state()
    today_utc = _today_utc_str()

    if state["daily_date_utc"] != today_utc:
        state = {
            "daily_date_utc": today_utc,
            "daily_opening_equity": current_equity,
            "hard_halted": state["hard_halted"],
            "daily_halted": False,
        }

    if state["hard_halted"]:
        _persist_kill_switch(state)
        return True, "fail level breached — this evaluation account is likely already closed by the prop firm"

    if state["daily_halted"]:
        _persist_kill_switch(state)
        return True, "daily line hit today (auto-resumes at 00:00 UTC)"

    buffer = config.KILL_SWITCH_SAFETY_BUFFER_PCT / 100

    fail_level = starting_balance * (1 - config.MAX_LOSS_PCT / 100)
    internal_fail_trigger = fail_level * (1 + buffer)  # halt a bit before the real line
    if current_equity <= internal_fail_trigger:
        state["hard_halted"] = True
        _persist_kill_switch(state)
        notify.kill_switch(
            f"FAIL LEVEL — equity {current_equity:,.2f} vs fixed fail level {fail_level:,.2f} "
            f"(starting balance {starting_balance:,.2f}). Halted — check the account immediately."
        )
        return True, f"fail level breached (equity {current_equity:,.2f} <= {fail_level:,.2f})"

    daily_amount = starting_balance * (config.DAILY_LOSS_PCT_OF_STARTING / 100)
    daily_floor = state["daily_opening_equity"] - daily_amount
    internal_daily_trigger = daily_floor * (1 + buffer)
    if current_equity <= internal_daily_trigger:
        state["daily_halted"] = True
        _persist_kill_switch(state)
        notify.kill_switch(
            f"DAILY LINE — equity {current_equity:,.2f} vs today's floor {daily_floor:,.2f} "
            f"(opening equity {state['daily_opening_equity']:,.2f} - {daily_amount:,.2f}). "
            f"Halted for the rest of the UTC trading day."
        )
        return True, f"daily line breached (equity {current_equity:,.2f} <= {daily_floor:,.2f})"

    _persist_kill_switch(state)
    return False, None


def _persist_kill_switch(state: dict) -> None:
    state_store.set_kill_switch_state(
        daily_date_utc=state["daily_date_utc"], daily_opening_equity=state["daily_opening_equity"],
        hard_halted=state["hard_halted"], daily_halted=state["daily_halted"],
    )


def clear_hard_halt() -> None:
    """The prop firm's own fail level closes the account server-side the
    moment it's touched — this just stops the bot from continuing to try.
    Only run this if you've confirmed with the firm that the account is
    actually still active (e.g. this was a false trigger from the safety
    buffer, not a real breach)."""
    state = state_store.get_kill_switch_state()
    state["hard_halted"] = False
    _persist_kill_switch(state)
    print("[risk_manager] Hard halt cleared.")


# --- inactivity rule -----------------------------------------------------------------

def check_inactivity() -> Optional[str]:
    """Returns a warning message once close to the limit, else None. Never
    forces a trade — only ever warns."""
    account = state_store.get_account_state()
    if account is None or not account["last_trade_closed_at"]:
        return None  # no trade has closed yet; nothing to measure from
    last_closed = datetime.fromisoformat(account["last_trade_closed_at"])
    days_idle = (datetime.now(UTC) - last_closed).days
    if days_idle >= config.INACTIVITY_LIMIT_DAYS:
        return f"{days_idle} days since the last closed trade — the {config.INACTIVITY_LIMIT_DAYS}-day inactivity limit may have already been breached."
    if days_idle >= config.INACTIVITY_WARNING_DAYS:
        return f"{days_idle} days since the last closed trade — {config.INACTIVITY_LIMIT_DAYS} days ends the account."
    return None


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
