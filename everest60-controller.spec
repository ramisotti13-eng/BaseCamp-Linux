# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the everest60-controller standalone binary."""

a = Analysis(
    ['everest60_entry.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['hid', 'devices.everest60.controller'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['_overlay_bootstrap.py'],
    excludes=[],
    noarchive=False,
)

# Remove bloat: system icon themes, locales and themes dragged in from the
# build host. On a native desktop build the GTK hook pulls in the whole
# cursor/icon theme set — 380 MB of it in one measured case, which is why the
# AppImages are built in the container / clean venv. Same filter as in
# BaseCamp-Linux.spec.
a.datas = [
    (dst, src, kind)
    for dst, src, kind in a.datas
    if not dst.startswith('share/icons/')
    and not dst.startswith('share/locale/')
    and not dst.startswith('share/themes/')
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='everest60-controller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
