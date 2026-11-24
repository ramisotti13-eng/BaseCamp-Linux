"""Starting other programs on the user's desktop.

Shell commands, applications, URLs and folders behind a key, a macro step or
a plugin are all started from here, with an environment that is the user's
and not the bundle's:

    run_shell("discord")              # bash -c, in a scope of its own
    open_target("https://...")        # xdg-open, for URLs and folders

clean_child_env() undoes what a PyInstaller or AppImage bundle injects into
the environment (#11, #49); every program the application starts, here or
elsewhere, gets its environment through it. Under sudo (USB access without
the udev rule) the program is started as the invoking user, with that
user's home, display and runtime directory.

This is the Linux implementation. A Windows port replaces run_shell and
open_target (os.startfile, cmd /c) and has no bundle paths to strip; the
names stay the same, so callers do not change.
"""
import os
import re
import subprocess
import sys

# Env vars that PyInstaller / AppImage inject so the bundled interpreter finds
# its own libraries. If they leak into an external program we launch (a browser,
# zenity, a GUI app, …) that program loads our bundled libs and crashes or
# silently fails to start — which is exactly why "App"/"Shell" actions and the
# native file dialog don't work from the AppImage while "URL" (handed off to the
# desktop portal over D-Bus) does. See GitHub issue #11.
_FROZEN_ENV_VARS = (
    "LD_LIBRARY_PATH", "LD_PRELOAD",
    "PYTHONPATH", "PYTHONHOME",
    "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    "GTK_PATH", "GTK_EXE_PREFIX", "GTK_DATA_PREFIX",
    "GDK_PIXBUF_MODULE_FILE", "GDK_PIXBUF_MODULEDIR",
    "GST_PLUGIN_PATH", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0",
    "GI_TYPELIB_PATH", "GSETTINGS_SCHEMA_DIR",
    "FONTCONFIG_FILE", "FONTCONFIG_PATH",
    "TCL_LIBRARY", "TK_LIBRARY", "TKPATH",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    # Set by PyInstaller's runtime hooks from inside Python rather than by the
    # bootloader, so they never get an _ORIG partner and were missed by the
    # name list until 3.0.5. GIO_MODULE_DIR is the one that breaks Plasma
    # System Monitor: its process and application pages are GIO modules.
    "PANGO_LIBDIR", "PANGO_SYSCONFDIR", "GIO_MODULE_DIR",
)

# A bundle announces itself by where it lives: AppImage type 2 mounts at
# $TMPDIR/.mount_<name><random>, a onefile PyInstaller build unpacks to
# $TMPDIR/_MEI<random>. Matching on the location rather than on a list of
# variable names is what makes the sweep below hold for the next PyInstaller
# release too, and for mount points that are not ours any more (see #49).
_BUNDLE_DIR_RE = re.compile(r"/(?:\.mount_|_MEI)[^/]*(?:/|$)")

# Vars that only describe our own bundle. They do not break the dynamic linker,
# but they announce "you are running inside an AppImage" to every program we
# start, and libraries do key off them. Dropped so a launched app sees the same
# environment it would get from the desktop launcher. See GitHub issue #49.
_BUNDLE_ID_VARS = (
    "APPDIR", "APPIMAGE", "APPIMAGE_UUID", "ARGV0", "OWD", "_MEIPASS2",
    "BASECAMP_OVERLAY_VERSION",
)

# Vars systemd sets for the unit BaseCamp itself runs in (KDE autostart puts us
# in app-basecamp-linux@autostart.service). They are inherited by every child,
# so a launched app logs into our journal stream, watches our cgroup's memory
# pressure and reports our invocation id, which is one of the ways it ends up
# behaving differently than the same app started from the launcher or a
# terminal. Not frozen-specific: a source or distro install started by the same
# autostart unit leaks them too. See GitHub issue #49.
_SYSTEMD_UNIT_VARS = (
    "INVOCATION_ID", "JOURNAL_STREAM",
    # WATCH names our cgroup's pressure file, WRITE carries the thresholds to
    # write into it. Only WATCH was listed, so a child still received our
    # thresholds; both halves of the pair have to go.
    "MEMORY_PRESSURE_WATCH", "MEMORY_PRESSURE_WRITE",
    "SYSTEMD_EXEC_PID", "MANAGERPID", "MANAGERPIDFDID", "NOTIFY_SOCKET",
    "LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES",
    "WATCHDOG_PID", "WATCHDOG_USEC",
    "SYSTEMD_ACTIVATION_UNIT", "SYSTEMD_ACTIVATION_SCOPE",
    "SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS",
)


#: Variables this process changes for its own toolkit (the web window's Qt
#: WebEngine switches, pywebview's choice of backend: ui_web/launch.py), with
#: the value each had before the change, None for "not set". A program we
#: start gets them back as they were, frozen or not: a Qt application started
#: from a key must not inherit "no sandbox" from us.
_OWN_VARS = {}


def remember_env(names, environ=None):
    """Note the current value of each of `names`, before this process changes
    it. The first note of a name counts; later calls do not overwrite it."""
    env = os.environ if environ is None else environ
    for name in names:
        if name not in _OWN_VARS:
            _OWN_VARS[name] = env.get(name)


