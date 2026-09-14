"""
Indicator math for the Donchian breakout + Agno-style risk overlay.
Pure Python — bar counts here are small (a couple hundred M1 candles),
so this isn't a performance concern.
"""

from typing import List


def true_range_series(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return tr


def atr_series(highs: List[float], lows: List[float], closes: List[float], period: int) -> List[float]:
    """Wilder's smoothed ATR — the same method MT5's built-in ATR indicator uses."""
    tr = true_range_series(highs, lows, closes)
    if len(tr) < period:
        return []
    atr = [sum(tr[:period]) / period]
    for i in range(period, len(tr)):
        atr.append((atr[-1] * (period - 1) + tr[i]) / period)
    return atr


def donchian_channel(highs: List[float], lows: List[float], lookback: int) -> dict:
    """Upper/lower channel over the last `lookback` COMPLETED bars,
    excluding the most recent one, so a live tick price can be compared
    against a channel that doesn't already include that same bar."""
    window_highs = highs[-lookback - 1:-1]
    window_lows = lows[-lookback - 1:-1]
    return {"upper": max(window_highs), "lower": min(window_lows)}


def adaptive_lookback(current_atr: float, baseline_atr: float, base: int, lo: int, hi: int) -> int:
    """Shrinks the Donchian lookback when volatility is elevated (so the
    channel reacts faster to a genuine expansion) and lengthens it when
    volatility is subdued (so small moves in a quiet market don't
    trigger noise breakouts). This direction is a design choice — the
    original pseudocode's adaptive_function() wasn't specified further,
    so tune DONCHIAN_LOOKBACK_MIN/MAX in config.py if you want it to
    behave differently."""
    if baseline_atr <= 0:
        return base
    scaled = round(base * (baseline_atr / current_atr))
    return max(lo, min(scaled, hi))


def efficiency_ratio(closes: List[float], period: int) -> float:
    """Kaufman's Efficiency Ratio: net movement over `period` bars
    divided by the total path length travelled to get there. Near 1.0
    = efficient/trending; near 0 = choppy/noisy. Used here as the
    'market regime' proxy — Donchian breakouts are far more reliable
    in a trending regime than a choppy one."""
    if len(closes) < period + 1:
        return 0.0
    window = closes[-period - 1:]
    net_change = abs(window[-1] - window[0])
    path_length = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path_length == 0:
        return 0.0
    return net_change / path_length
