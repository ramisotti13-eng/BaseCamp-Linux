"""Which interface a start gets, and what the web window's toolkit needs from
the environment before it loads.

    basecamp, python3 gui.py      the web interface (ui_web.app), or the
                                  classic one when it cannot start here
    basecamp --classic            the CustomTkinter interface, no questions
    basecamp --web                the web interface (the same as no flag)

gui.run() asks ready() before it builds anything. The web window needs
pywebview and one of its backends: Qt WebEngine (what the AppImage carries,
through PySide6) or WebKit2GTK (what a source install usually takes from the
distribution). Without them the classic interface starts instead, with one
line on the console and a hint in its window (fallback_notice). That is
also what an AppImage from before 4.0 gets when the in-app update brings
it this source: the update carries Python files only, never pywebview.

Nothing here imports a toolkit at module level; ready() imports pywebview's
backend module, which is what the window would import a moment later.
"""
import importlib
import os
import sys

#: What the console says when the classic interface starts in place of the
#: web one; `{why}` is the reason from ready().
FALLBACK_LINE = ("[BaseCamp] The new interface cannot start ({why}); starting the "
                 "classic one. --classic starts it directly.")

#: Variables the web window's toolkit reads that this module or pywebview
#: change for this process. Programs the application starts get them back
#: as they were (core.platform.launcher.remember_env).
TOOLKIT_VARS = ("PYWEBVIEW_GUI", "QT_API", "QT_QPA_PLATFORM", "GDK_BACKEND",
                "QTWEBENGINE_DISABLE_SANDBOX", "QTWEBENGINE_CHROMIUM_FLAGS",
                "QT_STYLE_OVERRIDE", "JSC_SIGNAL_FOR_GC")

_result = None


def _say(line):
    print(line, file=sys.stderr, flush=True)


def _short(exc):
    text = str(exc).strip().splitlines()
    return "%s: %s" % (type(exc).__name__, text[0] if text else "")


def backend_order(environ):
    """The order pywebview tries its Linux backends in (webview.guilib):
    Qt first when PYWEBVIEW_GUI says qt, or in a KDE session when
    PYWEBVIEW_GUI says nothing; GTK first otherwise."""
    forced = (environ.get("PYWEBVIEW_GUI") or "").lower()
    if not forced and "KDE_FULL_SESSION" in environ:
        forced = "qt"
    return ("qt", "gtk") if forced == "qt" else ("gtk", "qt")


def prepare_environment(environ=None, frozen=None):
    """Settings the toolkit reads when it loads, so they go in first.

    - The AppImage carries the Qt backend only, through PySide6: pywebview
      and qtpy are told so instead of trying GTK and PyQt first.
    - In a Wayland session with XWayland the window runs as an X11 client.
      It is frameless and moves itself when its top bar is dragged, which a
      Wayland client cannot do (the compositor places windows), and its saved
      position could not be restored either. The classic interface (Tk) has
      always run under XWayland. A QT_QPA_PLATFORM or GDK_BACKEND that is
      already set is left alone.
    - WebKitGTK's JavaScript engine takes SIGUSR1 for its garbage collector
      unless told otherwise, and SIGUSR1 is the tray helper's Open: the
      window did not come up, the process died. It gets SIGPWR instead
      (WebKit prints "invalid option: JSC_SIGNAL_FOR_GC" for it and uses it
      all the same). Qt WebEngine does not care.
    Returns the names it set."""
    env = os.environ if environ is None else environ
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    try:
        from core.platform.launcher import remember_env
        remember_env(TOOLKIT_VARS, environ=env)
    except Exception:
        pass
    wanted = {}
    if frozen:
        wanted["PYWEBVIEW_GUI"] = "qt"
        wanted["QT_API"] = "pyside6"
    if sys.platform.startswith("linux") and env.get("WAYLAND_DISPLAY") and env.get("DISPLAY"):
        wanted["QT_QPA_PLATFORM"] = "xcb"
        wanted["GDK_BACKEND"] = "x11"
    if sys.platform.startswith("linux"):
        import signal
        wanted["JSC_SIGNAL_FOR_GC"] = str(int(getattr(signal, "SIGPWR", 30)))
    done = []
    for name, value in wanted.items():
        if not env.get(name):
            env[name] = value
            done.append(name)
    return done


