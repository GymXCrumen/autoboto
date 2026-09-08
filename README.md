# OANDA Martingale Bot

A self-contained Python bot that opens a position, scales into it up to
3 times if price moves against it, and exits on a moving take-profit
or a hard stop. Talks to OANDA's v20 REST API directly over `requests`.
Styled to match your No-Wick bot's conventions: Telegram push
notifications, WAT timestamps, Railway deployment, SQLite state.

## Why OANDA and not MetaTrader

MetaTrader 5's Python package requires the MT5 desktop terminal
running on the same (Windows) machine — a poor fit for an unattended
bot on Railway. OANDA's REST API has no such requirement. If you
specifically need MT5, say so — the martingale logic in
`martingale_engine.py` doesn't change, only `oanda_client.py` would be
swapped for an MT5 equivalent.

## Files

- `config.py` — every tunable parameter, plus env vars (`_require()`
  pattern matches your No-Wick bot's config section).
- `oanda_client.py` — thin REST wrapper (price, orders, close, candles).
- `entry_signal.py` — decides trade **direction** only. Ships with a
  placeholder SMA-crossover; swap in your real signal here.
- `martingale_engine.py` — the scaling/risk logic: base entry, up to
  3 scale-ins, take-profit, hard stop.
- `notify.py` — Telegram push notifications (open/scale/close/error),
  same border-and-emoji style as your existing alerts. Optional — the
  bot trades fine without Telegram configured, it just won't notify.
- `state_store.py` — SQLite persistence for the open position (so a
  Railway restart doesn't forget an in-progress trade) plus a simple
  closed-trades log, same spirit as your `trade_memory.db`.
- `main.py` — wires it together and runs the loop.
- `Procfile`, `railway.json`, `runtime.txt` — same deploy setup as
  your other bots (worker dyno, NIXPACKS, Python 3.11.9).

## What "3 scale" means here

Implemented as: 1 base entry + 3 additional scaled-in entries = **4
legs total**, each `SCALE_MULTIPLIER` (default 2x) bigger than the
last, added every time price moves `SCALE_TRIGGER_PIPS` further
against the average entry. If you meant 3 legs *total* rather than 3
*additions*, set `MAX_SCALES = 2` in `config.py`.

## The hard stop — read this part

A pure martingale has no ceiling on loss: if price keeps moving one
direction, you keep adding size until you run out of margin. This bot
caps that with `HARD_STOP_PIPS_BEYOND_LAST_SCALE`: once all scales are
in, a further adverse move of that many pips force-closes everything
at a loss instead of adding a 5th leg. That turns "unbounded" into "a
large but fixed number" — it does not make the strategy safe, just
bounded. Work out what that fixed number actually costs in your
account currency at your configured `BASE_UNITS` and `SCALE_MULTIPLIER`
before running this with real money.

## Setup

```bash
pip install -r requirements.txt
export OANDA_API_TOKEN="your-token"
export OANDA_ACCOUNT_ID="your-account-id"

# optional — Telegram push notifications
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"

python main.py
```

`config.py` defaults `ENVIRONMENT` to `"practice"`. Switch to live with:

```bash
export OANDA_ENV="live"
```

On Railway: same as your No-Wick bot — set the env vars above in
Railway → Variables, and `DB_PATH` will default to
`/app/data/martingale_state.db` (attach a persistent volume at
`/app/data`, same as your existing setup, or state won't survive a
redeploy).

## Resuming after a restart

`railway.json` restarts the process on failure (up to 10 times). On
startup, the bot checks `state_store` for a saved open position and
resumes managing it instead of starting blind. This reads from the
bot's own local record, not from OANDA directly — if you ever
manually intervene on the account outside this bot, check OANDA's
open positions before trusting a resume.

## Before you flip it to live

- Run it on your OANDA **practice** account first, long enough to see
  it go through at least one full scale-in-and-recover cycle *and*
  one hard-stop cycle, so you've actually watched both outcomes happen.
- Check your broker's terms allow automated/API trading on your account
  type, and check what your local regulations say about algorithmic
  forex trading — this varies a lot by country.
- This is a template, not a profitable strategy on its own — the
  included entry signal is a placeholder. The martingale sizing can
  make a mediocre signal look fine for a long time and then produce a
  large loss all at once; that pattern is inherent to the strategy,
  not a bug to fix.

This isn't financial advice, and I'm not a financial advisor — the
above is general information about how the code behaves, not a
recommendation to trade this way.
