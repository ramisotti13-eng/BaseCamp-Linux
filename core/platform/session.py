"""When the desktop session locks and the computer goes to sleep.

The seam is SessionEvents:

    events = session_events(scheduler)
    events.start(notify)     # True when it watches; notify(kind, active)
    ...
    events.stop()

`kind` is "lock" (the session was locked, or its screen saver came on;
active False when it is unlocked or the saver goes off) or "sleep" (the
computer is about to sleep; active False when it has woken up). notify may
be called on any thread; the caller hands it on to its own.

Linux (GdbusSessionEvents): logind on the system bus says both,
`PrepareForSleep` on its manager and, for our session, `Lock` / `Unlock` and
the `LockedHint` property that screen lockers set. Where a locker does not
set the hint, the session bus's `org.freedesktop.ScreenSaver` says
`ActiveChanged`. The project has no D-Bus binding, so each bus is followed by
a `gdbus monitor` process (from GLib, on every desktop that has D-Bus). Its
output is read without blocking from a timer on the caller's scheduler, so no
thread is started for it, and stop() ends the processes. A monitor that ends
on its own (a bus restart) is started again, less often each time.

Windows has no implementation yet (NullSessionEvents): the port adds one from
WTSRegisterSessionNotification and the power broadcast messages.
"""
import ctypes
import os
import re
import shutil
import signal
import subprocess
import time

from core.platform import IS_LINUX


class SessionEvents:
    """Interface, and the do-nothing version where nothing can be watched."""

    #: True while something is actually being followed.
    watching = False

    def start(self, notify):
        """Begin watching; notify(kind, active) hears each change. Returns
        whether anything is watched."""
        return False

    def stop(self):
        """Stop watching. Must not raise; calling it twice is harmless."""


class NullSessionEvents(SessionEvents):
    pass


# ── Reading gdbus monitor ─────────────────────────────────────────────────────

LOGIN1 = "org.freedesktop.login1"
SCREENSAVER = "org.freedesktop.ScreenSaver"

_SLEEP = re.compile(r"\.Manager\.PrepareForSleep \((true|false),\)")
_LOCK_SIGNAL = re.compile(r"^(\S+): org\.freedesktop\.login1\.Session\.(Lock|Unlock) \(\)")
_LOCKED_HINT = re.compile(
    r"^(\S+): org\.freedesktop\.DBus\.Properties\.PropertiesChanged "
    r"\('org\.freedesktop\.login1\.Session',.*'LockedHint': <(true|false)>")
_SAVER = re.compile(r"\.ScreenSaver\.ActiveChanged \((true|false),\)")


def parse_line(line, session_path=None):
    """(kind, active) for a line of `gdbus monitor` output that says
    something, else None. Lock lines of another session than
    `session_path` say nothing; without a path every session counts."""
    line = line.strip()
    m = _SLEEP.search(line)
    if m:
        return "sleep", m.group(1) == "true"
    m = _LOCK_SIGNAL.match(line)
    if m:
        if session_path and m.group(1) != session_path:
            return None
        return "lock", m.group(2) == "Lock"
    m = _LOCKED_HINT.match(line)
    if m:
        if session_path and m.group(1) != session_path:
            return None
        return "lock", m.group(2) == "true"
    m = _SAVER.search(line)
    if m:
        return "lock", m.group(1) == "true"
    return None


def bus_path_label(text):
    """A logind id as it appears in an object path (systemd's
    bus_label_escape): letters stay, so do digits after the first
    character, anything else is _ and two lowercase hex digits."""
    if not text:
        return "_"
    out = []
    for i, ch in enumerate(text):
        if ("a" <= ch <= "z" or "A" <= ch <= "Z") or (i > 0 and "0" <= ch <= "9"):
            out.append(ch)
        else:
            for b in ch.encode("utf-8"):
                out.append("_%02x" % b)
    return "".join(out)


def own_session_path(environ=None, proc_file="/proc/self/sessionid"):
    """logind's object path of the session this process runs in, or None.

    From XDG_SESSION_ID, which the session's programs inherit; else from the
    audit session id, which logind uses as its own id where audit is on and
    which a process started with sudo keeps."""
    environ = os.environ if environ is None else environ
    sid = (environ.get("XDG_SESSION_ID") or "").strip()
    if not sid:
        try:
            with open(proc_file) as f:
                sid = f.read().strip()
        except OSError:
            sid = ""
        if sid in ("", "4294967295", "-1"):
            sid = ""
    return "/org/freedesktop/login1/session/" + bus_path_label(sid) if sid else None


try:
    _libc = ctypes.CDLL("libc.so.6", use_errno=True) if IS_LINUX else None
except Exception:                         # no glibc
    _libc = None


def _die_with_parent():
    """Runs in the child between fork and exec: have the kernel end the
    monitor when this process ends, however it ends (see
    core/platform/audio.py, which does the same for `pactl subscribe`)."""
    if _libc is not None:
        _libc.prctl(1, signal.SIGTERM)     # PR_SET_PDEATHSIG


