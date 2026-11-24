"""A virtual pad: its grid, pages and keys, stored one folder per pad.

A pad is a grid of up to MAX_ROWS x MAX_COLS keys with named pages (page 0 is
"Main"). A key's index is `row * MAX_COLS + col`, whatever size the grid has,
so shrinking and growing the grid never moves a key: keys outside the grid
stay in the file and come back when the grid grows again.

`PadPageModel` shows a pad to the DisplayPad's `ActionRunner` with the
semantics of `PageModel` (page targets by name, an int id also accepted), so
a pad key runs through the same code as a hardware key.

Files: `<root>/<id>/pad.json`, written to a temp file and renamed so a crash
never leaves half a file. A deleted pad moves to `<root>/.trash`.
"""
import json
import logging
import os
import re
import secrets
import shutil
import time

log = logging.getLogger("basecamp.core.vpads")

MAX_ROWS = 6
MAX_COLS = 8
MAX_NAME = 40
PAD_FILE = "pad.json"
NO_ACTION = {"type": "none", "action": ""}
ID_RE = re.compile(r"p[0-9a-f]{8}")


def _clamp(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = lo
    return max(lo, min(hi, v))


class Pad:
    def __init__(self, pad_id, name, rows, cols, pages=None):
        self.id = pad_id
        self.name = str(name or "")[:MAX_NAME]
        self.rows = _clamp(rows, 1, MAX_ROWS)
        self.cols = _clamp(cols, 1, MAX_COLS)
        self.pages = pages if pages else {0: {"name": "Main", "keys": {}}}
        self.current_page = 0       # not stored

    def in_grid(self, idx):
        row, col = divmod(idx, MAX_COLS)
        return 0 <= idx and row < self.rows and col < self.cols

    def key(self, idx, page=None):
        """The key with every field present (defaults when unset)."""
        page = self.current_page if page is None else page
        raw = (self.pages.get(page) or {}).get("keys", {}).get(idx)
        if not isinstance(raw, dict):
            raw = {}
        act = raw.get("action")
        return {
            "image": raw.get("image") or "",
            "text": raw.get("text") or "",
            "text_style": raw.get("text_style") or {},
            "action": dict(act) if isinstance(act, dict) else dict(NO_ACTION),
        }

    def as_dict(self, page=None):
        page = self.current_page if page is None else page
        keys = (self.pages.get(page) or {}).get("keys", {})
        return {
            "id": self.id, "name": self.name, "rows": self.rows, "cols": self.cols,
            "pages": [{"id": i, "name": pg.get("name", "")} for i, pg in sorted(self.pages.items())],
            "current_page": self.current_page,
            "keys": {str(i): self.key(i, page) for i in sorted(keys) if self.in_grid(i)},
        }

    def to_json(self):
        return {
            "id": self.id, "name": self.name, "rows": self.rows, "cols": self.cols,
            "pages": {str(i): {"name": pg.get("name", ""),
                               "keys": {str(k): v for k, v in pg.get("keys", {}).items()}}
                      for i, pg in self.pages.items()},
        }

    @classmethod
    def from_json(cls, data):
        pages = {}
        for i, pg in (data.get("pages") or {}).items():
            pages[int(i)] = {"name": str(pg.get("name", "")),
                             "keys": {int(k): v for k, v in (pg.get("keys") or {}).items()}}
        if 0 not in pages:
            pages[0] = {"name": "Main", "keys": {}}
        return cls(str(data["id"]), data.get("name"), data.get("rows"), data.get("cols"), pages)


class PadStore:
    def __init__(self, root):
        self.root = root

    def pad_dir(self, pad_id):
        """The pad's folder; every path goes through here, so only ids
        create() makes are accepted (an id may arrive from a phone)."""
        if not isinstance(pad_id, str) or not ID_RE.fullmatch(pad_id):
            raise ValueError("bad pad id: %r" % (pad_id,))
        return os.path.join(self.root, pad_id)

    def _file(self, pad_id):
        return os.path.join(self.pad_dir(pad_id), PAD_FILE)

    def load(self, pad_id):
        with open(self._file(pad_id), "r", encoding="utf-8") as f:
            pad = Pad.from_json(json.load(f))
        if pad.id != pad_id:
            raise ValueError("stored id %s differs from folder %s" % (pad.id, pad_id))
        return pad

    def save(self, pad):
        os.makedirs(self.pad_dir(pad.id), exist_ok=True)
        path = self._file(pad.id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pad.to_json(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def create(self, name, rows, cols):
        pad = Pad("p" + secrets.token_hex(4), name, rows, cols)
        self.save(pad)
        return pad

    def list(self):
        """Every readable pad; a folder with a broken pad.json is skipped."""
        pads = []
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return pads
        for n in names:
            if not ID_RE.fullmatch(n) or not os.path.isfile(self._file(n)):
                continue
            try:
                pads.append(self.load(n))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                log.warning("virtual pad %s skipped: %s", n, e)
        return pads

    def delete(self, pad_id):
        src = self.pad_dir(pad_id)
        if not os.path.isdir(src):
            return
        trash = os.path.join(self.root, ".trash")
        os.makedirs(trash, exist_ok=True)
        shutil.move(src, os.path.join(trash, "%s-%d-%s" % (pad_id, int(time.time()), secrets.token_hex(2))))


class PadPageModel:
    """What ActionRunner asks of a PageModel, for one pad."""

    prev_page = 0
    page_timeout = {}       # step 1: no page timeouts on a pad

    def __init__(self, pad):
        self.pad = pad

    @property
    def current_page(self):
        return self.pad.current_page

    def get_action_dict(self, idx, page=None):
        return self.pad.key(idx, page)["action"]

    def get_extra_actions(self, idx):
        extra = self.get_action_dict(idx).get("actions")
        return extra if isinstance(extra, list) else []

    def get_double_action(self, idx):
        d = self.get_action_dict(idx).get("double")
        if isinstance(d, dict) and d.get("type", "none") not in ("none", ""):
            return d
        return None

    def page_target(self, step, idx):
        """Page id a 'page' step goes to: by name, or an id; else Main."""
        t = (step or {}).get("target")
        if isinstance(t, str):
            for pid, pg in self.pad.pages.items():
                if pg.get("name") == t:
                    return pid
        try:
            pid = int(t)
        except (TypeError, ValueError):
            return 0
        return pid if pid in self.pad.pages else 0

    def timeout_target(self, page):
        return 0