def find_backend(environ=None, importer=None):
    """(backend, None) when the web window can open here, else (None, why).

    `backend` is "qt" or "gtk" on Linux, the name pywebview's guilib would
    pick, found the way it finds it: by importing webview.platforms.<name>.
    `importer` (importlib.import_module) is for tests."""
    env = os.environ if environ is None else environ
    imp = importer or importlib.import_module
    try:
        imp("webview")
    except ImportError:
        return None, "pywebview is not installed"
    except Exception as e:
        return None, "pywebview does not load: %s" % _short(e)
    if not sys.platform.startswith("linux"):
        return "native", None
    errors = []
    for name in backend_order(env):
        try:
            imp("webview.platforms." + name)
            return name, None
        except Exception as e:      # ImportError, gi's ValueError, a broken library
            errors.append("%s: %s" % (name, _short(e)))
    return None, "no web view for pywebview: " + "; ".join(errors)


# ── Qt WebEngine's sandbox ───────────────────────────────────────────────────

def _read(path):
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


_CLONE_NEWUSER = 0x10000000
_CLONE_NEWNET = 0x40000000


def _unshare(flags):
    if hasattr(os, "unshare"):
        os.unshare(flags)
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(flags) != 0:
        raise OSError(ctypes.get_errno(), "unshare")


def userns_works():
    """Whether this process may build the namespaces Chromium's sandbox
    needs: a user namespace, and inside it a network namespace. Tried in a
    child process. Docker's default seccomp profile refuses the first,
    Ubuntu's AppArmor restriction (23.10 on) allows it and refuses the
    second. True when it cannot be tried at all, so the sandbox stays."""
    if not hasattr(os, "fork"):
        return True
    try:
        pid = os.fork()
    except OSError:
        return True
    if pid == 0:
        code = 1
        try:
            _unshare(_CLONE_NEWUSER)
            _unshare(_CLONE_NEWNET)
            code = 0
        except BaseException:
            pass
        os._exit(code)
    try:
        _pid, status = os.waitpid(pid, 0)
    except OSError:
        return True
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def qt_sandbox_off_reason(environ=None, euid=None, read=_read, probe=userns_works):
    """Why Qt WebEngine's sandbox cannot run here, or None when it can (or
    when the user has decided already).

    Chromium's sandbox wants user namespaces and refuses to run as root.
    Without them the page process dies and the window stays blank, so it is
    switched off exactly then (QTWEBENGINE_DISABLE_SANDBOX). The page is the
    application's own, served from this process; the sandbox is kept
    wherever it works."""
    env = os.environ if environ is None else environ
    if env.get("QTWEBENGINE_DISABLE_SANDBOX") or \
            "--no-sandbox" in (env.get("QTWEBENGINE_CHROMIUM_FLAGS") or ""):
        return None
    if euid is None:
        euid = os.geteuid() if hasattr(os, "geteuid") else None
    if euid == 0:
        return "running as root"
    if read("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") == "1":
        return "AppArmor restricts user namespaces"
    if read("/proc/sys/kernel/unprivileged_userns_clone") == "0" \
            or read("/proc/sys/user/max_user_namespaces") == "0":
        return "user namespaces are switched off"
    if not probe():
        return "user namespaces are not available here"
    return None


# ── The decision ─────────────────────────────────────────────────────────────

def ready(environ=None, frozen=None, importer=None, sandbox=None, say=_say):
    """(backend, None) when the web window can open, else (None, why).

    Prepares the environment, finds the backend and, for Qt, switches the
    sandbox off where it cannot work (with one line saying why). The answer
    is kept: gui.run() and ui_web.app.main() both ask. `sandbox` is
    qt_sandbox_off_reason, for tests."""
    global _result
    if _result is not None and environ is None and importer is None:
        return _result
    env = os.environ if environ is None else environ
    prepare_environment(env, frozen=frozen)
    backend, why = find_backend(env, importer=importer)
    if backend == "qt":
        off = (sandbox or qt_sandbox_off_reason)(env)
        if off:
            env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
            say("[webui] Qt WebEngine runs without its sandbox here (%s)" % off)
    result = (backend, why)
    if environ is None and importer is None:
        _result = result
    return result


def fallback_line(why):
    return FALLBACK_LINE.format(why=why)


def fallback_notice(app_version, why, *, frozen, has_webview, seen, mark):
    """The hint the classic interface shows after a fallback, or None.

    Once per application version. {"kind": "appimage", ...} for an AppImage
    that has no pywebview (one from before 4.0 that the in-app update
    brought here: only a new AppImage brings the new interface), else
    {"kind": "install", ...}: pywebview or its backend is missing or does not
    load. `seen(key)` / `mark(key)` keep track."""
    if not why:
        return None
    key = str(app_version)
    if seen(key):
        return None
    mark(key)
    kind = "appimage" if frozen and not has_webview else "install"
    return {"kind": kind, "why": why}


def has_webview():
    """Whether pywebview itself can be found (without importing it)."""
    try:
        import importlib.util
        return importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError):
        return False
