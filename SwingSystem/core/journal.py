"""
Signal journal (SQLite). Every call the scanner makes is written here BEFORE
the outcome is known; the signal fields are never edited afterwards. The
tracker only fills in the outcome columns. This forward record is the honest
test of whether a strategy works.
"""

import sqlite3

import pandas as pd

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    signal_date   TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    rank          INTEGER,
    score         REAL,
    final_score   REAL,
    regime        TEXT,
    ref_close     REAL,
    stop          REAL,
    reason        TEXT,
    -- outcome (filled by tracker)
    status        TEXT NOT NULL DEFAULT 'PENDING',
    entry_date    TEXT, entry_price REAL, target REAL,
    exit_date     TEXT, exit_price REAL, exit_reason TEXT,
    current_stop  REAL, mark_price REAL, bars_held INTEGER,
    return_pct    REAL, r_multiple REAL,
    updated_at    TEXT,
    UNIQUE(signal_date, symbol, strategy)
);
"""

# Columns added after the first release (2026-10-10). Signal-time: features, strategy_version.
# Review (filled by the tracker): excursions, benchmark, post-exit move, diagnosis.
EXTRA_COLUMNS = {"features": "TEXT", "strategy_version": "TEXT", "mfe_pct": "REAL", "mae_pct": "REAL",
                 "mfe_r": "REAL", "days_to_mfe": "INTEGER", "nifty_ret_pct": "REAL",
                 "post_exit_5d_pct": "REAL", "diagnosis": "TEXT", "reviewed_on": "TEXT",
                 "final_reviewed_on": "TEXT"}
REVIEW_FIELDS = ["mfe_pct", "mae_pct", "mfe_r", "days_to_mfe", "nifty_ret_pct", "post_exit_5d_pct", "diagnosis"]

OUTCOME_FIELDS = ["status", "entry_date", "entry_price", "target", "exit_date", "exit_price",
                  "exit_reason", "current_stop", "mark_price", "bars_held", "return_pct", "r_multiple"]


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.JOURNAL_DB)
    con.execute(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(calls)")}
    for col, typ in EXTRA_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE calls ADD COLUMN {col} {typ}")
    con.commit()
    return con


def has_active(con, symbol: str, strategy: str, before: str | None = None) -> bool:
    """True if a call from an EARLIER signal day is still pending/open (a re-scan of the same day is a dup)."""
    row = con.execute("SELECT 1 FROM calls WHERE symbol=? AND strategy=? AND status IN ('PENDING','OPEN')"
                      " AND signal_date < ?", (symbol, strategy, before or "9999-12-31")).fetchone()
    return row is not None


def insert_call(con, call: dict) -> bool:
    """Insert a new call; returns False if this exact call was already logged."""
    cur = con.execute(
        """INSERT OR IGNORE INTO calls (created_at, signal_date, symbol, strategy, rank, score,
           final_score, regime, ref_close, stop, reason, features, strategy_version)
           VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (call["signal_date"], call["symbol"], call["strategy"], call["rank"], call["score"],
         call["final_score"], call["regime"], call["ref_close"], call["stop"], call["reason"],
         call.get("features"), call.get("strategy_version")),
    )
    con.commit()
    return cur.rowcount > 0


def active_calls(con) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM calls WHERE status IN ('PENDING','OPEN')", con)


def calls_to_enrich(con) -> pd.DataFrame:
    """Calls whose review fields can still change: open ones, closed ones awaiting the post-exit
    move, and any call whose signal-time features were never recorded."""
    return pd.read_sql("""SELECT * FROM calls WHERE status = 'OPEN' OR features IS NULL
                          OR (status = 'CLOSED' AND post_exit_5d_pct IS NULL)""", con)


def set_fields(con, call_id: int, fields: dict):
    """Fill review/signal-time fields (never the signal itself)."""
    if not fields:
        return
    con.execute(f"UPDATE calls SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), call_id])
    con.commit()


def mark_reviewed(con, ids, day: str, column: str = "reviewed_on"):
    assert column in ("reviewed_on", "final_reviewed_on")
    con.executemany(f"UPDATE calls SET {column}=? WHERE id=?", [(day, int(i)) for i in ids])
    con.commit()


def all_calls(con) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM calls ORDER BY signal_date, rank", con,
                       parse_dates=["signal_date", "entry_date", "exit_date"])


def update_outcome(con, call_id: int, outcome: dict):
    vals = {f: outcome.get(f) for f in OUTCOME_FIELDS}
    if vals["status"] == "OPEN" and outcome.get("exit_due"):
        vals["exit_reason"] = "DUE: " + outcome["exit_due"]   # still open; exit happens next session
    for f in ("entry_date", "exit_date"):
        if vals[f] is not None:
            vals[f] = pd.Timestamp(vals[f]).strftime("%Y-%m-%d")
    sets = ", ".join(f"{f}=?" for f in OUTCOME_FIELDS)
    con.execute(f"UPDATE calls SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
                [*vals.values(), call_id])
    con.commit()
