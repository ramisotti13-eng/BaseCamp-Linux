#!/usr/bin/env python3
"""
Checks that plugin updates are noticed and shown (#103).

    python3 tools/test_plugin_updates.py

The list showed every plugin with the same green dot whether or not a newer
version was published, the count meant for the sidebar never got there, and
nothing looked for updates unless the Plugins screen was opened. Here the
plugin index and the repository download are stand-ins, so no network is
used, and the configuration goes to a temporary directory.

It needs a display. Monitor Mode is kept from starting.
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("no display, skipping the plugin update checks")
    sys.exit(0)

_TMP_HOME = tempfile.mkdtemp(prefix="basecamp-plugin-updates-test-")
os.environ["HOME"] = _TMP_HOME
_RUN = tempfile.mkdtemp(prefix="plupd-", dir="/tmp")
os.environ["XDG_RUNTIME_DIR"] = _RUN

import gui                                          # noqa: E402
from devices.everest_max import panel as emp        # noqa: E402
from devices.plugins import panel as plp            # noqa: E402

emp.EverestMaxPanel._start_cpu_auto = lambda self: None
emp.EverestMaxPanel._start_cpu_auto_clean = lambda self: None
emp.EverestMaxPanel._toggle_cpu = lambda self: None

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-58s %s" % (name, detail), flush=True)
    if not ok:
        failures.append("%s: %s" % (name, detail))


URL = "https://github.com/example/plugins/tree/main/now_playing"
INDEX = [
    {"id": "now_playing", "name": "Now Playing", "version": "9.9", "url": URL},
    {"id": "not_installed", "name": "Something Else", "version": "1.0",
     "url": "https://github.com/example/plugins/tree/main/not_installed"},
]
plp.fetch_plugin_index = lambda timeout=8: [dict(p) for p in INDEX]

# The repository as a download would unpack it: the newer Now Playing.
_REPO = tempfile.mkdtemp(prefix="plupd-repo-")
downloads = []


def _fake_download(url):
    downloads.append(url)
    tmp = tempfile.mkdtemp(prefix="plupd-zip-")
    root = os.path.join(tmp, "plugins-main")
    src = os.path.join(gui.__file__.rsplit("/", 1)[0], "plugins", "now_playing")
    shutil.copytree(src, os.path.join(root, "now_playing"))
    manifest = os.path.join(root, "now_playing", "plugin.json")
    with open(manifest) as f:
        data = json.load(f)
    data["version"] = "9.9"
    with open(manifest, "w") as f:
        json.dump(data, f)
    return tmp, root, "now_playing"


plp._download_repo = _fake_download

app = gui.App()
app.geometry("1200x800")
toasts = []
app.toast = lambda text, kind="info", ms=3500: toasts.append(text)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.03)


def checks():
    pm = app._plugin_manager
    installed = pm._manifests.get("now_playing", {}).get("version")
    check("the bundled Now Playing is installed", installed is not None, installed)

    # The application's own look, without the Plugins screen ever opened.
    app._on_plugins_fetched(plp.fetch_plugin_index(), announce=True)
    nav = app._nav_items["plugins"]._label.cget("text")
    check("the sidebar counts the update", nav == "Plugins (1)", nav)
    check("and it is said once", len(toasts) == 1, toasts)
    app._on_plugins_fetched(plp.fetch_plugin_index(), announce=True)
    check("but not again for the same update", len(toasts) == 1, toasts)

    # The screen's own fetch reports to the sidebar as well.
    nav_item = app._nav_items["plugins"]
    nav_item.set_text("Plugins")
    app._switch_device("plugins")
    pump(1.5)
    panel = app._panels["plugins"]
    check("the Plugins screen's own fetch reaches the sidebar",
          nav_item._label.cget("text") == "Plugins (1)", nav_item._label.cget("text"))
    check("the list marks the plugin with an update",
          panel._has_update("now_playing", pm._manifests["now_playing"]) is not None)
    btn = panel._update_all_btn
    check("an Update all button is offered",
          btn.winfo_ismapped() and "(1)" in btn.cget("text"), btn.cget("text"))

    panel._update_all()
    end = time.time() + 6
    while time.time() < end and pm._manifests.get("now_playing", {}).get("version") != "9.9":
        app.update()
        time.sleep(0.05)
    pump(0.5)
    check("Update all puts the newer version in place",
          pm._manifests.get("now_playing", {}).get("version") == "9.9",
          pm._manifests.get("now_playing", {}).get("version"))
    check("from one download", len(downloads) == 1, downloads)
    check("and the button goes once nothing is left to update",
          not panel._update_all_btn.winfo_ismapped())
    app._on_plugins_fetched(plp.fetch_plugin_index())
    nav = app._nav_items["plugins"]._label.cget("text")
    check("the sidebar count goes with it", nav == "Plugins", nav)


def run():
    try:
        checks()
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("raised, see the traceback above")
    finally:
        app.after(50, app.destroy)


app.after(1500, run)
app.mainloop()
for d in (_TMP_HOME, _RUN, _REPO):
    shutil.rmtree(d, ignore_errors=True)
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
