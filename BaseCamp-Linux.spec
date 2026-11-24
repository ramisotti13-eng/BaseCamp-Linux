# -*- mode: python ; coding: utf-8 -*-
import importlib.util

from PyInstaller.utils.hooks import collect_submodules

# core.devices.install_all() imports the service modules by name at run time,
# which PyInstaller's analysis cannot see; take the whole package.
_CORE_MODULES = collect_submodules('core')

# The web interface (the default since 4.0). gui.run() reaches ui_web through
# imports inside functions; take the whole package all the same. Its page,
# ui_web/static, is data and goes in as it is in git (no build step).
_WEB_MODULES = collect_submodules('ui_web')

# Its window: pywebview with the Qt backend, Qt WebEngine through PySide6
# (Dockerfile.debian pins the versions). pywebview picks its backend by
# importing webview.platforms.<name> inside a function, and qtpy finds its
# binding the same way; both are named so the analysis takes them. Only Qt
# is carried: WebKit2GTK finds its helper processes and libraries under
# paths fixed when it was compiled, which a bundle cannot provide, while Qt
# WebEngine is relocatable and PyInstaller has hooks for it.
_WEB_HIDDEN = ['webview', 'webview.platforms.qt', 'qtpy',
               'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
               'PySide6.QtNetwork', 'PySide6.QtWebChannel',
               'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets']
# Backends and bindings this bundle does not carry. Excluded so a build
# machine that happens to have them installed does not drag them in (two Qt
# bindings in one bundle is an error in PyInstaller).
_WEB_EXCLUDES = ['PyQt5', 'PyQt6', 'PySide2', 'gi', 'PySide6.QtDataVisualization',
                 'webview.platforms.gtk', 'webview.platforms.cef',
                 'webview.platforms.cocoa', 'webview.platforms.winforms',
                 'webview.platforms.edgechromium', 'webview.platforms.mshtml',
                 'webview.platforms.android']

# Without these the AppImage would only ever start the classic interface (the
# fallback in gui.run()), which a release must not ship by accident.
_missing = [m for m in ('webview', 'qtpy', 'PySide6')
            if importlib.util.find_spec(m) is None]
if _missing:
    raise SystemExit('BaseCamp-Linux.spec: %s not installed; the web interface needs '
                     'them (see Dockerfile.debian for the pinned versions)'
                     % ', '.join(_missing))

import sys as _sys
_sys.path.insert(0, SPECPATH)
# BUNDLED_VERSION: _overlay_bootstrap.py leaves out an overlay older than this bundle.
from bundle_version import bundle_version_data
_BUNDLE_VERSION = [bundle_version_data(workpath)]

a = Analysis(
    ['appentry.py'],
    pathex=[],
    binaries=[],
    datas=_BUNDLE_VERSION + [('lang', 'lang'), ('resources', 'resources'), ('default_presets.json', '.'), ('default_makalu_presets.json', '.'), ('default_presets_60.json', '.'), ('plugins', 'plugins'),
           ('ui_web/static', 'ui_web/static')],
    hiddenimports=['PIL', 'PIL._tkinter_finder', 'PIL._imagingtk', 'psutil', 'pystray', 'obsws_python',
                   'devices.macros', 'devices.macros.panel', 'shared.macros',
                   'devices.plugins', 'devices.plugins.panel', 'shared.plugins', 'shared.plugin_api',
                   'gui'] + _CORE_MODULES + _WEB_MODULES + _WEB_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['_overlay_bootstrap.py'],
    excludes=_WEB_EXCLUDES,
    # noarchive=True keeps user-side .py files on disk as .pyc inside
    # _internal/, which lets the runtime hook prepend an overlay dir to
    # sys.path and have user modules resolved from there. Bundled 3rd-party
    # deps still come from PYZ for size — only user code is overlay-able.
    noarchive=True,
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


# Qt WebEngine's interface languages (its context menu, error pages): the
# interface speaks English and German, and en-US is the one Chromium falls
# back to. The other ~50 are dropped.
def _keep_webengine_locale(dst):
    path = dst.replace('\\', '/')
    if '/qtwebengine_locales/' not in path:
        return True
    return os.path.basename(path) in ('en-US.pak', 'de.pak')


a.datas = [entry for entry in a.datas if _keep_webengine_locale(entry[0])]


# Qt WebEngine's widget (what pywebview's Qt backend shows) is drawn through
# Qt Quick, so the QML hooks run, and they bring every QML plugin PySide6 has
# (Quick 3D, Charts, Multimedia, Location, ...) with the Qt libraries those
# need: about 100 MB the window never loads. Dropped: the QML plugin tree, and
# every Qt library that nothing else in the bundle links against (followed
# through their dependencies, as PyInstaller itself resolves them).
def _posix(path):
    return path.replace('\\', '/')


def _prune_qt(binaries, datas):
    from PyInstaller.depend import bindepend
    qml, qtlib = 'PySide6/Qt/qml/', 'PySide6/Qt/lib/'
    binaries = [e for e in binaries if not _posix(e[0]).startswith(qml)]
    datas = [e for e in datas if not _posix(e[0]).startswith(qml)]
    reached, done = set(), set()
    todo = [e[1] for e in binaries if not _posix(e[0]).startswith(qtlib)]
    while todo:
        src = todo.pop()
        if src in done:
            continue
        done.add(src)
        for name, path in bindepend.get_imports(src):
            if name not in reached:
                reached.add(name)
                if path:
                    todo.append(path)
    kept = [e for e in binaries
            if not _posix(e[0]).startswith(qtlib) or os.path.basename(e[0]) in reached]
    return kept, datas


a.binaries, a.datas = _prune_qt(a.binaries, a.datas)

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
