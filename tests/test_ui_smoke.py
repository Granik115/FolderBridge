from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QIcon  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402

    from folderbridge.models import Direction, SyncJob, SyncMode  # noqa: E402
    from folderbridge.paths import icon_path  # noqa: E402
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

    def test_task_dialog_values_persist_for_every_direction_and_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.sqlite3")
            for direction in Direction:
                for mode in SyncMode:
                    with self.subTest(direction=direction, mode=mode):
                        dialog = TaskDialog(None, [])
                        dialog.name_edit.setText(f"{direction.value}-{mode.value}")
                        dialog.local_edit.setText(str(Path(temp) / "exchange"))
                        dialog.remote_edit.setText(f"tests/{direction.value}/{mode.value}")
                        dialog.direction_combo.setCurrentIndex(
                            dialog.direction_combo.findData(direction.value)
                        )
                        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData(mode.value))

                        job = dialog.value()
                        self.assertIsInstance(job.direction, Direction)
                        self.assertIsInstance(job.mode, SyncMode)
                        saved = store.save_job(job)
                        loaded = store.get_job(saved.id)
                        self.assertEqual(loaded.direction, direction)
                        self.assertEqual(loaded.mode, mode)
                        dialog.close()

    def test_application_icon_resource_loads(self):
        self.assertTrue(icon_path().is_file())
        self.assertFalse(QIcon(str(icon_path())).isNull())

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
                [{"tag_name": "v0.1.0b2", "prerelease": True, "assets": []}]
            )
            self.assertEqual(versions.table.rowCount(), 1)
            versions.close()
            window._exiting = True
            window.close()


if __name__ == "__main__":
    unittest.main()
