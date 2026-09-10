"""
Entry point. Wires the MT5 client, strategy hooks, and martingale
engine together.

Before running:
  1. Install and open the MT5 terminal on this machine, log into your
     account once manually so the terminal has cached credentials,
     and enable "Algo Trading" (Tools -> Options -> Expert Advisors,
     AND the toolbar toggle).
  2. Create a .env (or set env vars) with MT5_LOGIN, MT5_PASSWORD,
     MT5_SERVER — see .env.example / README.md.
  3. Review config.py — especially BASE_LOT, MAX_SCALES,
     HARD_STOP_PIPS_BEYOND_LAST_SCALE, DAILY_LOSS_LIMIT_PCT,
     MAX_DRAWDOWN_PCT.
  4. Replace the placeholder logic in entry_signal.py with the real
     magic lines rules once you've shared them.
  5. Test on a demo account/server first.
"""

import config
import risk_manager
import state_store
from entry_signal import get_entry_signal, is_trend_confirmed
from martingale_engine import MartingaleEngine
from mt5_client import MT5Client


def main():
    state_store.init()
    client = MT5Client(config.MT5_LOGIN, config.MT5_PASSWORD, config.MT5_SERVER)
    try:
        engine = MartingaleEngine(client)
        engine.run_forever(get_entry_signal, is_trend_confirmed)
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
