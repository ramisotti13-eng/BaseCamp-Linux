#!/usr/bin/env python3
"""
Checks the DisplayPad button-action editor keeps what was typed into it.

    python3 tools/test_displaypad_actions.py

Issue #87: a value typed into one of the editor's dropdowns and then saved
with the button, without touching another field first, was thrown away. Two
separate causes, both pinned here:

  * the dropdowns hand over what was typed only on a list pick or a focus
    change, and clicking a button changes no focus
  * saving one key makes the panel re-sync the dialog (#84), which re-reads
    all twelve rows from storage; doing that inside the save-everything loop
    reverted every row the loop had not reached yet

It needs a display, and no DisplayPad: the editor is Tk widgets over stored
actions, and it stores them whether or not a pad is plugged in. Its config
goes to a temporary directory, so running it never touches yours.
"""
import os
import shutil
import sys
import tempfile
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("no display, skipping the editor checks")
    sys.exit(0)

_TMP_HOME = tempfile.mkdtemp(prefix="basecamp-dp-actions-test-")
os.environ["HOME"] = _TMP_HOME

import gui                                       # noqa: E402
from devices.displaypad import panel as dpp      # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-50s %s" % (name, detail))
    if not ok:
        failures.append("%s: %s" % (name, detail))


app = gui.App()
app.geometry("1200x800")


