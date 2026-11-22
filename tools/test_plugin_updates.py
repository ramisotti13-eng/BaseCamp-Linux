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

import customtkinter as ctk                         # noqa: E402
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
PUBLISHED = {"version": "9.9"}
_ZIPS = []      # a single install clears its download after five seconds


def _fake_download(url):
    downloads.append(url)
    tmp = tempfile.mkdtemp(prefix="plupd-zip-")
    _ZIPS.append(tmp)
    root = os.path.join(tmp, "plugins-main")
    src = os.path.join(gui.__file__.rsplit("/", 1)[0], "plugins", "now_playing")
    shutil.copytree(src, os.path.join(root, "now_playing"))
    manifest = os.path.join(root, "now_playing", "plugin.json")
    with open(manifest) as f:
        data = json.load(f)
    data["version"] = PUBLISHED["version"]
    with open(manifest, "w") as f:
        json.dump(data, f)
    return tmp, root, "now_playing"


plp._download_repo = _fake_download

app = gui.App()
app.geometry("1200x800")
toasts = []
app.toast = lambda text, kind="info", ms=3500, action=None: toasts.append((text, action))


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
    # Started into the tray, as autostart does: a message now would be
    # shown to nobody.
    app.withdraw()
    pump(0.3)
    app._on_plugins_fetched(plp.fetch_plugin_index(), announce=True)
    nav_item = app._nav_items["plugins"]
    check("the sidebar shows the count as a badge beside Plugins",
          nav_item.badge() == 1 and nav_item._label.cget("text") == "Plugins",
          (nav_item.badge(), nav_item._label.cget("text")))
    check("nothing is said while the window is in the tray", toasts == [], toasts)
    app.deiconify()
    pump(1.0)
    check("it is said once the window is opened", len(toasts) == 1, toasts)
    text, action = toasts[0] if toasts else ("", None)
    check("with a button that leads to the Plugins screen",
          action is not None and action[0] == app.T("plugin_updates_show"), action)
    check("the badge is drawn", nav_item._badge is not None
          and nav_item._badge.winfo_ismapped())
    app._on_plugins_fetched(plp.fetch_plugin_index(), announce=True)
    check("but not again for the same update", len(toasts) == 1, toasts)
    if action:
        action[1]()
        pump(0.5)
    check("and the button opens it", app._active_device == "plugins",
          app._active_device)
    app._switch_device("macros")
    pump(0.3)

    # The screen's own fetch reports to the sidebar as well.
    nav_item.set_badge(0)
    app._switch_device("plugins")
    panel = app._panels["plugins"]
    panel._fetch_available()        # built by the button above, so ask again
    pump(1.5)
    check("the Plugins screen's own fetch reaches the sidebar",
          nav_item.badge() == 1, nav_item.badge())
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
    check("the badge goes with it", nav_item.badge() == 0
          and not (nav_item._badge and nav_item._badge.winfo_ismapped()),
          nav_item.badge())

    # An update that cannot be put in place says so, and names the plugin.
    # The folder of a plugin someone works on is often a link to their own
    # checkout, which the update cannot remove: Update all said nothing then,
    # and the old version went on running (#105).
    dest = os.path.join(plp._PLUGINS_DIR, "now_playing")
    own = os.path.join(_REPO, "own_now_playing")
    shutil.copytree(dest, own)
    shutil.rmtree(dest)
    os.symlink(own, dest)
    PUBLISHED["version"] = "10.0"
    INDEX[0]["version"] = "10.0"
    panel._restart_lbl.configure(text="")
    panel._fetch_available()
    pump(1.5)
    panel._update_all()
    end = time.time() + 6
    while time.time() < end and getattr(panel, "_updating_all", False):
        app.update()
        time.sleep(0.05)
    pump(0.5)
    fail = app.T("pluginmgr_install_fail", err="")
    msg = panel._restart_lbl.cget("text")
    check("an update that cannot be put in place says so", fail in msg, msg)
    check("and names the plugin", "Now Playing" in msg, msg)
    check("and the link, and where it leads", "now_playing" in msg
          and "own_now_playing" in msg and "Errno" not in msg, msg)
    check("the installed version is left as it was",
          pm._manifests.get("now_playing", {}).get("version") == "9.9",
          pm._manifests.get("now_playing", {}).get("version"))
    check("and so is the link", os.path.islink(dest)
          and os.path.isfile(os.path.join(own, "plugin.json")))
    check("Update all is offered again", panel._update_all_btn.winfo_ismapped()
          and panel._update_all_btn.cget("state") == "normal",
          panel._update_all_btn.cget("text"))

    # The plugin's own Update button said nothing either.
    panel._restart_lbl.configure(text="")
    panel._install_available(dict(INDEX[0]))
    end = time.time() + 6
    while time.time() < end and not panel._restart_lbl.cget("text"):
        app.update()
        time.sleep(0.05)
    msg = panel._restart_lbl.cget("text")
    check("so does the plugin's own Update button",
          fail in msg and "Now Playing" in msg, msg)

    # A second copy of the plugin under another folder name is the one that
    # gets loaded when its name sorts later; an update beside it changes
    # nothing that runs, and has to say so instead of reporting success.
    os.unlink(dest)
    shutil.copytree(own, dest)
    twin = os.path.join(plp._PLUGINS_DIR, "zz_now_playing_copy")
    shutil.copytree(own, twin)
    pm.discover()
    panel._restart_lbl.configure(text="")
    panel._update_all()
    end = time.time() + 6
    while time.time() < end and getattr(panel, "_updating_all", False):
        app.update()
        time.sleep(0.05)
    pump(0.5)
    msg = panel._restart_lbl.cget("text")
    check("a copy elsewhere that runs instead is named",
          fail in msg and "zz_now_playing_copy" in msg, msg)
    shutil.rmtree(twin)
    pm.discover()

    # A copy whose version was raised by hand is never offered the published
    # one: FransM's Pipe Text said 2.0, the published one 1.2.3, and the old
    # code ran on without a word (#105). Said beside the plugin, with a way
    # back, but not pushed through the sidebar or Update all.
    with open(os.path.join(dest, "plugin.json")) as f:
        data = json.load(f)
    data["version"] = "99.0"
    with open(os.path.join(dest, "plugin.json"), "w") as f:
        json.dump(data, f)
    pm.discover()
    panel._restart_lbl.configure(text="")
    panel._fetch_available()
    pump(1.5)
    panel._select("now_playing", True)
    pump(0.3)

    def texts(widget):
        out = []
        for w in widget.winfo_children():
            try:
                out.append((w, w.cget("text")))
            except Exception:
                pass
            out += texts(w)
        return out

    shown = texts(panel._detail)
    note = app.T("pluginmgr_newer_installed", have="99.0", pub="10.0")
    back = app.T("pluginmgr_install_published", ver="10.0")
    check("a higher installed version is pointed out beside the plugin",
          any(t == note for _w, t in shown), [t for _w, t in shown][:6])
    buttons = [w for w, t in shown if t == back and isinstance(w, ctk.CTkButton)]
    check("with a button for the published version", len(buttons) == 1,
          [t for _w, t in shown][:8])
    app._on_plugins_fetched(plp.fetch_plugin_index())
    check("but neither the sidebar nor Update all count it",
          nav_item.badge() == 0 and not panel._update_all_btn.winfo_ismapped(),
          nav_item.badge())
    if buttons:
        buttons[0].invoke()
        end = time.time() + 6
        while time.time() < end and pm._manifests.get("now_playing", {}).get("version") != "10.0":
            app.update()
            time.sleep(0.05)
        pump(0.5)
    check("the button puts the published version in place",
          pm._manifests.get("now_playing", {}).get("version") == "10.0",
          pm._manifests.get("now_playing", {}).get("version"))
    check("and the note goes with it",
          not any(t == note for _w, t in texts(panel._detail)))


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
for d in [_TMP_HOME, _RUN, _REPO] + _ZIPS:
    shutil.rmtree(d, ignore_errors=True)
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
