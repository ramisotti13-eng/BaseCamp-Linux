"""Virtual pads in windows of their own.

A virtual pad can leave the main window: `shell.open_pad_window(pad)` opens
it in a pywebview window of its own (ui_web/static/padwin.html#<pad id>, the
pad's grid pressed with the mouse), one window per pad. Several can be open
at once, each placed by hand, on any screen.

Each window's place is remembered in CONFIG_DIR/vpad_windows.json:

    {pad id: {"open": bool, "x", "y", "width", "height", "on_top": bool}}

written atomically (temp file, then os.replace): at once on open, close and
"always on top", at most once per SAVE_DELAY_S while a window moves or is
resized. At the next start (restore(), after the main window, before
webview.start) the pads marked open come back where they were; a pad that
no longer exists is dropped, a window that would be off every screen is
pulled back onto the nearest one (the screens as restore() reads them,
before webview.start: afterwards webview.screens would be asked off the
toolkit's thread).

Closing a pad window (its close button, the window manager) is a user
close: the pad stays closed. Quitting the application closes them too, but
then they stay marked open. pywebview's Qt backend keeps running while any
window is left, so app_quitting() closes the pad windows when the main
window closes the application, and no pad window opens while it quits.
On Qt the main loop must also outlive the last visible window (the main
one may sit in the tray): ui_web/launch.py keep_running_without_windows().
A Qt-wide quit (Ctrl+C, the session manager at logout) closes the windows
in Qt's order, the pad windows possibly before the main one, and skips a
main window hidden in the tray: ui_web/qt_gui.py reports it first,
system_quit(True), so those closes keep the pads marked open too
(system_quit(False) when the quit was refused). A restart that replaces the
process (an update, the interface switch) writes pending moves first:
flush().

A pad window does not take the keyboard focus (focus=False): a pad beside
a game is clicked with the mouse, and a key that types text or presses
keys has to reach the application that had the focus.

Deleting a pad (vpads.changed {what: "list"}) closes its window and drops
its entry; a rename retitles the window.

The pad window's page talks to the core through the same Bridge as the main
window (PadWinApi: call, image, ready) and gets the same events once its
page is ready; win_state tells it whether it is on top (a window restored
on top). Each window opened or closed is announced on the bus as
shell.pad_windows_changed {open: [pad ids]} (the desktop's list of pads
marks them). Nothing here imports pywebview: the module is passed in.
"""
import json
import logging
import os
import threading

from core.commands import CommandError
from core.vpads.model import ID_RE

log = logging.getLogger("basecamp.ui_web.pad_windows")

STATE_FILE = "vpad_windows.json"
PAGE = "padwin.html"
#: The first size: about 96 px per tile, plus the window's chrome.
TILE = 96
CHROME_W = 24
CHROME_H = 64
MIN_SIZE = (160, 120)
#: Moves and resizes are written at most once per this many seconds.
SAVE_DELAY_S = 0.5
#: Without screen information, a position farther out than this is not kept
#: (a minimised window on some systems reports -32000).
FAR = 16384
#: A window is reachable when this much of its top strip lies on a screen.
GRAB_W = 48
GRAB_H = 24

#: The pad window page's methods, as PadWinApi has them.
PADWIN_METHODS = ("call", "image", "ready", "win_close", "win_on_top", "win_state")
#: Published when a pad window opens or closes: {"open": [pad ids]}.
CHANGED_TOPIC = "shell.pad_windows_changed"


def first_size(rows, cols):
    """(width, height) a new pad window opens with."""
    try:
        rows, cols = max(1, int(rows)), max(1, int(cols))
    except (TypeError, ValueError):
        rows, cols = 3, 4
    return (max(MIN_SIZE[0], cols * TILE + CHROME_W),
            max(MIN_SIZE[1], rows * TILE + CHROME_H))


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reachable(x, y, w, s):
    sx, sy, sw, sh = s
    overlap = min(x + w, sx + sw) - max(x, sx)
    return overlap >= min(GRAB_W, w) and sy <= y and y + GRAB_H <= sy + sh


