"""GitHub release discovery, verified download and self-update helpers."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from packaging.version import InvalidVersion, Version

from folderbridge import __version__
from folderbridge.paths import project_root

GITHUB_REPO = "Granik115/FolderBridge"
APP_EXE = "FolderBridge.exe"
USER_AGENT = f"FolderBridge-Updater/{__version__}"


class DownloadCancelled(Exception):
    pass


def parse_version(tag: str) -> Version:
    try:
        return Version(tag.lstrip("vV"))
    except InvalidVersion:
        return Version("0")


def github_request(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def list_releases() -> list[dict]:
    payload = json.loads(
        github_request(f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=100").decode(
            "utf-8"
        )
    )
    if not isinstance(payload, list):
        raise RuntimeError("GitHub вернул неожиданный список релизов.")
    return [release for release in payload if not release.get("draft")]


def newest_eligible_release(releases: list[dict], current: str = __version__) -> dict | None:
    current_version = parse_version(current)
    allow_prerelease = current_version.is_prerelease
    candidates = [
        release
        for release in releases
        if allow_prerelease or not release.get("prerelease")
    ]
    candidates.sort(key=lambda item: parse_version(item.get("tag_name", "")), reverse=True)
    for release in candidates:
        if parse_version(release.get("tag_name", "")) > current_version:
            return release
    return None


def find_setup_asset(release: dict) -> dict | None:
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "").lower()
        if name.startswith("folderbridge-") and name.endswith("-setup.exe"):
            return asset
    return None


def find_portable_asset(release: dict) -> dict | None:
    preferred: list[dict] = []
    fallback: list[dict] = []
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "").lower()
        if not name.endswith(".zip"):
            continue
        if name.startswith("folderbridge-") and "windows-x64" in name:
            preferred.append(asset)
        elif "folderbridge" in name and "portable" in name:
            fallback.append(asset)
    return (preferred or fallback or [None])[0]


def runtime_mode() -> str:
    if not getattr(sys, "frozen", False):
        return "source"
    app_dir = Path(sys.executable).resolve().parent
    if any(app_dir.glob("unins*.exe")):
        return "installer"
    return "portable"


def select_asset(release: dict, mode: str | None = None) -> dict | None:
    mode = mode or runtime_mode()
    if mode == "source":
        return find_setup_asset(release)
    if mode == "installer":
        return find_setup_asset(release) or find_portable_asset(release)
    return find_portable_asset(release) or find_setup_asset(release)


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _verify_asset_digest(asset: dict, path: str | Path) -> None:
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        return
    expected = digest.split(":", 1)[1].casefold()
    if _sha256(path) != expected:
        Path(path).unlink(missing_ok=True)
        raise ValueError("SHA-256 скачанного файла не совпал с GitHub Release.")


def verify_release_checksum(release: dict, asset: dict, path: str | Path) -> None:
    checksum_asset = next(
        (
            item
            for item in release.get("assets") or []
            if str(item.get("name") or "").casefold() == "sha256sums.txt"
        ),
        None,
    )
    if checksum_asset is None:
        _verify_asset_digest(asset, path)
        return
    raw = github_request(str(checksum_asset["browser_download_url"]), timeout=30).decode(
        "ascii", errors="replace"
    )
    target_name = str(asset.get("name") or "")
    expected = None
    for line in raw.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == target_name:
            expected = parts[0].casefold()
            break
    if expected is None:
        raise ValueError(f"Для {target_name} нет записи в SHA256SUMS.txt.")
    if _sha256(path) != expected:
        Path(path).unlink(missing_ok=True)
        raise ValueError("SHA-256 скачанного файла не совпал с SHA256SUMS.txt.")


def download_release_asset(
    asset: dict,
    destination: str | Path,
    *,
    attempts: int = 4,
    progress_callback: Callable[[int, int, int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Download a release asset with retry and HTTP Range resume support."""
    url = asset.get("browser_download_url")
    if not url:
        raise ValueError("У файла релиза отсутствует ссылка для скачивания.")
    destination = Path(destination)
    partial = destination.with_name(destination.name + ".part")
    destination.unlink(missing_ok=True)
    expected_size = int(asset.get("size") or 0)
    if expected_size and partial.exists() and partial.stat().st_size > expected_size:
        partial.unlink()
    transient = (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError)
    last_error: BaseException | None = None

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Загрузка отменена.")

    for attempt in range(1, max(1, attempts) + 1):
        check_cancel()
        downloaded = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if downloaded:
            headers["Range"] = f"bytes={downloaded}-"
        if progress_callback:
            progress_callback(downloaded, expected_size, attempt, attempts)
        try:
            request = urllib.request.Request(str(url), headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                status = getattr(response, "status", None) or response.getcode()
                append = downloaded > 0 and status == 206
                if not append:
                    downloaded = 0
                response_size = int(response.headers.get("Content-Length") or 0)
                total = expected_size or response_size + (downloaded if append else 0)
                with partial.open("ab" if append else "wb") as output:
                    while True:
                        check_cancel()
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total, attempt, attempts)
            if expected_size and partial.stat().st_size != expected_size:
                raise OSError(
                    f"получен неполный файл: {partial.stat().st_size} из {expected_size} байт"
                )
            os.replace(partial, destination)
            _verify_asset_digest(asset, destination)
            return
        except DownloadCancelled:
            partial.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        except transient as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = 2 ** (attempt - 1)
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    partial.unlink(missing_ok=True)
                    raise DownloadCancelled("Загрузка отменена.") from exc
            else:
                time.sleep(delay)
    partial.unlink(missing_ok=True)
    destination.unlink(missing_ok=True)
    raise ConnectionError(
        f"Загрузка прервалась после {attempts} попыток: {last_error}"
    ) from last_error


