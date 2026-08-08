"""Small background scheduler that never blocks the Qt event loop."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal

from folderbridge.google_drive import GoogleDriveBackend
from folderbridge.models import SyncJob
from folderbridge.storage import StateStore
from folderbridge.sync_engine import SyncEngine


class SyncScheduler(QObject):
    busy_changed = Signal(bool)
    batch_finished = Signal(object)

    def __init__(
        self,
        store: StateStore,
        engine: SyncEngine,
        drive_factory: Callable[[], object] | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.engine = engine
        self.drive_factory = drive_factory or GoogleDriveBackend.from_saved
        self.paused = False
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    @staticmethod
    def _is_due(job: SyncJob) -> bool:
        if not job.enabled:
            return False
        if not job.last_run_at:
            return True
        try:
            last = datetime.fromisoformat(job.last_run_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            return elapsed >= job.interval_s
        except ValueError:
            return True

    def tick(self) -> None:
        if self.paused or self._busy:
            return
        due = [job for job in self.store.list_jobs() if self._is_due(job)]
        if due:
            self._start(due)

    def run_now(self, task_ids: list[int] | None = None) -> None:
        if self._busy:
            return
        jobs = self.store.list_jobs()
        if task_ids is not None:
            wanted = set(task_ids)
            jobs = [job for job in jobs if job.id in wanted]
        if jobs:
            self._start(jobs)

    def _start(self, jobs: list[SyncJob]) -> None:
        self._busy = True
        self.busy_changed.emit(True)

        def worker() -> None:
            outcomes: list[tuple[int | None, bool, str]] = []
            try:
                drive = self.drive_factory()
            except Exception as exc:
                message = str(exc)
                for job in jobs:
                    if job.id is not None:
                        self.store.mark_run(job.id, message)
                        self.store.add_event("ERROR", message, job.id)
                    outcomes.append((job.id, False, message))
            else:
                for job in jobs:
                    try:
                        result = self.engine.sync(job, drive)
                        outcomes.append(
                            (job.id, True, f"Изменено {result.changed}, пропущено {result.skipped}")
                        )
                    except Exception as exc:
                        outcomes.append((job.id, False, str(exc)))
            self._busy = False
            self.busy_changed.emit(False)
            self.batch_finished.emit(outcomes)

        threading.Thread(target=worker, name="FolderBridgeSync", daemon=True).start()

