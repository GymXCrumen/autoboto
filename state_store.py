"""
SQLite persistence for the Donchian scalping bot.

Three jobs:
1. Remember the open position's bookkeeping (ticket, direction, SL/TP)
   across restarts — simpler than the martingale version since there's
   no scaling to track anymore.
2. A closed-trade log.
3. Two kinds of risk state: account-level kill switches (daily/hard
   halt, unchanged from before) and the Agno-style consecutive-loss
   cooldown/selectivity state (new).
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
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                ticket        INTEGER NOT NULL,
                direction     TEXT NOT NULL,
                lot           REAL NOT NULL,
                sl            REAL NOT NULL,
                tp            REAL NOT NULL,
                atr           REAL NOT NULL,
                breakeven_set INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                direction  TEXT NOT NULL,
                lot        REAL NOT NULL,
                profit     REAL,
                outcome    TEXT NOT NULL,
                closed_at  TEXT NOT NULL
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS regime_state (
                id                   INTEGER PRIMARY KEY CHECK (id = 1),
                consecutive_losses   INTEGER NOT NULL DEFAULT 0,
                cooldown_until       TEXT,
                reduced_size_active  INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.commit()


# --- open position ---------------------------------------------------------------

def save_open_position(ticket: int, direction: str, lot: float, sl: float, tp: float,
                        atr: float, breakeven_set: bool) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO open_position (id, ticket, direction, lot, sl, tp, atr, breakeven_set) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET ticket=excluded.ticket, direction=excluded.direction, "
            "lot=excluded.lot, sl=excluded.sl, tp=excluded.tp, atr=excluded.atr, "
            "breakeven_set=excluded.breakeven_set",
            (ticket, direction, lot, sl, tp, atr, int(breakeven_set)),
        )
        con.commit()


def load_open_position() -> Optional[dict]:
    with _connect() as con:
        row = con.execute(
            "SELECT ticket, direction, lot, sl, tp, atr, breakeven_set FROM open_position WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return {
        "ticket": row[0], "direction": row[1], "lot": row[2], "sl": row[3], "tp": row[4],
        "atr": row[5], "breakeven_set": bool(row[6]),
    }


def clear_open_position() -> None:
    with _connect() as con:
        con.execute("DELETE FROM open_position WHERE id = 1")
        con.commit()


def log_closed_trade(direction: str, lot: float, profit: Optional[float], outcome: str, closed_at: str) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO closed_trades (direction, lot, profit, outcome, closed_at) VALUES (?, ?, ?, ?, ?)",
            (direction, lot, profit, outcome, closed_at),
        )
        con.commit()


# --- kill switch state -------------------------------------------------------------

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
        "daily_date": row[0], "daily_start_equity": row[1], "peak_equity": row[2],
        "hard_halted": bool(row[3]), "daily_halted": bool(row[4]),
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


# --- regime / consecutive-loss state (Agno layer) -----------------------------------

def get_regime_state() -> dict:
    with _connect() as con:
        row = con.execute(
            "SELECT consecutive_losses, cooldown_until, reduced_size_active FROM regime_state WHERE id = 1"
        ).fetchone()
    if not row:
        return {"consecutive_losses": 0, "cooldown_until": None, "reduced_size_active": False}
    return {"consecutive_losses": row[0], "cooldown_until": row[1], "reduced_size_active": bool(row[2])}


def set_regime_state(consecutive_losses: int, cooldown_until: Optional[str], reduced_size_active: bool) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO regime_state (id, consecutive_losses, cooldown_until, reduced_size_active) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET consecutive_losses=excluded.consecutive_losses, "
            "cooldown_until=excluded.cooldown_until, reduced_size_active=excluded.reduced_size_active",
            (consecutive_losses, cooldown_until, int(reduced_size_active)),
        )
        con.commit()
