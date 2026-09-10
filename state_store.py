"""
SQLite persistence for the MT5 martingale bot.

Three jobs:
1. Remember the open position's bot-side bookkeeping (scales used,
   last leg's lot size, current average price, the precomputed
   hard-stop price, whether breakeven has been set) across restarts —
   MT5 remembers the actual position, but not this bookkeeping.
2. A simple closed-trade log.
3. Kill-switch state: daily starting equity, all-time peak equity,
   and whether a daily or hard halt is currently in effect.
"""

import os
import sqlite3
from typing import Optional

import config


def _connect():
    directory = os.path.dirname(config.DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return sqlite3.connect(config.DB_PATH)


def init() -> None:
    with _connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS open_position (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                ticket          INTEGER NOT NULL,
                direction       TEXT NOT NULL,
                scales_done     INTEGER NOT NULL,
                last_leg_lot    REAL NOT NULL,
                last_avg_price  REAL NOT NULL,
                hard_stop_price REAL NOT NULL,
                breakeven_set   INTEGER NOT NULL DEFAULT 0
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch_state (
                id                  INTEGER PRIMARY KEY CHECK (id = 1),
                daily_date          TEXT NOT NULL,
                daily_start_equity  REAL NOT NULL,
                peak_equity         REAL NOT NULL,
                hard_halted         INTEGER NOT NULL DEFAULT 0,
                daily_halted        INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.commit()


# --- open position -------------------------------------------------------------

def save_open_position(ticket: int, direction: str, scales_done: int, last_leg_lot: float,
                        last_avg_price: float, hard_stop_price: float, breakeven_set: bool) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO open_position "
            "(id, ticket, direction, scales_done, last_leg_lot, last_avg_price, hard_stop_price, breakeven_set) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "ticket=excluded.ticket, direction=excluded.direction, scales_done=excluded.scales_done, "
            "last_leg_lot=excluded.last_leg_lot, last_avg_price=excluded.last_avg_price, "
            "hard_stop_price=excluded.hard_stop_price, breakeven_set=excluded.breakeven_set",
            (ticket, direction, scales_done, last_leg_lot, last_avg_price, hard_stop_price, int(breakeven_set)),
        )
        con.commit()


def load_open_position() -> Optional[dict]:
    with _connect() as con:
        row = con.execute(
            "SELECT ticket, direction, scales_done, last_leg_lot, last_avg_price, "
            "hard_stop_price, breakeven_set FROM open_position WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return {
        "ticket": row[0],
        "direction": row[1],
        "scales_done": row[2],
        "last_leg_lot": row[3],
        "last_avg_price": row[4],
        "hard_stop_price": row[5],
        "breakeven_set": bool(row[6]),
    }


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


# --- kill switch state -----------------------------------------------------------

def get_kill_switch_state() -> dict:
    with _connect() as con:
        row = con.execute(
            "SELECT daily_date, daily_start_equity, peak_equity, hard_halted, daily_halted "
            "FROM kill_switch_state WHERE id = 1"
        ).fetchone()
    if not row:
        return {
            "daily_date": "", "daily_start_equity": 0.0, "peak_equity": 0.0,
            "hard_halted": False, "daily_halted": False,
        }
    return {
        "daily_date": row[0],
        "daily_start_equity": row[1],
        "peak_equity": row[2],
        "hard_halted": bool(row[3]),
        "daily_halted": bool(row[4]),
    }


def set_kill_switch_state(daily_date: str, daily_start_equity: float, peak_equity: float,
                           hard_halted: bool, daily_halted: bool) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO kill_switch_state "
            "(id, daily_date, daily_start_equity, peak_equity, hard_halted, daily_halted) "
            "VALUES (1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "daily_date=excluded.daily_date, daily_start_equity=excluded.daily_start_equity, "
            "peak_equity=excluded.peak_equity, hard_halted=excluded.hard_halted, "
            "daily_halted=excluded.daily_halted",
            (daily_date, daily_start_equity, peak_equity, int(hard_halted), int(daily_halted)),
        )
        con.commit()
