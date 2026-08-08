"""OAuth credential persistence using the operating system keyring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SERVICE_NAME = "FolderBridge.GoogleDrive"
TOKEN_KEY = "oauth_token"
CLIENT_KEY = "oauth_client"


def load_desktop_client_file(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не удалось прочитать OAuth JSON: {exc}") from exc
    installed = payload.get("installed")
    if not isinstance(installed, dict):
        raise ValueError("Нужен OAuth Client JSON типа Desktop app (секция 'installed').")
    required = {"client_id", "client_secret", "auth_uri", "token_uri"}
    if not required.issubset(installed):
        raise ValueError("OAuth JSON не содержит обязательные поля Desktop app.")
    return payload


class CredentialStore:
    def _keyring(self):
        try:
            import keyring

            if __import__("sys").platform == "win32":
                try:
                    from keyring.backends.Windows import WinVaultKeyring

                    keyring.set_keyring(WinVaultKeyring())
                except Exception:
                    pass
            return keyring
        except Exception as exc:
            raise RuntimeError(
                "Системное хранилище учётных данных недоступно. Установите пакет keyring."
            ) from exc

    def save(self, client_config: dict[str, Any], token: dict[str, Any]) -> None:
        keyring = self._keyring()
        try:
            keyring.set_password(SERVICE_NAME, CLIENT_KEY, json.dumps(client_config))
            keyring.set_password(SERVICE_NAME, TOKEN_KEY, json.dumps(token))
        except Exception as exc:
            raise RuntimeError(f"Не удалось сохранить OAuth в системном хранилище: {exc}") from exc

    def load(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        keyring = self._keyring()
        try:
            client_raw = keyring.get_password(SERVICE_NAME, CLIENT_KEY)
            token_raw = keyring.get_password(SERVICE_NAME, TOKEN_KEY)
        except Exception as exc:
            raise RuntimeError(
                f"Не удалось прочитать OAuth из системного хранилища: {exc}"
            ) from exc
        if not client_raw or not token_raw:
            return None
        try:
            return json.loads(client_raw), json.loads(token_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Сохранённые OAuth-данные повреждены.") from exc

    def update_token(self, token: dict[str, Any]) -> None:
        keyring = self._keyring()
        try:
            keyring.set_password(SERVICE_NAME, TOKEN_KEY, json.dumps(token))
        except Exception as exc:
            raise RuntimeError(f"Не удалось обновить OAuth-токен: {exc}") from exc

    def clear(self) -> None:
        keyring = self._keyring()
        for key in (CLIENT_KEY, TOKEN_KEY):
            try:
                keyring.delete_password(SERVICE_NAME, key)
            except Exception:
                pass
