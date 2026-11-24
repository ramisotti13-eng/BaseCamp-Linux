"""System volume, read from the default PipeWire sink (3.x name).

The code lives in core/platform/audio.py since 4.0. This module keeps the
names the Everest panel, emax_controller and the tests import, as the same
functions; anything else (the module's own state, such as the last level)
is read from there at the moment it is asked for.
"""
from core.platform import audio as _audio
from core.platform.audio import (       # noqa: F401
    POLL_INTERVAL, PR_SET_PDEATHSIG,
    parse_wpctl_volume, is_volume_event, parse_pactl_volume, parse_pactl_mute,
    start_watch, system_volume,
    _child_env, _run, _read_wpctl, _read_pactl, _read,
    _die_with_parent, _watch_loop,
)


def __getattr__(name):
    # _level, _watcher, _reader, _lock, _libc...: always the live value.
    return getattr(_audio, name)
