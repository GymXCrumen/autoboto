"""
Synchronous facade over the MetaApi cloud SDK (which is natively
async). Every call runs on a dedicated event loop so the rest of this
codebase — written against a plain synchronous client interface —
doesn't need an async rewrite. That's fine for a simple poll-every-
few-seconds bot; it would NOT be a good fit if you later want
MetaApi's streaming/push updates instead of polling, which need a
genuinely async listener.

Setup (one-time, outside this code):
  1. Sign up at https://app.metaapi.cloud and add your MT5 account
     there (your broker login/password/server go in ONCE, on their
     site — this bot never touches them directly).
  2. Grab an API token (app.metaapi.cloud/token) for METAAPI_TOKEN,
     and the account id MetaApi assigns your connected account for
     METAAPI_ACCOUNT_ID (NOT your MT5 login number).

Field names for candles/positions/symbol-specs/order results below
match MetaApi's documented schema (metaapi.cloud/docs/client/) as of
when this was written. Third-party APIs do change their fields over
time — if a call errors on something like a missing key, that page
is the place to check first, not a bug in the bot's logic.

One explicit simplification: order/modify calls below don't inspect
a broker return code for success — MetaApi's Python SDK raises an
exception on a rejected trade rather than returning a silent error
code (this is the common pattern for this SDK), and any exception
here already propagates up into the engine's existing error handling
(logged, Telegram-notified, loop continues). If your account/broker
combination behaves differently, that's the first place to add an
explicit check.
"""

import asyncio
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from metaapi_cloud_sdk import MetaApi

MAGIC_NUMBER = 20260910


class MetaApiError(RuntimeError):
    pass


class MetaApiClient:
    def __init__(self, token: str, account_id: str):
        self._loop = asyncio.new_event_loop()
        self.api = MetaApi(token)
        self.account = self._run(self.api.metatrader_account_api.get_account(account_id))
        self.connection = self.account.get_rpc_connection()
        self._run(self.connection.connect())
        self._run(self.connection.wait_synchronized())

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def shutdown(self) -> None:
        try:
            self._run(self.connection.close())
        except Exception:
            pass
        self._loop.close()

    def ensure_symbol(self, symbol: str) -> None:
        self._run(self.connection.subscribe_to_market_data(symbol))

    # --- market data -------------------------------------------------------

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list:
        """Dicts with open/high/low/close, oldest first."""
        candles = self._run(self.account.get_historical_candles(
            symbol=symbol, timeframe=timeframe,
            start_time=datetime.now(dt_timezone.utc), limit=count,
        ))
        candles = list(reversed(candles))  # API returns newest-first
        return [
            {"open": float(c["open"]), "high": float(c["high"]),
             "low": float(c["low"]), "close": float(c["close"])}
            for c in candles
        ]

    def get_price(self, symbol: str) -> dict:
        price = self._run(self.connection.get_symbol_price(symbol))
        return {"bid": float(price["bid"]), "ask": float(price["ask"])}

    def get_symbol_trading_info(self, symbol: str) -> dict:
        spec = self._run(self.connection.get_symbol_specification(symbol))
        tick_size = float(spec["tickSize"])
        contract_size = float(spec.get("contractSize", 100000))
        return {
            "tick_size": tick_size,
            # Approximation: exact when your account currency matches the
            # symbol's quote currency (e.g. a USD account trading XXX/USD).
            # MetaApi's symbol spec doesn't return a ready-made tick value
            # the way native MT5 does, and computing it exactly needs a
            # currency-conversion lookup this client doesn't perform.
            # Cross-check against MetaApi's margin calculator before
            # trusting this on a cross pair.
            "tick_value": tick_size * contract_size,
            "volume_min": float(spec.get("minVolume", 0.01)),
            "volume_max": float(spec.get("maxVolume", 100)),
            "volume_step": float(spec.get("volumeStep", 0.01)),
        }

    def get_account_info(self) -> dict:
        info = self._run(self.connection.get_account_information())
        return {"balance": float(info["balance"]), "equity": float(info["equity"])}

    # --- positions -----------------------------------------------------------

    def get_position(self, symbol: str) -> Optional[dict]:
        positions = self._run(self.connection.get_positions())
        ours = [p for p in positions if p.get("symbol") == symbol and p.get("magic") == MAGIC_NUMBER]
        if not ours:
            return None
        p = ours[0]
        return {
            "ticket": p["id"],
            "direction": "long" if p["type"] == "POSITION_TYPE_BUY" else "short",
            "volume": float(p["volume"]),
            "price_open": float(p["openPrice"]),
            "sl": p.get("stopLoss"),
            "tp": p.get("takeProfit"),
        }

    def get_last_deal_profit(self, ticket) -> Optional[float]:
        try:
            deals = self._run(self.connection.get_deals_by_position(position_id=str(ticket)))
        except Exception:
            return None
        if not deals:
            return None
        return sum(
            float(d.get("profit", 0)) + float(d.get("commission", 0)) + float(d.get("swap", 0))
            for d in deals
        )

    # --- trading ---------------------------------------------------------------

    def market_order(self, symbol: str, direction: str, lot: float,
                      sl: Optional[float] = None, tp: Optional[float] = None) -> dict:
        options = {"magic": MAGIC_NUMBER, "comment": "donchian-bot"}
        if direction == "long":
            result = self._run(self.connection.create_market_buy_order(
                symbol=symbol, volume=lot, stop_loss=sl, take_profit=tp, options=options))
        else:
            result = self._run(self.connection.create_market_sell_order(
                symbol=symbol, volume=lot, stop_loss=sl, take_profit=tp, options=options))
        price = result.get("price")
        if price is None:
            price = self.get_price(symbol)["ask" if direction == "long" else "bid"]
        return {"price": float(price)}

    def modify_sl_tp(self, symbol: str, ticket, sl: Optional[float] = None, tp: Optional[float] = None) -> None:
        if sl is None or tp is None:
            current = self.get_position(symbol)
            if current is None:
                raise MetaApiError(f"No open position found for {symbol} to modify.")
            sl = sl if sl is not None else current["sl"]
            tp = tp if tp is not None else current["tp"]
        self._run(self.connection.modify_position(position_id=str(ticket), stop_loss=sl, take_profit=tp))
