from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication  # noqa: E402

    from folderbridge.models import Direction, SyncJob  # noqa: E402
    from folderbridge.scheduler import SyncScheduler  # noqa: E402
    from folderbridge.storage import StateStore  # noqa: E402
    from folderbridge.sync_engine import SyncEngine  # noqa: E402
    from folderbridge.ui import MainWindow, TaskDialog, VersionDialog  # noqa: E402

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime libraries are not available")
class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_task_dialog_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            job = SyncJob(None, "test", temp, "Autotuner/test", Direction.UPLOAD)
            dialog = TaskDialog(None, [], job)
            self.assertEqual(dialog.value().remote_path, "Autotuner/test")
            dialog.close()

    def test_main_window_and_version_dialog_construct(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.sqlite3")
            store.set_setting("minimize_to_tray", "0")
            store.set_setting("auto_updates", "0")
            engine = SyncEngine(store, Path(temp) / "quarantine")
            scheduler = SyncScheduler(store, engine, drive_factory=lambda: None)
            window = MainWindow(store, scheduler)
            self.assertIn("FolderBridge", window.windowTitle())
            versions = VersionDialog(window)
            versions.set_releases(
                [{"tag_name": "v0.1.0b1", "prerelease": True, "assets": []}]
            )
            self.assertEqual(versions.table.rowCount(), 1)
            versions.close()
            window._exiting = True
            window.close()


if __name__ == "__main__":
    unittest.main()
