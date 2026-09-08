"""
Entry signal — decides the DIRECTION of the base (first) trade only.

This is deliberately separated from the martingale scaling/risk logic
in martingale_engine.py, which doesn't care how the direction was
chosen. Replace get_entry_signal() with whatever you actually want to
trade on (a different indicator, a news filter, manual triggers, etc).

Placeholder implementation: a simple fast/slow SMA crossover on
5-minute candles.
"""

from typing import Optional

from oanda_client import OandaClient


def get_entry_signal(client: OandaClient, instrument: str, fast: int = 10, slow: int = 30) -> Optional[str]:
    """Return 'long', 'short', or None (no signal)."""
    closes = client.get_candles(instrument, count=slow + 1)
    if len(closes) < slow + 1:
        return None

    fast_now = sum(closes[-fast:]) / fast
    slow_now = sum(closes[-slow:]) / slow
    fast_prev = sum(closes[-fast - 1:-1]) / fast
    slow_prev = sum(closes[-slow - 1:-1]) / slow

    crossed_up = fast_prev <= slow_prev and fast_now > slow_now
    crossed_down = fast_prev >= slow_prev and fast_now < slow_now

    if crossed_up:
        return "long"
    if crossed_down:
        return "short"
    return None
