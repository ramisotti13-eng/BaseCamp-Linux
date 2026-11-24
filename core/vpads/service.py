"""The `vpads` service: user-built virtual pads a phone can press.

A pad is a grid of keys with pages (core/vpads/model.py). A press does not
get a mechanism of its own: it goes through the DisplayPad's `ActionRunner`,
so a virtual key runs the same action types, "also on press" chains, double
clicks, page switches and `set_key` as a hardware key, through the one key
action dispatcher (the macro service's). One runner per pad, made when the
pad is first pressed.

Threading: every command runs on the core scheduler, like the DisplayPad's
(`_on_scheduler`): at once when the caller already is on it, else posted and
waited for, so list/keys/create can return their data and the model is only
ever touched from one thread. `pictures()` is a plain method with the same
rule (the bridge asks from its own thread).

Events (`vpads.*`) say that something changed and where, never what an
action does: an action's value may be a shell command or a typed text, and
the events go to every connected phone.
"""
import os
import re
import shutil
import threading

from core.commands import CommandError
from core.devices.displaypad.actions import (
    ActionRunner, DispatcherActionEnv, parse_set_key,
)
from core.service import Service
from core.vpads.model import MAX_COLS, MAX_NAME, MAX_ROWS, PadPageModel, PadStore


PICTURE_FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "BMP")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean_text_style(style):
    """A key's text style as stored, the way the DisplayPad cleans a key's
    text (images.clean_text_overlay): only its known keys, each kept only
    with a good value. `font` one of the families the system has
    (images.list_fonts), `pos` top|bottom|center, `color` white|black or
    #rrggbb, `size_pt` an integer clamped to 6..18, bold, italic and
    underline as booleans. It reaches the desktop's page as a style, so
    nothing else gets through."""
    from core.devices.displaypad import images as dpi
    out = {}
    font = style.get("font")
    if isinstance(font, str) and font.strip() in dpi.list_fonts():
        out["font"] = font.strip()
    if style.get("pos") in dpi.TEXT_POSITIONS:
        out["pos"] = style["pos"]
    color = style.get("color")
    if isinstance(color, str) and (color in dpi.TEXT_COLORS or _HEX_COLOR.match(color)):
        out["color"] = color
    if "size_pt" in style and not isinstance(style["size_pt"], bool):
        try:
            size = int(style["size_pt"])
        except (TypeError, ValueError, OverflowError):
            size = None
        if size is not None:
            out["size_pt"] = max(dpi.TEXT_SIZE_MIN, min(dpi.TEXT_SIZE_MAX, size))
    for flag in ("bold", "italic", "underline"):
        if flag in style:
            out[flag] = bool(style[flag])
    return out


class VpadsEnv(DispatcherActionEnv):
    OWNER = "vpads"


