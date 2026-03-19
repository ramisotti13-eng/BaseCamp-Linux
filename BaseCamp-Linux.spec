# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[('lang', 'lang'), ('logo.png', '.'), ('resources', 'resources'), ('default_presets.json', '.')],
    hiddenimports=['PIL', 'PIL._tkinter_finder', 'PIL._imagingtk', 'psutil', 'pystray', 'obsws_python'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Remove bloat: system icon themes, locales, themes bundled from Fedora
import os
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
    [],
    exclude_binaries=True,
    name='BaseCamp-Linux',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='BaseCamp-Linux',
)