def clamp_geometry(x, y, w, h, screens):
    """(x, y, w, h) to open a window with. The size is raised to MIN_SIZE.
    With `screens` ((x, y, width, height) each): a position whose top strip
    lies on no screen moves onto the nearest one (and the size shrinks to
    fit it). Without: a position beyond FAR is dropped (x and y None)."""
    w, h = max(MIN_SIZE[0], w), max(MIN_SIZE[1], h)
    if x is None or y is None:
        return None, None, w, h
    if not screens:
        if abs(x) > FAR or abs(y) > FAR:
            return None, None, w, h
        return x, y, w, h
    if any(_reachable(x, y, w, s) for s in screens):
        return x, y, w, h
    cx, cy = x + w / 2.0, y + h / 2.0

    def distance(s):
        sx, sy, sw, sh = s
        dx = max(sx - cx, 0, cx - (sx + sw))
        dy = max(sy - cy, 0, cy - (sy + sh))
        return dx * dx + dy * dy
    sx, sy, sw, sh = min(screens, key=distance)
    w, h = min(w, sw), min(h, sh)
    x = min(max(x, sx), sx + sw - w)
    y = min(max(y, sy), sy + sh - h)
    return x, y, w, h


def _clean_entry(entry):
    out = {"open": entry.get("open") is True, "on_top": entry.get("on_top") is True}
    for k in ("x", "y"):
        v = _int(entry.get(k))
        if v is not None:
            out[k] = v
    for k in ("width", "height"):
        v = _int(entry.get(k))
        if v is not None and v > 0:
            out[k] = v
    return out


