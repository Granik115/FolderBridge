from __future__ import annotations

import unittest

from folderbridge.google_drive import FOLDER_MIME, GoogleDriveBackend


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeFiles:
    def __init__(self, responses):
        self.responses = list(responses)

    def list(self, **_kwargs):
        return FakeRequest(self.responses.pop(0))


class FakeService:
    def __init__(self, responses):
        self._files = FakeFiles(responses)

    def files(self):
        return self._files


def folder(file_id: str, name: str) -> dict:
    return {"id": file_id, "name": name, "mimeType": FOLDER_MIME}


class GoogleDriveBackendTests(unittest.TestCase):
    def test_duplicate_root_folders_are_rejected(self):
        service = FakeService(
            [{"files": [folder("one", "FolderBridge"), folder("two", "FolderBridge")]}]
        )
        with self.assertRaisesRegex(RuntimeError, "несколько папок"):
            GoogleDriveBackend(service).ensure_root()

    def test_unsafe_remote_filename_is_rejected_before_local_use(self):
        service = FakeService(
            [
                {"files": [folder("root-id", "FolderBridge")]},
                {
                    "files": [
                        {
                            "id": "evil",
                            "name": "../outside.txt",
                            "mimeType": "text/plain",
                            "size": "1",
                        }
                    ]
                },
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "небезопасное"):
            GoogleDriveBackend(service).list_tree("")


if __name__ == "__main__":
    unittest.main()

