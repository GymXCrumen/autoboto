"""
Minimal OANDA v20 REST API client built on `requests`.
Only the handful of endpoints this bot needs — kept dependency-light
and easy to read/audit rather than pulling in a full SDK.
"""

from typing import Optional

import requests

HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class OandaClient:
    def __init__(self, api_token: str, account_id: str, environment: str = "practice"):
        if not api_token or not account_id:
            raise ValueError(
                "OANDA_API_TOKEN and OANDA_ACCOUNT_ID must be set as environment variables."
            )
        if environment not in HOSTS:
            raise ValueError(f"environment must be one of {list(HOSTS)}")

        self.account_id = account_id
        self.base_url = HOSTS[environment]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        })

    def get_price(self, instrument: str) -> dict:
        """Return {'bid': float, 'ask': float} for an instrument."""
        url = f"{self.base_url}/v3/accounts/{self.account_id}/pricing"
        resp = self.session.get(url, params={"instruments": instrument}, timeout=10)
        resp.raise_for_status()
        price = resp.json()["prices"][0]
        return {
            "bid": float(price["bids"][0]["price"]),
            "ask": float(price["asks"][0]["price"]),
        }

    def get_open_position(self, instrument: str) -> Optional[dict]:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/positions/{instrument}"
        resp = self.session.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()["position"]

    def market_order(self, instrument: str, units: int) -> dict:
        """units > 0 = buy, units < 0 = sell."""
        url = f"{self.base_url}/v3/accounts/{self.account_id}/orders"
        body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }
        resp = self.session.post(url, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def close_position(self, instrument: str, side: str) -> dict:
        """side = 'long' or 'short'."""
        url = f"{self.base_url}/v3/accounts/{self.account_id}/positions/{instrument}/close"
        body = {"longUnits": "ALL"} if side == "long" else {"shortUnits": "ALL"}
        resp = self.session.put(url, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_account_summary(self) -> dict:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/summary"
        resp = self.session.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()["account"]

    def get_candles(self, instrument: str, count: int, granularity: str = "M5") -> list:
        """Return a list of completed mid-close prices, oldest first."""
        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        resp = self.session.get(
            url,
            params={"granularity": granularity, "count": count, "price": "M"},
            timeout=10,
        )
        resp.raise_for_status()
        candles = resp.json()["candles"]
        return [float(c["mid"]["c"]) for c in candles if c["complete"]]
