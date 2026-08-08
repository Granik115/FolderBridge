"""SQLite persistence for jobs, scheduler state and the visible operation log."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from folderbridge.models import Direction, SyncJob, SyncMode


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    def _ensure_schema(self) -> None:
        with self._schema_lock, self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    remote_path TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    interval_s INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transfer_state (
                    task_id INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    remote_id TEXT,
                    local_size INTEGER,
                    local_mtime_ns INTEGER,
                    remote_modified_time TEXT,
                    remote_md5 TEXT,
                    remote_sha256 TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, relative_path),
                    FOREIGN KEY(task_id) REFERENCES jobs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    task_id INTEGER,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events(id DESC);
                """
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> SyncJob:
        return SyncJob(
            id=int(row["id"]),
            name=row["name"],
            local_path=row["local_path"],
            remote_path=row["remote_path"],
            direction=Direction(row["direction"]),
            mode=SyncMode(row["mode"]),
            interval_s=int(row["interval_s"]),
            enabled=bool(row["enabled"]),
            last_run_at=row["last_run_at"],
            last_success_at=row["last_success_at"],
            last_error=row["last_error"],
        )

    def list_jobs(self) -> list[SyncJob]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [self._row_to_job(row) for row in rows]

    def get_job(self, task_id: int) -> SyncJob | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def save_job(self, job: SyncJob) -> SyncJob:
        now = utc_now()
        with self._connect() as db:
            if job.id is None:
                cursor = db.execute(
                    """
                    INSERT INTO jobs(
                        name, local_path, remote_path, direction, mode, interval_s, enabled,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.name.strip(),
                        job.local_path,
                        job.remote_path,
                        job.direction.value,
                        job.mode.value,
                        job.interval_s,
                        int(job.enabled),
                        now,
                        now,
                    ),
                )
                task_id = int(cursor.lastrowid)
            else:
                db.execute(
                    """
                    UPDATE jobs SET
                        name=?, local_path=?, remote_path=?, direction=?, mode=?, interval_s=?,
                        enabled=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        job.name.strip(),
                        job.local_path,
                        job.remote_path,
                        job.direction.value,
                        job.mode.value,
                        job.interval_s,
                        int(job.enabled),
                        now,
                        job.id,
                    ),
                )
                task_id = job.id
        saved = self.get_job(task_id)
        if saved is None:
            raise RuntimeError("Не удалось сохранить задание синхронизации.")
        return saved

    def delete_job(self, task_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM jobs WHERE id=?", (task_id,))

    def mark_run(self, task_id: int, error: str | None = None) -> None:
        now = utc_now()
        with self._connect() as db:
            if error:
                db.execute(
                    "UPDATE jobs SET last_run_at=?, last_error=?, updated_at=? WHERE id=?",
                    (now, error, now, task_id),
                )
            else:
                db.execute(
                    """
                    UPDATE jobs SET last_run_at=?, last_success_at=?, last_error=NULL, updated_at=?
                    WHERE id=?
                    """,
                    (now, now, now, task_id),
                )

    def set_enabled(self, task_id: int, enabled: bool) -> None:
        now = utc_now()
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), now, task_id),
            )

    def set_transfer_state(
        self,
        task_id: int,
        relative_path: str,
        *,
        remote_id: str | None,
        local_size: int | None,
        local_mtime_ns: int | None,
        remote_modified_time: str | None,
        remote_md5: str | None,
        remote_sha256: str | None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO transfer_state(
                    task_id, relative_path, remote_id, local_size, local_mtime_ns,
                    remote_modified_time, remote_md5, remote_sha256, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, relative_path) DO UPDATE SET
                    remote_id=excluded.remote_id,
                    local_size=excluded.local_size,
                    local_mtime_ns=excluded.local_mtime_ns,
                    remote_modified_time=excluded.remote_modified_time,
                    remote_md5=excluded.remote_md5,
                    remote_sha256=excluded.remote_sha256,
                    updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    relative_path,
                    remote_id,
                    local_size,
                    local_mtime_ns,
                    remote_modified_time,
                    remote_md5,
                    remote_sha256,
                    utc_now(),
                ),
            )

    def get_transfer_state(self, task_id: int, relative_path: str) -> dict[str, object] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM transfer_state WHERE task_id=? AND relative_path=?",
                (task_id, relative_path),
            ).fetchone()
        return dict(row) if row else None

    def clear_transfer_state(self, task_id: int, relative_path: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM transfer_state WHERE task_id=? AND relative_path=?",
                (task_id, relative_path),
            )

    def setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    def add_event(self, level: str, message: str, task_id: int | None = None) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO events(created_at, level, task_id, message) VALUES(?, ?, ?, ?)",
                (utc_now(), level.upper(), task_id, message),
            )
            # Keep the database bounded without deleting useful recent diagnostics.
            db.execute(
                "DELETE FROM events WHERE id NOT IN "
                "(SELECT id FROM events ORDER BY id DESC LIMIT 5000)"
            )

    def recent_events(self, limit: int = 300) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, created_at, level, task_id, message "
                "FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
