"""SQLite storage: one connection guarded by a lock, WAL mode."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_time INTEGER NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_signals_created ON signals(created_at_utc);
CREATE TABLE IF NOT EXISTS signal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    time_utc TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_sigev ON signal_events(signal_id);
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    kind TEXT NOT NULL,
    signal_id TEXT,
    symbol TEXT,
    state TEXT NOT NULL,
    mode TEXT NOT NULL,
    ticket INTEGER,
    comment TEXT,
    reason TEXT,
    delivered_at REAL,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_intents_state ON intents(state);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_key TEXT NOT NULL UNIQUE,
    ticket INTEGER,
    symbol TEXT,
    side TEXT,
    volume REAL,
    price_open REAL,
    price_close REAL,
    profit REAL,
    commission REAL,
    swap REAL,
    opened_at_utc TEXT,
    closed_at_utc TEXT,
    close_reason TEXT,
    mode TEXT,
    signal_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_closed ON trades(closed_at_utc);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    ref_id TEXT,
    text TEXT NOT NULL,
    button_url TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at REAL NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at_utc TEXT NOT NULL,
    sent_at_utc TEXT
);
CREATE INDEX IF NOT EXISTS ix_notif_status ON notifications(status, next_retry_at);
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time_utc TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    tf TEXT NOT NULL,
    t INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL, tv REAL, sp REAL,
    PRIMARY KEY (symbol, tf, t)
);
CREATE TABLE IF NOT EXISTS equity_marks (
    account_key TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    day_start_equity REAL NOT NULL,
    peak_equity REAL NOT NULL,
    updated_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS bars_archive (
    symbol TEXT NOT NULL,
    tf TEXT NOT NULL,
    t INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL, tv REAL, sp REAL,
    PRIMARY KEY (symbol, tf, t)
);
CREATE TABLE IF NOT EXISTS spread_stats (
    symbol TEXT NOT NULL,
    hour_utc INTEGER NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    sum_pts REAL NOT NULL DEFAULT 0,
    sum_sq REAL NOT NULL DEFAULT 0,
    max_pts REAL,
    PRIMARY KEY (symbol, hour_utc)
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    tf TEXT NOT NULL,
    bar_time INTEGER NOT NULL,
    created_ts REAL,
    account_key TEXT,
    direction TEXT,
    candidate_side TEXT,
    entry REAL,
    sl REAL,
    tp REAL,
    score REAL,
    blockers_json TEXT,
    signal_id TEXT,
    ai_verdict TEXT,
    outcome TEXT,
    outcome_r REAL,
    mfe_r REAL,
    mae_r REAL,
    labeled_ts REAL,
    UNIQUE (symbol, tf, bar_time)
);
CREATE INDEX IF NOT EXISTS ix_analyses_bar ON analyses(bar_time);
CREATE INDEX IF NOT EXISTS ix_analyses_signal ON analyses(signal_id);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    account_key TEXT NOT NULL,
    ts INTEGER NOT NULL,
    balance REAL,
    equity REAL,
    PRIMARY KEY (account_key, ts)
);
CREATE TABLE IF NOT EXISTS claude_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    signal_id TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS ix_claude_usage_ts ON claude_usage(ts);
CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    created_ts REAL NOT NULL,
    status TEXT NOT NULL,
    params_json TEXT,
    metrics_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS trade_paths (
    source TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    ts INTEGER NOT NULL,
    minutes REAL,
    r_now REAL,
    mfe_r REAL,
    mae_r REAL,
    price REAL,
    atr REAL,
    ema_dist_atr REAL,
    spread_pts REAL,
    PRIMARY KEY (source, trade_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_trade_paths_ts ON trade_paths(ts);
CREATE TABLE IF NOT EXISTS forward_meta (
    name TEXT PRIMARY KEY,
    started_ts REAL NOT NULL,
    config_json TEXT,
    decided_json TEXT
);
CREATE TABLE IF NOT EXISTS forward_positions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    opened_ts REAL NOT NULL,
    bar_time INTEGER,
    point REAL,
    digits INTEGER,
    spread_pts REAL,
    std_spread_pts REAL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_forward_positions_name ON forward_positions(name);
CREATE TABLE IF NOT EXISTS forward_trades (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    opened_ts REAL NOT NULL,
    closed_ts REAL NOT NULL,
    exit_price REAL,
    reason TEXT,
    r REAL,
    r_adj REAL,
    usd_adj REAL,
    bar_time INTEGER,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_forward_trades_name ON forward_trades(name, closed_ts);
"""

# additive columns on tables that already exist in older databases: (table, column, type)
ADDED_COLUMNS = [
    ("trades", "account_key", "TEXT"),
    ("trades", "signal_entry", "REAL"),
    ("trades", "initial_sl", "REAL"),
    ("trades", "risk_money", "REAL"),
    ("trades", "r_multiple", "REAL"),
    ("signals", "account_key", "TEXT"),
    ("intents", "account_key", "TEXT"),
    ("forward_meta", "decided_json", "TEXT"),
]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Additive only: never drops or rewrites existing data. Returns the columns added."""
    added = []
    for table, col, typ in ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            added.append(f"{table}.{col}")
    return added


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            self.migrated = migrate(self.conn)

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: list) -> None:
        if not rows:
            return
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self.lock:
            cur = self.conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # settings key/value
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value_json FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except Exception:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value_json) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value)),
        )

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.close()
            except Exception:
                pass
