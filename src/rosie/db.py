"""SQLite storage. One file, schema migrations applied in order at startup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .grocery import DEFAULT_ORDER, SECTIONS


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def _seed_lists() -> str:
    """Costco and Meijer (store lists, walk-ordered sections) and To-do."""
    sql = [
        "INSERT INTO lists (id, name, kind, position) VALUES"
        " (1, 'Costco', 'store', 1), (2, 'Meijer', 'store', 2), (3, 'To-do', 'todo', 3)"
    ]
    for list_id, store in ((1, "Costco"), (2, "Meijer")):
        order = DEFAULT_ORDER.get(store, SECTIONS)
        values = ", ".join(f"({list_id}, {_q(sec)}, {i})" for i, sec in enumerate(order))
        sql.append(f"INSERT INTO list_sections (list_id, section, position) VALUES {values}")
    return ";\n".join(sql) + ";"


# Statements are split on ";", so comments inside must not contain one.
MIGRATIONS = [
    # 1: users, sessions, login throttling, API usage, saved recipes
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
        password_hash TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    );
    CREATE TABLE login_failures (
        username TEXT NOT NULL COLLATE NOCASE,
        at INTEGER NOT NULL
    );
    CREATE INDEX login_failures_at ON login_failures(at);
    CREATE TABLE api_usage (
        id INTEGER PRIMARY KEY,
        at INTEGER NOT NULL,
        user_id INTEGER,
        feature TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cost_usd REAL NOT NULL,
        request_id TEXT
    );
    CREATE INDEX api_usage_at ON api_usage(at);
    CREATE TABLE recipes (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        markdown TEXT NOT NULL,
        source_url TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at INTEGER NOT NULL
    );
    """,
    # 2: shared lists (#5)
    """
    CREATE TABLE lists (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('store', 'todo')),
        position INTEGER NOT NULL
    );
    CREATE TABLE list_sections (
        list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
        section TEXT NOT NULL,
        position INTEGER NOT NULL,
        PRIMARY KEY (list_id, section)
    );
    CREATE TABLE list_items (
        id INTEGER PRIMARY KEY,
        list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        item_key TEXT NOT NULL,
        section TEXT,
        aisle TEXT NOT NULL DEFAULT '',
        checked INTEGER NOT NULL DEFAULT 0,
        added_by INTEGER REFERENCES users(id),
        added_at INTEGER NOT NULL,
        checked_at INTEGER
    );
    CREATE INDEX list_items_list ON list_items(list_id, checked);
    -- Section an item belongs in, shared by all stores. source: keyword, ai or user.
    CREATE TABLE item_sections (
        item_key TEXT PRIMARY KEY,
        section TEXT NOT NULL,
        source TEXT NOT NULL
    );
    -- Aisle/bay remembered per store list.
    CREATE TABLE item_aisles (
        list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
        item_key TEXT NOT NULL,
        aisle TEXT NOT NULL,
        PRIMARY KEY (list_id, item_key)
    );
    """
    + _seed_lists(),
    # 3: Home actions log (#7)
    """
    CREATE TABLE home_actions (
        id INTEGER PRIMARY KEY,
        at INTEGER NOT NULL,
        user_id INTEGER REFERENCES users(id),
        entity TEXT NOT NULL,
        action TEXT NOT NULL,
        value REAL
    );
    """,
    # 4: Saved chats, private to each user (#23). messages = what the model is
    # sent next time, display = what the screen shows (bubbles and recipe cards)
    """
    CREATE TABLE chats (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        messages TEXT NOT NULL,
        display TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE INDEX chats_by_user ON chats (user_id, kind, updated_at);
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.execute("BEGIN")
        try:
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {i}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
