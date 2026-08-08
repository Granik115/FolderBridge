from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path.cwd().resolve()

hidden = (
    collect_submodules("googleapiclient")
    + collect_submodules("google_auth_oauthlib")
    + collect_submodules("keyring.backends")
)

a = Analysis(
    [str(root / "src" / "folderbridge" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "README.md"), ".")],
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
)
coll = COLLECT(a.binaries, a.datas, exe, name="FolderBridge")

