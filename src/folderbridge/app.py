"""FolderBridge application entry point."""

from __future__ import annotations

import argparse
import sys
import traceback

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from folderbridge import __version__
from folderbridge.paths import app_data_dir, database_path, icon_path, quarantine_dir
from folderbridge.scheduler import SyncScheduler
from folderbridge.storage import StateStore
from folderbridge.sync_engine import SyncEngine
from folderbridge.theme import stylesheet
from folderbridge.ui import MainWindow


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="FolderBridge", add_help=True)
    parser.add_argument("--background", action="store_true", help="start minimized to tray")
    return parser.parse_args(argv)


def main() -> int:
    args = _arguments(sys.argv[1:])
    app = QApplication(sys.argv[:1])
    app.setOrganizationName("Granik115")
    app.setApplicationName("FolderBridge")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    icon = QIcon(str(icon_path()))
    if not icon.isNull():
        app.setWindowIcon(icon)

    instance_lock = QLockFile(str(app_data_dir() / "FolderBridge.lock"))
    instance_lock.setStaleLockTime(5000)
    if not instance_lock.tryLock(100):
        if not args.background:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                None,
                "FolderBridge уже запущен",
                "Откройте существующее окно через значок FolderBridge в системном трее.",
            )
        return 0
    app.aboutToQuit.connect(instance_lock.unlock)

    store = StateStore(database_path())

    def exception_hook(exc_type, exc_value, exc_traceback) -> None:
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        try:
            store.add_event("ERROR", "Необработанная ошибка:\n" + message)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_hook
    engine = SyncEngine(store, quarantine_dir())
    scheduler = SyncScheduler(store, engine)
    window = MainWindow(store, scheduler, start_background=args.background)
    window.show()
    if args.background and window.tray is not None and window.tray.isVisible():
        window.hide()
    return app.exec()
