# Donchian Breakout Bot — 5-Minute (MT5 via MetaApi)

Single-entry Donchian channel breakout with an ATR-based stop/target,
a volatility-adaptive lookback, and an efficiency-ratio regime filter
("Agno" layer) that pauses the bot after a losing streak and comes
back more selective. No martingale — a position is only ever added to
once it's already profitable (pyramiding), never to recover a loss.

Connects to your MT5 account through MetaApi (a cloud bridge) rather
than a local terminal, so this runs on Railway like your other bots.

## Strategy, as implemented

- **Entry**: price breaks above/below a Donchian channel (highest
  high / lowest low over a lookback window), computed from completed
  5-minute candles.
- **Adaptive lookback**: the lookback shrinks when current ATR is
  elevated vs. its 100-bar baseline (channel reacts faster to a real
  expansion) and lengthens when volatility is subdued (fewer noise
  breakouts in a quiet market). This direction was a judgment call
  since the original pseudocode's `adaptive_function()` wasn't
  specified further — tune `DONCHIAN_LOOKBACK_MIN/MAX` in `config.py`
  if you want different behavior.
- **Regime filter**: implemented as Kaufman's Efficiency Ratio (net
  move / total path length over the window) — near 1.0 means
  trending, near 0 means choppy. Below `REGIME_EFFICIENCY_THRESHOLD`,
  the bot sits out new entries *and* pyramid adds regardless of any
  breakout. This is the concrete form of the pseudocode's
  `evaluate_market_regime() == "unfavorable"`.
- **Stop / target**: `entry ± ATR × multiplier`, both multipliers in
  `config.py` (1.5 / 2.0, matching the pseudocode). Both are real
  broker-side orders — the bot doesn't execute the exit itself, it
  detects that MT5 already did.
- **Position sizing**: `risk_per_trade% × equity`, divided by the ATR
  stop distance converted to account currency via the symbol's tick
  size/value, then clamped to the broker's lot step and
  `MAX_LOT_SIZE`. See the tick-value caveat in `metaapi_client.py` —
  it's an approximation that's exact for same-currency pairs (e.g. a
  USD account trading XXX/USD) and may need a cross-rate adjustment
  otherwise.

## Breakeven and pyramiding

- **Breakeven**: once price has moved `BREAKEVEN_TRIGGER_ATR_MULTIPLE`
  ATRs in the position's favor, SL moves to the average entry price
  plus a small buffer, and stays there.
- **Pyramiding** (the "multiple positions on a highly accurate signal"
  requirement): only triggers *after* breakeven is set — i.e., only
  on a position that's already proven itself. If a fresh same-direction
  breakout fires while the regime filter still passes, the bot adds
  another leg, risk-sized the same way, capped so the position's
  *total* volume never exceeds `PYRAMID_MAX_TOTAL_LOT` (0.5). Your
  account is netting, so this shows up as MT5 growing one position's
  volume, not as separate tickets.
  **Known limitation**: because the Donchian channel only updates once
  new bars roll in, price can stay above/below it for many ticks in a
  row during a strong sustained move — as written, each qualifying
  tick can trigger another pyramid add (bounded only by the 0.5 lot
  ceiling). If that adds faster than you'd like, the cheapest fix is
  requiring a fresh bar to close beyond the *previous pyramid's* entry
  price, not just "still above the original channel" — flag it if you
  want that built in.

## Kill switches vs. the Agno layer — two different things, both kept

