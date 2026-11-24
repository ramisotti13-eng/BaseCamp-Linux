"""Macro execution engine for BaseCamp Linux (3.x names).

Macros are sequences of actions (key press/release, delays, text, shell commands)
executed via xdotool (X11) or ydotool (Wayland).

Since 4.0 the code lives in the core: input injection in
core/platform/input.py, launching programs and the child environment in
core/platform/launcher.py, playing macros in core/macros/engine.py, mouse
recordings in core/macros/recordings.py. This module keeps every name it had,
because the DisplayPad, emax_controller, plugins (snippets, now_playing) and
the tests import them from here, and the tests replace some of them.

Each function here calls into the core when it is called, so a caller that
looks a name up in this module at call time (as the key dispatchers do) gets
whatever is installed under that name now.
"""
from core.macros import engine as _engine
from core.macros import recordings as _recordings
from core.macros.model import (            # noqa: F401
    ACTION_TYPES, ACTION_LABELS, REPEAT_MODES, generate_macro_id,
)
from core.platform import input as _input
from core.platform import launcher as _launcher
from core.platform.input import (          # noqa: F401
    KEY_MAP, KEY_CHOICES, KEYSYM_TO_FRIENDLY, YDOTOOL_KEY_MAP,
    MOUSE_CLICK_BUTTONS, MOUSE_SCROLL_DIRS,
)
from core.platform.launcher import (       # noqa: F401
    _FROZEN_ENV_VARS, _BUNDLE_DIR_RE, _BUNDLE_ID_VARS, _SYSTEMD_UNIT_VARS,
    _bundle_roots, _strip_bundle_paths, clean_child_env, user_scope_prefix,
)


# ── Environment and launching ────────────────────────────────────────────────

def _build_env():
    """Build environment for input tools, running as real user if under sudo."""
    return _launcher.user_env()


def _run_xdg_open(target):
    """Open a URL or folder via xdg-open."""
    _launcher.open_target(target)


def _run_shell(command):
    """Run a shell command."""
    _launcher.run_shell(command)


# ── Input tools ──────────────────────────────────────────────────────────────

def _resolve_key(name):
    """Map a friendly key name to an xdotool keysym."""
    return _input.resolve_key(name)


def _resolve_key_ydotool(name):
    """Map a friendly key name to a ydotool key code."""
    return _input.resolve_key_ydotool(name)


def _detect_session():
    """Detect display server: 'wayland' or 'x11'."""
    return _input.detect_session()


def _find_tool():
    """Find the best available input automation tool.
    Returns ('xdotool', path) or ('ydotool', path) or (None, None).
    """
    return _input.find_tool()


def _run_tool(tool_name, *args):
    """Run xdotool or ydotool with correct user environment."""
    _input.run_tool(tool_name, *args)


def _run_tool_output(tool_name, *args):
    """Run tool and return stdout."""
    return _input.run_tool_output(tool_name, *args)


def check_macro_tools():
    """Check if required tools are available. Returns (tool_name, session_type) or (None, session_type)."""
    return _input.tools_status()


def simulate_keypress(key_name):
    """Simulate a single keypress (or combo like 'ctrl+c') via xdotool or ydotool.
    Picks the available tool automatically — works on X11 and Wayland."""
    _input.simulate_keypress(key_name)


def simulate_text(text):
    """Type out text via xdotool or ydotool. Works on X11 and Wayland."""
    _input.simulate_text(text)


def get_mouse_location():
    """Get current mouse position. Returns (x, y) or None."""
    return _input.cursor_pos()


# ── Macros ───────────────────────────────────────────────────────────────────

def execute_macro(macro_data, stop_event=None):
    """Execute a macro's action sequence.

    macro_data: dict with 'actions', 'repeat_mode', 'repeat_count'
    stop_event: threading.Event for toggle mode cancellation
    """
    _engine.execute_macro(macro_data, stop_event)


def _run_actions(actions, stop_event=None):
    _engine.run_actions(actions, stop_event)


def _sleep_ms(ms, stop_event=None):
    """Sleep for ms milliseconds, checking stop_event every 50ms."""
    _engine.sleep_ms(ms, stop_event)


# The step helpers take the tool by name, as they did in 3.x.

def _exec_key(tool, atype, value):
    inj = _input.injector_for(tool)
    if inj is not None:
        inj.key(atype, value)


def _exec_text(tool, value):
    inj = _input.injector_for(tool)
    if inj is not None:
        inj.type_text(value)


def _exec_mouse_click(tool, value):
    inj = _input.injector_for(tool)
    if inj is not None:
        inj.mouse_click(value)


def _exec_mouse_move(tool, value):
    inj = _input.injector_for(tool)
    if inj is not None:
        _engine.mouse_move_step(inj, value)


def _exec_mouse_path(tool, filename, stop_event=None):
    """Play back a mouse recording file."""
    inj = _input.injector_for(tool)
    if inj is not None:
        _engine.play_mouse_path(inj, filename, stop_event)


def _exec_mouse_scroll(tool, value):
    inj = _input.injector_for(tool)
    if inj is not None:
        _engine.mouse_scroll_step(inj, value)


# ── Mouse recordings ─────────────────────────────────────────────────────────

def save_mouse_recording(name, positions, click_at_end=False):
    """Save mouse positions to a recording file. Returns filename."""
    return _recordings.save_mouse_recording(name, positions, click_at_end)


def list_mouse_recordings():
    """Return list of (filename, display_name) for saved recordings."""
    return _recordings.list_mouse_recordings()