def safe_extract_zip(zip_path: str | Path, destination: str | Path) -> None:
    destination = Path(destination).resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            target = (destination / member).resolve()
            escapes = destination != target and destination not in target.parents
            if member.is_absolute() or escapes:
                raise ValueError(f"Недопустимый путь в архиве: {info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"Символические ссылки запрещены: {info.filename}")
        archive.extractall(destination)


def find_extracted_app(extract_dir: str | Path) -> Path:
    root = Path(extract_dir)
    for executable in root.rglob(APP_EXE):
        return executable.parent
    raise FileNotFoundError(f"В архиве не найден {APP_EXE}.")


def _quoted(value: str | Path) -> str:
    return str(value).replace('"', '""')


def build_installer_batch(installer_path: str | Path, app_executable: str | Path) -> str:
    return f'''@echo off
chcp 65001 >nul
setlocal
set "INSTALLER={_quoted(installer_path)}"
set "APP_EXE={_quoted(app_executable)}"
set "ERROR_FILE=%TEMP%\\FolderBridge_update_error.txt"
del /f /q "%ERROR_FILE%" >nul 2>&1
:wait_for_app
tasklist /FI "IMAGENAME eq {APP_EXE}" 2>nul | find /I "{APP_EXE}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_for_app
)
"%INSTALLER%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /SP-
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo Installer exit code: %RESULT% > "%ERROR_FILE%"
)
if exist "%APP_EXE%" start "" "%APP_EXE%"
del /f /q "%INSTALLER%" >nul 2>&1
del "%~f0" >nul 2>&1
'''


def build_portable_batch(
    source_dir: str | Path,
    destination_dir: str | Path,
    extract_dir: str | Path,
    archive_path: str | Path,
    backup_dir: str | Path,
) -> str:
    destination = Path(destination_dir)
    executable = destination / APP_EXE
    return f'''@echo off
chcp 65001 >nul
setlocal
set "SRC={_quoted(source_dir)}"
set "DEST={_quoted(destination)}"
set "BACKUP={_quoted(backup_dir)}"
set "EXTRACT={_quoted(extract_dir)}"
set "ARCHIVE={_quoted(archive_path)}"
set "APP_EXE={_quoted(executable)}"
set "ERROR_FILE=%TEMP%\\FolderBridge_update_error.txt"
del /f /q "%ERROR_FILE%" >nul 2>&1
:wait_for_app
tasklist /FI "IMAGENAME eq {APP_EXE}" 2>nul | find /I "{APP_EXE}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_for_app
)
robocopy "%DEST%" "%BACKUP%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS >nul
robocopy "%SRC%" "%DEST%" /MIR /R:8 /W:2 /NFL /NDL /NJH /NJS
if errorlevel 8 (
    echo Portable update copy failed: %errorlevel% > "%ERROR_FILE%"
    robocopy "%BACKUP%" "%DEST%" /MIR /R:4 /W:1 /NFL /NDL /NJH /NJS >nul
)
if exist "%APP_EXE%" start "" "%APP_EXE%"
rd /s /q "%EXTRACT%" >nul 2>&1
rd /s /q "%BACKUP%" >nul 2>&1
del /f /q "%ARCHIVE%" >nul 2>&1
del "%~f0" >nul 2>&1
'''


def launch_batch(batch_path: str | Path) -> None:
    creation_flags = 0x08000000 if sys.platform == "win32" else 0
    subprocess.Popen(
        ["cmd.exe", "/c", str(batch_path)],
        creationflags=creation_flags,
        close_fds=True,
    )


def consume_update_error() -> str:
    path = Path(tempfile.gettempdir()) / "FolderBridge_update_error.txt"
    try:
        message = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    path.unlink(missing_ok=True)
    return message


def source_pull() -> str:
    root = project_root()
    if root is None:
        raise RuntimeError("Git checkout не найден.")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    if status.stdout.strip():
        raise RuntimeError("Есть незакоммиченные изменения. Сначала сохраните их в Git.")
    result = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return (result.stdout or result.stderr).strip() or "Git checkout уже актуален."


def prepare_update(
    release: dict,
    asset: dict,
    progress_callback: Callable[[int, int, int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    suffix = ".exe" if str(asset.get("name") or "").lower().endswith(".exe") else ".zip"
    raw_tag = str(release.get("tag_name") or "update")
    safe_tag = "".join(ch for ch in raw_tag if ch.isalnum() or ch in ".-_")
    package = Path(tempfile.gettempdir()) / f"FolderBridge-{safe_tag}{suffix}"
    download_release_asset(
        asset,
        package,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )
    verify_release_checksum(release, asset, package)
    return package


def stage_update(package: Path) -> Path:
    """Create a launcher batch. Caller should launch it, then quit the Qt app."""
    mode = runtime_mode()
    if package.suffix.casefold() == ".exe":
        current_exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else (
            Path(os.getenv("LOCALAPPDATA") or Path.home()) / "Programs" / "FolderBridge" / APP_EXE
        )
        batch = Path(tempfile.gettempdir()) / "FolderBridge_installer_update.bat"
        batch.write_text(build_installer_batch(package, current_exe), encoding="utf-8")
        return batch
    if mode == "source":
        raise RuntimeError(
            "Portable-пакет нельзя накладывать поверх Git checkout. Используйте setup.exe."
        )
    extract_dir = Path(tempfile.mkdtemp(prefix="folderbridge_update_"))
    safe_extract_zip(package, extract_dir)
    source_dir = find_extracted_app(extract_dir)
    destination = Path(sys.executable).resolve().parent
    backup = Path(tempfile.mkdtemp(prefix="folderbridge_backup_"))
    batch = Path(tempfile.gettempdir()) / "FolderBridge_portable_update.bat"
    batch.write_text(
        build_portable_batch(source_dir, destination, extract_dir, package, backup),
        encoding="utf-8",
    )
    return batch
