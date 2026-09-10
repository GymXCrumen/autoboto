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


def position_opened(direction: str, symbol: str, lot: float, price: float) -> None:
    send(
        f"🕯 *MARTINGALE — BASE OPENED*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"📍 Entry : `{price}`\n"
        f"📦 Lot   : `{lot}`"
    )


def scaled_in(direction: str, symbol: str, scale_number: int, lot: float, price: float, avg_price: float) -> None:
    send(
        f"⚠️ *MARTINGALE — SCALE {scale_number}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"📍 Added @ `{price}` — `{lot}` lot\n"
        f"📊 New average : `{avg_price:.5f}`"
    )


def breakeven_set(direction: str, symbol: str, sl_price: float) -> None:
    send(
        f"🛡 *MARTINGALE — SL MOVED TO BREAKEVEN*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"New SL : `{sl_price:.5f}`"
    )


def position_closed(direction: str, symbol: str, reason: str, avg_price: float) -> None:
    send(
        f"🏁 *MARTINGALE — CLOSED*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_dir_line(direction)}  {symbol}\n"
        f"🕐 `{now_wat()}`\n\n"
        f"Reason  : {reason}\n"
        f"Last known average : `{avg_price:.5f}`"
    )


def kill_switch(message: str) -> None:
    send(f"🚨 *KILL SWITCH*\n{message}")


def error(message: str) -> None:
    send(f"❌ *Martingale bot error*\n`{message}`")
