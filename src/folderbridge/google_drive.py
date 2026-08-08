"""Narrow Google Drive adapter used by the synchronization engine."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

from folderbridge.credentials import CredentialStore, load_desktop_client_file
from folderbridge.models import RemoteEntry, normalize_remote_path

# Full Drive access is required for a true Drive -> PC inbox: files placed in the
# FolderBridge tree by ChatGPT or another client were not created/opened by this OAuth
# app and therefore are not guaranteed to be visible through drive.file. Every mutating
# method below is still constrained to the named FolderBridge root.
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_MIME_PREFIX = "application/vnd.google-apps."
ROOT_FOLDER_NAME = "FolderBridge"
FILE_FIELDS = "id,name,mimeType,size,modifiedTime,md5Checksum,sha256Checksum,trashed,parents"


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _as_remote_entry(payload: dict[str, Any], relative_path: str) -> RemoteEntry:
    size = payload.get("size")
    return RemoteEntry(
        id=str(payload["id"]),
        relative_path=relative_path,
        name=str(payload.get("name") or PurePosixPath(relative_path).name),
        is_folder=payload.get("mimeType") == FOLDER_MIME,
        size=int(size) if size is not None else None,
        modified_time=payload.get("modifiedTime"),
        md5=payload.get("md5Checksum"),
        sha256=payload.get("sha256Checksum"),
        mime_type=payload.get("mimeType"),
    )


class GoogleDriveBackend:
    """Google Drive v3 operations logically constrained to the FolderBridge root."""

    def __init__(
        self,
        service: Any,
        credential_store: CredentialStore | None = None,
        creds: Any = None,
    ):
        self.service = service
        self.credential_store = credential_store
        self.creds = creds
        self._root_id: str | None = None

    @classmethod
    def authorize(
        cls,
        client_json_path: str | Path,
        credential_store: CredentialStore | None = None,
    ) -> GoogleDriveBackend:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        store = credential_store or CredentialStore()
        client_config = load_desktop_client_file(client_json_path)
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        token_payload = json.loads(creds.to_json())
        store.save(client_config, token_payload)
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        backend = cls(service, store, creds)
        backend.ensure_root()
        return backend

    @classmethod
    def from_saved(
        cls,
        credential_store: CredentialStore | None = None,
    ) -> GoogleDriveBackend:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        store = credential_store or CredentialStore()
        saved = store.load()
        if saved is None:
            raise RuntimeError("Google Drive не подключён.")
        _client_config, token_payload = saved
        creds = Credentials.from_authorized_user_info(token_payload, SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                store.update_token(json.loads(creds.to_json()))
            else:
                raise RuntimeError("OAuth-токен недействителен. Подключите Google Drive заново.")
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return cls(service, store, creds)

    def account_label(self) -> str:
        data = self.service.about().get(fields="user(displayName,emailAddress)").execute()
        user = data.get("user") or {}
        return str(user.get("emailAddress") or user.get("displayName") or "Google Drive")

    def _list_children(self, parent_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page_token = None
        while True:
            response = (
                self.service.files()
                .list(
                    q=f"'{_escape_query(parent_id)}' in parents and trashed = false",
                    spaces="drive",
                    fields=f"nextPageToken,files({FILE_FIELDS})",
                    pageSize=1000,
                    orderBy="name,modifiedTime desc",
                    pageToken=page_token,
                )
                .execute()
            )
            result.extend(response.get("files") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                return result

    def _find_folder(self, parent_id: str, name: str) -> dict[str, Any] | None:
        escaped_parent = _escape_query(parent_id)
        escaped_name = _escape_query(name)
        response = (
            self.service.files()
            .list(
                q=(
                    f"'{escaped_parent}' in parents and name = '{escaped_name}' "
                    f"and mimeType = '{FOLDER_MIME}' and trashed = false"
                ),
                spaces="drive",
                fields=f"files({FILE_FIELDS})",
                pageSize=10,
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files = response.get("files") or []
        if len(files) > 1:
            raise RuntimeError(
                f"На Drive есть несколько папок с именем {name!r}. Переименуйте лишние."
            )
        return files[0] if files else None

    def ensure_root(self) -> str:
        if self._root_id:
            return self._root_id
        found = self._find_folder("root", ROOT_FOLDER_NAME)
        if found:
            self._root_id = str(found["id"])
            return self._root_id
        created = (
            self.service.files()
            .create(
                body={"name": ROOT_FOLDER_NAME, "mimeType": FOLDER_MIME, "parents": ["root"]},
                fields="id",
            )
            .execute()
        )
        self._root_id = str(created["id"])
        return self._root_id

    def ensure_folder_path(self, remote_path: str) -> str:
        normalized = normalize_remote_path(remote_path)
        parent_id = self.ensure_root()
        if not normalized:
            return parent_id
        for part in normalized.split("/"):
            found = self._find_folder(parent_id, part)
            if found:
                parent_id = str(found["id"])
                continue
            created = (
                self.service.files()
                .create(
                    body={"name": part, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                    fields="id",
                )
                .execute()
            )
            parent_id = str(created["id"])
        return parent_id

    def list_tree(self, remote_path: str) -> list[RemoteEntry]:
        start_id = self.ensure_folder_path(remote_path)
        entries: list[RemoteEntry] = []
        queue: list[tuple[str, str]] = [(start_id, "")]
        while queue:
            parent_id, relative_parent = queue.pop(0)
            seen_names: set[str] = set()
            for item in self._list_children(parent_id):
                name = str(item.get("name") or "")
                if name in {".", ".."} or any(char in name for char in ("/", "\\", "\x00")):
                    raise RuntimeError(
                        f"На Drive найдено имя, небезопасное для локальной папки: {name!r}"
                    )
                if not name:
                    continue
                if name in seen_names:
                    where = relative_parent or "/"
                    raise RuntimeError(
                        f"На Drive есть два объекта с именем {name!r} в папке {where!r}. "
                        "Переименуйте один из них."
                    )
                seen_names.add(name)
                relative = f"{relative_parent}/{name}".lstrip("/")
                entry = _as_remote_entry(item, relative)
                entries.append(entry)
                if entry.is_folder:
                    queue.append((entry.id, relative))
        return entries

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        existing_id: str | None = None,
    ) -> RemoteEntry:
        from googleapiclient.http import MediaFileUpload

        normalized = normalize_remote_path(remote_path)
        if not normalized:
            raise ValueError("Для файла требуется непустой удалённый путь.")
        target = PurePosixPath(normalized)
        parent_path = str(target.parent)
        if parent_path == ".":
            parent_path = ""
        parent_id = self.ensure_folder_path(parent_path)
        mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        media = MediaFileUpload(
            str(local_path),
            mimetype=mime_type,
            chunksize=4 * 1024 * 1024,
            resumable=True,
        )
        fields = FILE_FIELDS
        if existing_id:
            request = self.service.files().update(
                fileId=existing_id,
                body={"name": target.name},
                media_body=media,
                fields=fields,
            )
        else:
            request = self.service.files().create(
                body={"name": target.name, "parents": [parent_id]},
                media_body=media,
                fields=fields,
            )
        response = None
        while response is None:
            _status, response = request.next_chunk(num_retries=3)
        return _as_remote_entry(response, normalized)

    def download_file(self, entry: RemoteEntry, destination: Path) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        if entry.is_folder or (entry.mime_type or "").startswith(GOOGLE_MIME_PREFIX):
            raise ValueError(
                f"Файл Google Workspace нельзя скачать как blob: {entry.relative_path}"
            )
        request = self.service.files().get_media(fileId=entry.id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request, chunksize=4 * 1024 * 1024)
            done = False
            while not done:
                _status, done = downloader.next_chunk(num_retries=3)

    def trash(self, file_id: str) -> None:
        self.service.files().update(fileId=file_id, body={"trashed": True}, fields="id").execute()
