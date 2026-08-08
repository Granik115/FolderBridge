from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from folderbridge import updates


class UpdateTests(unittest.TestCase):
    def test_version_selection_includes_prerelease_for_beta_channel(self):
        releases = [
            {"tag_name": "v0.1.0b2", "prerelease": True},
            {"tag_name": "v0.1.0", "prerelease": False},
        ]
        selected = updates.newest_eligible_release(releases, "0.1.0b1")
        self.assertEqual(selected["tag_name"], "v0.1.0")
        selected_stable = updates.newest_eligible_release(releases, "0.0.9")
        self.assertEqual(selected_stable["tag_name"], "v0.1.0")

    def test_asset_selection_matches_runtime_mode(self):
        release = {
            "assets": [
                {"name": "FolderBridge-0.1.0b1-windows-x64.zip"},
                {"name": "FolderBridge-0.1.0b1-setup.exe"},
            ]
        }
        self.assertTrue(updates.select_asset(release, "installer")["name"].endswith("setup.exe"))
        self.assertTrue(updates.select_asset(release, "portable")["name"].endswith(".zip"))

    def test_safe_zip_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                updates.safe_extract_zip(archive, root / "extract")
            self.assertFalse((root / "escape.txt").exists())

    def test_safe_zip_extracts_regular_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "good.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("FolderBridge/FolderBridge.exe", "binary")
            destination = root / "extract"
            updates.safe_extract_zip(archive, destination)
            self.assertEqual(
                updates.find_extracted_app(destination), destination / "FolderBridge"
            )

    def test_batches_wait_for_app_and_restore_portable_backup(self):
        installer = updates.build_installer_batch("setup.exe", "FolderBridge.exe")
        portable = updates.build_portable_batch("src", "dest", "extract", "archive", "backup")
        self.assertIn("tasklist", installer)
        self.assertIn("/VERYSILENT", installer)
        self.assertIn("robocopy", portable)
        self.assertIn("BACKUP", portable)


if __name__ == "__main__":
    unittest.main()

