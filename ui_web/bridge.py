"""The page's way into the core.

The page (ui_web/static) holds no device logic. It asks the core for
everything with commands and hears what happens through events, and this
module is the one door for both, whichever way the page is shown:

    pywebview window   app.py hands Bridge.api to pywebview as js_api; the
                       page calls window.pywebview.api.<method>(...), events
                       are delivered with evaluate_js (see EVENT_JS)
    browser (dev)      devserver.py serves the same Bridge over HTTP and
                       server-sent events

The page's methods (JsApi, and the same names on Bridge):

    call(cmd, params)        -> the dispatch reply {ok: true, ...} or
                                {ok: false, error}; any core command
    image(path, size)        -> "data:image/png;base64,..." or None; only
                                files under the configuration, frame and
                                resources directories, pictures a
                                DisplayPad key carries and files picked in
                                this session (pick_file)
    pick_file(opts)          -> a path or None; opts {title, kind: image|any}
    open_external(url)       -> True when an http(s) URL was handed to the
                                system browser
    win_minimize(), win_toggle_maximize(), win_close()
                             -> True when there was a window to do it to
    ready()                  -> the page's event listener is in place

Events: every core event ("*") is queued by the publishing thread (a device
worker, the scheduler, the bus) and nothing else happens on that thread. A
timer thread takes the queue every 50 ms and hands it on as one batch, in
order, as a list of {"topic", "payload"}; within a batch the frequent topics
(displaypad.key_frame, *.upload_progress, *.job_progress) are coalesced to
the latest payload per key (the payload's idx or id), at the place of the
last one. Payloads are made JSON-safe on the way in. In the window a batch
becomes

    window.dispatchEvent(new CustomEvent("bc:events", {detail: batch}))

and only once the window exists and its page has loaded; before that a
batch is dropped (the page asks for the state it shows when it starts).
"""
import base64
import collections
import io
import json
import logging
import os
import threading
import urllib.parse

log = logging.getLogger("basecamp.ui_web.bridge")

#: The DOM event a batch of core events arrives as in the window.
EVENT_NAME = "bc:events"
EVENT_JS = 'window.dispatchEvent(new CustomEvent("%s", {detail: %%s}));' % EVENT_NAME

#: Topics that come often and only matter in their latest state.
FREQUENT_EXACT = ("displaypad.key_frame",)
FREQUENT_SUFFIXES = (".upload_progress", ".job_progress")

#: Largest picture image() hands out without a size (the longest side).
MAX_IMAGE_SIDE = 2048
#: The image cache: entries, and bytes of data URLs kept.
CACHE_ENTRIES = 256
CACHE_BYTES = 48 * 1024 * 1024


def is_frequent(topic):
    return topic in FREQUENT_EXACT or topic.endswith(FREQUENT_SUFFIXES)


def coalesce_key(topic, payload):
    """Events with the same key replace each other within a batch."""
    sub = None
    if isinstance(payload, dict):
        sub = payload.get("idx", payload.get("id"))
    return (topic, jsonable(sub) if sub is not None else None)


def jsonable(value, _depth=0):
    """`value` as something json.dumps takes: dicts with string keys, lists,
    strings, numbers, booleans and None. Sets become sorted lists, bytes
    their length, anything else its str()."""
    if _depth > 20:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, dict):
        return {str(k): jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v, _depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [jsonable(v, _depth + 1) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value)}
    return str(value)


def coalesce(events):
    """`events` with every frequent topic reduced to its last payload per
    key, kept where that last one was."""
    last = {}
    for i, ev in enumerate(events):
        if is_frequent(ev["topic"]):
            last[coalesce_key(ev["topic"], ev["payload"])] = i
    if not last:
        return list(events)
    keep = set(last.values())
    return [ev for i, ev in enumerate(events)
            if i in keep or not is_frequent(ev["topic"])]


