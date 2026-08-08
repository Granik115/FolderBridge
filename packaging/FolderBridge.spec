from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path.cwd().resolve()
icon_file = root / "src" / "folderbridge" / "resources" / "folderbridge.ico"

hidden = (
    collect_submodules("googleapiclient")
    + collect_submodules("google_auth_oauthlib")
    + collect_submodules("keyring.backends")
)

a = Analysis(
    [str(root / "src" / "folderbridge" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "README.md"), "."),
        (str(icon_file), "folderbridge/resources"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FolderBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_file),
)
coll = COLLECT(a.binaries, a.datas, exe, name="FolderBridge")