class GdbusSessionEvents(SessionEvents):
    """Follows logind and the screen saver through `gdbus monitor`.

    Everything runs on `scheduler`: a timer every `poll_ms` reads what the
    monitors printed since and calls notify for each line that says
    something. `popen`, `which` and `environ` are seams for the tests."""

    POLL_MS = 500
    RESTART_MS = 10_000
    RESTART_MAX_MS = 300_000

    def __init__(self, scheduler, popen=subprocess.Popen, which=shutil.which,
                 environ=None, poll_ms=None, session_path=None):
        self.scheduler = scheduler
        self._popen = popen
        self._which = which
        self._environ = os.environ if environ is None else environ
        self.poll_ms = poll_ms or self.POLL_MS
        self.session_path = (session_path if session_path is not None
                             else own_session_path(self._environ))
        self._notify = None
        self._gdbus = None
        self._procs = {}            # name -> [proc, fd, partial line, started]
        self._restart = {}          # name -> handle of its restart
        self._delay = {}            # name -> wait before its next restart (ms)
        self._poll_handle = None
        self.watching = False

    def _monitors(self):
        """{name: argv} of the monitors to run."""
        out = {"system": [self._gdbus, "monitor", "--system", "--dest", LOGIN1]}
        env = self._environ
        if env.get("DBUS_SESSION_BUS_ADDRESS") or (
                env.get("XDG_RUNTIME_DIR")
                and os.path.exists(os.path.join(env["XDG_RUNTIME_DIR"], "bus"))):
            out["session"] = [self._gdbus, "monitor", "--session", "--dest", SCREENSAVER]
        return out

    def start(self, notify):
        if self.watching:
            return True
        try:
            self._gdbus = self._which("gdbus")
        except Exception:
            self._gdbus = None
        if not self._gdbus:
            return False
        self._notify = notify
        self.watching = True
        for name in self._monitors():
            self._spawn(name)
        if not self._procs and not self._restart:
            self.watching = False
            return False
        self._poll_handle = self.scheduler.call_later(self.poll_ms, self._poll)
        return True

    def _spawn(self, name):
        if not self.watching:
            return
        self._restart.pop(name, None)
        argv = self._monitors().get(name)
        if argv is None:
            return
        try:
            from core.platform.launcher import clean_child_env
            env = clean_child_env(self._environ)
            env["LC_ALL"] = "C"
            proc = self._popen(argv, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               env=env, preexec_fn=_die_with_parent)
            fd = proc.stdout.fileno()
            os.set_blocking(fd, False)
        except Exception:
            self._later(name)
            return
        self._procs[name] = [proc, fd, b"", time.monotonic()]

    def _later(self, name):
        """Start monitor `name` again after a while, twice as long as the
        last time while it keeps ending soon after it starts."""
        delay = self._delay.get(name, self.RESTART_MS)
        self._delay[name] = min(delay * 2, self.RESTART_MAX_MS)
        self._restart[name] = self.scheduler.call_later(delay, self._spawn, name)

    def _poll(self):
        self._poll_handle = None
        if not self.watching:
            return
        for name, entry in list(self._procs.items()):
            proc, fd, buf, started = entry
            ended = False
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    break
                except OSError:
                    ended = True
                    break
                if not chunk:
                    ended = True
                    break
                buf += chunk
            *lines, buf = buf.split(b"\n")
            entry[2] = buf
            for raw in lines:
                self._hear(raw)
            if ended or proc.poll() is not None:
                self._end(name)
                if not self.watching:
                    return              # stop() came in meanwhile
                if time.monotonic() - started > 60:
                    self._delay.pop(name, None)     # it ran: no backoff
                self._later(name)
        if self.watching:
            self._poll_handle = self.scheduler.call_later(self.poll_ms, self._poll)

    def _hear(self, raw):
        event = parse_line(raw.decode("utf-8", "replace"), self.session_path)
        if event is not None and self._notify is not None:
            try:
                self._notify(*event)
            except Exception:
                pass

    def _end(self, name):
        entry = self._procs.pop(name, None)
        if entry is None:
            return
        proc = entry[0]
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    proc.kill()
                    proc.wait(timeout=0.5)
        except Exception:
            pass
        try:
            proc.stdout.close()
        except Exception:
            pass

    def stop(self):
        self.watching = False
        self._notify = None
        if self._poll_handle is not None:
            self.scheduler.cancel(self._poll_handle)
            self._poll_handle = None
        for h in list(self._restart.values()):
            self.scheduler.cancel(h)
        self._restart.clear()
        for name in list(self._procs):
            self._end(name)


def session_events(scheduler):
    """The watcher for this operating system."""
    if IS_LINUX:
        return GdbusSessionEvents(scheduler)
    return NullSessionEvents()
