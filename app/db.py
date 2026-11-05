"""SQLite storage for runs, timeline steps, receipts, and approval clicks."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class DB:
    """A small locked SQLite database."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    urls TEXT NOT NULL,
                    mode TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    listing_url TEXT,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    container_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    listing_url TEXT,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    label TEXT
                );
                CREATE TABLE IF NOT EXISTS verdicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    listing_url TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    reasons TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    note TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def create_run(self, urls: list[str], mode: str) -> str:
        """Insert a run and return its id."""
        run_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, created_at, status, urls, mode) VALUES (?, ?, ?, ?, ?)",
                (run_id, _now(), "queued", "\n".join(urls), mode),
            )
            self._conn.commit()
        return run_id

    def set_status(self, run_id: str, status: str) -> None:
        """Update a run's status."""
        with self._lock:
            self._conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
            self._conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        """Return one run, or None."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_urls(self, run_id: str) -> list[str]:
        """Return the URLs stored on a run."""
        run = self.get_run(run_id)
        if not run:
            return []
        return [line for line in run["urls"].splitlines() if line.strip()]

    def add_step(
        self,
        run_id: str,
        listing_url: str,
        name: str,
        status: str,
        detail: str,
        container_id: str,
    ) -> int:
        """Append a timeline step and return its id."""
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO steps (run_id, listing_url, name, status, detail, container_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, listing_url, name, status, detail, container_id, _now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_steps(self, run_id: str) -> list[dict]:
        """Return every step for a run, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def steps_after(self, run_id: str, step_id: int) -> list[dict]:
        """Return steps with id greater than ``step_id``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM steps WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, step_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_artifact(self, run_id: str, listing_url: str, kind: str, path: str, label: str) -> int:
        """Store a receipt path and return its id."""
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO artifacts (run_id, listing_url, kind, path, label)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, listing_url, kind, path, label),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def find_artifact(self, run_id: str, listing_url: str, kind: str) -> int | None:
        """Return the latest artifact id of a kind for one listing."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id FROM artifacts
                WHERE run_id = ? AND listing_url = ? AND kind = ?
                ORDER BY id DESC LIMIT 1
                """,
                (run_id, listing_url, kind),
            ).fetchone()
        return int(row["id"]) if row else None

    def get_artifact(self, artifact_id: int) -> dict | None:
        """Return one artifact row."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return dict(row) if row else None

    def list_artifacts(self, run_id: str, listing_url: str | None = None) -> list[dict]:
        """Return artifacts for a run, optionally one listing."""
        with self._lock:
            if listing_url is None:
                rows = self._conn.execute(
                    "SELECT * FROM artifacts WHERE run_id = ? ORDER BY id",
                    (run_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM artifacts WHERE run_id = ? AND listing_url = ? ORDER BY id",
                    (run_id, listing_url),
                ).fetchall()
        return [dict(row) for row in rows]

    def save_verdict(
        self,
        run_id: str,
        listing_url: str,
        risk: str,
        score: int,
        summary: str,
        reasons: str,
    ) -> None:
        """Replace any previous verdict for this listing."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM verdicts WHERE run_id = ? AND listing_url = ?",
                (run_id, listing_url),
            )
            self._conn.execute(
                """
                INSERT INTO verdicts (run_id, listing_url, risk, score, summary, reasons)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, listing_url, risk, score, summary, reasons),
            )
            self._conn.commit()

    def list_verdicts(self, run_id: str) -> list[dict]:
        """Return verdicts for a run."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM verdicts WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_approval(self, run_id: str, action: str, note: str) -> dict:
        """Record a human click. This does not send a message or buy anything."""
        created = _now()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO approvals (run_id, action, created_at, note)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, action, created, note),
            )
            self._conn.commit()
            approval_id = int(cur.lastrowid)
        return {"id": approval_id, "run_id": run_id, "action": action, "created_at": created, "note": note}

    def list_approvals(self, run_id: str) -> list[dict]:
        """Return approval clicks for a run."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]
