"""
Entry point. Wires the MetaApi client, the Donchian strategy, and the
scalping engine together.

Before running:
  1. Sign up at app.metaapi.cloud, add your MT5 account there (your
     broker login/password/server are entered on their site, once),
     and grab an API token + the account id MetaApi assigns it.
  2. Create a .env (or set env vars) with METAAPI_TOKEN,
     METAAPI_ACCOUNT_ID — see .env.example.
  3. Review config.py — especially RISK_PER_TRADE_PCT, the ATR
     multipliers, PYRAMID_MAX_TOTAL_LOT, CONSECUTIVE_LOSS_LIMIT, and
     the kill-switch %s.
  4. Test on a demo account/server first.
"""

import config
import state_store
from metaapi_client import MetaApiClient
from scalping_engine import ScalpingEngine
from strategy import get_entry_signal


def main():
    state_store.init()
    client = MetaApiClient(config.METAAPI_TOKEN, config.METAAPI_ACCOUNT_ID)
    try:
        engine = ScalpingEngine(client)
        engine.run_forever(get_entry_signal)
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