- **Account-level kill switches now mirror the evaluation account's
  actual rules, not generic percentages:**
  - **Fail level**: fixed at `MAX_LOSS_PCT` (6%) below the account's
    *starting* balance — captured once, on the very first run, and
    never recalculated, not even as the account makes new profit
    highs. This deliberately does NOT trail a peak — the evaluation's
    own fail level doesn't either ("it never moves — not with profit,
    not with a new peak").
  - **Daily line**: a *fixed amount* — `DAILY_LOSS_PCT_OF_STARTING`
    (4%) of the *starting* balance — subtracted from *today's opening
    equity*, on a UTC trading day boundary (the evaluation's rule,
    not the WAT used for display timestamps elsewhere in this repo).
    Auto-resumes at the next UTC day.
  - Both include `KILL_SWITCH_SAFETY_BUFFER_PCT` (1%) of internal
    margin, since the evaluation judges live, every tick, and this
    bot only polls every few seconds — a fast move could otherwise
    cross the real line between two polls.
  - Hitting the fail level halts the bot, but the prop firm's own
    system closes the account server-side the moment it happens —
    `python risk_manager.py` only clears the bot's own halt flag, for
    the case where the buffer triggered early on something that
    turned out fine. It can't and doesn't un-close an actually-failed
    account.
- **Agno consecutive-loss layer** (unchanged, and account-wide across
  all symbols, not per-symbol): after `CONSECUTIVE_LOSS_LIMIT` (3)
  losses in a row on ANY of the majors, the bot pauses new entries
  everywhere for `COOLDOWN_MINUTES` (30), then resumes at
  `SIZE_REDUCTION_FACTOR` (half) size and a stricter efficiency
  threshold, until a win resets it. Softer than the kill switches
  above — it can trip and recover several times a day.
- **Inactivity rule**: the evaluation ends the account after
  `INACTIVITY_LIMIT_DAYS` (18) with no closed trade. The bot tracks
  this and sends a Telegram warning starting at
  `INACTIVITY_WARNING_DAYS` (14) — it never forces a trade to avoid
  this, since trading purely to satisfy a calendar rule defeats the
  point of having a strategy at all.

## Trading two pairs, not one

`config.MAJOR_PAIRS` is narrowed to EURUSD and GBPUSD — both USD-quote
pairs on a USD account, so the tick-value approximation in
`metaapi_client.py` is exact (no cross-rate gap like a USDJPY/USDCHF/
USDCAD pair would introduce), and both are the most liquid,
tightest-spread majors, which matters for the kill-switch safety
buffer (less of it gets eaten by spread/slippage noise). Gold/XAUUSD
was deliberately left out — its ATR swings are large enough that the
fixed ATR multipliers below would need separate tuning to suit it.

The engine still scans every symbol in the list independently every
tick — each can have its own open position, its own breakeven/pyramid
state, all sharing the SAME account-level kill switches and Agno
cooldown above.

`MAX_CONCURRENT_POSITIONS` (2) caps how many majors can be open at
once, specifically because of the math against the limits above: at
`RISK_PER_TRADE_PCT` = 2%, 2 concurrent positions means a worst case
of ~4% simultaneous exposure if both stopped out at once — under the
6% fail level and close to (but under) the 4% daily line. Raising the
cap without lowering per-trade risk pushes worst-case exposure past
the daily line in a single bad moment; `config.py` prints a warning
at import time if the numbers you set don't clear this math.

## The learning layer (`trade_memory.py`)

The bot adapts to its own trade history and current live-market
conditions, rather than trading every signal identically forever. This
is a statistical bucket-and-score system, not an LLM call in the
trading loop — a 10-second-poll scalper can't afford that latency or
external dependency on every tick.

**What it tracks per trade** — captured once, at the moment of entry,
from the same bar snapshot that produced the signal:
`symbol`, `direction`, `hour_bucket` (4-hour UTC blocks), `day_of_week`,
`efficiency_bucket` (how trending the regime was), `volatility_regime`
(current ATR vs its 100-bar baseline — contracting/normal/expanding),
`lookback_bucket` (how far the adaptive Donchian lookback had shrunk
or stretched).

**What it does with that:** every time a trade closes, each of those
feature buckets gets its win/loss count updated
(`state_store.feature_stats`). Before the NEXT candidate signal is
acted on, each of its own feature buckets is looked up the same way,
averaged into one composite confidence score, and that score:

- **Scales position size down** (never up) on setups whose matching
  history has been weak — `risk_manager.position_size()`'s new
  `confidence` parameter.
