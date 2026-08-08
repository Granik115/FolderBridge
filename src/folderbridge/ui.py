"""Qt user interface for FolderBridge."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from folderbridge import __version__, autostart, updates
from folderbridge.credentials import CredentialStore
from folderbridge.google_drive import GoogleDriveBackend
from folderbridge.models import Direction, SyncJob, SyncMode, normalize_remote_path, validate_job
from folderbridge.paths import project_root
from folderbridge.scheduler import SyncScheduler
from folderbridge.storage import StateStore
from folderbridge.theme import ERROR, SUCCESS, TEXT_MUTED, WARNING


def _human_status(job: SyncJob) -> str:
    if job.last_error:
        return "Ошибка: " + job.last_error
    if job.last_success_at:
        return "OK · " + job.last_success_at.replace("T", " ")[:19]
    return "Ещё не запускалось"


class TaskDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        existing_jobs: list[SyncJob],
        job: SyncJob | None = None,
    ) -> None:
        super().__init__(parent)
        self.existing_jobs = existing_jobs
        self.original = job
        self.setWindowTitle("Задание синхронизации")
        self.setMinimumWidth(660)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.name_edit = QLineEdit(job.name if job else "")
        self.name_edit.setPlaceholderText("Например: Autotuner — команды")
        form.addRow("Название", self.name_edit)

        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        self.local_edit = QLineEdit(job.local_path if job else "")
        self.local_edit.setPlaceholderText(r"C:\AutotunerExchange\inbox")
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._browse_local)
        local_layout.addWidget(self.local_edit, 1)
        local_layout.addWidget(browse)
        form.addRow("Локальная папка", local_row)

        self.remote_edit = QLineEdit(job.remote_path if job else "")
        self.remote_edit.setPlaceholderText("Autotuner/to_pc")
        form.addRow("Путь на Drive", self.remote_edit)

        remote_hint = QLabel(
            "Путь задаётся внутри Google Drive / FolderBridge. "
            "Сам корень создаётся автоматически."
        )
        remote_hint.setObjectName("muted")
        remote_hint.setWordWrap(True)
        form.addRow("", remote_hint)

        self.direction_combo = QComboBox()
        # Keep only plain strings in Qt item data.  PySide on Windows may unwrap a
        # ``str, Enum`` instance to ``str`` while crossing the QVariant boundary.
        self.direction_combo.addItem(Direction.UPLOAD.label, Direction.UPLOAD.value)
        self.direction_combo.addItem(Direction.DOWNLOAD.label, Direction.DOWNLOAD.value)
        direction = job.direction if job else Direction.UPLOAD
        self.direction_combo.setCurrentIndex(
            self.direction_combo.findData(Direction(direction).value)
        )
        form.addRow("Направление", self.direction_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(SyncMode.COPY.label + " (без удалений)", SyncMode.COPY.value)
        self.mode_combo.addItem(
            SyncMode.MIRROR.label + " (лишнее убирается)", SyncMode.MIRROR.value
        )
        mode = job.mode if job else SyncMode.COPY
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(SyncMode(mode).value))
        self.mode_combo.currentIndexChanged.connect(self._update_warning)
        form.addRow("Режим", self.mode_combo)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(3, 86400)
        self.interval_spin.setSuffix(" с")
        self.interval_spin.setValue(job.interval_s if job else 15)
        form.addRow("Проверять каждые", self.interval_spin)

        self.enabled_check = QCheckBox("Задание включено")
        self.enabled_check.setChecked(job.enabled if job else True)
        form.addRow("", self.enabled_check)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        self._update_warning()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _browse_local(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Локальная папка",
            self.local_edit.text().strip() or str(Path.home()),
        )
        if chosen:
            self.local_edit.setText(chosen)

    def _update_warning(self) -> None:
        if SyncMode(self.mode_combo.currentData()) is SyncMode.MIRROR:
            self.warning.setText(
                "⚠ Зеркало: лишние файлы на Drive попадут в корзину, лишние локальные — "
                "в карантин FolderBridge. Для первого теста рекомендуется «Копирование»."
            )
            self.warning.setStyleSheet(f"color:{WARNING};")
        else:
            self.warning.setText("Копирование ничего не удаляет ни на ПК, ни на Google Drive.")
            self.warning.setStyleSheet(f"color:{SUCCESS};")

    def value(self) -> SyncJob:
        original_id = self.original.id if self.original else None
        return SyncJob(
            id=original_id,
            name=self.name_edit.text().strip(),
            local_path=self.local_edit.text().strip(),
            remote_path=normalize_remote_path(self.remote_edit.text()),
            direction=Direction(self.direction_combo.currentData()),
            mode=SyncMode(self.mode_combo.currentData()),
            interval_s=self.interval_spin.value(),
            enabled=self.enabled_check.isChecked(),
            last_run_at=self.original.last_run_at if self.original else None,
            last_success_at=self.original.last_success_at if self.original else None,
            last_error=self.original.last_error if self.original else None,
        )

    def _accept(self) -> None:
        try:
            job = self.value()
            validate_job(job, self.existing_jobs)
            if job.direction is Direction.UPLOAD and not Path(job.local_path).expanduser().is_dir():
                raise ValueError(
                    "Для направления ПК → Drive локальная папка уже должна существовать."
                )
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Проверьте задание", str(exc))
            return
        becoming_mirror = job.mode is SyncMode.MIRROR and (
            self.original is None or self.original.mode is not SyncMode.MIRROR
        )
        if becoming_mirror:
            answer = QMessageBox.question(
                self,
                "Включить зеркало?",
                "Режим «Зеркало» убирает элементы, которых нет в источнике.\n\n"
                "На Drive они отправляются в корзину, на ПК — в карантин. Продолжить?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()


class SettingsDialog(QDialog):
    source_update_requested = Signal()
    disconnect_requested = Signal()

    def __init__(self, parent: QWidget | None, store: StateStore) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        self.autostart_check = QCheckBox("Запускать FolderBridge вместе с Windows")
        self.autostart_check.setChecked(autostart.is_enabled())
        self.autostart_check.setEnabled(sys.platform == "win32")
        layout.addWidget(self.autostart_check)

        self.tray_check = QCheckBox("При закрытии сворачивать в системный трей")
        self.tray_check.setChecked(store.setting("minimize_to_tray", "1") == "1")
        layout.addWidget(self.tray_check)

        self.updates_check = QCheckBox("Автоматически проверять новые версии при запуске")
        self.updates_check.setChecked(store.setting("auto_updates", "1") == "1")
        layout.addWidget(self.updates_check)

        layout.addSpacing(10)
        disconnect = QPushButton("Отключить Google Drive на этом ПК")
        disconnect.clicked.connect(lambda _checked=False: self.disconnect_requested.emit())
        layout.addWidget(disconnect)

        if project_root() is not None:
            source_btn = QPushButton("Обновить clone (git pull --ff-only)")
            source_btn.setToolTip("Работает только при чистом рабочем дереве Git")
            source_btn.clicked.connect(lambda _checked=False: self.source_update_requested.emit())
            layout.addWidget(source_btn)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _save(self) -> None:
        try:
            autostart.set_enabled(self.autostart_check.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, "Автозапуск", str(exc))
            return
        self.store.set_setting("minimize_to_tray", "1" if self.tray_check.isChecked() else "0")
        self.store.set_setting("auto_updates", "1" if self.updates_check.isChecked() else "0")
        self.accept()


class VersionDialog(QDialog):
    install_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"FolderBridge {__version__} — версии")
        self.resize(760, 520)
        self.releases: list[dict] = []
        layout = QVBoxLayout(self)

        self.status = QLabel("Загрузка списка GitHub Releases…")
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Версия", "Тип", "Дата", "Название"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 2)

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText("Описание выбранного релиза")
        layout.addWidget(self.notes, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Закрыть")
        self.install_btn = QPushButton("Перейти на выбранную версию")
        self.install_btn.setObjectName("primary")
        self.install_btn.setEnabled(False)
        close.clicked.connect(self.reject)
        self.install_btn.clicked.connect(self._install)
        buttons.addWidget(close)
        buttons.addWidget(self.install_btn)
        layout.addLayout(buttons)

    def set_releases(self, releases: list[dict]) -> None:
        self.releases = sorted(
            releases,
            key=lambda release: updates.parse_version(str(release.get("tag_name") or "")),
            reverse=True,
        )
        self.table.setRowCount(len(self.releases))
        for row, release in enumerate(self.releases):
            tag = str(release.get("tag_name") or "—")
            kind = "Pre-release" if release.get("prerelease") else "Stable"
            date = str(release.get("published_at") or release.get("created_at") or "")[:10]
            title = str(release.get("name") or "")
            values = [tag, kind, date, title]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            if updates.parse_version(tag) == updates.parse_version(__version__):
                self.table.item(row, 0).setText(tag + " · текущая")
        self.status.setText(
            f"Найдено версий: {len(self.releases)} · текущая {__version__} · "
            f"режим {updates.runtime_mode()}"
        )
        if self.releases:
            self.table.selectRow(0)

    def show_error(self, message: str) -> None:
        self.status.setText("Не удалось загрузить версии: " + message)
        self.status.setStyleSheet(f"color:{ERROR};")

    def _selected(self) -> dict | None:
        row = self.table.currentRow()
        return self.releases[row] if 0 <= row < len(self.releases) else None

    def _selection_changed(self) -> None:
        release = self._selected()
        asset = updates.select_asset(release) if release else None
        self.install_btn.setEnabled(asset is not None)
        if release:
            selected = updates.parse_version(str(release.get("tag_name") or ""))
            current = updates.parse_version(__version__)
            if selected == current:
                self.install_btn.setText("Переустановить")
            elif selected < current:
                self.install_btn.setText("Откатиться на выбранную версию")
            else:
                self.install_btn.setText("Обновиться до выбранной версии")
            self.notes.setPlainText(str(release.get("body") or "Описание релиза отсутствует."))

    def _install(self) -> None:
        release = self._selected()
        if release:
            self.install_requested.emit(release)


class MainWindow(QMainWindow):
    ui_call = Signal(object)

    def __init__(
        self,
        store: StateStore,
        scheduler: SyncScheduler,
        *,
        start_background: bool = False,
    ) -> None:
        super().__init__()
        self.store = store
        self.scheduler = scheduler
        self.start_background = start_background
        self._exiting = False
        self._version_dialog: VersionDialog | None = None
        self._update_progress: QProgressDialog | None = None
        self._update_cancel: threading.Event | None = None
        self._oauth_busy = False
        self._drive_connected = False
        self.ui_call.connect(lambda callback: callback())

        self.setWindowTitle(f"FolderBridge {__version__} — PC ↔ Google Drive")
        self.resize(1180, 760)
        icon = QApplication.windowIcon()
        if icon.isNull():
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)
        self.setWindowIcon(icon)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(9)
        self.setCentralWidget(root)

        root_layout.addWidget(self._build_header())
        root_layout.addLayout(self._build_toolbar())
        root_layout.addWidget(self._build_table(), 3)
        root_layout.addWidget(self._build_log(), 2)
        self.statusBar().showMessage("Готово")

        self.scheduler.busy_changed.connect(self._sync_busy_changed)
        self.scheduler.batch_finished.connect(self._sync_finished)
        self.scheduler.paused = self.store.setting("autosync", "1") != "1"
        self._update_pause_button()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.scheduler.tick)
        self.timer.start()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(3000)
        self.refresh_timer.timeout.connect(self._refresh_all)
        self.refresh_timer.start()

        self._create_tray(icon)
        self._refresh_all()
        QTimer.singleShot(400, self._probe_drive)
        QTimer.singleShot(1200, self._show_previous_update_error)
        if self.store.setting("auto_updates", "1") == "1":
            QTimer.singleShot(8000, self._silent_update_check)
        if start_background and self.tray and self.tray.isVisible():
            QTimer.singleShot(0, self.hide)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        title_box = QVBoxLayout()
        title = QLabel("FolderBridge")
        title.setObjectName("title")
        subtitle = QLabel(f"v{__version__} · независимая синхронизация папок")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch(1)
        self.drive_label = QLabel("Google Drive: проверка…")
        self.drive_label.setObjectName("subtitle")
        layout.addWidget(self.drive_label)
        self.connect_btn = QPushButton("Подключить Drive")
        self.connect_btn.clicked.connect(self._connect_drive)
        layout.addWidget(self.connect_btn)
        versions = QPushButton("Версии")
        versions.clicked.connect(self._open_versions)
        layout.addWidget(versions)
        settings = QPushButton("Настройки")
        settings.clicked.connect(self._open_settings)
        layout.addWidget(settings)
        return header

    def _build_toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        add = QPushButton("+ Новое задание")
        add.setObjectName("primary")
        edit = QPushButton("Изменить")
        delete = QPushButton("Удалить")
        toggle = QPushButton("Вкл / выкл")
        self.sync_btn = QPushButton("Синхронизировать сейчас")
        self.pause_btn = QPushButton()
        add.clicked.connect(self._add_job)
        edit.clicked.connect(self._edit_job)
        delete.clicked.connect(self._delete_job)
        toggle.clicked.connect(self._toggle_job)
        self.sync_btn.clicked.connect(self._sync_now)
        self.pause_btn.clicked.connect(self._toggle_pause)
        for button in (add, edit, delete, toggle, self.sync_btn):
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(self.pause_btn)
        return layout

    def _build_table(self) -> QWidget:
        group = QGroupBox("Задания")
        layout = QVBoxLayout(group)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Вкл",
                "Название",
                "Направление",
                "Режим",
                "Локальная папка",
                "Drive",
                "Интервал",
                "Статус",
            ]
        )
        header = self.table.horizontalHeader()
        for column in (0, 2, 3, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._edit_job)
        layout.addWidget(self.table)
        hint = QLabel(
            "Для Autotuner используйте две разные пары: команды Drive→ПК и результаты ПК→Drive. "
            "Начинать лучше с режима «Копирование»."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return group

    def _build_log(self) -> QWidget:
        group = QGroupBox("Журнал")
        layout = QVBoxLayout(group)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(600)
        layout.addWidget(self.log)
        return group

    def _create_tray(self, icon: QIcon) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(f"FolderBridge {__version__}")
        menu = QMenu()
        show_action = QAction("Открыть FolderBridge", menu)
        sync_action = QAction("Синхронизировать сейчас", menu)
        quit_action = QAction("Выход", menu)
        show_action.triggered.connect(self._show_from_tray)
        sync_action.triggered.connect(self._sync_now)
        quit_action.triggered.connect(self._quit)
        menu.addAction(show_action)
        menu.addAction(sync_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._show_from_tray()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        tray.show()
        self.tray = tray

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _selected_job(self) -> SyncJob | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        task_id = item.data(Qt.ItemDataRole.UserRole)
        return self.store.get_job(int(task_id)) if task_id is not None else None

    def _refresh_jobs(self) -> None:
        selected = self._selected_job()
        selected_id = selected.id if selected else None
        jobs = self.store.list_jobs()
        self.table.setRowCount(len(jobs))
        row_to_select = -1
        for row, job in enumerate(jobs):
            values = [
                "●" if job.enabled else "○",
                job.name,
                job.direction.label,
                job.mode.label,
                job.local_path,
                f"FolderBridge/{job.remote_path}",
                f"{job.interval_s} с",
                _human_status(job),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, job.id)
                    item.setForeground(QColor(SUCCESS if job.enabled else TEXT_MUTED))
                if column == 7 and job.last_error:
                    item.setForeground(QColor(ERROR))
                self.table.setItem(row, column, item)
            if job.id == selected_id:
                row_to_select = row
        if row_to_select >= 0:
            self.table.selectRow(row_to_select)

    def _refresh_log(self) -> None:
        lines = []
        for event in self.store.recent_events(350):
            stamp = str(event["created_at"]).replace("T", " ")[:19]
            task = f" #{event['task_id']}" if event["task_id"] else ""
            lines.append(f"{stamp} [{event['level']}]{task} {event['message']}")
        current = "\n".join(lines)
        if self.log.toPlainText() != current:
            self.log.setPlainText(current)
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _refresh_all(self) -> None:
        self._refresh_jobs()
        self._refresh_log()

    def _add_job(self) -> None:
        dialog = TaskDialog(self, self.store.list_jobs())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                saved = self.store.save_job(dialog.value())
                self.store.add_event("INFO", f"Создано задание «{saved.name}»", saved.id)
                self._refresh_all()
            except Exception as exc:
                QMessageBox.critical(self, "Не удалось сохранить", str(exc))

    def _edit_job(self, *_args) -> None:
        job = self._selected_job()
        if job is None:
            QMessageBox.information(self, "Задания", "Сначала выберите строку.")
            return
        dialog = TaskDialog(self, self.store.list_jobs(), job)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                saved = self.store.save_job(dialog.value())
                self.store.add_event("INFO", f"Изменено задание «{saved.name}»", saved.id)
                self._refresh_all()
            except Exception as exc:
                QMessageBox.critical(self, "Не удалось сохранить", str(exc))

    def _delete_job(self) -> None:
        job = self._selected_job()
        if job is None or job.id is None:
            return
        if QMessageBox.question(
            self,
            "Удалить задание?",
            f"Удалить «{job.name}»?\n\nФайлы на ПК и Google Drive затронуты не будут.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_job(job.id)
        self.store.add_event("INFO", f"Удалено задание «{job.name}»")
        self._refresh_all()

    def _toggle_job(self) -> None:
        job = self._selected_job()
        if job is None or job.id is None:
            return
        self.store.set_enabled(job.id, not job.enabled)
        message = "Задание включено" if not job.enabled else "Задание выключено"
        self.store.add_event("INFO", message, job.id)
        self._refresh_all()

    def _sync_now(self, *_args) -> None:
        if self.scheduler.busy:
            self.statusBar().showMessage("Синхронизация уже выполняется")
            return
        job = self._selected_job()
        self.scheduler.run_now([job.id] if job and job.id is not None else None)

    def _toggle_pause(self, *_args) -> None:
        self.scheduler.paused = not self.scheduler.paused
        self.store.set_setting("autosync", "0" if self.scheduler.paused else "1")
        self._update_pause_button()

    def _update_pause_button(self) -> None:
        self.pause_btn.setText("▶ Запустить авто" if self.scheduler.paused else "⏸ Пауза авто")
        self.statusBar().showMessage(
            "Автосинхронизация на паузе" if self.scheduler.paused else "Автосинхронизация включена"
        )

    def _sync_busy_changed(self, busy: bool) -> None:
        self.sync_btn.setEnabled(not busy)
        if busy:
            self.statusBar().showMessage("Синхронизация…")

    def _sync_finished(self, outcomes: list[tuple[int | None, bool, str]]) -> None:
        self._refresh_all()
        failures = [message for _task, ok, message in outcomes if not ok]
        if failures:
            self.statusBar().showMessage("Ошибка синхронизации: " + failures[0], 10000)
        else:
            self.statusBar().showMessage("Синхронизация завершена", 5000)

    def _probe_drive(self) -> None:
        def worker() -> None:
            try:
                backend = GoogleDriveBackend.from_saved()
                label = backend.account_label()
                self.ui_call.emit(lambda: self._drive_ok(label))
            except Exception as exc:
                self.ui_call.emit(lambda e=exc: self._drive_missing(str(e)))

        threading.Thread(target=worker, daemon=True, name="FolderBridgeDriveProbe").start()

    def _drive_ok(self, account: str) -> None:
        self._drive_connected = True
        self.drive_label.setText(f"Google Drive: {account}")
        self.drive_label.setStyleSheet(f"color:{SUCCESS};")
        self.connect_btn.setText("Переподключить")
        self.connect_btn.setEnabled(True)

    def _drive_missing(self, message: str) -> None:
        self._drive_connected = False
        short = "не подключён" if "не подключ" in message.casefold() else "недоступен"
        self.drive_label.setText(f"Google Drive: {short}")
        self.drive_label.setStyleSheet(f"color:{WARNING};")
        self.connect_btn.setText("Подключить Drive")
        self.connect_btn.setEnabled(True)

    def _connect_drive(self) -> None:
        if self._oauth_busy:
            return
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "OAuth Desktop JSON",
            str(Path.home()),
            "OAuth JSON (*.json);;Все файлы (*.*)",
        )
        if not filename:
            return
        QMessageBox.information(
            self,
            "Google Drive",
            "Сейчас откроется браузер Google. Выберите свой аккаунт и разрешите доступ.\n\n"
            "Для автоматического чтения файлов, созданных другими клиентами, Google требует "
            "полный Drive scope. Сам FolderBridge программно работает только внутри папки "
            "Drive/FolderBridge.",
        )
        self._oauth_busy = True
        self.connect_btn.setEnabled(False)
        self.drive_label.setText("Google Drive: ожидание авторизации…")

        def worker() -> None:
            try:
                backend = GoogleDriveBackend.authorize(filename)
                label = backend.account_label()
            except Exception as exc:
                self.ui_call.emit(lambda e=exc: self._oauth_failed(str(e)))
                return
            self.ui_call.emit(lambda: self._oauth_finished(label))

        threading.Thread(target=worker, daemon=True, name="FolderBridgeOAuth").start()

    def _oauth_finished(self, account: str) -> None:
        self._oauth_busy = False
        self._drive_ok(account)
        self.store.add_event("INFO", f"Google Drive подключён: {account}")
        QMessageBox.information(
            self,
            "Google Drive подключён",
            "Авторизация сохранена в системном хранилище. Можно создавать задания.",
        )

    def _oauth_failed(self, message: str) -> None:
        self._oauth_busy = False
        self._drive_missing(message)
        QMessageBox.critical(self, "Не удалось подключить Google Drive", message)

    def _disconnect_drive(self) -> None:
        if QMessageBox.question(
            self,
            "Отключить Google Drive?",
            "OAuth-данные будут удалены с этого ПК. Файлы на Drive не изменятся.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            CredentialStore().clear()
        except Exception as exc:
            QMessageBox.warning(self, "Google Drive", str(exc))
            return
        self._drive_missing("Google Drive не подключён")
        self.store.add_event("INFO", "Google Drive отключён на этом ПК")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self, self.store)
        dialog.disconnect_requested.connect(self._disconnect_drive)
        dialog.source_update_requested.connect(self._source_pull)
        dialog.exec()

    def _source_pull(self) -> None:
        def worker() -> None:
            try:
                message = updates.source_pull()
            except Exception as exc:
                self.ui_call.emit(
                    lambda e=exc: QMessageBox.warning(self, "Git pull", str(e))
                )
                return
            self.ui_call.emit(
                lambda: QMessageBox.information(
                    self,
                    "Git pull",
                    message + "\n\nПерезапустите FolderBridge, чтобы применить новый код.",
                )
            )

        threading.Thread(target=worker, daemon=True, name="FolderBridgeGitPull").start()

    def _open_versions(self) -> None:
        if self._version_dialog and self._version_dialog.isVisible():
            self._version_dialog.raise_()
            return
        dialog = VersionDialog(self)
        dialog.install_requested.connect(self._install_release)
        dialog.finished.connect(lambda _result: setattr(self, "_version_dialog", None))
        self._version_dialog = dialog
        dialog.show()

        def worker() -> None:
            try:
                releases = updates.list_releases()
                self.ui_call.emit(lambda: dialog.set_releases(releases))
            except Exception as exc:
                self.ui_call.emit(lambda e=exc: dialog.show_error(str(e)))

        threading.Thread(target=worker, daemon=True, name="FolderBridgeVersions").start()

    def _install_release(self, release: dict) -> None:
        asset = updates.select_asset(release)
        if asset is None:
            QMessageBox.warning(self, "Версии", "У релиза нет подходящего Windows-пакета.")
            return
        tag = str(release.get("tag_name") or "")
        selected = updates.parse_version(tag)
        current = updates.parse_version(__version__)
        if selected == current:
            relation = "переустановить"
        elif selected < current:
            relation = "откатиться на"
        else:
            relation = "обновиться до"
        if QMessageBox.question(
            self,
            "Смена версии",
            f"{relation.capitalize()} {tag}?\n\n"
            f"Будет загружен {asset.get('name')}. Во время установки FolderBridge перезапустится.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._perform_update(release, asset)

    def _perform_update(self, release: dict, asset: dict) -> None:
        if self._update_progress is not None:
            return
        progress = QProgressDialog("Подготовка загрузки…", "Отмена", 0, 100, self)
        progress.setWindowTitle("Смена версии FolderBridge")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        cancel = threading.Event()
        progress.canceled.connect(cancel.set)
        self._update_progress = progress
        self._update_cancel = cancel
        progress.show()

        def report(downloaded: int, total: int, attempt: int, attempts: int) -> None:
            percent = int(downloaded * 100 / total) if total else 0
            text = f"Загрузка {asset.get('name')} — попытка {attempt}/{attempts}"
            self.ui_call.emit(lambda p=percent, t=text: self._set_update_progress(p, t))

        def worker() -> None:
            try:
                package = updates.prepare_update(release, asset, report, cancel)
                if cancel.is_set():
                    self.ui_call.emit(self._finish_update_progress)
                    return
                batch = updates.stage_update(package)
            except updates.DownloadCancelled:
                self.ui_call.emit(self._finish_update_progress)
                return
            except Exception as exc:
                self.ui_call.emit(lambda e=exc: self._update_failed(str(e)))
                return
            self.ui_call.emit(lambda: self._launch_update(batch))

        threading.Thread(target=worker, daemon=True, name="FolderBridgeUpdate").start()

    def _set_update_progress(self, percent: int, text: str) -> None:
        if self._update_progress:
            self._update_progress.setValue(max(0, min(100, percent)))
            self._update_progress.setLabelText(text)

    def _finish_update_progress(self) -> None:
        if self._update_progress:
            self._update_progress.close()
        self._update_progress = None
        self._update_cancel = None

    def _update_failed(self, message: str) -> None:
        self._finish_update_progress()
        QMessageBox.critical(
            self,
            "Ошибка обновления",
            "Не удалось подготовить обновление:\n" + message + "\n\nТекущая версия не изменена.",
        )

    def _launch_update(self, batch: Path) -> None:
        self._finish_update_progress()
        try:
            updates.launch_batch(batch)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка обновления", str(exc))
            return
        self._exiting = True
        QApplication.quit()

    def _silent_update_check(self) -> None:
        def worker() -> None:
            try:
                releases = updates.list_releases()
                release = updates.newest_eligible_release(releases)
            except Exception:
                return
            if release is None:
                return
            self.ui_call.emit(lambda: self._offer_update(release))

        threading.Thread(target=worker, daemon=True, name="FolderBridgeUpdateCheck").start()

    def _offer_update(self, release: dict) -> None:
        tag = str(release.get("tag_name") or "новая версия")
        if QMessageBox.question(
            self,
            "Доступно обновление",
            f"Доступна версия {tag}; сейчас установлена {__version__}.\n\nОбновиться?",
        ) == QMessageBox.StandardButton.Yes:
            asset = updates.select_asset(release)
            if asset is None:
                QMessageBox.warning(self, "Обновление", "У релиза нет подходящего пакета.")
            else:
                self._perform_update(release, asset)

    def _show_previous_update_error(self) -> None:
        message = updates.consume_update_error()
        if message:
            QMessageBox.warning(
                self,
                "Предыдущее обновление не завершено",
                message + "\n\nFolderBridge восстановил/оставил предыдущую рабочую версию.",
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            not self._exiting
            and self.store.setting("minimize_to_tray", "1") == "1"
            and self.tray is not None
            and self.tray.isVisible()
        ):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "FolderBridge",
                "Программа продолжает синхронизацию в фоне.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            return
        event.accept()

    def _quit(self) -> None:
        self._exiting = True
        if self.tray:
            self.tray.hide()
        QApplication.quit()
