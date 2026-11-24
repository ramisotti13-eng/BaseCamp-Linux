"""System volume, read from the default PipeWire sink.

Two callers need it: the monitor loop in emax_controller, which pushes the
level to the keyboard's wheel display over `11 83`, and the Everest panel,
which mirrors what the keyboard is being sent. Both reach it through its 3.x
name, shared/volume.py.

This is the Linux implementation (wpctl or pactl). A Windows port answers
system_volume() and start_watch() from the Core Audio API instead.

The default sink is re-resolved on every read, so switching output device is
followed with no extra work. `wpctl` is used where WirePlumber runs and `pactl`
otherwise, because a PulseAudio system without WirePlumber has no `wpctl` at
all and would otherwise report nothing at all.

Both are run with the bundle's library-path injection stripped, the house rule
for anything we launch (#49), and with `LC_ALL=C`: `pactl` translates its
output, so on a German desktop `pactl subscribe` says `Ereignis` where the
filter looks for `Event` and every change would be missed.

Why a watcher and not a poll: the loop pushes the level several times a second.
Any staleness in the cached value is pushed *over* the level the firmware just
set from a wheel turn, so the display visibly snaps back to the old number
until the next poll. `pactl subscribe` makes the update immediate instead of
merely frequent. If it is unavailable the code falls back to a rate-limited
poll, which still works — it just flickers on wheel turns.
"""
import ctypes
import re
import signal
import subprocess
import threading
import time

from core.platform.launcher import clean_child_env

POLL_INTERVAL = 0.5          # fallback only, when the watcher is not running


def _child_env():
    """Environment for the mixer commands: no bundle paths, no translation."""
    env = clean_child_env()
    env["LC_ALL"] = "C"
    return env

_lock = threading.Lock()
_level = None                # last known level, from watcher or poll
_last_poll = 0.0
_watcher = None
_reader = None               # the mixer command that answered last


def parse_wpctl_volume(text):
    """Level 0-100 from `wpctl get-volume` output, 0 when muted, None if the
    line is not one we understand. Levels above 1.0 are legal and clamp to 100.
    """
    if not text or not text.startswith("Volume:"):
        return None
    if "[MUTED]" in text:
        return 0
    parts = text.split()
    if len(parts) < 2:
        return None
    try:
        return max(0, min(100, round(float(parts[1]) * 100)))
    except ValueError:
        return None


def is_volume_event(line):
    """True for a `pactl subscribe` line that can change the level we show.

    Sink events cover volume and mute; server events cover the default sink
    being switched to another device, which changes whose level we report.
    """
    if not line or "Event" not in line:
        return False
    return " on sink " in line or " on server " in line


def parse_pactl_volume(text):
    """Level 0-100 from `pactl get-sink-volume` output, None if unrecognised.

    pactl prints one reading per channel, `front-left: 32768 /  50% / -18.06
    dB, front-right: ...`. The channels of a sink move together for our
    purposes, so the first percentage is the level.
    """
    if not text or not text.startswith("Volume:"):
        return None
    found = re.search(r"(\d+)%", text)
    if not found:
        return None
    return max(0, min(100, int(found.group(1))))


def parse_pactl_mute(text):
    """True/False from `pactl get-sink-mute` output, None if unrecognised.

    Not a comparison against `yes` alone: pactl translates this line too
    (`Mute: ja`), and reading an unrecognised word as "not muted" would show a
    level for a silent sink. Unknown is None here, like everything else in this
    file, so the caller keeps the last good value instead of a wrong one.
    """
    if not text or not text.startswith("Mute:"):
        return None
    parts = text.split()
    if len(parts) < 2:
        return None
    if parts[1] == "yes":
        return True
    if parts[1] == "no":
        return False
    return None


def _run(cmd):
    """stdout of a mixer command, or None if it is missing or fails."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1,
                              env=_child_env())
    except Exception:                     # not installed, or it hung
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _read_wpctl():
    return parse_wpctl_volume(_run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]))


def _read_pactl():
    """The PulseAudio path. Mute is a second call here; wpctl reports it inline."""
    level = parse_pactl_volume(_run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]))
    if level is None:
        return None
    muted = parse_pactl_mute(_run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"]))
    if muted is None:                     # unreadable mute, so the level alone
        return None                       # cannot be trusted to mean anything
    return 0 if muted else level


def _read():
    """Ask whichever mixer this system actually has.

    The working one is remembered so the other is not forked on every read, and
    forgotten again the moment it stops answering, so a system that gains or
    loses WirePlumber is not stuck with the wrong choice.
    """
    global _reader
    if _reader is not None:
        value = _reader()
        if value is not None:
            return value
        _reader = None
    for reader in (_read_wpctl, _read_pactl):
        value = reader()
        if value is not None:
            _reader = reader
            return value
    return None


try:
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
except Exception:                         # not Linux, or no glibc
    _libc = None

PR_SET_PDEATHSIG = 1


def _die_with_parent():
    """Ask the kernel to signal this child when its parent dies.

    The watcher thread is a daemon, so it is killed outright when the monitor
    process ends and its `finally` never runs. Without this every start and
    stop of Monitor Mode would strand a `pactl subscribe` process.

    This runs in the forked child, between fork and exec, where almost nothing
    is safe to do: another thread may have held the dynamic loader's lock at
    the moment of the fork, and loading a library here would then deadlock a
    child that can never be reaped. `libc` is therefore resolved above, in the
    parent, and this only calls through the pointer.
    """
    if _libc is not None:
        _libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


def _watch_loop():
    global _level
    try:
        proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                env=_child_env(), preexec_fn=_die_with_parent)
    except Exception:
        return
    try:
        for line in proc.stdout:
            if is_volume_event(line):
                value = _read()
                if value is not None:
                    with _lock:
                        _level = value
    except Exception:
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def start_watch():
    """Begin following volume changes as they happen.

    Safe to call from anywhere, any number of times: the panel calls it when
    the screen is shown and the monitor loop calls it when it starts, and both
    may be in the same process.
    """
    global _watcher, _level
    seed = _read()                        # before the lock: this forks a process
    with _lock:
        if seed is not None:
            _level = seed                 # so the first push is a real level
        if _watcher is not None and _watcher.is_alive():
            return
        # Started while holding the lock: a thread that exists but has not run
        # yet reports itself as not alive, so releasing first would let a second
        # caller start another one.
        _watcher = threading.Thread(target=_watch_loop, daemon=True)
        _watcher.start()


def system_volume():
    """Volume of the currently selected output, 0-100, or None if unreadable.

    Free to call every loop iteration: with the watcher running this is a
    variable read, and without it the poll is rate limited.
    """
    global _level, _last_poll
    if _watcher is not None and _watcher.is_alive():
        with _lock:
            return _level
    now = time.monotonic()
    if now - _last_poll >= POLL_INTERVAL:
        _last_poll = now
        value = _read()
        if value is not None:             # a failed read keeps the last good
            with _lock:                   # level rather than blanking the
                _level = value            # display
    with _lock:
        return _level
