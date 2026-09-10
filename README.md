# MT5 Martingale Bot

A self-contained Python bot for MetaTrader 5: opens a position, scales
into it up to 3 times if price moves against it, moves the stop to
breakeven once a real trend is confirmed, and is wrapped in
account-level kill switches. Talks to a local MT5 terminal via the
official `MetaTrader5` package.

## ⚠️ Strategy is a placeholder — read this first

Everything in `entry_signal.py` (`get_entry_signal` and
`is_trend_confirmed`) is a temporary SMA-crossover stand-in. The
actual "magic lines" entry/trend rules haven't been provided yet —
once you share that file or describe its exact rules, only
`entry_signal.py` needs to change; nothing else in this repo depends
on how the signal is computed.

## Why MT5 instead of OANDA

OANDA doesn't support Nigerian accounts. MT5's Python package only
works with a live MT5 terminal running on the same (Windows) machine
— it's local IPC, not a network API — so this bot has to run on a
Windows VPS with the terminal open and logged in, not on Railway like
your other two bots.

## Files

- `config.py` — every tunable parameter, validated at import time
  (it hard-fails if your lot-size settings are inconsistent, rather
  than silently trading outside the limits you set).
- `mt5_client.py` — wrapper around the `MetaTrader5` package (candles,
  price, positions, orders, SL/TP modification). Assumes a **netting**
  account; see the note at the top of the file if you're on hedging.
- `entry_signal.py` — the two "magic lines"-dependent hooks (currently
  placeholders).
- `risk_manager.py` — lot-size clamping and the two kill switches.
- `martingale_engine.py` — ties it together: open, scale, breakeven,
  detect closes.
- `state_store.py` — SQLite bookkeeping (scales used, kill-switch
  state) that survives a restart.
- `notify.py` — Telegram push notifications, optional.
- `main.py` — entry point.
- `run_bot.bat` — double-click launcher for Windows.
- `.env.example` — copy to `.env` and fill in your credentials.

## Risk parameters, as specified

- **Base lot**: `BASE_LOT = 0.02`, must stay within `[0.02, 0.05]` —
  enforced at startup, not just documented.
- **Scaling**: doubles by default (`SCALE_MULTIPLIER = 2.0`) on each
  of up to `MAX_SCALES = 3` scale-ins, but every leg is clamped to
  `MAX_LOT_SIZE = 0.30` regardless of what the multiplier computes —
  e.g. a 0.05 base would compute 0.10 → 0.20 → 0.40, and that last one
  gets clamped down to 0.30.
- **Hard stop**: a real broker-side SL, computed once at base-entry
  time from the worst-case fully-scaled price, so it protects you even
  if this process crashes.
- **Breakeven**: once `is_trend_confirmed()` says yes, SL moves to the
  average entry + a small buffer (`BREAKEVEN_BUFFER_PIPS`) to cover
  spread, and stays there — this supersedes the hard-stop backstop.
- **Daily loss limit**: `DAILY_LOSS_LIMIT_PCT = 5.0` — halts new
  trading for the rest of the day, auto-resumes the next day.
- **Max drawdown**: `MAX_DRAWDOWN_PCT = 15.0` from the highest equity
  ever recorded — halts completely; does **not** auto-resume. Clear it
  with `python risk_manager.py` once you've reviewed what happened.

## Setup (on the Windows VPS)

1. Install and open the MT5 terminal, log into your account once so
   it's cached, then enable **Algo Trading** in both places it needs
   enabling: `Tools → Options → Expert Advisors`, and the toolbar
   toggle button. Missing either one silently rejects every order.
2. `pip install -r requirements.txt`
3. `copy .env.example .env` and fill in `MT5_LOGIN`, `MT5_PASSWORD`,
   `MT5_SERVER` (and `MT5_SYMBOL` if not EURUSD — check the exact
   symbol name in Market Watch; some brokers append suffixes like
   `.a` or `m`).
4. `python main.py`, or double-click `run_bot.bat`.

## Running unattended

MT5's Python package needs the terminal open, so this isn't a fit for
`Procfile`/`railway.json`-style deployment. Two practical options on
the VPS itself:

- **Task Scheduler**: create a task that runs `run_bot.bat` at startup
  and on a schedule, with "run whether user is logged on or not."
- **NSSM** (Non-Sucking Service Manager): wraps `python main.py` as a
  proper Windows service that restarts on crash — closer to what
  `railway.json`'s `restartPolicyType: ON_FAILURE` gave your other
  bots.

Either way, make sure the MT5 terminal itself is also set to launch at
startup and stay logged in — the bot can't do anything without it
running.

## Before you go live

- Test on a demo account/server first, through at least one full
  scale-in-and-recover cycle, one breakeven trigger, and one hard-stop
  cycle, so you've watched all three actually happen.
- This bot trades a single symbol at a time (`config.SYMBOL`) — it
  doesn't multiplex across GBP/JPY, EUR/JPY, XAU/USD, USD/JPY the way
  No-Wick's Telegram alerts do.
- The martingale sizing can make a mediocre entry signal look fine for
  a long stretch and then produce a large loss all at once — the kill
  switches bound that, they don't prevent it.

This isn't financial advice, and I'm not a financial advisor — the
above describes how the code behaves, not a recommendation to trade
this way.
