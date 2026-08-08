from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from folderbridge.models import Direction, RemoteEntry, SyncJob, SyncMode
from folderbridge.storage import StateStore
from folderbridge.sync_engine import SyncEngine


def _entry(file_id: str, relative: str, content: bytes, folder: bool = False) -> RemoteEntry:
    return RemoteEntry(
        id=file_id,
        relative_path=relative,
        name=Path(relative).name,
        is_folder=folder,
        size=None if folder else len(content),
        md5=None if folder else hashlib.md5(content).hexdigest(),  # noqa: S324 - Drive metadata
        sha256=None if folder else hashlib.sha256(content).hexdigest(),
        mime_type="application/vnd.google-apps.folder" if folder else "application/octet-stream",
    )


class FakeDrive:
    def __init__(self, remote_root: str, files: dict[str, bytes] | None = None):
        self.remote_root = remote_root.strip("/")
        self.files = dict(files or {})
        self.folders: set[str] = set()
        self.trashed: list[str] = []
        self.upload_calls = 0
        self.corrupt_download = False

    def ensure_folder_path(self, remote_path: str) -> str:
        path = remote_path.strip("/")
        if path and path != self.remote_root:
            relative = path.removeprefix(self.remote_root).strip("/")
            if relative:
                self.folders.add(relative)
        return "folder"

    def list_tree(self, remote_path: str) -> list[RemoteEntry]:
        entries = [
            _entry("dir:" + path, path, b"", True)
            for path in sorted(self.folders)
        ]
        entries.extend(
            _entry("file:" + path, path, content)
            for path, content in sorted(self.files.items())
        )
        return entries

    def upload_file(self, local_path: Path, remote_path: str, existing_id: str | None = None):
        del existing_id
        relative = remote_path.removeprefix(self.remote_root).strip("/")
        content = local_path.read_bytes()
        self.files[relative] = content
        self.upload_calls += 1
        return _entry("file:" + relative, relative, content)

    def download_file(self, entry: RemoteEntry, destination: Path) -> None:
        content = self.files[entry.relative_path]
        destination.write_bytes(content + (b"bad" if self.corrupt_download else b""))

    def trash(self, file_id: str) -> None:
        self.trashed.append(file_id)
        prefix, relative = file_id.split(":", 1)
        if prefix == "file":
            self.files.pop(relative, None)
        else:
            self.folders.discard(relative)


class SyncEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = StateStore(root / "state.sqlite3")
        self.quarantine = root / "quarantine"
        self.engine = SyncEngine(self.store, self.quarantine)
        self.local = root / "local"
        self.local.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _job(self, direction: Direction, mode: SyncMode = SyncMode.COPY) -> SyncJob:
        return self.store.save_job(
            SyncJob(None, "test", str(self.local), "Autotuner/test", direction, mode, 10)
        )

    def test_upload_copy_keeps_remote_extra_and_skips_unchanged(self):
        (self.local / "a.txt").write_text("hello", encoding="utf-8")
        drive = FakeDrive("Autotuner/test", {"extra.txt": b"remote"})
        job = self._job(Direction.UPLOAD)
        first = self.engine.sync(job, drive)
        self.assertEqual(first.uploaded, 1)
        self.assertIn("extra.txt", drive.files)
        second = self.engine.sync(self.store.get_job(job.id), drive)
        self.assertEqual(second.uploaded, 0)
        self.assertEqual(second.skipped, 1)
        self.assertEqual(drive.upload_calls, 1)

    def test_upload_mirror_trashes_only_missing_remote_root(self):
        (self.local / "keep.txt").write_bytes(b"keep")
        drive = FakeDrive(
            "Autotuner/test",
            {"keep.txt": b"keep", "old/file.txt": b"old"},
        )
        drive.folders.add("old")
        job = self._job(Direction.UPLOAD, SyncMode.MIRROR)
        result = self.engine.sync(job, drive)
        self.assertEqual(result.trashed_remote, 1)
        self.assertEqual(drive.trashed, ["dir:old"])

    def test_download_copy_is_atomic_and_keeps_local_extra(self):
        (self.local / "extra.txt").write_text("local", encoding="utf-8")
        drive = FakeDrive("Autotuner/test", {"nested/a.bin": b"payload"})
        drive.folders.add("nested")
        job = self._job(Direction.DOWNLOAD)
        result = self.engine.sync(job, drive)
        self.assertEqual(result.downloaded, 1)
        self.assertEqual((self.local / "nested" / "a.bin").read_bytes(), b"payload")
        self.assertTrue((self.local / "extra.txt").exists())
        self.assertEqual(list(self.local.rglob(".folderbridge.part-*")), [])

    def test_download_mirror_quarantines_local_extra(self):
        (self.local / "old").mkdir()
        (self.local / "old" / "data.txt").write_text("recover me", encoding="utf-8")
        drive = FakeDrive("Autotuner/test", {})
        job = self._job(Direction.DOWNLOAD, SyncMode.MIRROR)
        result = self.engine.sync(job, drive)
        self.assertEqual(result.quarantined_local, 1)
        self.assertFalse((self.local / "old").exists())
        recovered = list(self.quarantine.rglob("data.txt"))
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].read_text(encoding="utf-8"), "recover me")

    def test_bad_download_does_not_replace_existing_destination(self):
        target = self.local / "a.txt"
        target.write_bytes(b"old")
        drive = FakeDrive("Autotuner/test", {"a.txt": b"new"})
        drive.corrupt_download = True
        job = self._job(Direction.DOWNLOAD)
        with self.assertRaisesRegex(IOError, "Размер"):
            self.engine.sync(job, drive)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(list(self.local.glob(".folderbridge.part-*")), [])

    def test_file_directory_conflict_stops_before_mutation(self):
        (self.local / "same").mkdir()
        drive = FakeDrive("Autotuner/test", {"same": b"remote file"})
        job = self._job(Direction.DOWNLOAD)
        with self.assertRaisesRegex(RuntimeError, "Конфликт типов"):
            self.engine.sync(job, drive)
        self.assertTrue((self.local / "same").is_dir())


if __name__ == "__main__":
    unittest.main()
