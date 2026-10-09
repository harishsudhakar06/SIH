"""SQLite access layer: WAL mode, foreign keys, transactions, schema bootstrap."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config import BASE_DIR, config

SCHEMA_PATH = BASE_DIR / "schema.sql"
_local = threading.local()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_conn() -> sqlite3.Connection:
    """Thread-local connection reused across a request."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    _local.in_tx = False


@contextmanager
def tx(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Explicit multi-statement transaction: commits on success, rolls back on error.

    While inside this block, execute() calls defer their commit to the block exit
    so a failure anywhere rolls back the whole unit of work.
    """
    c = conn or get_conn()
    outer = getattr(_local, "in_tx", False)
    _local.in_tx = True
    try:
        yield c
        if not outer:
            c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        _local.in_tx = outer


def query(sql: str, params: tuple | dict = (), conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    cur = (conn or get_conn()).execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple | dict = (), conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    cur = (conn or get_conn()).execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple | dict = (), conn: sqlite3.Connection | None = None) -> int:
    """Execute a write. Commits immediately unless inside an explicit tx() block."""
    c = conn or get_conn()
    cur = c.execute(sql, params)
    if not getattr(_local, "in_tx", False):
        c.commit()
    return int(cur.lastrowid or 0)


def executescript(script: str, conn: sqlite3.Connection | None = None) -> None:
    c = conn or get_conn()
    c.executescript(script)
    c.commit()


def init_db(db_path: Path | None = None) -> None:
    config.ensure_dirs()
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def is_db_initialised(db_path: Path | None = None) -> bool:
    path = Path(db_path or config.DB_PATH)
    if not path.exists():
        return False
    conn = connect(path)
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        return row is not None
    finally:
        conn.close()
