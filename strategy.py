"""
Donchian channel breakout with a volatility-adaptive lookback and a
trend-efficiency regime filter. Replaces the earlier "magic lines"
placeholder now that concrete rules are available (entry_signal.py is
retired — this is its replacement).

get_entry_signal() returns a dict rather than a bare direction, since
the engine also needs the ATR and the efficiency reading from the
SAME bar snapshot that produced the signal — recomputing them a
moment later against fresh candles could disagree with what actually
triggered the entry.
"""

from typing import TYPE_CHECKING, Optional

import config
import indicators

if TYPE_CHECKING:
    from metaapi_client import MetaApiClient


def get_entry_signal(client: "MetaApiClient", symbol: str, min_efficiency: float) -> Optional[dict]:
    candles = client.get_candles(symbol, config.TIMEFRAME, config.CANDLE_LOOKBACK)
    if len(candles) < config.ATR_BASELINE_PERIOD + config.ATR_PERIOD:
        return None

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    atr_vals = indicators.atr_series(highs, lows, closes, config.ATR_PERIOD)
    if len(atr_vals) < config.ATR_BASELINE_PERIOD:
        return None
    current_atr = atr_vals[-1]
    baseline_atr = sum(atr_vals[-config.ATR_BASELINE_PERIOD:]) / config.ATR_BASELINE_PERIOD

    lookback = indicators.adaptive_lookback(
        current_atr, baseline_atr,
        config.DONCHIAN_LOOKBACK_BASE, config.DONCHIAN_LOOKBACK_MIN, config.DONCHIAN_LOOKBACK_MAX,
    )
    channel = indicators.donchian_channel(highs, lows, lookback)

    efficiency = indicators.efficiency_ratio(closes, config.EFFICIENCY_RATIO_PERIOD)
    if efficiency < min_efficiency:
        return None  # choppy regime — sit out regardless of any breakout

    price = client.get_price(symbol)

    if price["ask"] > channel["upper"]:
        return {"direction": "long", "atr": current_atr, "lookback": lookback, "efficiency": efficiency}
    if price["bid"] < channel["lower"]:
        return {"direction": "short", "atr": current_atr, "lookback": lookback, "efficiency": efficiency}
    return None
