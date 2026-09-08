"""
Entry point. Wires the OANDA client, entry signal, and martingale
engine together.

Before running:
  1. export OANDA_API_TOKEN="..."
  2. export OANDA_ACCOUNT_ID="..."
  3. (optional) export TELEGRAM_BOT_TOKEN=... / TELEGRAM_CHAT_ID=...
     for push notifications.
  4. Review config.py — especially ENVIRONMENT, BASE_UNITS, MAX_SCALES,
     and HARD_STOP_PIPS_BEYOND_LAST_SCALE.
  5. Run against ENVIRONMENT="practice" first if at all possible. Only
     switch to "live" once you've watched it handle a real losing
     streak on demo money.
"""

import config
import state_store
from entry_signal import get_entry_signal
from martingale_engine import MartingaleEngine
from oanda_client import OandaClient


def main():
    state_store.init()
    client = OandaClient(config.API_TOKEN, config.ACCOUNT_ID, config.ENVIRONMENT)
    engine = MartingaleEngine(client)
    engine.run_forever(get_entry_signal)


if __name__ == "__main__":
    main()
