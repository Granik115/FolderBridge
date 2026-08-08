from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from folderbridge.models import (
    Direction,
    SyncJob,
    SyncMode,
    normalize_remote_path,
    paths_overlap,
    remote_paths_overlap,
    validate_job,
)


class ModelTests(unittest.TestCase):
    def test_normalize_remote_path(self):
        self.assertEqual(normalize_remote_path(r"/Autotuner\to_pc/"), "Autotuner/to_pc")
        with self.assertRaises(ValueError):
            normalize_remote_path("Autotuner/../secret")

    def test_overlap_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertTrue(paths_overlap(str(root), str(root / "child")))
            self.assertFalse(paths_overlap(str(root / "a"), str(root / "b")))
        self.assertTrue(remote_paths_overlap("Autotuner", "Autotuner/to_pc"))
        self.assertFalse(remote_paths_overlap("Autotuner/to_pc", "Autotuner/from_pc"))

    def test_validate_rejects_overlapping_enabled_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = SyncJob(
                id=1,
                name="one",
                local_path=str(root / "one"),
                remote_path="Autotuner/one",
                direction=Direction.UPLOAD,
            )
            second = SyncJob(
                id=None,
                name="two",
                local_path=str(root / "one" / "nested"),
                remote_path="Autotuner/two",
                direction=Direction.DOWNLOAD,
                mode=SyncMode.COPY,
            )
            with self.assertRaisesRegex(ValueError, "пересекается"):
                validate_job(second, [first])

    def test_disabled_jobs_may_be_prepared_before_enabling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = SyncJob(1, "one", str(root / "same"), "one", Direction.UPLOAD)
            second = SyncJob(
                None,
                "two",
                str(root / "same"),
                "two",
                Direction.DOWNLOAD,
                enabled=False,
            )
            validate_job(second, [first])


if __name__ == "__main__":
    unittest.main()

