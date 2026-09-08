"""
Configuration for the OANDA martingale forex bot.

SAFETY: This file ships with ENVIRONMENT = "practice" by default.
Don't change it to "live" until you've run this against your OANDA
practice account for a meaningful stretch and understand the
worst-case loss the parameters below can produce (see README.md).
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


# --- OANDA connection ---------------------------------------------------
# Set these as environment variables (or in a local .env) — never
# hardcode real credentials here.
API_TOKEN = _require("OANDA_API_TOKEN")
ACCOUNT_ID = _require("OANDA_ACCOUNT_ID")

# "practice" or "live" — controls which OANDA API host is used.
# Override with: export OANDA_ENV="live"
ENVIRONMENT = os.environ.get("OANDA_ENV", "practice")

# --- Telegram notifications (optional) -----------------------------------
# Same idea as the other bots in this repo, but push-only: the bot
# still trades fine if these aren't set, it just won't notify.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- State persistence ----------------------------------------------------
# Railway's restart policy (see railway.json) restarts this process on
# failure — without persisting the open position, a restart mid-trade
# would forget which legs are actually open at the broker.
DB_PATH = os.environ.get("DB_PATH", "/app/data/martingale_state.db")

WAT = timezone(timedelta(hours=1))  # West Africa Time (Abuja) — matches the rest of the repo

# --- Instrument -----------------------------------------------------------
INSTRUMENT = "EUR_USD"

# --- Martingale scaling ---------------------------------------------------
# Units for the FIRST (base) entry. OANDA sizes positions in "units" of
# the base currency, not lots: 100,000 units = 1 standard lot,
# 10,000 = 1 mini lot, 1,000 = 1 micro lot.
BASE_UNITS = 1000  # 0.01 lots — deliberately small to start.

# Multiplier applied to position size at each successive scale-in.
# Classic martingale doubles (2.0); some traders use a gentler ramp.
SCALE_MULTIPLIER = 2.0

# Maximum number of scale-ins AFTER the base entry.
# "3 scale" is implemented here as: base entry + 3 additional scaled
# entries = 4 legs total. If you meant 3 legs TOTAL, set MAX_SCALES = 2.
MAX_SCALES = 3

# Distance (in pips) price must move AGAINST the current average entry
# before the bot adds the next scaled-in leg.
SCALE_TRIGGER_PIPS = 20

# Distance (in pips) FROM THE AVERAGE ENTRY PRICE at which the bot
# takes profit and closes the whole position.
TAKE_PROFIT_PIPS = 15

# --- Hard risk cap ----------------------------------------------------------
# This is the piece that actually caps a martingale's otherwise-unbounded
# downside. Once all MAX_SCALES legs are in, if price moves this many
# pips further against the position, the bot force-closes everything at
# a loss instead of waiting or adding more.
HARD_STOP_PIPS_BEYOND_LAST_SCALE = 30

# How often the bot checks price and re-evaluates (seconds).
POLL_INTERVAL_SECONDS = 15
