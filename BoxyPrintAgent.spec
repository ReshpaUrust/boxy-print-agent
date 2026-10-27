# Hand-maintained PyInstaller specification; build artifacts remain ignored.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('PIL') + [
    'win32con',
    'win32crypt',
    'win32print',
    'win32ui',
]

a = Analysis(
    ['boxy_print_agent_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BoxyPrintAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
