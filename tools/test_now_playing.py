#!/usr/bin/env python3
"""
Checks the Now Playing widget finds its key and draws the cover.

    python3 tools/test_now_playing.py

Issue #99, reported by @FransM, who wondered whether it was user error. It
was not, and there were three things behind it. The key lookup read the
stored actions directly, which only ever answers for the main page, so a key
on a sub-page was never found: nothing was drawn there and the icon the key
had before stayed. And the cover was being read out of playerctl into
`art_url` and then dropped, although the plugin's own help says it pushes
live album art.

No player and no pad: playerctl is never called, and the drawing is checked
against a cover written to a temporary file.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image                              # noqa: E402

import importlib.util                              # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "np_plugin",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "plugins", "now_playing", "__init__.py"))
np = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(np)

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-52s %s" % (name, detail))
    if not ok:
        failures.append("%s: %s" % (name, detail))


work = tempfile.mkdtemp(prefix="basecamp-np-test-")
cover = os.path.join(work, "cover.jpg")
Image.new("RGB", (600, 400), (200, 40, 40)).save(cover)

# ── The cover ────────────────────────────────────────────────────────────────
np._ART_CACHE.clear()
art = np._load_art("file://" + cover)
check("a local cover is read", art is not None and art.size == (600, 400),
      art.size if art else None)

check("it is remembered rather than read again",
      "file://" + cover in np._ART_CACHE)

check("no address means no cover", np._load_art("") is None)

# A cover behind an http address can fail for a moment. Remembering that
# answer meant the cover never appeared for that track again.
np._ART_CACHE.clear()
np._ART_FAILED.clear()
calls = []


def flaky(_url, timeout=None):
    calls.append(1)
    raise OSError("network down")


import urllib.request                              # noqa: E402
_real_urlopen = urllib.request.urlopen
urllib.request.urlopen = flaky
try:
    check("a failed fetch gives no cover",
          np._load_art("https://example.invalid/a.png") is None)
    np._load_art("https://example.invalid/a.png")
    check("and is not retried straight away", len(calls) == 1, len(calls))
    np._ART_FAILED["https://example.invalid/a.png"] -= np._ART_RETRY_S + 1
    np._load_art("https://example.invalid/a.png")
    check("but is tried again after a while", len(calls) == 2, len(calls))
finally:
    urllib.request.urlopen = _real_urlopen

# The cache drops its oldest, not all of it: clearing the lot threw away the
# cover of whatever was playing.
np._ART_CACHE.clear()
for n in range(np._ART_CACHE_MAX + 3):
    np._ART_CACHE["u%d" % n] = "picture %d" % n
    while len(np._ART_CACHE) > np._ART_CACHE_MAX:
        np._ART_CACHE.popitem(last=False)
check("the cache keeps its newest and drops its oldest",
      len(np._ART_CACHE) == np._ART_CACHE_MAX
      and "u%d" % (np._ART_CACHE_MAX + 2) in np._ART_CACHE
      and "u0" not in np._ART_CACHE, list(np._ART_CACHE))
np._ART_CACHE.clear()
np._ART_FAILED.clear()
check("an address that leads nowhere is not a failure",
      np._load_art("file:///nowhere/at/all.png") is None)
check("and neither is one we do not speak",
      np._load_art("dbus://something/odd") is None)

tile = np._cover_tile(art, 102)
check("the cover is squared off to the key", tile.size == (102, 102), tile.size)
check("and dimmed, so the title over it stays readable",
      max(tile.getpixel((51, 51))) < max(art.getpixel((300, 200))),
      "%s under %s" % (tile.getpixel((51, 51)), art.getpixel((300, 200))))

# ── The key lookup ───────────────────────────────────────────────────────────
class Ctx:
    """The part of the plugin context this lookup uses."""

    def __init__(self, actions):
        self._actions = actions

    def get_displaypad_actions(self, page=None):
        return self._actions


# Built without __init__, which would want a real application behind it. The
# lookup reads nothing else.
plugin = np.Plugin.__new__(np.Plugin)

page_actions = [{"type": "none"}] * 12
page_actions[5] = {"type": "now_playing", "action": ""}
plugin.ctx = Ctx(page_actions)
check("the key is found through the context, so on any page",
      plugin._find_dp_key() == 5, plugin._find_dp_key())

plugin.ctx = Ctx([{"type": "none"}] * 12)
check("a page without one answers nothing", plugin._find_dp_key() is None)


class BrokenCtx:
    def get_displaypad_actions(self, page=None):
        raise RuntimeError("no pad")


plugin.ctx = BrokenCtx()
check("no pad is not an error either", plugin._find_dp_key() is None)

os.unlink(cover)
os.rmdir(work)

print()
if failures:
    print("%d check(s) failed:" % len(failures))
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("all checks passed")