class EventForwarder:
    """Core events to whoever shows them, in batches, off the publishers'
    threads.

    `_on_event` is what the bus calls, on whatever thread published: it makes
    the payload JSON-safe, appends it under a lock and returns. A timer
    thread flushes every `interval` seconds to the sinks, `sink(batch)`; a
    sink that is slow only delays the next batch, never a publisher. The
    queue is bounded: past `max_queue` the oldest events are dropped.
    """

    def __init__(self, bus, interval=0.05, max_queue=10000, pattern="*"):
        self._bus = bus
        self.interval = interval
        self._pattern = pattern
        self._lock = threading.Lock()
        self._queue = collections.deque(maxlen=max_queue)
        self._sinks = []
        self._unsubscribe = None
        self._stop = threading.Event()
        self._thread = None
        self.dropped = 0
        self.batches = 0

    # ── Sinks ──────────────────────────────────────────────────────────────
    def add_sink(self, sink):
        """Hand every batch to `sink(batch)`. Returns the function that
        removes it."""
        with self._lock:
            self._sinks.append(sink)

        def remove():
            with self._lock:
                if sink in self._sinks:
                    self._sinks.remove(sink)
        return remove

    # ── Lifecycle ──────────────────────────────────────────────────────────
    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self._unsubscribe is None:
            self._unsubscribe = self._bus.subscribe(self._pattern, self._on_event)
        if not self.running:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="web-bridge-events")
            self._thread.start()

    def stop(self, timeout=2.0):
        """Stop listening and flushing. Never raises; events still queued
        are dropped."""
        unsub, self._unsubscribe = self._unsubscribe, None
        if unsub is not None:
            unsub()
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
        with self._lock:
            self._queue.clear()

    # ── The two threads ────────────────────────────────────────────────────
    def _on_event(self, topic, payload):
        ev = {"topic": topic, "payload": jsonable(payload)}
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(ev)

    def take(self):
        """The queued events as a batch (coalesced), leaving the queue empty."""
        with self._lock:
            if not self._queue:
                return []
            events = list(self._queue)
            self._queue.clear()
        return coalesce(events)

    def flush(self):
        """Hand the queued events to the sinks now. Returns the batch."""
        batch = self.take()
        if not batch:
            return batch
        self.batches += 1
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink(batch)
            except Exception:
                log.exception("an event sink failed")
        return batch

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.flush()
            except Exception:
                log.exception("flushing events failed")


def default_opener():
    """How a link reaches the system browser. On Linux xdg-open with the
    bundle's environment taken back (core.platform.launcher.open_target):
    Python's webbrowser hands the AppImage's LD_LIBRARY_PATH to kde-open,
    which then loads the bundled Qt instead of the system's and dies (#49),
    so most links did nothing. Elsewhere webbrowser.open."""
    import sys
    if sys.platform.startswith("linux"):
        from core.platform.launcher import open_target
        return open_target
    import webbrowser
    return webbrowser.open