def checks():
    # A plugin action type with value_options is what turns the value field
    # into an editable dropdown. That is the widget the issue is about.
    app._plugin_manager._action_types["widget_demo"] = {
        "label": "Widget Demo", "handler": lambda _v: None, "owner": "demo",
        "value_options": lambda: [("Seconds", "sec"), ("Minutes", "min")],
    }

    panel = app._displaypad_panel
    app._switch_device("displaypad")
    app.update()

    ids = dpp.action_type_ids(app)
    labels = dpp.action_type_labels(app)
    demo_label = labels[ids.index("widget_demo")]

    panel._open_actions_dialog()
    app.update()
    dialog = panel._actions_dialog_win

    def as_widget(idx):
        dialog._act_type[idx].set("widget_demo")
        dialog._on_type_change(demo_label, idx)
        app.update()

    # ── One key, typed and saved with the row's own button ───────────────────
    as_widget(0)
    check("the widget's value field is a dropdown",
          dialog._plugin_combos[0].winfo_ismapped())
    dialog._plugin_combos[0].set("sec")          # typed, nothing else touched
    app.update()
    check("what was typed is not in the variable yet",
          dialog._act_cmd[0].get() != "sec",
          "variable holds %r" % dialog._act_cmd[0].get())
    dialog._apply(0)
    app.update()
    check("saving reads it out of the widget",
          panel._get_action_dict(0) == {"type": "widget_demo", "action": "sec"},
          panel._get_action_dict(0))

    # ── Several keys at once, saved with the dialog's button ─────────────────
    # This is where saving key 1 used to revert keys 2 to 12.
    as_widget(1)
    dialog._plugin_combos[1].set("min")
    as_widget(2)
    dialog._plugin_combos[2].set("hours")
    dialog._act_type[3].set("shell")
    dialog._on_type_change(labels[ids.index("shell")], 3)
    dialog._act_cmd[3].set("echo three")
    app.update()
    dialog._apply_all_and_close()
    app.update()
    for idx, expected in ((1, {"type": "widget_demo", "action": "min"}),
                          (2, {"type": "widget_demo", "action": "hours"}),
                          (3, {"type": "shell", "action": "echo three"})):
        check("K%d survives saving all twelve at once" % (idx + 1),
              panel._get_action_dict(idx) == expected,
              panel._get_action_dict(idx))

    # ── The other dropdowns have the same shape ──────────────────────────────
    # OBS, macro and Hue have no focus-out handler at all, so they lost typed
    # values on every route, not only on the button.
    panel._open_actions_dialog()
    app.update()
    dialog = panel._actions_dialog_win
    dialog._act_type[4].set("obs")
    dialog._on_type_change(labels[ids.index("obs")], 4)
    app.update()
    if dialog._obs_combos[4].winfo_ismapped():
        dialog._obs_combos[4].set("Scene By Hand")
        dialog._apply(4)
        app.update()
        check("an OBS scene typed by hand is saved",
              panel._get_action_dict(4).get("action") == "scene:Scene By Hand",
              panel._get_action_dict(4))
    else:
        check("an OBS scene typed by hand is saved", False,
              "the OBS dropdown was not shown")

    # ── The two editors still have to agree (#84) ────────────────────────────
    # The fix above stops this dialog re-reading itself while it saves. The
    # inspector beside the key grid must still be told, and the dialog must
    # still hear about an edit made over there.
    dialog._act_type[5].set("shell")
    dialog._on_type_change(labels[ids.index("shell")], 5)
    dialog._act_cmd[5].set("from the dialog")
    dialog._apply(5)
    app.update()
    panel._select_key(5)
    app.update()
    check("an edit in the dialog reaches the inspector",
          panel._insp_value_var.get() == "from the dialog",
          panel._insp_value_var.get())

    panel._insp_value_var.set("from the inspector")
    panel._save_inspector("shell")
    app.update()
    check("and an edit in the inspector reaches the dialog",
          dialog._act_cmd[5].get() == "from the inspector",
          dialog._act_cmd[5].get())

    # ── A widget's frame has to reach the grid too (#90) ─────────────────────
    # The pad showed the widget while the editor kept the icon stored for that
    # key, which after a restart is whatever it was cleared to.
    from PIL import Image as _Image

    redrawn = []
    real_refresh = panel._refresh_panel_tile
    panel._refresh_panel_tile = lambda idx: redrawn.append(idx)
    try:
        frame = _Image.new("RGB", (102, 102), (10, 120, 200))
        panel._images["6"] = os.path.join(_TMP_HOME, "widget-frame-1.png")
        panel.push_plugin_image(6, frame)
        app.update()
        check("a widget's first frame redraws its tile", 6 in redrawn, redrawn)

        # The same file again, straight away, is a video pushing frames: the
        # floor is what keeps that from costing a redraw per frame.
        redrawn.clear()
        panel.push_plugin_image(6, frame)
        app.update()
        check("the same file again at once does not redraw it",
              not redrawn, redrawn)

        # But the same file a second later is a clock, whose file name never
        # changes and whose content does. Waiting for the path to change left
        # the editor on the first frame it ever drew (#96).
        redrawn.clear()
        panel._tile_drawn[6] = time.monotonic() - dpp._TILE_REDRAW_MIN - 0.01
        panel.push_plugin_image(6, frame)
        app.update()
        check("the same file after the floor redraws it", 6 in redrawn, redrawn)

        # A new file is a new picture, whenever it arrives.
        redrawn.clear()
        panel._images["6"] = os.path.join(_TMP_HOME, "widget-frame-2.png")
        panel.push_plugin_image(6, frame)
        app.update()
        check("a new frame file redraws it", 6 in redrawn, redrawn)
    finally:
        panel._refresh_panel_tile = real_refresh

    # ── A widget assigned to the page you are on has to start (#97) ──────────
    # Starting and stopping the services was only ever done on a page switch,
    # so assigning a widget to a key on the page already shown started nothing:
    # the key was stored and worked after a restart, and did nothing until then.
    synced = []
    real_sync = app._plugin_manager.sync_services_for_page
    app._plugin_manager.sync_services_for_page = lambda page: synced.append(page)
    try:
        panel._save_page_action(panel._current_page, 7, "widget_demo", "sec")
        app.update()
        check("assigning a widget asks for a service sync",
              panel._svc_sync_id is not None)
        check("and it has not run per row yet", not synced, synced)

        # Applying twelve rows must not stop and start a service twelve times.
        for idx in range(8, 12):
            panel._save_page_action(panel._current_page, idx, "widget_demo", "sec")
        app.update()
        deadline = time.monotonic() + 3.0
        while not synced and time.monotonic() < deadline:
            app.update()
            time.sleep(0.02)
        check("the sync runs once for the whole batch",
              synced == [panel._current_page], synced)

        # Clearing the key has to stop it again, by the same route.
        synced.clear()
        panel._save_page_action(panel._current_page, 7, "none", "")
        deadline = time.monotonic() + 3.0
        while not synced and time.monotonic() < deadline:
            app.update()
            time.sleep(0.02)
        check("clearing a widget key syncs too", synced == [panel._current_page],
              synced)
    finally:
        app._plugin_manager.sync_services_for_page = real_sync

    # ── Right click clears the key it is on, and moves there (#98) ───────────
    panel._select_key(2)
    app.update()
    panel._clear_slot(9)
    app.update()
    check("clearing a key selects that key",
          panel._selected_key == 9, panel._selected_key)
    check("and the inspector is showing it",
          panel._insp_title.cget("text") == "K10", panel._insp_title.cget("text"))

    # ── Saving must not disturb a key nobody touched ─────────────────────────
    panel._save_page_action(panel._current_page, 11, "shell", "untouched")
    dialog._load_page(dialog._page)
    app.update()
    dialog._apply_all_and_close()
    app.update()
    check("a key nobody edited keeps its action",
          panel._get_action_dict(11) == {"type": "shell", "action": "untouched"},
          panel._get_action_dict(11))


def run():
    try:
        checks()
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("the editor raised, see the traceback above")
    finally:
        app.after(50, app.destroy)


app.after(1500, run)
_watchdog = threading.Timer(90, lambda: os._exit(1))
_watchdog.daemon = True
_watchdog.start()
app.mainloop()
_watchdog.cancel()

shutil.rmtree(_TMP_HOME, ignore_errors=True)

print()
if failures:
    print("%d check(s) failed:" % len(failures))
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("all checks passed")
