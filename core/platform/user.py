"""Who the application runs for.

On Linux the application can run under sudo (for USB access on a system
without the udev rule). It must then still read and write the invoking user's
configuration, so SUDO_USER is honoured, but only when it names a real
non-root account: a poisoned variable must not redirect root's writes into
some other user's files. Windows has no sudo and no uid; there the current
user is the user.
"""
import os

from core.platform import IS_WINDOWS


def resolve_real_user():
    """Return (home_dir, uid, gid) of the user the application works for.

    uid and gid are None unless running as root on behalf of another account
    (and always None on Windows).
    """
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and not IS_WINDOWS:
        try:
            import pwd
            pw = pwd.getpwnam(sudo_user)
        except (ImportError, KeyError):
            pw = None
        if pw and pw.pw_uid != 0:
            return pw.pw_dir, pw.pw_uid, pw.pw_gid
    return os.path.expanduser("~"), None, None


def euid():
    """Effective uid, or None where the OS has none (Windows)."""
    fn = getattr(os, "geteuid", None)
    return fn() if fn else None


def is_root():
    return euid() == 0
