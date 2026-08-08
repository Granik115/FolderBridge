from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from folderbridge.models import Direction, SyncJob, SyncMode
from folderbridge.storage import StateStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "state.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_job_crud_and_runtime_state(self):
        saved = self.store.save_job(
            SyncJob(
                None,
                "commands",
                str(Path(self.temp.name) / "inbox"),
                "Autotuner/to_pc",
                Direction.DOWNLOAD,
                SyncMode.COPY,
                9,
            )
        )
        self.assertIsNotNone(saved.id)
        self.assertEqual(self.store.list_jobs()[0].name, "commands")
        changed = SyncJob(
            saved.id,
            "commands edited",
            saved.local_path,
            saved.remote_path,
            saved.direction,
            SyncMode.MIRROR,
            30,
            False,
        )
        self.store.save_job(changed)
        loaded = self.store.get_job(saved.id)
        self.assertEqual(loaded.name, "commands edited")
        self.assertEqual(loaded.mode, SyncMode.MIRROR)
        self.assertFalse(loaded.enabled)
        self.store.mark_run(saved.id, "offline")
        self.assertEqual(self.store.get_job(saved.id).last_error, "offline")
        self.store.mark_run(saved.id)
        self.assertIsNone(self.store.get_job(saved.id).last_error)
        self.assertIsNotNone(self.store.get_job(saved.id).last_success_at)
        self.store.delete_job(saved.id)
        self.assertEqual(self.store.list_jobs(), [])

    def test_settings_events_and_transfer_state(self):
        job = self.store.save_job(
            SyncJob(None, "x", str(Path(self.temp.name) / "x"), "x", Direction.UPLOAD)
        )
        self.store.set_setting("autosync", "1")
        self.assertEqual(self.store.setting("autosync"), "1")
        self.store.add_event("info", "hello", job.id)
        self.assertEqual(self.store.recent_events()[0]["message"], "hello")
        self.store.set_transfer_state(
            job.id,
            "a.txt",
            remote_id="remote-1",
            local_size=3,
            local_mtime_ns=4,
            remote_modified_time="now",
            remote_md5="abc",
            remote_sha256="def",
        )
        state = self.store.get_transfer_state(job.id, "a.txt")
        self.assertEqual(state["remote_id"], "remote-1")
        self.store.clear_transfer_state(job.id, "a.txt")
        self.assertIsNone(self.store.get_transfer_state(job.id, "a.txt"))


if __name__ == "__main__":
    unittest.main()

