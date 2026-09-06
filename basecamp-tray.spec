# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['tray_entry.py'],
    pathex=['.'],
    binaries=[],
    datas=[('lang', 'lang'), ('resources', 'resources')],
    hiddenimports=['pystray', 'PIL', 'PIL._tkinter_finder', 'tray_helper'],
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
    name='basecamp-tray',
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
