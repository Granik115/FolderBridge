"""Small domain model for synchronization jobs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path


class Direction(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"

    @property
    def label(self) -> str:
        return "ПК → Drive" if self is Direction.UPLOAD else "Drive → ПК"


class SyncMode(str, Enum):
    COPY = "copy"
    MIRROR = "mirror"

    @property
    def label(self) -> str:
        return "Копирование" if self is SyncMode.COPY else "Зеркало"


@dataclass(frozen=True, slots=True)
class SyncJob:
    id: int | None
    name: str
    local_path: str
    remote_path: str
    direction: Direction
    mode: SyncMode = SyncMode.COPY
    interval_s: int = 15
    enabled: bool = True
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None

    def without_runtime_state(self) -> SyncJob:
        return replace(self, last_run_at=None, last_success_at=None, last_error=None)


@dataclass(slots=True)
class SyncResult:
    uploaded: int = 0
    downloaded: int = 0
    skipped: int = 0
    trashed_remote: int = 0
    quarantined_local: int = 0
    bytes_transferred: int = 0

    @property
    def changed(self) -> int:
        return self.uploaded + self.downloaded + self.trashed_remote + self.quarantined_local


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    id: str
    relative_path: str
    name: str
    is_folder: bool
    size: int | None = None
    modified_time: str | None = None
    md5: str | None = None
    sha256: str | None = None
    mime_type: str | None = None


def normalize_remote_path(value: str) -> str:
    value = value.replace("\\", "/").strip().strip("/")
    pieces = [piece.strip() for piece in value.split("/") if piece.strip()]
    if any(piece in {".", ".."} for piece in pieces):
        raise ValueError("Путь Drive не должен содержать '.' или '..'.")
    if any("\x00" in piece for piece in pieces):
        raise ValueError("Недопустимый символ в пути Drive.")
    return "/".join(pieces)


def _canonical_local(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def paths_overlap(first: str, second: str) -> bool:
    a = _canonical_local(first)
    b = _canonical_local(second)
    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass
    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False


def remote_paths_overlap(first: str, second: str) -> bool:
    a = normalize_remote_path(first).casefold()
    b = normalize_remote_path(second).casefold()
    if not a or not b:
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def is_dangerous_local_root(value: str) -> bool:
    path = _canonical_local(value)
    anchor = Path(path.anchor) if path.anchor else None
    if anchor and path == anchor:
        return True
    try:
        return path == Path.home().resolve(strict=False)
    except OSError:
        return False


def validate_job(job: SyncJob, existing: list[SyncJob] | None = None) -> None:
    if not job.name.strip():
        raise ValueError("Введите название задания.")
    if not job.local_path.strip():
        raise ValueError("Выберите локальную папку.")
    local = _canonical_local(job.local_path)
    if is_dangerous_local_root(str(local)):
        raise ValueError("Нельзя синхронизировать корень диска или домашнюю папку целиком.")
    if job.interval_s < 3:
        raise ValueError("Минимальный интервал — 3 секунды.")
    if job.interval_s > 86400:
        raise ValueError("Максимальный интервал — 24 часа.")
    remote = normalize_remote_path(job.remote_path)
    if not remote:
        raise ValueError("Укажите подпапку внутри FolderBridge на Google Drive.")
    for other in existing or []:
        if other.id == job.id or not other.enabled or not job.enabled:
            continue
        if paths_overlap(job.local_path, other.local_path):
            raise ValueError(
                f"Локальная папка пересекается с включённым заданием «{other.name}»."
            )
        if remote_paths_overlap(job.remote_path, other.remote_path):
            raise ValueError(
                f"Путь Drive пересекается с включённым заданием «{other.name}»."
            )

