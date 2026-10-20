"""
Telegram push notifications — same visual style (borders, emoji,
backticked numbers) as the alert messages elsewhere in this repo.

Optional by design: if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID aren't
set, calls here just print instead of sending, so the bot still
trades fine without notifications configured.
"""

from datetime import datetime

import requests

import config


def now_wat() -> str:
    return datetime.now(config.WAT).strftime("%Y-%m-%d %H:%M WAT")


def send(text: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print(f"[notify] (Telegram not configured) {text}")
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[notify] Telegram send failed: {exc!r}")


def _dir_line(direction: str) -> str:
    return "🟢 *LONG*" if direction == "long" else "🔴 *SHORT*"


def position_opened(direction: str, symbol: str, lot: float, price: float,
                     sl: float, tp: float, efficiency: float, confidence: float = 1.0) -> None:
    conf_note = "" if confidence >= 0.99 else f" _(sized down — thin/weak history for this setup)_"
    send(
        f"⚡ *SCALP — OPENED*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"📍 Entry : `{price}`\n"
        f"📦 Lot   : `{lot}`\n"
        f"🛑 SL    : `{sl:.5f}`\n"
        f"🎯 TP    : `{tp:.5f}`\n"
        f"📈 Efficiency : `{efficiency:.2f}`\n"
        f"🧠 Confidence : `{confidence:.2f}`{conf_note}"
    )


def breakeven_set(direction: str, symbol: str, sl_price: float) -> None:
    send(
        f"🛡 *SCALP — SL MOVED TO BREAKEVEN*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"New SL : `{sl_price:.5f}`"
    )


def pyramid_added(direction: str, symbol: str, lot: float, price: float, total_volume: float) -> None:
    send(
        f"➕ *SCALP — PYRAMID ADD*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"📍 Added @ `{price}` — `{lot}` lot\n"
        f"📦 Total position : `{total_volume}` lot"
    )


def position_closed(direction: str, symbol: str, outcome: str, profit: float) -> None:
    emoji = "✅" if outcome == "win" else "🛑"
    send(
        f"{emoji} *SCALP — CLOSED ({outcome.upper()})*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"P/L : `{profit:.2f}`"
    )


def learning_summary(summary: str) -> None:
    send(f"🧠 *LEARNING UPDATE*\n━━━━━━━━━━━━━━━━━━━━━━━\n{summary}")


def inactivity_warning(message: str) -> None:
    send(f"⏳ *INACTIVITY WARNING*\n{message}")


def kill_switch(message: str) -> None:
    send(f"🚨 *KILL SWITCH*\n{message}")


def error(message: str) -> None:
    send(f"❌ *Bot error*\n`{message}`")
