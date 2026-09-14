"""
Configuration for the Donchian breakout scalping bot.

Single-entry-plus-pyramid design: one base position with a real
ATR-based SL/TP (no averaging into losers, unlike the earlier
martingale draft), risk-sized per trade, with room to add to a
WINNING position on a fresh same-direction breakout (pyramiding, not
martingale). Account-level kill switches and the Agno-style
consecutive-loss cooldown both sit on top, independent of the entry
logic itself.

Runs against MetaApi (a cloud bridge to your MT5 account) rather than
a local MT5 terminal, so this deploys on Railway like your other bots
instead of needing a Windows VPS.
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


# --- MetaApi connection -------------------------------------------------------
# From app.metaapi.cloud: METAAPI_TOKEN is your API token, METAAPI_ACCOUNT_ID
# is the id of the MT5 account you added there (NOT your MT5 login number).
METAAPI_TOKEN = _require("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = _require("METAAPI_ACCOUNT_ID")

# --- Telegram notifications (optional) -----------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- State persistence ------------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", "/app/data/scalping_state.db")

WAT = timezone(timedelta(hours=1))  # West Africa Time (Abuja)

# --- Instrument / timeframe ------------------------------------------------------
SYMBOL = os.environ.get("MT5_SYMBOL", "EURUSD")

# MT5 has no native candle below 1 minute, so M1 is the practical floor
# for "scalping" here — a true sub-minute/tick-based version would need
# a different data source than candles (see README.md).
TIMEFRAME = "1m"
CANDLE_LOOKBACK = 200  # bars fetched per check — covers the 100-bar ATR baseline below

# How often the bot checks price and re-evaluates (seconds). The Donchian
# channel itself only updates once a minute (M1 bars), but comparing the
# live tick price against it each poll still catches a breakout close to
# when it actually happens rather than waiting for the next full bar.
POLL_INTERVAL_SECONDS = 3

# --- Donchian channel (adaptive lookback) ----------------------------------------
DONCHIAN_LOOKBACK_BASE = 20
DONCHIAN_LOOKBACK_MIN = 10
DONCHIAN_LOOKBACK_MAX = 50

# --- ATR (stop/target sizing and the adaptive-lookback volatility ratio) --------
ATR_PERIOD = 14
ATR_BASELINE_PERIOD = 100  # longer average ATR used as the "normal volatility" reference
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.0

# --- Position sizing (risk-based) ------------------------------------------------
RISK_PER_TRADE_PCT = 2.0
# Absolute ceiling on a single BASE entry, regardless of what the risk
# calculation produces — a backstop against a data glitch or an
# unrealistically tight stop.
MAX_LOT_SIZE = 0.30

# --- Pyramiding on strength (NOT martingale — only adds to a position
# that's already at/beyond breakeven, never to one that's losing) ---------------
# Once breakeven is set (see below) and a FRESH same-direction breakout
# fires while regime is still favorable, the bot adds another leg —
# still risk-sized the same way, but capped so the position's TOTAL
# volume never exceeds this, distinct from (and higher than) the
# single-entry MAX_LOT_SIZE above.
PYRAMID_MAX_TOTAL_LOT = 0.5

# --- Breakeven ---------------------------------------------------------------------
# Once price has moved this many ATRs in favor of the position, SL moves
# to the average entry price + a small buffer (spread cover), and stays
# there. This is also the gate that allows pyramiding (see above) —
# a position only gets added to once it's proven itself, never while losing.
BREAKEVEN_TRIGGER_ATR_MULTIPLE = 1.0
BREAKEVEN_BUFFER_ATR_MULTIPLE = 0.1

# --- Market regime filter (Agno layer, implemented as Kaufman's Efficiency Ratio) -
# Donchian breakouts are far more reliable in a trending market than a
# choppy one. Efficiency Ratio is net movement / total path length over
# the period: near 1.0 = trending, near 0 = choppy. Below the threshold,
# the bot sits out new entries (and new pyramid legs) regardless of any
# breakout.
EFFICIENCY_RATIO_PERIOD = 20
REGIME_EFFICIENCY_THRESHOLD = 0.30

# --- Consecutive-loss handling (Agno layer: pause, then reduce + get more selective) -
CONSECUTIVE_LOSS_LIMIT = 3
COOLDOWN_MINUTES = 30          # no new entries for this long after the limit is hit
SIZE_REDUCTION_FACTOR = 0.5    # applied to risk-based lot size once trading resumes
SELECTIVITY_EFFICIENCY_BOOST = 0.15  # raises the efficiency threshold during the reduced-size period

# --- Account-level kill switches --------------------------------------------------
DAILY_LOSS_LIMIT_PCT = 5.0
MAX_DRAWDOWN_PCT = 15.0

# --- Validation ---------------------------------------------------------------------
if RISK_PER_TRADE_PCT <= 0 or RISK_PER_TRADE_PCT > 10:
    raise SystemExit(f"❌ RISK_PER_TRADE_PCT ({RISK_PER_TRADE_PCT}) looks wrong — expected a small percent, e.g. 2.0.")
if PYRAMID_MAX_TOTAL_LOT < MAX_LOT_SIZE:
    raise SystemExit("❌ PYRAMID_MAX_TOTAL_LOT must be >= MAX_LOT_SIZE.")