def _int(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CommandError("%s must be a number" % what)
    try:
        return int(value)
    except ValueError:
        raise CommandError("%s must be a number" % what) from None


class VpadsService(Service):
    name = "vpads"

    def __init__(self, app, root, env=None):
        super().__init__(app)
        self.root = root
        self.store = PadStore(root)
        self.env = env or VpadsEnv(self._dispatcher)
        self._pads = None           # id -> Pad, read on first use
        self._runners = {}          # id -> ActionRunner

    # ── Plumbing ──────────────────────────────────────────────────────────────

    def _dispatcher(self):
        actions = getattr(self.app.service("macros"), "actions", None)
        if actions is not None:
            return actions
        if getattr(self, "_own_actions", None) is None:
            from core.actions import ActionDispatcher
            self._own_actions = ActionDispatcher()
        return self._own_actions

    def _on_scheduler(self, fn, *args, timeout=5.0):
        if self.app.scheduler.in_scheduler_thread():
            return fn(*args)
        done = threading.Event()
        box = {}

        def run():
            try:
                box["result"] = fn(*args)
            except Exception as e:       # handed back to the caller
                box["error"] = e
            finally:
                done.set()
        self.app.scheduler.call_soon(run)
        if not done.wait(timeout):
            raise CommandError("the virtual pads did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _all(self):
        if self._pads is None:
            self._pads = {p.id: p for p in self.store.list()}
        return self._pads

    def _pad(self, pad_id):
        pad = self._all().get(pad_id) if isinstance(pad_id, str) else None
        if pad is None:
            raise CommandError("no such pad")
        return pad

    def _page(self, pad, page):
        page = _int(page, "page")
        if page not in pad.pages:
            raise CommandError("no such page")
        return page

    def _key(self, pad, key):
        key = _int(key, "key")
        if not pad.in_grid(key):
            raise CommandError("that key is outside the pad's grid")
        return key

    def _save(self, pad):
        try:
            self.store.save(pad)
        except OSError as e:
            raise CommandError("the pad could not be saved: %s" % e) from None

    def _changed(self, pad, what):
        self.publish("changed", pad=pad.id, what=what)

    @staticmethod
    def _name(name):
        name = str(name or "").strip()[:MAX_NAME]
        if not name:
            raise CommandError("a name is needed")
        return name

    def stop(self):
        for r in self._runners.values():
            r.cancel_double_clicks()
        self._runners.clear()

    # ── Pads ──────────────────────────────────────────────────────────────────

    def list(self):
        def run():
            out = []
            for p in self._all().values():
                d = p.as_dict()
                d.pop("keys", None)
                out.append(d)
            return {"pads": out}
        return self._on_scheduler(run)

    def create(self, name, rows=3, cols=4):
        def run():
            try:
                pad = self.store.create(self._name(name), rows, cols)
            except OSError as e:
                raise CommandError("the pad could not be created: %s" % e) from None
            self._all()[pad.id] = pad
            self._changed(pad, "list")
            return {"pad": pad.as_dict()}
        return self._on_scheduler(run)

    def rename(self, pad, name):
        def run():
            p = self._pad(pad)
            p.name = self._name(name)
            self._save(p)
            self._changed(p, "list")
        self._on_scheduler(run)

    def delete(self, pad):
        def run():
            p = self._pad(pad)
            try:
                self.store.delete(p.id)
            except OSError as e:
                raise CommandError("the pad could not be deleted: %s" % e) from None
            r = self._runners.pop(p.id, None)
            if r is not None:
                r.cancel_double_clicks()
            del self._all()[p.id]
            self._changed(p, "list")
        self._on_scheduler(run)

    def set_grid(self, pad, rows, cols):
        def run():
            p = self._pad(pad)
            r, c = _int(rows, "rows"), _int(cols, "cols")
            if not (1 <= r <= MAX_ROWS and 1 <= c <= MAX_COLS):
                raise CommandError("the grid is 1 to %d rows and 1 to %d columns"
                                   % (MAX_ROWS, MAX_COLS))
            p.rows, p.cols = r, c
            self._save(p)
            self._changed(p, "grid")
        self._on_scheduler(run)

    def keys(self, pad, page=None):
        def run():
            p = self._pad(pad)
            return p.as_dict(None if page is None else self._page(p, page))
        return self._on_scheduler(run)

    def pictures(self):
        """Every image path the pads use."""
        def run():
            out = set()
            for p in self._all().values():
                for pg in p.pages.values():
                    for k in pg.get("keys", {}).values():
                        if isinstance(k, dict) and k.get("image"):
                            out.add(k["image"])
            return out
        return self._on_scheduler(run)

    # ── Keys ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_picture(path):
        """True for a real PNG/JPEG/GIF/WEBP/BMP file. A path may come from
        a phone, so nothing else is copied into a pad (and served back)."""
        try:
            from PIL import Image
            if not os.path.isfile(path):
                return False
            with Image.open(path) as im:
                if im.format not in PICTURE_FORMATS:
                    return False
                im.verify()
            return True
        except Exception:
            return False

    def _adopt_image(self, pad, path):
        """The path of `path` inside the pad's folder, copying it there."""
        if not path:
            return ""
        if not isinstance(path, str):
            raise CommandError("image must be a path")
        src = os.path.abspath(path)
        dest_dir = os.path.abspath(self.store.pad_dir(pad.id))
        if not self._is_picture(src):
            raise CommandError("that file is not a picture")
        if os.path.dirname(src) == dest_dir:
            return src
        base = os.path.basename(src)
        dest = os.path.join(dest_dir, base)
        n = 1
        while os.path.exists(dest):
            stem, ext = os.path.splitext(base)
            dest = os.path.join(dest_dir, "%s-%d%s" % (stem, n, ext))
            n += 1
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copyfile(src, dest)
        except OSError as e:
            raise CommandError("the picture could not be copied: %s" % e) from None
        return dest

    def set_key(self, pad, key, page=None, image=None, text=None,
                text_style=None, action=None):
        def run():
            p = self._pad(pad)
            idx = self._key(p, key)
            pg = p.current_page if page is None else self._page(p, page)
            if text is not None and not isinstance(text, str):
                raise CommandError("text must be a string")
            if text_style is not None and not isinstance(text_style, dict):
                raise CommandError("text_style must be an object")
            if action is not None and not (
                    isinstance(action, dict) and isinstance(action.get("type", "none"), str)):
                raise CommandError("action must be an object with a type")
            cur = dict(p.pages[pg]["keys"].get(idx) or {})
            if image is not None:
                cur["image"] = self._adopt_image(p, image)
            if text is not None:
                cur["text"] = text
            if text_style is not None:
                cur["text_style"] = clean_text_style(text_style)
            if action is not None:
                cur["action"] = dict(action)
            p.pages[pg]["keys"][idx] = cur
            self._save(p)
            self._changed(p, "keys")
        self._on_scheduler(run)

    def clear_key(self, pad, key, page=None):
        def run():
            p = self._pad(pad)
            idx = self._key(p, key)
            pg = p.current_page if page is None else self._page(p, page)
            p.pages[pg]["keys"].pop(idx, None)
            self._save(p)
            self._changed(p, "keys")
        self._on_scheduler(run)

    # ── Pressing ──────────────────────────────────────────────────────────────

    def _runner(self, pad):
        r = self._runners.get(pad.id)
        if r is None:
            r = ActionRunner(
                PadPageModel(pad), self.app.scheduler, self.env,
                switch_page=lambda page, _hints, p=pad: self._go(p, page),
                set_key=lambda action_json, idx, p=pad: self._runner_set_key(p, action_json, idx))
            self._runners[pad.id] = r
        return r

    def _go(self, pad, page):
        if page not in pad.pages or pad.id not in self._all():
            return
        runner = self._runners.get(pad.id)
        if runner is not None:
            runner.cancel_double_clicks()
        pad.current_page = page
        self.publish("page", pad=pad.id, page=page)

    def _runner_set_key(self, pad, action_json, host_idx):
        """A 'set_key' action: redefine the action of a key of this pad."""
        parsed = parse_set_key(action_json, pad.current_page, host_idx)
        if parsed is None:
            return
        page, key, btype, action = parsed
        if page not in pad.pages or not pad.in_grid(key):
            return
        cur = dict(pad.pages[page]["keys"].get(key) or {})
        cur["action"] = {"type": btype, "action": action}
        pad.pages[page]["keys"][key] = cur
        try:
            self.store.save(pad)
        except OSError:
            pass
        self._changed(pad, "keys")

    def press(self, pad, key):
        def run():
            p = self._pad(pad)
            idx = self._key(p, key)
            self.publish("pressed", pad=p.id, key=idx)
            self._runner(p).key_pressed(idx)
        self._on_scheduler(run)

    # ── Pages ─────────────────────────────────────────────────────────────────

    def switch_page(self, pad, page):
        def run():
            p = self._pad(pad)
            self._go(p, self._page(p, page))
        self._on_scheduler(run)

    def create_page(self, pad, name):
        def run():
            p = self._pad(pad)
            pid = max(p.pages) + 1
            p.pages[pid] = {"name": self._name(name), "keys": {}}
            self._save(p)
            self._changed(p, "pages")
            return {"page": pid}
        return self._on_scheduler(run)

    def rename_page(self, pad, page, name):
        def run():
            p = self._pad(pad)
            p.pages[self._page(p, page)]["name"] = self._name(name)
            self._save(p)
            self._changed(p, "pages")
        self._on_scheduler(run)

    def delete_page(self, pad, page):
        def run():
            p = self._pad(pad)
            pg = self._page(p, page)
            if pg == 0:
                raise CommandError("the first page cannot be deleted")
            del p.pages[pg]
            if p.current_page == pg:
                self._go(p, 0)
            self._save(p)
            self._changed(p, "pages")
        self._on_scheduler(run)

    def commands(self):
        return {n: getattr(self, n) for n in (
            "list", "create", "rename", "delete", "set_grid", "keys", "set_key",
            "clear_key", "press", "switch_page", "create_page", "rename_page",
            "delete_page")}
