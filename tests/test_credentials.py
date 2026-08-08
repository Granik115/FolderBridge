from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from folderbridge.credentials import load_desktop_client_file
from folderbridge.google_drive import SCOPES


class CredentialTests(unittest.TestCase):
    def test_scope_can_see_files_created_by_other_clients(self):
        self.assertEqual(SCOPES, ["https://www.googleapis.com/auth/drive"])

    def test_accepts_desktop_client(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "oauth.json"
            path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "id",
                            "client_secret": "secret",
                            "auth_uri": "https://accounts.example/auth",
                            "token_uri": "https://accounts.example/token",
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertIn("installed", load_desktop_client_file(path))

    def test_rejects_web_client(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "oauth.json"
            path.write_text(json.dumps({"web": {"client_id": "id"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Desktop app"):
                load_desktop_client_file(path)


if __name__ == "__main__":
    unittest.main()
