from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallerPackagingTests(unittest.TestCase):
    def test_installer_shortcuts_use_bundled_folderbridge_icon(self):
        script = (ROOT / "installer" / "FolderBridge.iss").read_text(encoding="utf-8")

        self.assertIn(
            'Source: "..\\src\\folderbridge\\resources\\folderbridge.ico"; DestDir: "{app}"',
            script,
        )
        self.assertIn("UninstallDisplayIcon={app}\\folderbridge.ico", script)
        icons_section = script.split("[Icons]", 1)[1].split("[Run]", 1)[0]
        shortcut_lines = [line for line in icons_section.splitlines() if line.startswith("Name:")]
        self.assertEqual(len(shortcut_lines), 2)
        for line in shortcut_lines:
            self.assertIn('IconFilename: "{app}\\folderbridge.ico"', line)


if __name__ == "__main__":
    unittest.main()
