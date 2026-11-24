# -*- mode: python ; coding: utf-8 -*-

import sys as _sys
_sys.path.insert(0, SPECPATH)
# BUNDLED_VERSION: _overlay_bootstrap.py leaves out an overlay older than this bundle.
from bundle_version import bundle_version_data
_BUNDLE_VERSION = [bundle_version_data(workpath)]

a = Analysis(
    ['makalu_entry.py'],
    pathex=['.'],
    binaries=[],
    datas=_BUNDLE_VERSION,
    hiddenimports=['hid', 'devices.makalu67.controller'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['_overlay_bootstrap.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
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
    name='makalu-controller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
