"""
Strategy hooks — everything that depends on "magic lines" lives here,
clearly marked as placeholder until you share that strategy's actual
rules. The rest of the bot (risk sizing, kill switches, scaling,
breakeven mechanics) doesn't need to change once you do — only the
bodies of these two functions do.

get_entry_signal()     — decides the direction of the BASE entry.
is_trend_confirmed()   — decides when to move the SL to breakeven.

Placeholder implementation below: a fast/slow SMA crossover over the
configured 200-candle lookback, with is_trend_confirmed() always
False (so breakeven never fires) until real logic replaces it.
"""

from typing import Optional

import config
from mt5_client import MT5Client


def get_entry_signal(client: MT5Client, symbol: str, fast: int = 20, slow: int = 50) -> Optional[str]:
    """Return 'long', 'short', or None. TEMPORARY placeholder — replace
    with the magic lines entry rules."""
    closes = client.get_candles(symbol, config.TIMEFRAME, config.CANDLE_LOOKBACK)
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


def is_trend_confirmed(client: MT5Client, symbol: str, direction: str) -> bool:
    """Return True once there's a 'legitimate' trend continuation in
    `direction`, at which point the engine moves SL to breakeven.
    TEMPORARY placeholder — always False until replaced with the
    magic lines trend-confirmation rule."""
    return False