- **Skips the trade entirely** if confidence falls below
  `trade_memory.MIN_CONFIDENCE_TO_TRADE` (0.45).
- A bucket with fewer than `MIN_SAMPLES_FOR_WEIGHT` (8) trades stays
  neutral (1.0x) — not enough history to trust yet, so it doesn't bias
  anything either way.

**Safety invariant — read this before touching the file:**
`compute_confidence()` is hard-clamped to a ceiling of 1.0 everywhere
in `trade_memory.py`, and `risk_manager.risk_amount_for()` clamps it
again independently as a second line of defense. The learning layer
can only ever shrink risk below `RISK_PER_TRADE_PCT`, never raise it
above — `config.py`'s own exposure-math validation at the bottom of
that file assumes `RISK_PER_TRADE_PCT` as the ceiling, and a
confidence score above 1.0 would silently break that guarantee. If
you ever change this, re-check that validation still holds.

**Pyramid adds are excluded from learning** (both scoring and
feeding results back in) — they share the same MT5 ticket and net P&L
as the base entry, so there's no clean way to attribute the trade's
outcome specifically to the pyramid leg's conditions. They're already
conservative by design (only ever add to a position already past
breakeven), so this isn't a meaningful gap.

**Reporting:** every 5th closed trade, `notify.learning_summary()`
pushes a short Telegram message showing the best/worst-performing
bucket per feature, once that feature has enough samples to say
anything meaningful.

## Files

- `config.py` — every tunable parameter, validated at import time.
- `metaapi_client.py` — synchronous wrapper around the (async)
  MetaApi SDK; read the module docstring for the tick-value caveat
  and the one part of the order-result handling I couldn't fully
  verify against a live account.
- `indicators.py` — ATR, Donchian channel, adaptive lookback,
  efficiency ratio. Pure functions, no client dependency.
- `strategy.py` — turns those indicators into an entry signal (now
  also returns `atr_ratio` for the learning layer's volatility bucket).
- `risk_manager.py` — position sizing (now confidence-aware), account
  kill switches, and the Agno cooldown/selectivity state machine.
- `trade_memory.py` — the learning layer described above.
- `state_store.py` — SQLite: open position (now carries an entry
  feature snapshot + risk amount), closed-trade log, kill-switch
  state, Agno regime state, feature_stats (learning layer).
- `scalping_engine.py` — ties it all together each tick.
- `notify.py` — Telegram push notifications, optional.
- `main.py` — entry point.

## Setup

1. Sign up at [app.metaapi.cloud](https://app.metaapi.cloud), add your
   MT5 account there (broker login/password/server go in once, on
   their site — this bot never touches them directly), and grab an
   API token plus the account id MetaApi assigns your connected
   account.
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and fill in `METAAPI_TOKEN`,
   `METAAPI_ACCOUNT_ID`, and `MT5_SYMBOL` if not EURUSD.
4. `python main.py` to test locally first.

## Deploying on Railway

Same shape as your other two bots: push this to its own repo, create
a Railway project from it (`Procfile` / `railway.json` are already
set up for a worker dyno), set the env vars from `.env.example` in
Railway's Variables tab, and attach a volume at `/app/data` so
`scalping_state.db` survives redeploys — without it, a restart
forgets which position is open and any Agno cooldown in progress.

## Before you go live

- Test on a demo MT5 account connected through MetaApi first, through
  at least one breakeven trigger, one pyramid add, one Agno cooldown,
  and one hard-stop cycle — you want to have watched all four actually
  happen before real money is involved.
- MT5 has no native candle below 1 minute; this runs on 5-minute
  candles, which filters out a lot of the pure noise a 1-minute
  breakout would chase, while still trading actively within a day.
- The efficiency-ratio regime filter and the specific ATR/breakeven
  multiples are reasonable starting points, not tuned/backtested
  values — expect to adjust them against your own results.

This isn't financial advice, and I'm not a financial advisor — the
above describes how the code behaves, not a recommendation to trade
this way.
