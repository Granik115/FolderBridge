"""Deterministic one-way synchronization with recoverable mirror deletions."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from folderbridge.models import (
    Direction,
    RemoteEntry,
    SyncJob,
    SyncMode,
    SyncResult,
    is_dangerous_local_root,
    normalize_remote_path,
)
from folderbridge.storage import StateStore


class DriveBackend(Protocol):
    def ensure_folder_path(self, remote_path: str) -> str: ...

    def list_tree(self, remote_path: str) -> list[RemoteEntry]: ...

    def upload_file(
        self, local_path: Path, remote_path: str, existing_id: str | None = None
    ) -> RemoteEntry: ...

    def download_file(self, entry: RemoteEntry, destination: Path) -> None: ...

    def trash(self, file_id: str) -> None: ...


def _ignored(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.startswith(".folderbridge.part-")
        or name == ".folderbridge"
        or name.endswith(".folderbridge.tmp")
    )


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_join(root: str, relative: str) -> str:
    root = normalize_remote_path(root)
    relative = normalize_remote_path(relative)
    return "/".join(piece for piece in (root, relative) if piece)


def _local_tree(root: Path) -> tuple[dict[str, Path], set[str]]:
    files: dict[str, Path] = {}
    directories: set[str] = set()
    if not root.exists():
        return files, directories
    for current_root, dir_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        dir_names[:] = [
            name
            for name in dir_names
            if not _ignored(current / name) and not (current / name).is_symlink()
        ]
        for name in dir_names:
            relative = (current / name).relative_to(root).as_posix()
            directories.add(relative)
        for name in file_names:
            path = current / name
            if _ignored(path) or path.is_symlink():
                continue
            files[path.relative_to(root).as_posix()] = path
    return files, directories


def _state_is_current(
    state: dict[str, object] | None,
    path: Path,
    remote: RemoteEntry,
) -> bool:
    if not state:
        return False
    stat = path.stat()
    if state.get("local_size") != stat.st_size or state.get("local_mtime_ns") != stat.st_mtime_ns:
        return False
    if state.get("remote_id") != remote.id:
        return False
    if remote.sha256 and state.get("remote_sha256") != remote.sha256:
        return False
    if not remote.sha256 and remote.md5 and state.get("remote_md5") != remote.md5:
        return False
    return True


def _content_matches(path: Path, remote: RemoteEntry) -> bool:
    before = path.stat()
    if remote.size is not None and remote.size != before.st_size:
        return False
    if remote.sha256:
        matches = _hash_file(path, "sha256").casefold() == remote.sha256.casefold()
    elif remote.md5:
        matches = _hash_file(path, "md5").casefold() == remote.md5.casefold()
    else:
        # No content checksum (for example a Google Workspace document) means we cannot
        # safely assert equality from size alone.
        return False
    after = path.stat()
    stable = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
    return matches and stable


def _extra_roots(paths: set[str]) -> list[str]:
    """Return only highest absent paths so mirror never deletes a child twice."""
    result: list[str] = []
    for candidate in sorted(paths, key=lambda value: (value.count("/"), value.casefold())):
        parent = PurePosixPath(candidate).parent
        has_extra_parent = False
        while str(parent) not in {".", ""}:
            if parent.as_posix() in paths:
                has_extra_parent = True
                break
            parent = parent.parent
        if not has_extra_parent:
            result.append(candidate)
    return result


class SyncEngine:
    def __init__(self, store: StateStore, quarantine_root: Path):
        self.store = store
        self.quarantine_root = Path(quarantine_root)
        self.staging_root = self.quarantine_root.parent / "staging"
        if self.staging_root.exists():
            for stale in self.staging_root.glob("upload-*"):
                if stale.is_file():
                    stale.unlink(missing_ok=True)

    def _event(self, level: str, message: str, task_id: int | None) -> None:
        self.store.add_event(level, message, task_id)

    def sync(self, job: SyncJob, drive: DriveBackend) -> SyncResult:
        if job.id is None:
            raise ValueError("Задание должно быть сохранено перед синхронизацией.")
        local_root = Path(job.local_path).expanduser().resolve(strict=False)
        if is_dangerous_local_root(str(local_root)):
            raise ValueError("Защита FolderBridge запрещает синхронизацию корня диска/профиля.")
        self._event("INFO", f"Старт: {job.direction.label}, {job.mode.label}", job.id)
        try:
            if job.direction is Direction.UPLOAD:
                result = self._upload(job, local_root, drive)
            else:
                result = self._download(job, local_root, drive)
        except Exception as exc:
            self.store.mark_run(job.id, str(exc))
            self._event("ERROR", f"Ошибка: {exc}", job.id)
            raise
        self.store.mark_run(job.id)
        self._event(
            "INFO",
            (
                f"Готово: ↑{result.uploaded} ↓{result.downloaded}, "
                f"без изменений {result.skipped}, Drive→корзина {result.trashed_remote}, "
                f"ПК→карантин {result.quarantined_local}"
            ),
            job.id,
        )
        return result

    def _remember(self, job: SyncJob, relative: str, local: Path, remote: RemoteEntry) -> None:
        if job.id is None:
            return
        stat = local.stat()
        self.store.set_transfer_state(
            job.id,
            relative,
            remote_id=remote.id,
            local_size=stat.st_size,
            local_mtime_ns=stat.st_mtime_ns,
            remote_modified_time=remote.modified_time,
            remote_md5=remote.md5,
            remote_sha256=remote.sha256,
        )

    def _stable_snapshot(self, local_path: Path) -> tuple[Path, os.stat_result] | None:
        """Copy one stable source version so an actively written log is never torn."""
        before = local_path.stat()
        self.staging_root.mkdir(parents=True, exist_ok=True)
        snapshot = self.staging_root / f"upload-{uuid.uuid4().hex}{local_path.suffix}"
        try:
            shutil.copyfile(local_path, snapshot)
            after = local_path.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                snapshot.unlink(missing_ok=True)
                return None
            return snapshot, after
        except Exception:
            snapshot.unlink(missing_ok=True)
            raise

    def _upload(self, job: SyncJob, local_root: Path, drive: DriveBackend) -> SyncResult:
        if not local_root.is_dir():
            raise FileNotFoundError(f"Локальная папка не найдена: {local_root}")
        remote_root = normalize_remote_path(job.remote_path)
        drive.ensure_folder_path(remote_root)
        remote_entries = drive.list_tree(remote_root)
        remote_files = {item.relative_path: item for item in remote_entries if not item.is_folder}
        remote_dirs = {item.relative_path: item for item in remote_entries if item.is_folder}
        local_files, local_dirs = _local_tree(local_root)
        conflicts = (set(remote_files) & local_dirs) | (set(remote_dirs) & set(local_files))
        if conflicts:
            first = sorted(conflicts)[0]
            raise RuntimeError(
                f"Конфликт типов: {first!r} является файлом с одной стороны и папкой с другой."
            )
        result = SyncResult()

        for relative in sorted(local_dirs, key=lambda value: (value.count("/"), value.casefold())):
            if relative not in remote_dirs:
                drive.ensure_folder_path(_remote_join(remote_root, relative))

        for relative, local_path in sorted(local_files.items()):
            remote = remote_files.get(relative)
            if remote is not None:
                state = self.store.get_transfer_state(job.id, relative) if job.id else None
                if _state_is_current(state, local_path, remote) or _content_matches(
                    local_path, remote
                ):
                    self._remember(job, relative, local_path, remote)
                    result.skipped += 1
                    continue
            snapshot_info = self._stable_snapshot(local_path)
            if snapshot_info is None:
                result.skipped += 1
                self._event(
                    "WARNING",
                    f"Файл менялся во время чтения; повторим позже: {relative}",
                    job.id,
                )
                continue
            snapshot, source_stat = snapshot_info
            try:
                uploaded = drive.upload_file(
                    snapshot,
                    _remote_join(remote_root, relative),
                    existing_id=remote.id if remote else None,
                )
                result.bytes_transferred += snapshot.stat().st_size
            finally:
                snapshot.unlink(missing_ok=True)
            current = local_path.stat()
            if (
                current.st_size == source_stat.st_size
                and current.st_mtime_ns == source_stat.st_mtime_ns
            ):
                self._remember(job, relative, local_path, uploaded)
            else:
                self.store.clear_transfer_state(job.id, relative)
                self._event(
                    "WARNING",
                    f"Файл изменился после снимка; новая версия уйдёт следующим циклом: {relative}",
                    job.id,
                )
            result.uploaded += 1
            self._event("INFO", f"Загружен: {relative}", job.id)

        if job.mode is SyncMode.MIRROR:
            wanted = set(local_files) | local_dirs
            remote_map = {item.relative_path: item for item in remote_entries}
            extras = set(remote_map) - wanted
            for relative in _extra_roots(extras):
                drive.trash(remote_map[relative].id)
                self.store.clear_transfer_state(job.id, relative)
                result.trashed_remote += 1
                self._event("WARNING", f"В корзину Drive: {relative}", job.id)
        return result

    def _download(self, job: SyncJob, local_root: Path, drive: DriveBackend) -> SyncResult:
        remote_root = normalize_remote_path(job.remote_path)
        local_root.mkdir(parents=True, exist_ok=True)
        remote_entries = drive.list_tree(remote_root)
        result = SyncResult()
        local_files_before, local_dirs_before = _local_tree(local_root)
        remote_files_before = {
            item.relative_path for item in remote_entries if not item.is_folder
        }
        remote_dirs_before = {
            item.relative_path for item in remote_entries if item.is_folder
        }
        conflicts = (remote_files_before & local_dirs_before) | (
            remote_dirs_before & set(local_files_before)
        )
        if conflicts:
            first = sorted(conflicts)[0]
            raise RuntimeError(
                f"Конфликт типов: {first!r} является файлом с одной стороны и папкой с другой."
            )

        for entry in sorted(
            (item for item in remote_entries if item.is_folder),
            key=lambda item: (item.relative_path.count("/"), item.relative_path.casefold()),
        ):
            (local_root / Path(entry.relative_path)).mkdir(parents=True, exist_ok=True)

        remote_files: dict[str, RemoteEntry] = {}
        for entry in remote_entries:
            if entry.is_folder:
                continue
            if (entry.mime_type or "").startswith("application/vnd.google-apps."):
                result.skipped += 1
                self._event(
                    "WARNING",
                    f"Пропущен Google Workspace-файл (не blob): {entry.relative_path}",
                    job.id,
                )
                continue
            remote_files[entry.relative_path] = entry

        for relative, remote in sorted(remote_files.items()):
            destination = local_root / Path(relative)
            if destination.is_file() and not destination.is_symlink():
                state = self.store.get_transfer_state(job.id, relative) if job.id else None
                if _state_is_current(state, destination, remote) or _content_matches(
                    destination, remote
                ):
                    self._remember(job, relative, destination, remote)
                    result.skipped += 1
                    continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.parent / f".folderbridge.part-{uuid.uuid4().hex}-{destination.name}"
            try:
                drive.download_file(remote, temp)
                if remote.size is not None and temp.stat().st_size != remote.size:
                    raise OSError(f"Размер скачанного файла не совпал: {relative}")
                if (
                    remote.sha256
                    and _hash_file(temp, "sha256").casefold() != remote.sha256.casefold()
                ):
                    raise OSError(f"SHA-256 скачанного файла не совпал: {relative}")
                if (
                    not remote.sha256
                    and remote.md5
                    and _hash_file(temp, "md5").casefold() != remote.md5.casefold()
                ):
                    raise OSError(f"MD5 скачанного файла не совпал: {relative}")
                os.replace(temp, destination)
            finally:
                if temp.exists():
                    temp.unlink(missing_ok=True)
            self._remember(job, relative, destination, remote)
            result.downloaded += 1
            result.bytes_transferred += destination.stat().st_size
            self._event("INFO", f"Скачан: {relative}", job.id)

        if job.mode is SyncMode.MIRROR:
            local_files, local_dirs = _local_tree(local_root)
            wanted = {item.relative_path for item in remote_entries}
            extras = (set(local_files) | local_dirs) - wanted
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
            quarantine = self.quarantine_root / str(job.id) / timestamp
            for relative in _extra_roots(extras):
                source = local_root / Path(relative)
                if not source.exists():
                    continue
                target = quarantine / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                self.store.clear_transfer_state(job.id, relative)
                result.quarantined_local += 1
                self._event("WARNING", f"В локальный карантин: {relative}", job.id)
        return result
