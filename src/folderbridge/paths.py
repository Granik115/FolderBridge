"""Application paths shared by source and frozen builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "FolderBridge"


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or Path.home())
    else:
        base = Path(os.getenv("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    override = os.getenv("FOLDERBRIDGE_DB")
    return Path(override) if override else app_data_dir() / "folderbridge.sqlite3"


def quarantine_dir() -> Path:
    path = app_data_dir() / "quarantine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_root() -> Path | None:
    """Return a source checkout root when running from an editable clone."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").is_file() and (parent / ".git").exists():
            return parent
    return None

