"""
SQLite persistence for the Donchian scalping bot.

Four jobs:
1. Open positions — one row PER SYMBOL now that the bot scans all of
   config.MAJOR_PAIRS, not just one.
2. A closed-trade log (also feeds the inactivity check below).
3. Account-level state: the starting balance (captured once, ever —
   this is what the fixed fail level and daily line are computed
   from) and the last time any trade closed (for the inactivity rule).
4. Two kinds of risk state: kill switches (fail level / daily line,
   both tied to the evaluation's actual rules) and the Agno-style
   consecutive-loss cooldown/selectivity state — both still account-
   wide, shared across every symbol.
"""

import json
import os
import sqlite3
from typing import Optional, Tuple

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
                symbol        TEXT PRIMARY KEY,
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
                symbol     TEXT NOT NULL,
                direction  TEXT NOT NULL,
                lot        REAL NOT NULL,
                profit     REAL,
                outcome    TEXT NOT NULL,
                closed_at  TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS account_state (
                id                   INTEGER PRIMARY KEY CHECK (id = 1),
                starting_balance     REAL NOT NULL,
                last_trade_closed_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch_state (
                id                   INTEGER PRIMARY KEY CHECK (id = 1),
                daily_date_utc       TEXT NOT NULL,
                daily_opening_equity REAL NOT NULL,
                hard_halted          INTEGER NOT NULL DEFAULT 0,
                daily_halted         INTEGER NOT NULL DEFAULT 0
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
        # Learning layer: per-bucket win/loss counts, e.g. ("hour_bucket",
        # "08-12") -> (14 wins, 6 losses). trade_memory.py reads these to
        # score a NEW candidate signal against how similar past setups
        # actually performed, and writes to them every time a trade closes.
        con.execute("""
            CREATE TABLE IF NOT EXISTS feature_stats (
                feature_name TEXT NOT NULL,
                bucket       TEXT NOT NULL,
                wins         INTEGER NOT NULL DEFAULT 0,
                losses       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (feature_name, bucket)
            )
        """)
        con.commit()

        # Migrations for columns added after the original schema — safe to
        # run every startup; a failed ALTER (column already exists) is
        # swallowed. features_json carries the feature snapshot that was
        # true AT ENTRY from open -> close, so the learning layer scores
        # the trade against the conditions that actually produced it, not
        # whatever the market looks like by the time it closes.
        for table, col, ddl in [
            ("open_position", "features_json", "TEXT"),
            ("open_position", "risk_amount",   "REAL"),
            ("closed_trades", "features_json", "TEXT"),
            ("closed_trades", "r_multiple",    "REAL"),
        ]:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                con.commit()
            except sqlite3.OperationalError:
                pass  # column already exists from a previous run


# --- open positions (per symbol) --------------------------------------------------

def save_open_position(symbol: str, ticket: int, direction: str, lot: float, sl: float, tp: float,
                        atr: float, breakeven_set: bool, features_json: Optional[str] = None,
                        risk_amount: Optional[float] = None) -> None:
    """features_json/risk_amount are only ever SET on the base entry —
    the breakeven and pyramid calls elsewhere in the engine don't pass
    them, and COALESCE here means that leaves the original entry
    snapshot untouched rather than overwriting it with NULL."""
    with _connect() as con:
        con.execute(
            "INSERT INTO open_position (symbol, ticket, direction, lot, sl, tp, atr, breakeven_set, "
            "features_json, risk_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET ticket=excluded.ticket, direction=excluded.direction, "
            "lot=excluded.lot, sl=excluded.sl, tp=excluded.tp, atr=excluded.atr, "
            "breakeven_set=excluded.breakeven_set, "
            "features_json=COALESCE(excluded.features_json, open_position.features_json), "
            "risk_amount=COALESCE(excluded.risk_amount, open_position.risk_amount)",
            (symbol, ticket, direction, lot, sl, tp, atr, int(breakeven_set), features_json, risk_amount),
        )
        con.commit()


def load_open_position(symbol: str) -> Optional[dict]:
    with _connect() as con:
        row = con.execute(
            "SELECT ticket, direction, lot, sl, tp, atr, breakeven_set, features_json, risk_amount "
            "FROM open_position WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    if not row:
        return None
    return {
        "ticket": row[0], "direction": row[1], "lot": row[2], "sl": row[3], "tp": row[4],
        "atr": row[5], "breakeven_set": bool(row[6]),
        "features": json.loads(row[7]) if row[7] else {}, "risk_amount": row[8],
    }


def clear_open_position(symbol: str) -> None:
    with _connect() as con:
        con.execute("DELETE FROM open_position WHERE symbol = ?", (symbol,))
        con.commit()


def count_open_positions() -> int:
    with _connect() as con:
        return con.execute("SELECT COUNT(*) FROM open_position").fetchone()[0]


def log_closed_trade(symbol: str, direction: str, lot: float, profit: Optional[float],
                      outcome: str, closed_at: str, features_json: Optional[str] = None,
                      r_multiple: Optional[float] = None) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO closed_trades (symbol, direction, lot, profit, outcome, closed_at, "
            "features_json, r_multiple) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, direction, lot, profit, outcome, closed_at, features_json, r_multiple),
        )
        con.commit()


def count_closed_trades() -> int:
    with _connect() as con:
        return con.execute("SELECT COUNT(*) FROM closed_trades").fetchone()[0]


# --- account state (starting balance, inactivity tracking) ------------------------

def get_account_state() -> Optional[dict]:
    with _connect() as con:
        row = con.execute(
            "SELECT starting_balance, last_trade_closed_at FROM account_state WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return {"starting_balance": row[0], "last_trade_closed_at": row[1]}


def set_starting_balance(starting_balance: float) -> None:
    """Only ever called once — on the very first run, when no account_state
    row exists yet. The evaluation's fail level and daily line are both
    computed from this number for the life of the account."""
    with _connect() as con:
        con.execute(
            "INSERT INTO account_state (id, starting_balance, last_trade_closed_at) VALUES (1, ?, NULL) "
            "ON CONFLICT(id) DO NOTHING",
            (starting_balance,),
        )
        con.commit()


def set_last_trade_closed_at(closed_at: str) -> None:
    with _connect() as con:
        con.execute(
            "UPDATE account_state SET last_trade_closed_at = ? WHERE id = 1",
            (closed_at,),
        )
        con.commit()


# --- kill switch state ------------------------------------------------------------

def get_kill_switch_state() -> dict:
    with _connect() as con:
        row = con.execute(
            "SELECT daily_date_utc, daily_opening_equity, hard_halted, daily_halted "
            "FROM kill_switch_state WHERE id = 1"
        ).fetchone()
    if not row:
        return {"daily_date_utc": "", "daily_opening_equity": 0.0, "hard_halted": False, "daily_halted": False}
    return {
        "daily_date_utc": row[0], "daily_opening_equity": row[1],
        "hard_halted": bool(row[2]), "daily_halted": bool(row[3]),
    }


def set_kill_switch_state(daily_date_utc: str, daily_opening_equity: float,
                           hard_halted: bool, daily_halted: bool) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO kill_switch_state (id, daily_date_utc, daily_opening_equity, hard_halted, daily_halted) "
            "VALUES (1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "daily_date_utc=excluded.daily_date_utc, daily_opening_equity=excluded.daily_opening_equity, "
            "hard_halted=excluded.hard_halted, daily_halted=excluded.daily_halted",
            (daily_date_utc, daily_opening_equity, int(hard_halted), int(daily_halted)),
        )
        con.commit()


# --- regime / consecutive-loss state (Agno layer, account-wide) -------------------

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


# --- feature_stats (learning layer — per-bucket win/loss counts) ------------------

def get_feature_stats(feature_name: str, bucket: str) -> Tuple[int, int]:
    with _connect() as con:
        row = con.execute(
            "SELECT wins, losses FROM feature_stats WHERE feature_name = ? AND bucket = ?",
            (feature_name, bucket),
        ).fetchone()
    return (row[0], row[1]) if row else (0, 0)


def increment_feature_stats(feature_name: str, bucket: str, won: bool) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO feature_stats (feature_name, bucket, wins, losses) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(feature_name, bucket) DO UPDATE SET "
            "wins = wins + excluded.wins, losses = losses + excluded.losses",
            (feature_name, bucket, 1 if won else 0, 0 if won else 1),
        )
        con.commit()


def get_all_feature_stats() -> list:
    with _connect() as con:
        return con.execute(
            "SELECT feature_name, bucket, wins, losses FROM feature_stats "
            "ORDER BY feature_name, (wins + losses) DESC"
        ).fetchall()
