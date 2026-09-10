"""
Configuration for the MT5 martingale forex bot.

SAFETY: This file ships with ENVIRONMENT = "practice" by default,
meaning it expects a demo account's login/password/server. Point it
at a live account's credentials only when you're ready — there's no
separate "live" switch for MT5 like there was for OANDA; which
account you connect to is entirely determined by which
login/password/server you put in MT5_LOGIN / MT5_PASSWORD / MT5_SERVER.
Keep demo and live credentials in separate .env files so you never
paste the wrong one in by habit.
"""

import os
from datetime import timedelta, timezone

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise SystemExit(f"❌ Missing required environment variable: {key}")
    return val


# --- MT5 connection -------------------------------------------------------
# The MetaTrader5 terminal must be installed and running on this same
# machine (Windows). These just tell the Python package which account
# to attach to.
MT5_LOGIN = int(_require("MT5_LOGIN"))
MT5_PASSWORD = _require("MT5_PASSWORD")
MT5_SERVER = _require("MT5_SERVER")

# --- Telegram notifications (optional) -------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- State persistence ------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", "./martingale_state.db")

WAT = timezone(timedelta(hours=1))  # West Africa Time (Abuja) — matches the rest of the repo

# --- Instrument / lookback --------------------------------------------------
SYMBOL = os.environ.get("MT5_SYMBOL", "EURUSD")
TIMEFRAME = "M15"          # see mt5_client.TIMEFRAMES for supported values
CANDLE_LOOKBACK = 200      # bars fetched for signal calculation

# --- Position sizing --------------------------------------------------------
# Base (first) entry lot size. Must fall within [BASE_LOT_MIN, BASE_LOT_MAX] —
# enforced below, not just documented, since this is the actual risk cap.
BASE_LOT_MIN = 0.02
BASE_LOT_MAX = 0.05
BASE_LOT = 0.02

# Multiplier applied to lot size at each scale-in.
SCALE_MULTIPLIER = 2.0

# Absolute ceiling — no single leg's lot size may exceed this, no matter
# what the multiplier computes to.
MAX_LOT_SIZE = 0.30

# Maximum scale-ins AFTER the base entry ("3 scale" = base + 3 more = 4 legs).
MAX_SCALES = 3

# Distance (pips) price must move against the average entry before adding
# the next scaled-in leg.
SCALE_TRIGGER_PIPS = 20

# Distance (pips) from the average entry at which the bot takes profit.
TAKE_PROFIT_PIPS = 15

# --- Hard stop (kill-switch backstop on the position itself) ---------------
# Computed once at base-entry time from the WORST-CASE fully-scaled price
# (see martingale_engine.py) and set as a real broker-side SL, so the
# position closes even if this process crashes or the network drops —
# it does not depend on the polling loop running.
HARD_STOP_PIPS_BEYOND_LAST_SCALE = 30

# --- Breakeven ---------------------------------------------------------------
# Once entry_signal.is_trend_confirmed() says the move is a real trend
# continuation (not just noise), the SL is moved up to the average entry
# price plus this small buffer (covers spread so "breakeven" isn't a
# guaranteed micro-loss).
BREAKEVEN_BUFFER_PIPS = 2

# --- Account-level kill switches --------------------------------------------
# Daily loss limit: once today's equity has fallen this % below where it
# started the day, trading halts until the next day (auto-resets).
DAILY_LOSS_LIMIT_PCT = 5.0

# Max drawdown from the highest equity ever recorded: once breached, the
# bot halts completely and stays halted until you manually clear it
# (see risk_manager.clear_hard_halt()) — this one does NOT auto-reset.
MAX_DRAWDOWN_PCT = 15.0

# How often the bot checks price/equity and re-evaluates (seconds).
POLL_INTERVAL_SECONDS = 15

# --- Validation --------------------------------------------------------------
# Fail fast and loud rather than silently trading outside the risk limits
# you set above.
if not (BASE_LOT_MIN <= BASE_LOT <= BASE_LOT_MAX):
    raise SystemExit(
        f"❌ BASE_LOT ({BASE_LOT}) must be between BASE_LOT_MIN ({BASE_LOT_MIN}) "
        f"and BASE_LOT_MAX ({BASE_LOT_MAX})."
    )
if MAX_LOT_SIZE < BASE_LOT_MAX:
    raise SystemExit("❌ MAX_LOT_SIZE must be >= BASE_LOT_MAX.")