def _bundle_roots(env):
    """Directories whose contents belong to this bundle rather than the system."""
    roots = set()
    appdir = env.get("APPDIR")
    if appdir:
        roots.add(appdir.rstrip("/"))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.add(meipass.rstrip("/"))
    if getattr(sys, "frozen", False):
        roots.add(os.path.dirname(os.path.abspath(sys.executable)))
    return roots


def _strip_bundle_paths(value, roots):
    """Drop the path elements of `value` that point into a bundle.

    Returns the value unchanged when nothing matched, so a PATH keeps its exact
    original spelling (an empty element means "current directory" and is not
    ours to remove).
    """
    parts = value.split(os.pathsep)
    kept = [p for p in parts
            if not (p and (_BUNDLE_DIR_RE.search(p)
                           or any(p == r or p.startswith(r + "/") for r in roots)))]
    return value if len(kept) == len(parts) else os.pathsep.join(kept)


def clean_child_env(env=None):
    """Return a copy of `env` (default os.environ) safe to hand to an external
    program, with the PyInstaller / AppImage library-path injection undone.

    Two passes. By name: the vars PyInstaller and AppRun inject are restored
    from their ``<VAR>_ORIG`` partner where one exists, else dropped, so the
    program falls back to the system libraries. Then by value: every remaining
    variable loses the path elements that point into a bundle, and is dropped
    when nothing is left. The second pass is the one that holds, because the
    first depends on a list of names we maintain by hand and on _ORIG being
    trustworthy, and neither survived contact with reality (#49): the runtime
    hooks set PANGO_LIBDIR and GIO_MODULE_DIR without an _ORIG, and after an
    in-app update restart the _ORIG of LD_LIBRARY_PATH is itself the previous
    instance's mount point.

    The bundle-identity vars are removed, and the systemd unit vars of our own
    service are dropped in every install kind, frozen or not. So are the
    toolkit variables noted by remember_env(): they get their earlier value.
    """
    env = dict(os.environ if env is None else env)
    for var in _SYSTEMD_UNIT_VARS:
        env.pop(var, None)
    for var, orig in _OWN_VARS.items():
        if orig is None:
            env.pop(var, None)
        else:
            env[var] = orig
    if not (getattr(sys, "frozen", False) or env.get("APPDIR") or env.get("APPIMAGE")):
        return env
    roots = _bundle_roots(env)
    for var in _FROZEN_ENV_VARS:
        orig = env.pop(var + "_ORIG", "")
        orig = _strip_bundle_paths(orig, roots) if orig else ""
        if orig:
            env[var] = orig
        else:
            env.pop(var, None)
    for var in _BUNDLE_ID_VARS:
        env.pop(var, None)
    for var in [k for k in env if k.startswith("_PYI_")]:
        env.pop(var, None)
    for var, val in list(env.items()):
        if not val:
            continue
        cleaned = _strip_bundle_paths(val, roots)
        if cleaned == val:
            continue
        if cleaned:
            env[var] = cleaned
        else:
            env.pop(var, None)
    return env


def user_scope_prefix(env=None):
    """Return a systemd-run prefix that puts a launched program in its own scope.

    KDE and GNOME start applications in an ``app-*.scope`` of their own, while a
    program we start plainly inherits BaseCamp's cgroup. That ties its resource
    limits and its lifetime to ours: stopping BaseCamp's autostart unit takes
    the program down with it. Returns an empty list when no user systemd manager
    is reachable, so the caller runs the command unchanged. See issue #49.
    """
    import shutil
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime or not os.path.exists(os.path.join(runtime, "systemd", "private")):
        return []
    if not shutil.which("systemd-run"):
        return []
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect",
            "--slice=app.slice"]


def user_env():
    """Environment for a program started on the user's desktop, and the user
    to start it as: (env, sudo_user).

    sudo_user is None unless the application runs under sudo; then the
    environment is that user's (home, display, runtime directory, Wayland
    socket) and the caller wraps its command with sudo_prefix().
    """
    env = clean_child_env()
    sudo_user = env.get("SUDO_USER")
    if sudo_user:
        import pwd
        pw = pwd.getpwnam(sudo_user)
        uid = pw.pw_uid
        env["HOME"] = pw.pw_dir
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        if os.path.exists(f"/run/user/{uid}/wayland-0"):
            env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    return env, sudo_user


def sudo_prefix(sudo_user):
    """The prefix that runs a command as `sudo_user` with our environment."""
    return ["sudo", "-u", sudo_user, "-E"] if sudo_user else []


def open_target(target):
    """Open a URL or a folder with the desktop's default handler (xdg-open).

    Fire and forget: a handler that cannot be started is not an error the
    caller can do anything about.
    """
    env, sudo_user = user_env()
    cmd = sudo_prefix(sudo_user) + ["xdg-open", target]
    try:
        subprocess.Popen(cmd, env=env)
    except Exception:
        pass


def run_shell(command):
    """Run a shell command line (bash -c) in a scope of its own (#49)."""
    env, sudo_user = user_env()
    cmd = sudo_prefix(sudo_user) + user_scope_prefix(env) + ["bash", "-c", command]
    try:
        subprocess.Popen(cmd, env=env)
    except Exception:
        pass
