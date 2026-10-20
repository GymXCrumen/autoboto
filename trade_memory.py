"""
Adaptive learning layer.

This is NOT an LLM call in the trading loop — that would add latency
and an external dependency on every tick for a 10-second-poll scalper,
which is the wrong trade-off here. Instead it's the same statistical
pattern as the Telegram no-wick bot's memory engine: bucket the LIVE
features of a candidate signal, look up how similar past trades
actually performed, and turn that into a confidence score that scales
(never raises) position size, with a hard floor that skips genuinely
weak setups outright. "Generative" here means the bot's own behavior
changes shape over time from its trade history and the live market
reading at each decision — not that it calls a generative model.

Feature snapshot is taken ONCE at entry (strategy.py's signal dict +
the moment in time) and travels with the position via
state_store.open_position.features_json, so a trade is always scored
against the conditions that actually produced it, never conditions
read back later at close time.

SAFETY INVARIANT: compute_confidence() is hard-clamped to a ceiling of
1.0 everywhere in this file. Risk per trade is already validated in
config.py against the evaluation's exposure limits at
RISK_PER_TRADE_PCT — the learning layer is only ever allowed to shrink
that, never amplify it. Don't change the ceiling without re-checking
config.py's own validation block still holds.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import config
import state_store

UTC = timezone.utc

# Features the learning layer tracks per trade. Each must have a
# matching _bucket_* function below that turns a live value into a
# short string key.
FEATURE_KEYS = [
    "symbol", "direction", "hour_bucket", "day_of_week",
    "efficiency_bucket", "volatility_regime", "lookback_bucket",
]

# Tunables live in config.py (LEARNING_*) to match the rest of this
# project's convention of keeping every tunable parameter there. These
# names are kept as local aliases so the rest of this file reads cleanly.
MIN_SAMPLES_FOR_WEIGHT = config.LEARNING_MIN_SAMPLES
MIN_CONFIDENCE_TO_TRADE = config.LEARNING_MIN_CONFIDENCE_TO_TRADE
CONFIDENCE_FLOOR = config.LEARNING_CONFIDENCE_FLOOR
CONFIDENCE_CEILING = config.LEARNING_CONFIDENCE_CEILING

if CONFIDENCE_CEILING > 1.0:
    raise SystemExit(
        "❌ LEARNING_CONFIDENCE_CEILING > 1.0 — this would let the learning layer "
        "size UP past RISK_PER_TRADE_PCT, invalidating config.py's own exposure-math "
        "validation. This is a safety invariant, not a tuning knob — see trade_memory.py "
        "module docstring and the README's learning-layer section."
    )


# --- bucketing: live feature value -> a short string key for lookup ---------------

def _hour_bucket(dt_utc: datetime) -> str:
    start = (dt_utc.hour // 4) * 4
    return f"{start:02d}-{start + 4:02d}"


def _day_of_week(dt_utc: datetime) -> str:
    return dt_utc.strftime("%a")  # Mon, Tue, ...


def _efficiency_bucket(efficiency: float) -> str:
    if efficiency < 0.40:
        return "0.30-0.40"
    if efficiency < 0.50:
        return "0.40-0.50"
    if efficiency < 0.60:
        return "0.50-0.60"
    return "0.60+"


def _volatility_regime(atr_ratio: float) -> str:
    if atr_ratio < 0.8:
        return "contracting"
    if atr_ratio > 1.2:
        return "expanding"
    return "normal"


def _lookback_bucket(lookback: int) -> str:
    if lookback <= 15:
        return "short"
    if lookback <= 35:
        return "mid"
    return "long"


# --- feature extraction -------------------------------------------------------------

def extract_features(symbol: str, signal: dict) -> dict:
    """Build the feature snapshot for a candidate signal, at the moment
    it fired. signal is the dict strategy.get_entry_signal() returns
    (direction, atr, lookback, efficiency, atr_ratio)."""
    now = datetime.now(UTC)
    return {
        "symbol": symbol,
        "direction": signal["direction"],
        "hour_bucket": _hour_bucket(now),
        "day_of_week": _day_of_week(now),
        "efficiency_bucket": _efficiency_bucket(signal["efficiency"]),
        "volatility_regime": _volatility_regime(signal.get("atr_ratio", 1.0)),
        "lookback_bucket": _lookback_bucket(signal["lookback"]),
    }


# --- confidence scoring --------------------------------------------------------------

def _feature_weight(feature_name: str, bucket: str) -> float:
    wins, losses = state_store.get_feature_stats(feature_name, bucket)
    total = wins + losses
    if total < MIN_SAMPLES_FOR_WEIGHT:
        return 1.0  # not enough history on this bucket yet — stay neutral, don't guess
    win_rate = wins / total
    # 0% historical win rate -> 0.5x, 50% -> 1.0x, 100% -> 1.5x, then
    # clamped down to CONFIDENCE_CEILING below so it can only ever
    # shrink the eventual composite, never grow it past the base risk.
    return 0.5 + win_rate


def compute_confidence(features: dict) -> float:
    """Composite confidence in [CONFIDENCE_FLOOR, CONFIDENCE_CEILING].
    Averages the per-feature weights, then clamps — the clamp on the
    high side is the safety invariant described in the module
    docstring; the clamp on the low side keeps a single thin/unlucky
    bucket from zeroing out size entirely."""
    weights = [_feature_weight(key, features[key]) for key in FEATURE_KEYS if key in features]
    if not weights:
        return 1.0
    composite = sum(weights) / len(weights)
    return max(CONFIDENCE_FLOOR, min(composite, CONFIDENCE_CEILING))


def should_skip(confidence: float) -> bool:
    return confidence < MIN_CONFIDENCE_TO_TRADE


# --- feeding outcomes back in ---------------------------------------------------------

def log_trade_result(features: dict, won: bool) -> None:
    """Called once a trade CLOSES (base entries only — see
    scalping_engine.py; pyramid adds share the same ticket/net P&L so
    there's no clean way to attribute outcome to a specific leg,
    and attributing the whole trade's result to a pyramid add's
    features would misattribute signal quality that wasn't really
    being tested there)."""
    for key in FEATURE_KEYS:
        if key in features:
            state_store.increment_feature_stats(key, features[key], won)


# --- reporting ------------------------------------------------------------------------

def summary_text() -> str:
    """Short human-readable breakdown for a periodic Telegram push —
    overall sample size per feature and the best/worst bucket in each,
    only once a feature has enough samples to say anything meaningful."""
    rows = state_store.get_all_feature_stats()
    if not rows:
        return "No closed trades yet — nothing learned so far."

    by_feature: dict = {}
    for feature_name, bucket, wins, losses in rows:
        by_feature.setdefault(feature_name, []).append((bucket, wins, losses))

    lines = []
    for feature_name, buckets in by_feature.items():
        qualified = [(b, w, l) for b, w, l in buckets if (w + l) >= MIN_SAMPLES_FOR_WEIGHT]
        if not qualified:
            continue
        qualified.sort(key=lambda x: x[1] / (x[1] + x[2]), reverse=True)
        best = qualified[0]
        worst = qualified[-1]
        lines.append(
            f"*{feature_name}*: best `{best[0]}` ({best[1]}W/{best[2]}L, "
            f"{best[1] / (best[1] + best[2]) * 100:.0f}%), "
            f"worst `{worst[0]}` ({worst[1]}W/{worst[2]}L, "
            f"{worst[1] / (worst[1] + worst[2]) * 100:.0f}%)"
        )

    if not lines:
        return f"Learning in progress — no feature bucket has {MIN_SAMPLES_FOR_WEIGHT}+ trades yet."
    return "\n".join(lines)


def features_to_json(features: dict) -> str:
    return json.dumps(features)
