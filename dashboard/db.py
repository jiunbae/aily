"""Database layer using aiosqlite with raw SQL.

Provides async connection management, schema creation, and query helpers.
Uses WAL mode for concurrent reads during writes.
All queries use parameterized ? placeholders — no string interpolation.
"""

from __future__ import annotations

import contextvars
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

# Module-level connection — initialized once at startup
_db: aiosqlite.Connection | None = None

# Batch commit depth — per-coroutine via ContextVar for concurrency safety
_batch_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_batch_depth", default=0)

# Allowed table names for insert_or_ignore (prevents SQL injection via table name)
_VALID_TABLES = {"sessions", "messages", "events", "kv", "usage_snapshots", "command_queue", "session_limit_queue"}


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sessions (
    name            TEXT PRIMARY KEY,
    host            TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    agent_type      TEXT,
    working_dir     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    closed_at       TEXT,
    discord_thread_id   TEXT,
    discord_archived    INTEGER DEFAULT 0,
    slack_thread_ts     TEXT,
    slack_channel_id    TEXT,
    slack_archived      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name    TEXT NOT NULL REFERENCES sessions(name),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_id       TEXT,
    source_author   TEXT,
    timestamp       TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    dedup_hash      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup ON messages(dedup_hash);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_status_updated ON sessions(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_messages_session_role ON messages(session_name, role);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    session_name UNINDEXED,
    role UNINDEXED,
    content='messages',
    content_rowid='id'
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    session_name    TEXT,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS kv (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_snapshots (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider                    TEXT NOT NULL DEFAULT 'anthropic',
    polled_at                   TEXT NOT NULL,
    requests_limit              INTEGER,
    requests_remaining          INTEGER,
    requests_reset              TEXT,
    input_tokens_limit          INTEGER,
    input_tokens_remaining      INTEGER,
    input_tokens_reset          TEXT,
    output_tokens_limit         INTEGER,
    output_tokens_remaining     INTEGER,
    output_tokens_reset         TEXT,
    tokens_limit                INTEGER,
    tokens_remaining            INTEGER,
    tokens_reset                TEXT,
    poll_model                  TEXT,
    poll_status_code            INTEGER,
    error_message               TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_polled ON usage_snapshots(polled_at);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_snapshots(provider, polled_at);

CREATE TABLE IF NOT EXISTS command_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name    TEXT NOT NULL,
    host            TEXT NOT NULL,
    command         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    priority        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    executed_at     TEXT,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_cmdq_status ON command_queue(status);
CREATE INDEX IF NOT EXISTS idx_cmdq_created ON command_queue(created_at);

CREATE TABLE IF NOT EXISTS session_limit_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name    TEXT NOT NULL,
    host            TEXT NOT NULL,
    platform        TEXT NOT NULL,
    channel_id      TEXT NOT NULL,
    thread_id       TEXT,
    user_message    TEXT NOT NULL,
    user_name       TEXT,
    source_msg_id   TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 12,
    retry_interval  INTEGER NOT NULL DEFAULT 1800,
    next_retry_at   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_slq_status_platform ON session_limit_queue(status, platform);
CREATE INDEX IF NOT EXISTS idx_slq_next_retry ON session_limit_queue(next_retry_at);
"""

_TRIGGER_SQL = [
    """CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content, session_name, role)
        VALUES (new.id, new.content, new.session_name, new.role);
    END""",
    """CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content, session_name, role)
        VALUES ('delete', old.id, old.content, old.session_name, old.role);
    END""",
    """CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content, session_name, role)
        VALUES ('delete', old.id, old.content, old.session_name, old.role);
        INSERT INTO messages_fts(rowid, content, session_name, role)
        VALUES (new.id, new.content, new.session_name, new.role);
    END""",
]


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Initialize database connection and create schema.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        The aiosqlite connection object.
    """
    global _db

    # Ensure parent directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row

    # Execute schema (each statement separately for WAL pragma)
    for statement in SCHEMA_SQL.strip().split(";"):
        statement = statement.strip()
        if statement:
            await _db.execute(statement)

    for trigger_sql in _TRIGGER_SQL:
        await _db.execute(trigger_sql)

    await _db.commit()

    logger.info("Database initialized at %s", db_path)
    return _db


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("Database connection closed")


def get_db() -> aiosqlite.Connection:
    """Get the current database connection.

    Raises:
        RuntimeError: If the database has not been initialized.
    """
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


@asynccontextmanager
async def batch() -> AsyncIterator[None]:
    """Context manager for batching multiple writes into a single commit.

    Uses ContextVar for concurrency safety — each coroutine tracks its own
    batch depth independently. Supports reentrant (nested) usage.

    Usage:
        async with db.batch():
            await db.execute(...)
            await db.insert_or_ignore(...)
        # commit happens here automatically
    """
    depth = _batch_depth.get()
    _batch_depth.set(depth + 1)
    try:
        yield
    except BaseException:
        _batch_depth.set(depth)
        if depth == 0:
            await get_db().rollback()
        raise
    else:
        _batch_depth.set(depth)
        if depth == 0:
            await get_db().commit()


async def execute(sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
    """Execute a SQL statement with parameters."""
    db = get_db()
    cursor = await db.execute(sql, params)
    if _batch_depth.get() == 0:
        await db.commit()
    return cursor


async def fetchone(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Execute a query and return a single row as a dict, or None."""
    db = get_db()
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute a query and return all rows as a list of dicts."""
    db = get_db()
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def insert_or_ignore(
    table: str, data: dict[str, Any]
) -> aiosqlite.Cursor:
    """Insert a row, ignoring if it violates a unique constraint.

    Args:
        table: Table name (must be a known table — not user input).
        data: Column name -> value mapping.

    Returns:
        The cursor from the insert.
    """
    if table not in _VALID_TABLES:
        raise ValueError(
            f"Invalid table name {table!r}. Must be one of: {sorted(_VALID_TABLES)}"
        )

    for col in data.keys():
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", col):
            raise ValueError(f"Invalid column name: {col!r}")

    columns = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    values = tuple(data.values())

    db = get_db()
    cursor = await db.execute(
        f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})",
        values,
    )
    if _batch_depth.get() == 0:
        await db.commit()
    return cursor


async def cleanup_old_events(days: int = 30) -> int:
    """Delete events older than the given number of days. Returns deleted count."""
    days = max(1, days)
    cursor = await execute(
        "DELETE FROM events WHERE created_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    return cursor.rowcount or 0


def now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()
