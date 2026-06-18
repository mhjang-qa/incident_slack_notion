"""SQLite state used for idempotency and thread tracking."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class IncidentMapping:
    slack_ts: str
    thread_ts: str
    notion_page_id: str
    last_thread_ts: str | None


class Storage:
    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incident_mapping (
                    slack_ts TEXT PRIMARY KEY,
                    thread_ts TEXT NOT NULL UNIQUE,
                    notion_page_id TEXT NOT NULL,
                    last_thread_ts TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_mapping_thread_ts "
                "ON incident_mapping(thread_ts)"
            )

    def get(self, slack_ts: str | None = None, thread_ts: str | None = None) -> IncidentMapping | None:
        if not slack_ts and not thread_ts:
            return None
        query = (
            "SELECT * FROM incident_mapping WHERE slack_ts = ? OR thread_ts = ? LIMIT 1"
        )
        with self.connection() as connection:
            row = connection.execute(query, (slack_ts or "", thread_ts or "")).fetchone()
        return self._to_mapping(row) if row else None

    def upsert(
        self,
        slack_ts: str,
        thread_ts: str,
        notion_page_id: str,
        last_thread_ts: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO incident_mapping
                    (slack_ts, thread_ts, notion_page_id, last_thread_ts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(slack_ts) DO UPDATE SET
                    thread_ts = excluded.thread_ts,
                    notion_page_id = excluded.notion_page_id,
                    last_thread_ts = excluded.last_thread_ts,
                    updated_at = excluded.updated_at
                """,
                (slack_ts, thread_ts, notion_page_id, last_thread_ts, now, now),
            )

    def tracked_incidents(self) -> list[IncidentMapping]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM incident_mapping ORDER BY updated_at DESC"
            ).fetchall()
        return [self._to_mapping(row) for row in rows]

    @staticmethod
    def _to_mapping(row: sqlite3.Row) -> IncidentMapping:
        return IncidentMapping(
            slack_ts=row["slack_ts"],
            thread_ts=row["thread_ts"],
            notion_page_id=row["notion_page_id"],
            last_thread_ts=row["last_thread_ts"],
        )

