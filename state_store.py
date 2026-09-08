"""
SQLite persistence for the martingale bot.

Two jobs:
1. Remember the currently-open position (if any) across restarts.
   railway.json restarts this process on failure — without this, a
   restart mid-position would forget every leg that's actually open
   at the broker.
2. Keep a simple closed-trade log — same spirit as the trade_memory.db
   "memory engine" used elsewhere in this repo, sized to what this
   bot actually needs.

NOTE on resuming: on startup this restores whatever was last saved
locally, but does not reconcile that against OANDA's actual open
positions. In the ordinary case (this bot is the only thing trading
the account) that's fine; if you ever intervene manually on the
account, check /openPositions against this before trusting a resume.
"""

import json
import os
import sqlite3
from typing import List, Optional, Tuple

import config


def _connect():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    return sqlite3.connect(config.DB_PATH)


def init() -> None:
    with _connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS open_position (
                id        INTEGER PRIMARY KEY CHECK (id = 1),
                direction TEXT NOT NULL,
                entries   TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                direction     TEXT NOT NULL,
                scales_used   INTEGER NOT NULL,
                average_price REAL NOT NULL,
                reason        TEXT NOT NULL,
                closed_at     TEXT NOT NULL
            )
        """)
        con.commit()


def save_open_position(direction: str, entries: List[Tuple[float, int]]) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO open_position (id, direction, entries) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET direction=excluded.direction, entries=excluded.entries",
            (direction, json.dumps(entries)),
        )
        con.commit()


def load_open_position() -> Optional[Tuple[str, List[Tuple[float, int]]]]:
    with _connect() as con:
        row = con.execute("SELECT direction, entries FROM open_position WHERE id = 1").fetchone()
    if not row:
        return None
    direction, entries_json = row
    entries = [tuple(e) for e in json.loads(entries_json)]
    return direction, entries


def clear_open_position() -> None:
    with _connect() as con:
        con.execute("DELETE FROM open_position WHERE id = 1")
        con.commit()


def log_closed_trade(direction: str, scales_used: int, average_price: float, reason: str, closed_at: str) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO closed_trades (direction, scales_used, average_price, reason, closed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (direction, scales_used, average_price, reason, closed_at),
        )
        con.commit()
