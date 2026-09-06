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
np._ART_FAILED.clear()
art = np._load_art("file://" + cover)
check("a local cover is read", art is not None)
check("and comes back as the two tiles that get drawn, nothing bigger",
      art.key.size == (np._KEY_TILE, np._KEY_TILE)
      and art.card.size == (np._CARD_TILE, np._CARD_TILE),
      (art.key.size, art.card.size))

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
# The address question is a separate one, checked below; here the address is
# taken as allowed so the retry backoff is what is under the light.
_real_allowed = np._art_host_allowed
np._art_host_allowed = lambda _url: True
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
    np._art_host_allowed = _real_allowed

# The failed-address record drops its oldest too. Clearing the lot wiped the
# entry just written, so the next poll two seconds later tried the same dead
# address again instead of waiting out the retry.
np._ART_FAILED.clear()
urllib.request.urlopen = flaky
np._art_host_allowed = lambda _url: True
try:
    for n in range(np._ART_FAILED_MAX + 2):
        np._load_art("https://example%d.invalid/a.png" % n)
    calls_before = len(calls)
    np._load_art("https://example%d.invalid/a.png" % (np._ART_FAILED_MAX + 1))
    check("the newest failure is still remembered after an overflow",
          len(calls) == calls_before, len(calls) - calls_before)
    check("and the record is bounded",
          len(np._ART_FAILED) <= np._ART_FAILED_MAX, len(np._ART_FAILED))
finally:
    urllib.request.urlopen = _real_urlopen
    np._art_host_allowed = _real_allowed
np._ART_CACHE.clear()
np._ART_FAILED.clear()
check("an address that leads nowhere is not a failure",
      np._load_art("file:///nowhere/at/all.png") is None)
check("and neither is one we do not speak",
      np._load_art("dbus://something/odd") is None)

check("the key's tile is dimmed, so the title over it stays readable",
      max(art.key.getpixel((51, 51))) < max(art.card.getpixel((95, 95))),
      "%s under %s" % (art.key.getpixel((51, 51)), art.card.getpixel((95, 95))))

# The address is not the player's own idea: any page playing media sets it
# through the Media Session API. Fetching whatever it names would let a web
# page reach addresses on this network that it cannot reach itself.
for bad in ("http://127.0.0.1/admin", "http://192.168.1.1/reboot",
            "http://10.0.0.1/", "http://[::1]/", "http://169.254.1.1/"):
    check("an address on this machine or network is refused",
          np._art_host_allowed(bad) is False, bad)
check("and a name that resolves nowhere is refused too",
      np._art_host_allowed("http://no.such.host.invalid/a.png") is False)

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

# ── Stopping and starting again ──────────────────────────────────────────────
# The application brings a page's services in line whenever a key changes, not
# only on a page switch, so stop-then-start is ordinary. start() never reset
# the stop, so the new thread found it already set and returned: nothing was
# drawn for the rest of the session, which is the symptom this issue is about.
threads = []
_real_thread = np.threading.Thread


def spy(*a, **kw):
    t = _real_thread(*a, **kw)
    t.start = lambda: threads.append(kw.get("args", ()))
    return t


live = np.Plugin.__new__(np.Plugin)
live._stop = np.threading.Event()
np.threading.Thread = spy
try:
    live.start()
    first = live._stop
    live.stop()
    live.start()
finally:
    np.threading.Thread = _real_thread

check("it is startable again after a stop", not live._stop.is_set())
check("and does not revive the thread it stopped", first.is_set())
check("each thread has its own stop",
      len(threads) == 2 and threads[0][0] is not threads[1][0]
      and threads[1][0] is live._stop, threads)

os.unlink(cover)
os.rmdir(work)

print()
if failures:
    print("%d check(s) failed:" % len(failures))
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("all checks passed")