def default_allowed_dirs():
    """Where image() may read: the configuration directory (key pictures,
    generated icons, the libraries, imported device photos), the widget
    frame directory and the shipped resources."""
    from shared.config import CONFIG_DIR, FRAME_DIR
    from core import runtime
    dirs = [CONFIG_DIR, FRAME_DIR,
            os.path.join(runtime.RES, "resources"),
            os.path.join(runtime.HERE, "resources")]
    out = []
    for d in dirs:
        real = _norm(d)
        if real not in out:
            out.append(real)
    return out


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def _under(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


class Bridge:
    """The core for the page: commands, events, pictures, the file picker,
    links and the window.

    core         a CoreApp
    picker       (title, kind) -> path or None, the native file dialog
                 (None: there is none, pick_file answers None)
    opener       (url) -> None, opens a URL in the system browser
    window       an object with minimize(), toggle_maximize(), close()
                 (app.py's window controls), or None
    allowed_dirs where image() may read (default: default_allowed_dirs())
    """

    def __init__(self, core, *, interval=0.05, picker=None, opener=None,
                 window=None, allowed_dirs=None, cache_entries=CACHE_ENTRIES,
                 cache_bytes=CACHE_BYTES):
        self._core = core
        self.events = EventForwarder(core.bus, interval=interval)
        self._picker = picker
        self._opener = opener
        self._window = window
        self._dirs = ([_norm(d) for d in allowed_dirs] if allowed_dirs is not None
                      else default_allowed_dirs())
        self._picked = set()
        self._cache = collections.OrderedDict()
        self._cache_size = 0
        self._cache_lock = threading.Lock()
        self._cache_entries = cache_entries
        self._cache_bytes = cache_bytes
        self.cache_hits = 0
        # The webview: set by attach_webview(); events go to it once its
        # page has loaded.
        self._webview = None
        self._page_ready = False
        self._remove_webview_sink = None
        self.api = JsApi(self)

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def start(self):
        self.events.start()
        return self

    def stop(self):
        self._page_ready = False
        self.events.stop()

    # ── The pywebview window ───────────────────────────────────────────────
    def attach_webview(self, webview_window, controls=None):
        """Deliver events to a pywebview window (from its page's load on)
        and use `controls` for the window buttons."""
        self._webview = webview_window
        if controls is not None:
            self._window = controls
        if self._remove_webview_sink is None:
            self._remove_webview_sink = self.events.add_sink(self._to_webview)

    def page_loaded(self):
        """The window's page has loaded (pywebview's loaded event, or the
        page's own ready())."""
        self._page_ready = True

    def page_gone(self):
        """The window is closing or loading another page: no evaluate_js."""
        self._page_ready = False

    def _to_webview(self, batch):
        w = self._webview
        if w is None or not self._page_ready:
            return
        js = EVENT_JS % json.dumps(batch, ensure_ascii=True)
        run = getattr(w, "run_js", None) or w.evaluate_js
        run(js)

    # ── What the page calls ────────────────────────────────────────────────
    def call(self, cmd, params=None):
        """Run a core command: the dispatch reply, JSON-safe."""
        if not isinstance(cmd, str) or not cmd:
            return {"ok": False, "error": "missing \"cmd\""}
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {"ok": False, "error": "the parameters must be an object"}
        obj = dict(params)
        obj["cmd"] = cmd
        try:
            reply = self._core.dispatch(obj)
        except Exception as e:          # dispatch() does not raise; belt and braces
            log.exception("command %s failed", cmd)
            reply = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return jsonable(reply)

    def ready(self):
        self.page_loaded()
        return True

    # Pictures.

    def allowed(self, path):
        """May image() read `path`?"""
        if not isinstance(path, str) or not path or "\x00" in path:
            return False
        real = _norm(path)
        if any(_under(real, d) for d in self._dirs):
            return True
        if real in self._picked:
            return True
        pad = self._core.service("displaypad")
        pictures = getattr(pad, "pictures", None)
        if pictures is not None:
            try:
                return real in {_norm(p) for p in pictures()}
            except Exception:
                log.exception("asking the DisplayPad for its pictures failed")
        return False

    def image(self, path, size=None):
        """A PNG data URL of the picture at `path`, its longest side at most
        `size` pixels (MAX_IMAGE_SIDE without one), or None when it may not
        be read or is not a picture. A GIF gives its first frame."""
        try:
            size = int(size) if size not in (None, "", 0) else None
        except (TypeError, ValueError):
            size = None
        if size is not None:
            size = max(8, min(size, MAX_IMAGE_SIDE))
        if not self.allowed(path):
            return None
        real = os.path.realpath(path)
        try:
            st = os.stat(real)
        except OSError:
            return None
        key = (real, size, st.st_mtime_ns, st.st_size)
        with self._cache_lock:
            url = self._cache.get(key)
            if url is not None:
                self._cache.move_to_end(key)
                self.cache_hits += 1
                return url
        url = self._render(real, size)
        if url is None:
            return None
        with self._cache_lock:
            if key not in self._cache:
                self._cache[key] = url
                self._cache_size += len(url)
            while self._cache and (len(self._cache) > self._cache_entries
                                   or self._cache_size > self._cache_bytes):
                _k, old = self._cache.popitem(last=False)
                self._cache_size -= len(old)
        return url

    @staticmethod
    def _render(path, size):
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.seek(0)
                im = im.convert("RGBA")
            side = size or MAX_IMAGE_SIDE
            if max(im.size) > side:
                im.thumbnail((side, side), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
        except Exception:
            return None
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    # The file picker, links, the window.

    def pick_file(self, opts=None):
        """A path the user picked, or None. opts: {title, kind: image|any|folder|save,
        name: the suggested file name of a save dialog}."""
        opts = opts if isinstance(opts, dict) else {}
        if self._picker is None:
            return None
        kind = opts.get("kind") if opts.get("kind") in ("image", "any", "folder", "save") else "image"
        args = [str(opts.get("title") or ""), kind]
        if kind == "save":
            args.append(os.path.basename(str(opts.get("name") or "")))
        try:
            path = self._picker(*args)
        except Exception:
            log.exception("the file dialog failed")
            return None
        if not path or not isinstance(path, str):
            return None
        self._picked.add(_norm(path))
        return path

    def open_external(self, url):
        """Open an http(s) URL in the system browser. Anything else is
        refused (False)."""
        if not isinstance(url, str):
            return False
        try:
            parts = urllib.parse.urlsplit(url.strip())
        except ValueError:
            return False
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        opener = self._opener or default_opener()
        try:
            opener(url.strip())
        except Exception:
            log.exception("opening %s failed", url)
            return False
        return True

    def _window_do(self, what):
        w = self._window
        fn = getattr(w, what, None) if w is not None else None
        if fn is None:
            return False
        try:
            fn()
        except Exception:
            log.exception("window %s failed", what)
            return False
        return True

    def win_minimize(self):
        return self._window_do("minimize")

    def win_toggle_maximize(self):
        return self._window_do("toggle_maximize")

    def win_close(self):
        return self._window_do("close")


class JsApi:
    """What pywebview exposes to the page as window.pywebview.api: only the
    page's methods. (pywebview exposes every public attribute of its js_api
    object, so the bridge's own lifecycle stays on Bridge.)"""

    def __init__(self, bridge):
        self._bridge = bridge

    def call(self, cmd, params=None):
        return self._bridge.call(cmd, params)

    def image(self, path, size=None):
        return self._bridge.image(path, size)

    def pick_file(self, opts=None):
        return self._bridge.pick_file(opts)

    def open_external(self, url):
        return self._bridge.open_external(url)

    def win_minimize(self):
        return self._bridge.win_minimize()

    def win_toggle_maximize(self):
        return self._bridge.win_toggle_maximize()

    def win_close(self):
        return self._bridge.win_close()

    def ready(self):
        return self._bridge.ready()


#: The page's methods, as JsApi has them.
JS_METHODS = ("call", "image", "pick_file", "open_external", "win_minimize",
              "win_toggle_maximize", "win_close", "ready")