def load_state(path):
    """The state file as {pad id: entry}; ({}, False) when there is none or
    it is broken. The flag says whether anything was left out."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError):
        log.warning("%s is unreadable, starting without it", path)
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    out = {k: _clean_entry(v) for k, v in data.items()
           if isinstance(k, str) and ID_RE.fullmatch(k) and isinstance(v, dict)}
    return out, len(out) != len(data)


class _PadWin:
    """One open pad window."""

    def __init__(self, pad, window, handle, name):
        self.pad = pad
        self.window = window
        self.handle = handle
        self.name = name


class PadWinApi:
    """What pywebview exposes to a pad window's page as window.pywebview.api.
    Only the page's methods are public (pywebview exposes every public
    attribute of its js_api object)."""

    def __init__(self, owner, pad):
        self._owner = owner
        self._pad = pad
        self._win = None

    def call(self, cmd, params=None):
        return self._owner._bridge.call(cmd, params)

    def image(self, path, size=None):
        return self._owner._bridge.image(path, size)

    def ready(self):
        win = self._win
        if win is not None:
            win.handle.loaded()
        return True

    def win_close(self):
        return self._owner.close(self._pad)

    def win_on_top(self):
        return self._owner.toggle_on_top(self._pad)

    def win_state(self):
        return self._owner.win_state(self._pad)


class PadWindows:
    """The pad windows of one application run.

    app          the CoreApp (its "vpads" service and bus)
    bridge       the ui_web Bridge (add_window, call, image)
    static_dir   where padwin.html is
    webview      the pywebview module (or a stand-in)
    config_dir   where vpad_windows.json goes
    is_quitting  () -> True while the application quits (shell.quitting)
    """

    def __init__(self, app, bridge, static_dir, webview, config_dir, is_quitting=None, *,
                 save_delay=SAVE_DELAY_S, background=None):
        if background is None:
            from ui_web.app import BACKGROUND as background
        self._app = app
        self._bridge = bridge
        self._static = static_dir
        self._webview = webview
        self._path = os.path.join(config_dir, STATE_FILE)
        self._is_quitting = is_quitting or (lambda: False)
        self._save_delay = save_delay
        self._background = background
        self._lock = threading.RLock()
        self._opening = threading.Lock()
        self._state, self._dirty = load_state(self._path)
        self._open = {}                 # pad id -> _PadWin
        self._timer = None
        self._timer_gen = 0
        # The screens as restore() read them, before webview.start: after it
        # webview.screens would be asked off the toolkit's thread.
        self._screen_list = None
        self._app_quit = False
        self._sys_quit = False
        self._stopped = False
        self.writes = 0
        self._off = app.bus.subscribe("vpads.changed", self._on_pads_changed)

    # ── What the shell asks ────────────────────────────────────────────────
    def open(self, pad_id):
        """Open the pad's window, or bring it to the front when it is open:
        {"opened": True} or {"focused": True}. CommandError for an unknown
        pad."""
        if not isinstance(pad_id, str) or not ID_RE.fullmatch(pad_id):
            raise CommandError("no such pad")
        if self._quitting():
            # A window opened now would keep the toolkit running.
            raise CommandError("the application is quitting")
        with self._opening:
            with self._lock:
                win = self._open.get(pad_id)
            if win is None:
                pads = self._pads()
                if pads is None:
                    raise CommandError("the virtual pads are not available")
                pad = pads.get(pad_id)
                if pad is None:
                    raise CommandError("no such pad")
                self._create(pad)
                return {"opened": True}
        for what in ("show", "restore"):
            try:
                getattr(win.window, what)()
            except Exception:
                log.exception("pad window %s failed", what)
        return {"focused": True}

    def open_ids(self):
        with self._lock:
            return sorted(self._open)

    def close(self, pad_id):
        """A user close: the window goes and the pad stays closed. True when
        there was a window."""
        with self._lock:
            win = self._open.pop(pad_id, None)
            entry = self._state.get(pad_id)
            if entry is not None and entry.get("open"):
                entry["open"] = False
                self._write()
        if win is None:
            return False
        win.handle.remove()
        self._destroy(win.window)
        self._announce()
        return True

    def win_state(self, pad_id):
        """What the pad window's page shows of its window: {"on_top": bool}."""
        with self._lock:
            entry = self._state.get(pad_id) or {}
            return {"on_top": entry.get("on_top") is True}

    def toggle_on_top(self, pad_id):
        """Switch "always on top" of the pad's window. Returns the new value."""
        with self._lock:
            entry = self._state.get(pad_id)
            if entry is None:
                raise CommandError("that pad has no window")
            value = not entry.get("on_top")
            entry["on_top"] = value
            win = self._open.get(pad_id)
            self._write()
        if win is not None:
            try:
                win.window.on_top = value
            except Exception:
                log.exception("setting always on top failed")
        return value

    def restore(self):
        """At start: open the pads marked open where they were, drop the ones
        that no longer exist. Returns the pad ids opened."""
        with self._lock:
            if not self._state:
                if self._dirty:
                    self._write()
                return []
        with self._lock:
            placed = any(e.get("x") is not None for e in self._state.values())
        if placed and self._screen_list is None:
            self._screen_list = self._screens()
        pads = self._pads()
        if pads is None:
            return []
        with self._lock:
            for pid in [p for p in self._state if p not in pads]:
                del self._state[pid]
                self._dirty = True
            wanted = [p for p, e in self._state.items() if e.get("open")]
            if self._dirty:
                self._write()
        opened = []
        for pid in wanted:
            if self._quitting():
                break
            with self._opening:
                with self._lock:
                    if pid in self._open:
                        continue
                try:
                    self._create(pads[pid])
                    opened.append(pid)
                except Exception:
                    log.exception("reopening the window of pad %s failed", pid)
        return opened

    def app_quitting(self):
        """The main window closes the application: every pad window goes,
        and they stay marked open for the next start."""
        with self._lock:
            self._app_quit = True
            wins = list(self._open.values())
        for win in wins:
            win.handle.gone()
            # Not on this thread: it may be the toolkit's, and destroy()
            # waits for the window to have been shown.
            threading.Thread(target=self._destroy, args=(win.window,),
                             name="padwin-close", daemon=True).start()

    def system_quit(self, active):
        """A Qt-wide quit begins (True: windows closed from now on stay
        marked open) or was refused (False)."""
        with self._lock:
            self._sys_quit = bool(active)

    def flush(self):
        """Write what is pending now (moves within SAVE_DELAY_S): before
        the process is replaced."""
        with self._lock:
            if self._dirty:
                self._write()

    def stop(self):
        """At the end of the run: the last moves written, no more events."""
        off, self._off = self._off, None
        if off is not None:
            off()
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._dirty:
                self._write()

    # ── The windows ────────────────────────────────────────────────────────
    def _url(self, pad_id):
        return os.path.join(self._static, PAGE) + "#" + pad_id

    def _screens(self):
        try:
            screens = getattr(self._webview, "screens", None)
            # pywebview 6's webview.screens is a proxy of the list, which
            # also looks callable; an older one may be a function.
            try:
                items = list(screens or ())
            except TypeError:
                items = list(screens() or ())
            return [(int(s.x), int(s.y), int(s.width), int(s.height)) for s in items]
        except Exception:
            log.exception("asking for the screens failed")
            return []

    def _create(self, pad):
        pid = pad["id"]
        name = str(pad.get("name") or pid)
        with self._lock:
            entry = dict(self._state.get(pid) or {})
        w0, h0 = first_size(pad.get("rows"), pad.get("cols"))
        x, y = entry.get("x"), entry.get("y")
        x, y, w, h = clamp_geometry(x, y, entry.get("width") or w0, entry.get("height") or h0,
                                    self._screen_list or [])
        on_top = bool(entry.get("on_top"))
        api = PadWinApi(self, pid)
        # focus=False: clicks only, the keyboard stays with the application
        # that has it (Qt: WindowDoesNotAcceptFocus, GTK: accept-focus off).
        kwargs = dict(url=self._url(pid), js_api=api, frameless=True, easy_drag=False,
                      focus=False, min_size=MIN_SIZE, on_top=on_top,
                      background_color=self._background, width=w, height=h)
        if x is not None:
            kwargs["x"], kwargs["y"] = x, y
        try:
            window = self._webview.create_window(name, **kwargs)
        except TypeError as e:
            # A pywebview older than focus= (a source install's own).
            if "focus" not in str(e):
                raise
            kwargs.pop("focus", None)
            window = self._webview.create_window(name, **kwargs)
        if window is None:
            raise CommandError("the pad window could not be opened")
        win = _PadWin(pid, window, self._bridge.add_window(window), name)
        api._win = win
        with self._lock:
            self._open[pid] = win
            st = self._state.setdefault(pid, {})
            st.update(open=True, on_top=on_top, width=w, height=h)
            if x is not None:
                st["x"], st["y"] = x, y
            self._write()
            late = self._quitting()
        self._bind(win)
        self._announce()
        if late:
            # The application began to quit while this window was being made.
            win.handle.gone()
            threading.Thread(target=self._destroy, args=(window,),
                             name="padwin-close", daemon=True).start()
        return win

    def _bind(self, win):
        events = getattr(win.window, "events", None)

        def add(name, fn):
            ev = getattr(events, name, None)
            if ev is not None:
                ev += fn

        # pywebview calls a handler with the event's arguments, or with none
        # when it takes none; a parameter called "window" would get the window.
        add("loaded", lambda: win.handle.loaded())
        add("moved", lambda x=None, y=None, *_a: self._on_moved(win, x, y))
        add("resized", lambda width=None, height=None, *_a: self._on_resized(win, width, height))
        add("closing", lambda *_a: self._on_closed(win))
        add("closed", lambda *_a: self._on_closed(win))

    @staticmethod
    def _destroy(window):
        try:
            window.destroy()
        except Exception:
            log.exception("closing a pad window failed")

    def _quitting(self):
        if self._app_quit or self._sys_quit:
            return True
        try:
            return bool(self._is_quitting())
        except Exception:
            return False

    def _on_closed(self, win):
        """The window closes (pywebview's closing, then closed): by the user
        unless the application quits."""
        gone = False
        with self._lock:
            if self._open.get(win.pad) is win:
                gone = True
                del self._open[win.pad]
                entry = self._state.get(win.pad)
                if entry is not None and entry.get("open") and not self._quitting():
                    entry["open"] = False
                    self._write()
        win.handle.remove()
        if gone:
            self._announce()

    def _announce(self):
        """Tell the bus which pads are in a window now."""
        if self._stopped:
            return
        try:
            self._app.bus.publish(CHANGED_TOPIC, open=self.open_ids())
        except Exception:
            log.exception("announcing the pad windows failed")

    def _on_moved(self, win, x, y):
        x, y = _int(x), _int(y)
        if x is None or y is None:
            return
        self._geometry(win, x=x, y=y)

    def _on_resized(self, win, width, height):
        width, height = _int(width), _int(height)
        if not width or not height or width <= 0 or height <= 0:
            return
        self._geometry(win, width=width, height=height)

    def _geometry(self, win, **values):
        with self._lock:
            if self._stopped or self._open.get(win.pad) is not win or self._quitting():
                return
            entry = self._state.get(win.pad)
            if entry is None:
                return
            entry.update(values)
            self._dirty = True
            if self._timer is None:
                self._timer_gen += 1
                self._timer = threading.Timer(self._save_delay, self._timed_write,
                                              args=(self._timer_gen,))
                self._timer.daemon = True
                self._timer.start()

    def _timed_write(self, gen):
        with self._lock:
            # A timer cancelled (by a write) after it had already fired
            # must not take the place of the one started since.
            if self._timer is None or gen != self._timer_gen:
                return
            self._timer = None
            if self._dirty and not self._stopped:
                self._write()

    def _write(self):
        """Write the state now (under the lock)."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        from core.remote.pairing import save_json
        try:
            save_json(self._path, self._state)
        except OSError:
            log.exception("saving %s failed", self._path)
            return False
        self._dirty = False
        self.writes += 1
        return True

    # ── The pads ───────────────────────────────────────────────────────────
    def _pads(self):
        """{pad id: pad} from the vpads service, or None without it."""
        svc = self._app.service("vpads")
        if svc is None:
            return None
        try:
            pads = svc.list().get("pads", [])
        except Exception:
            log.exception("listing the virtual pads failed")
            return None
        return {p["id"]: p for p in pads if isinstance(p, dict) and "id" in p}

    def _on_pads_changed(self, _topic, payload):
        if not isinstance(payload, dict) or payload.get("what") != "list":
            return
        # Off the publishing thread (the scheduler's): a window's destroy()
        # may wait for it to have been shown.
        threading.Thread(target=self._sync, name="padwin-sync", daemon=True).start()

    def _sync(self):
        pads = self._pads()
        if pads is None:
            return
        renamed = []
        with self._lock:
            if self._stopped:
                return
            gone = [w for p, w in self._open.items() if p not in pads]
            for w in gone:
                del self._open[w.pad]
            dropped = [p for p in self._state if p not in pads]
            for p in dropped:
                del self._state[p]
            if dropped:
                self._write()
            for p, w in self._open.items():
                name = str(pads[p].get("name") or p)
                if name != w.name:
                    w.name = name
                    renamed.append((w, name))
        for w in gone:
            w.handle.remove()
            self._destroy(w.window)
        if gone:
            self._announce()
        for w, name in renamed:
            try:
                set_title = getattr(w.window, "set_title", None)
                if set_title is not None:
                    set_title(name)
                else:
                    w.window.title = name
            except Exception:
                log.exception("renaming a pad window failed")
