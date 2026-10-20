"""
Configuration for the Donchian breakout bot (5-minute timeframe).

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
# Narrowed from the original 7 majors to these 2. Both are USD-QUOTE
# pairs (EUR/USD, GBP/USD) on a USD account, so metaapi_client.py's
# tick_value approximation (tick_size * contract_size) is EXACT here —
# no cross-rate conversion gap like USDJPY/USDCHF/USDCAD would have.
# They're also the two most liquid, tightest-spread majors, which
# matters directly for this bot: the kill-switch safety buffer exists
# to cover slippage/lag between polls, and tighter spreads mean less
# of that buffer gets eaten by execution noise rather than genuine
# adverse moves. Gold/XAUUSD was considered and intentionally excluded
# — its much larger ATR swings don't suit the fixed ATR multipliers
# below as cleanly as they suit true forex majors.
MAJOR_PAIRS = ["EURUSD", "GBPUSD"]

# Caps how many of the majors can have an open position AT THE SAME TIME.
# This matters a lot here: RISK_PER_TRADE_PCT is per-trade, so N simultaneous
# positions each stopping out at once means roughly N x that % of equity at
# risk together. At 2% per trade, a cap of 2 keeps worst-case simultaneous
# exposure (4%) under the evaluation's 6% fail level and close to its 4%
# daily line — a cap of 3+ would put worst-case exposure AT or PAST the
# daily line in one bad moment. Raise this only with that arithmetic in mind.
MAX_CONCURRENT_POSITIONS = 2

# MT5 has no native candle below 1 minute; 5-minute strikes a better
# balance than 1-minute for this strategy — fewer low-quality breakouts
# from pure noise, still frequent enough to trade actively within a day.
TIMEFRAME = "5m"
CANDLE_LOOKBACK = 200  # bars fetched per check — covers the 100-bar ATR baseline below

# How often the bot checks price and re-evaluates (seconds). A 5-minute
# bar gives more time to react than a 1-minute one did, so this can be
# a bit more relaxed without missing anything meaningful.
POLL_INTERVAL_SECONDS = 10

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

# --- Account-level kill switches, matching the evaluation account's actual rules ---
# These replace the earlier generic 5%-daily/15%-trailing-drawdown switches
# with the evaluation's exact mechanics:
#
#  - Fail level is FIXED at MAX_LOSS_PCT below the account's STARTING
#    balance — captured once (state_store.account_state) and never moved,
#    not even as the account makes new profit highs. This is deliberately
#    NOT a trailing/peak-based drawdown check; the evaluation's fail level
#    genuinely doesn't trail.
#  - The daily line is a FIXED amount — DAILY_LOSS_PCT_OF_STARTING % of the
#    STARTING balance, e.g. 4% of a 1,500,000 start is a fixed 60,000 —
#    subtracted from TODAY's opening equity (which does change day to day).
#    The trading day boundary is UTC, per the evaluation's own rule, not
#    the WAT used elsewhere in this repo for display timestamps.
MAX_LOSS_PCT = 6.0
DAILY_LOSS_PCT_OF_STARTING = 4.0

# The evaluation judges both lines live, every tick; this bot only checks
# every POLL_INTERVAL_SECONDS. A fast move could cross the real line
# between two polls, so the bot halts a bit earlier than the actual rule
# to leave room for that lag (and spread/slippage) — tune down if you
# want it more conservative, but don't remove it entirely.
KILL_SWITCH_SAFETY_BUFFER_PCT = 1.0

# --- Learning layer (trade_memory.py) ----------------------------------------------
# A feature bucket (e.g. "hour_bucket: 08-12") needs at least this many
# closed trades before its win rate is trusted enough to move the
# confidence score — below it, the bucket stays neutral (1.0x).
LEARNING_MIN_SAMPLES = 8
# Composite confidence below this skips the trade entirely, regardless
# of position sizing.
LEARNING_MIN_CONFIDENCE_TO_TRADE = 0.45
# Size multiplier floor/ceiling. The ceiling is a SAFETY INVARIANT, not
# just a tuning knob: the learning layer may only ever shrink risk
# below RISK_PER_TRADE_PCT, never raise it above — the exposure-math
# validation at the bottom of this file assumes RISK_PER_TRADE_PCT as
# the ceiling. Do not raise LEARNING_CONFIDENCE_CEILING above 1.0.
LEARNING_CONFIDENCE_FLOOR = 0.4
LEARNING_CONFIDENCE_CEILING = 1.0

# --- Inactivity rule --------------------------------------------------------------
# The evaluation ends the account after this many days with no CLOSED
# trade. The bot never forces a trade to avoid this — that would be
# trading against the strategy's own judgment purely to satisfy a
# calendar rule — it only warns you with enough runway to act.
INACTIVITY_LIMIT_DAYS = 18
INACTIVITY_WARNING_DAYS = 14

# --- Validation ---------------------------------------------------------------------
if RISK_PER_TRADE_PCT <= 0 or RISK_PER_TRADE_PCT > 10:
    raise SystemExit(f"❌ RISK_PER_TRADE_PCT ({RISK_PER_TRADE_PCT}) looks wrong — expected a small percent, e.g. 2.0.")
if PYRAMID_MAX_TOTAL_LOT < MAX_LOT_SIZE:
    raise SystemExit("❌ PYRAMID_MAX_TOTAL_LOT must be >= MAX_LOT_SIZE.")
if MAX_CONCURRENT_POSITIONS * RISK_PER_TRADE_PCT > DAILY_LOSS_PCT_OF_STARTING:
    print(
        f"⚠️  MAX_CONCURRENT_POSITIONS ({MAX_CONCURRENT_POSITIONS}) x RISK_PER_TRADE_PCT "
        f"({RISK_PER_TRADE_PCT}%) = {MAX_CONCURRENT_POSITIONS * RISK_PER_TRADE_PCT}% worst-case "
        f"simultaneous exposure, which is AT OR PAST the {DAILY_LOSS_PCT_OF_STARTING}% daily line. "
        f"Not a hard stop, but worth lowering one of the two."
    )
