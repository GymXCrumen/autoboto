# Donchian Breakout Scalping Bot (MT5 via MetaApi)

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
  M1 candles.
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

- **Account-level kill switches** (unchanged from the martingale
  draft): 5% daily loss halts new trading for the rest of the day
  (auto-resumes tomorrow); 15% drawdown from peak equity halts
  completely until you run `python risk_manager.py` yourself. These
  are the hard backstop, independent of strategy performance.
- **Agno consecutive-loss layer**: after `CONSECUTIVE_LOSS_LIMIT` (3)
  losses in a row, the bot pauses new entries for `COOLDOWN_MINUTES`
  (30), then resumes at `SIZE_REDUCTION_FACTOR` (half) size and a
  stricter efficiency threshold, until a win resets it. This is
  softer and strategy-specific — it can trip and recover several
  times a day without ever touching the account-level switches above.

## Files

- `config.py` — every tunable parameter, validated at import time.
- `metaapi_client.py` — synchronous wrapper around the (async)
  MetaApi SDK; read the module docstring for the tick-value caveat
  and the one part of the order-result handling I couldn't fully
  verify against a live account.
- `indicators.py` — ATR, Donchian channel, adaptive lookback,
  efficiency ratio. Pure functions, no client dependency.
- `strategy.py` — turns those indicators into an entry signal.
- `risk_manager.py` — position sizing, account kill switches, and the
  Agno cooldown/selectivity state machine.
- `state_store.py` — SQLite: open position, closed-trade log,
  kill-switch state, Agno regime state.
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
- MT5 has no native candle below 1 minute, so M1 is the practical
  floor for "scalping" here. A true sub-minute/tick-based version
  would need tick data rather than candles as its input, which is a
  different (and noisier) signal to build against.
- The efficiency-ratio regime filter and the specific ATR/breakeven
  multiples are reasonable starting points, not tuned/backtested
  values — expect to adjust them against your own results.

This isn't financial advice, and I'm not a financial advisor — the
above describes how the code behaves, not a recommendation to trade
this way.
