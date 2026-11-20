"""Работа с SQLite: схема, транзакции (с вложенностью) и мелкие помощники."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Callable, Optional

from .models import from_row, to_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    login           TEXT NOT NULL UNIQUE,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL,
    role            TEXT NOT NULL,
    group_id        INTEGER REFERENCES groups(id),
    notify_channel  TEXT NOT NULL DEFAULT 'EMAIL',
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    group_id  INTEGER REFERENCES groups(id),
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sla_policies (
    priority          TEXT PRIMARY KEY,
    response_minutes  INTEGER NOT NULL CHECK (response_minutes > 0),
    resolve_minutes   INTEGER NOT NULL CHECK (resolve_minutes > 0)
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    number             TEXT UNIQUE,
    title              TEXT NOT NULL,
    description        TEXT NOT NULL,
    category_id        INTEGER NOT NULL REFERENCES categories(id),
    impact             TEXT NOT NULL,
    urgency            TEXT NOT NULL,
    priority           TEXT NOT NULL,
    status             TEXT NOT NULL,
    requester_id       INTEGER NOT NULL REFERENCES users(id),
    group_id           INTEGER REFERENCES groups(id),
    assignee_id        INTEGER REFERENCES users(id),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    sla_start_at       TEXT NOT NULL,
    response_due_at    TEXT NOT NULL,
    resolve_due_at     TEXT NOT NULL,
    first_response_at  TEXT,
    resolved_at        TEXT,
    closed_at          TEXT,
    waiting_since      TEXT,
    waited_seconds     INTEGER NOT NULL DEFAULT 0,
    resolution_text    TEXT,
    reopen_count       INTEGER NOT NULL DEFAULT 0,
    risk_notified      INTEGER NOT NULL DEFAULT 0,
    breach_notified    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tickets_status    ON tickets(status);
CREATE INDEX IF NOT EXISTS ix_tickets_assignee  ON tickets(assignee_id);
CREATE INDEX IF NOT EXISTS ix_tickets_requester ON tickets(requester_id);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    author_id   INTEGER NOT NULL REFERENCES users(id),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     INTEGER NOT NULL REFERENCES tickets(id),
    author_id     INTEGER NOT NULL REFERENCES users(id),
    filename      TEXT NOT NULL,
    content_type  TEXT NOT NULL,
    size          INTEGER NOT NULL,
    data          BLOB NOT NULL,
    created_at    TEXT NOT NULL
);

-- История: у каждого перехода/назначения/эскалации есть автор (NULL = система), время и причина.
CREATE TABLE IF NOT EXISTS status_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id),
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    author_id    INTEGER REFERENCES users(id),
    reason       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id),
    group_id     INTEGER REFERENCES groups(id),
    assignee_id  INTEGER REFERENCES users(id),
    author_id    INTEGER REFERENCES users(id),
    reason       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escalations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id         INTEGER NOT NULL REFERENCES tickets(id),
    kind              TEXT NOT NULL,
    author_id         INTEGER NOT NULL REFERENCES users(id),
    reason            TEXT NOT NULL,
    from_group_id     INTEGER REFERENCES groups(id),
    from_assignee_id  INTEGER REFERENCES users(id),
    to_group_id       INTEGER REFERENCES groups(id),
    to_assignee_id    INTEGER REFERENCES users(id),
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS field_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    author_id   INTEGER REFERENCES users(id),
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Оценка: 0..1 на заявку (UNIQUE), только 1..5.
CREATE TABLE IF NOT EXISTS feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     INTEGER NOT NULL UNIQUE REFERENCES tickets(id),
    requester_id  INTEGER NOT NULL REFERENCES users(id),
    rating        INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    ticket_id   INTEGER REFERENCES tickets(id),
    channel     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kb_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    keywords     TEXT NOT NULL DEFAULT '',
    category_id  INTEGER REFERENCES categories(id),
    author_id    INTEGER NOT NULL REFERENCES users(id),
    created_at   TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._depth = 0
        self._after_commit: list[Callable[[], None]] = []

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ транзакции
    @contextmanager
    def transaction(self):
        """Вложенные вызовы присоединяются к внешней транзакции.

        Любая ошибка откатывает всю операцию целиком (статус, история и уведомления).
        """
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._depth = 1
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            self._after_commit.clear()
            raise
        finally:
            self._depth = 0
        callbacks, self._after_commit = self._after_commit, []
        for callback in callbacks:
            callback()

    def after_commit(self, callback: Callable[[], None]) -> None:
        """Выполнить действие после успешного коммита (например, отправить письмо)."""
        if self._depth:
            self._after_commit.append(callback)
        else:
            callback()

    # ------------------------------------------------------------ запросы
    def execute(self, sql: str, params=()):
        return self.conn.execute(sql, tuple(to_db(p) for p in params))

    def insert(self, table: str, **values) -> int:
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = self.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", values.values())
        return cur.lastrowid

    def update(self, table: str, row_id: int, **values) -> None:
        assignments = ", ".join(f"{name} = ?" for name in values)
        self.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*values.values(), row_id))

    def query(self, cls, sql: str, params=()) -> list:
        return [from_row(cls, row) for row in self.execute(sql, params).fetchall()]

    def one(self, cls, sql: str, params=()) -> Optional[object]:
        row = self.execute(sql, params).fetchone()
        return from_row(cls, row) if row else None

    def scalar(self, sql: str, params=()):
        row = self.execute(sql, params).fetchone()
        return row[0] if row else None
