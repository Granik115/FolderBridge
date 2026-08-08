"""Current-user Windows startup integration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "FolderBridge"


def _command() -> str:
    if getattr(sys, "frozen", False):
        parts = [str(Path(sys.executable).resolve()), "--background"]
    else:
        python = Path(sys.executable).resolve()
        pythonw = python.with_name("pythonw.exe")
        launcher = pythonw if sys.platform == "win32" and pythonw.exists() else python
        parts = [str(launcher), "-m", "folderbridge", "--background"]
    return subprocess.list2cmdline(parts)


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
        return str(value) == _command()
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if sys.platform != "win32":
        if enabled:
            raise RuntimeError("Автозапуск сейчас поддерживается только в Windows.")
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass

