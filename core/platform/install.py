"""How this copy of the application was installed, and how it restarts itself.

Linux specifics of the update flow: the AppImage and the distro packages, the
source overlay under ~/.local/share, and the re-exec of the AppImage. A
Windows port answers "source" from detect_install_type() until it has an
installer of its own. Moved out of gui.py without changes in behaviour.
"""
import os
import subprocess
import sys


def detect_install_type(environ=None, exists=os.path.exists, run=subprocess.run):
    """Return one of 'appimage' | 'arch' | 'debian' | 'source'.
    Picked at runtime so the same code works across all packaging formats."""
    env = os.environ if environ is None else environ
    if env.get("APPIMAGE"):
        return "appimage"
    # AUR builds install a binary at /usr/bin/basecamp-linux, but Arch users
    # could also be running from source. Check the pacman db for our package.
    if exists("/etc/arch-release"):
        try:
            r = run(["pacman", "-Q", "basecamp-linux"],
                    capture_output=True, timeout=2)
            if r.returncode == 0:
                return "arch"
        except Exception:
            pass
    if exists("/etc/debian_version"):
        try:
            r = run(["dpkg", "-s", "basecamp-linux"],
                    capture_output=True, timeout=2)
            if r.returncode == 0:
                return "debian"
        except Exception:
            pass
    return "source"


def appimage_variant(appimage_path, read_os_release=None):
    """'debian' or 'fedora': which AppImage build suits this machine.

    The file name of the running AppImage wins if it explicitly says
    -debian/-fedora (the user picked it on download); otherwise
    /etc/os-release decides. Debian-family glibc is older than Fedora's, so
    the wrong variant fails to start with cryptic ld errors.
    """
    base = os.path.basename(appimage_path or "").lower()
    if "debian" in base:
        return "debian"
    if "fedora" in base:
        return "fedora"
    try:
        if read_os_release is None:
            with open("/etc/os-release") as f:
                osr = f.read().lower()
        else:
            osr = read_os_release().lower()
        if ("id=debian" in osr or "id=ubuntu" in osr
                or "id=linuxmint" in osr
                or "id_like=debian" in osr
                or "id_like=ubuntu" in osr):
            return "debian"
    except OSError:
        pass
    return "fedora"


def overlay_root(home):
    """Where the source overlay of an updated AppImage lives."""
    return os.path.join(home, ".local", "share", "basecamp-linux")


def download_cache(home, name):
    return os.path.join(home, ".cache", name)


def restart_self(appimage, env, execve=os.execve, popen=subprocess.Popen):
    """Start the (now updated) program again with environment `env`.

    Replaces the process when it can (the AppImage); otherwise starts a new
    one and returns, and the caller closes the old window.
    """
    try:
        if appimage and os.path.isfile(appimage):
            execve(appimage, [appimage], env)
    except Exception:
        pass
    try:
        popen([appimage] if appimage else [sys.executable, entry_script()], env=env)
    except Exception:
        pass


def entry_script():
    """gui.py of this tree: what a start from source runs (a bare
    sys.executable would start an interactive Python)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "gui.py")
