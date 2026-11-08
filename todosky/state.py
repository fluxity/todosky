"""SQLite store for Todoist ⇄ Skylight links, tokens and cached ids."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dtime
import json
from pathlib import Path
import sqlite3

from .skylight import Tokens

ACTIVE, DONE, REMOVED = "active", "done", "removed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    todoist_id  TEXT NOT NULL,
    due_date    TEXT NOT NULL,
    skylight_id TEXT NOT NULL,
    summary     TEXT NOT NULL,
    start_time  TEXT,
    status      TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (todoist_id, due_date)
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Link:
    """One Todoist task occurrence and the Skylight chore it was pushed as.

    `summary` and `start_time` are what was last pushed, so Todoist edits can be
    detected without overwriting edits made on the Skylight side.
    """

    todoist_id: str
    due_date: date
    skylight_id: str
    summary: str
    start_time: dtime | None
    status: str = ACTIVE


class State:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # links

    def links(self) -> list[Link]:
        rows = self._db.execute(
            "SELECT todoist_id, due_date, skylight_id, summary, start_time, status FROM links"
        ).fetchall()
        return [
            Link(r[0], date.fromisoformat(r[1]), r[2], r[3], dtime.fromisoformat(r[4]) if r[4] else None, r[5])
            for r in rows
        ]

    def upsert(self, link: Link) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO links VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                link.todoist_id,
                link.due_date.isoformat(),
                link.skylight_id,
                link.summary,
                link.start_time.strftime("%H:%M") if link.start_time else None,
                link.status,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._db.commit()

    def delete(self, todoist_id: str, due_date: date) -> None:
        self._db.execute("DELETE FROM links WHERE todoist_id = ? AND due_date = ?", (todoist_id, due_date.isoformat()))
        self._db.commit()

    def prune(self, before: date) -> None:
        """Forget finished links whose date is long past."""
        self._db.execute(
            "DELETE FROM links WHERE status != ? AND due_date < ?", (ACTIVE, before.isoformat())
        )
        self._db.commit()

    # key/value

    def get(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))
        self._db.commit()

    def tokens(self) -> Tokens | None:
        raw = self.get("skylight_tokens")
        if not raw:
            return None
        try:
            return Tokens(**json.loads(raw))
        except (TypeError, ValueError):
            return None

    def save_tokens(self, tokens: Tokens) -> None:
        self.put("skylight_tokens", json.dumps(tokens.__dict__))
