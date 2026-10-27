"""Pipeline state in SQLite: one row per saved post, plus a small key/value table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from reel_vault.models import SavedPost

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    shortcode     TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    owner         TEXT,
    posted_at     TEXT,
    caption       TEXT,
    hashtags      TEXT,            -- JSON list
    status        TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    category      TEXT,
    note_path     TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first schema. Added in place to existing databases.
MIGRATIONS = {
    "transcript": "TEXT",
    "language": "TEXT",
    "analysis": "TEXT",  # JSON from the analyze stage
    "enrichment": "TEXT",  # JSON list of resolved resources (enrich stage)
}

MAX_ATTEMPTS = 3


class Status(StrEnum):
    PENDING = "pending"
    MEDIA = "media"
    TRANSCRIBED = "transcribed"
    ANALYZED = "analyzed"
    WRITTEN = "written"
    FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(posts)")}
        with self._conn:
            for column, decl in MIGRATIONS.items():
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE posts ADD COLUMN {column} {decl}")

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def is_known(self, shortcode: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM posts WHERE shortcode = ?", (shortcode,)).fetchone()
        return row is not None

    def add_post(self, post: SavedPost) -> bool:
        """Record a newly seen post as pending. Returns False if it was already known."""
        now = _now()
        with self._conn:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO posts
                   (shortcode, kind, owner, posted_at, caption, hashtags,
                    status, first_seen_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    post.shortcode,
                    post.kind,
                    post.owner,
                    post.posted_at.isoformat(),
                    post.caption,
                    json.dumps(post.hashtags),
                    Status.PENDING,
                    now,
                    now,
                ),
            )
        return cur.rowcount == 1

    def get_post(self, shortcode: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM posts WHERE shortcode = ?", (shortcode,)).fetchone()

    def work_queue(self, limit: int = 0) -> list[sqlite3.Row]:
        """Posts still to process, in the order they were first seen (newest save first)."""
        sql = "SELECT * FROM posts WHERE status NOT IN (?, ?) ORDER BY rowid"
        params: tuple = (Status.WRITTEN, Status.FAILED)
        if limit:
            sql += " LIMIT ?"
            params += (limit,)
        return self._conn.execute(sql, params).fetchall()

    def written(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM posts WHERE status = ? ORDER BY posted_at DESC", (Status.WRITTEN,)
        ).fetchall()

    def save_transcript(self, shortcode: str, text: str, language: str | None) -> None:
        self._update(shortcode, status=Status.TRANSCRIBED, transcript=text, language=language)

    def save_analysis(self, shortcode: str, analysis: dict, category: str) -> None:
        self._update(
            shortcode, status=Status.ANALYZED, analysis=json.dumps(analysis), category=category
        )

    def set_owner(self, shortcode: str, owner: str) -> None:
        self._update(shortcode, owner=owner)

    def save_enrichment(self, shortcode: str, resources: list[dict]) -> None:
        self._update(shortcode, enrichment=json.dumps(resources))

    def mark_written(self, shortcode: str, note_path: str) -> None:
        self._update(shortcode, status=Status.WRITTEN, note_path=note_path, last_error=None)

    def record_failure(self, shortcode: str, error: str) -> bool:
        """Count a failed attempt. Returns True if the post is now permanently failed."""
        row = self.get_post(shortcode)
        attempts = (row["attempts"] if row else 0) + 1
        failed = attempts >= MAX_ATTEMPTS
        fields: dict = {"attempts": attempts, "last_error": error[:2000]}
        if failed:
            fields["status"] = Status.FAILED
        self._update(shortcode, **fields)
        return failed

    def retry_failed(self) -> int:
        """Send failed posts back to the start of the pipeline."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE posts SET status = ?, attempts = 0, updated_at = ? WHERE status = ?",
                (Status.PENDING, _now(), Status.FAILED),
            )
        return cur.rowcount

    def _update(self, shortcode: str, **fields) -> None:
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._conn:
            self._conn.execute(
                f"UPDATE posts SET {assignments} WHERE shortcode = ?",
                (*fields.values(), shortcode),
            )

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM posts GROUP BY status")
        return {row["status"]: row["n"] for row in rows}

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # The first full walk of the saved list must finish before we allow early
    # stopping; otherwise an interrupted backlog run would strand older saves.
    @property
    def backfill_complete(self) -> bool:
        return self.get_meta("backfill_complete") == "1"

    def mark_backfill_complete(self) -> None:
        self.set_meta("backfill_complete", "1")
