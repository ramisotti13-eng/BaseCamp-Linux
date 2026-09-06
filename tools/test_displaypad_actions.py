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
from shared.config import (_save_displaypad_buttons,   # noqa: E402
                           _save_displaypad_pages,
                           _load_displaypad_buttons,
                           _load_displaypad_pages)

# A configuration written under the old icon names, before the application is
# built, so the rename in the panel's own start-up is what is checked and not
# a hand call to it afterwards (#95). It has to persist what it renames, and
# that happens while the panel is still being constructed.
from PIL import Image as _SeedImage               # noqa: E402

os.makedirs(dpp.CONFIG_DIR, exist_ok=True)
_LEGACY = {
    "main_folder": os.path.join(dpp.CONFIG_DIR, "dp_folder_2.png"),
    "main_label": os.path.join(dpp.CONFIG_DIR, "dp_label_0_3.png"),
    "sub_label": os.path.join(dpp.CONFIG_DIR, "dp_label_5_1.png"),
}
for _path in _LEGACY.values():
    _SeedImage.new("RGB", (102, 102), (9, 9, 9)).save(_path)
_save_displaypad_buttons({"2": _LEGACY["main_folder"], "3": _LEGACY["main_label"]})
_save_displaypad_pages({"5": {"v": 2, "fullscreen": None,
                              "actions": [{"type": "none", "action": ""}] * 12,
                              "buttons": {"1": _LEGACY["sub_label"]}}})

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

    # ── The rename happened while the panel was starting up (#95) ────────────
    stored_main = _load_displaypad_buttons()
    check("the main page's stored paths were rewritten on start-up",
          stored_main.get("2") == dpp._generated_icon_name("folder", 0, 2)
          and stored_main.get("3") == dpp._generated_icon_name("label", 0, 3),
          stored_main)
    stored_sub = _load_displaypad_pages().get("5", {}).get("buttons", {})
    check("and so were a sub-page's",
          stored_sub.get("1") == dpp._generated_icon_name("label", 5, 1),
          stored_sub)
    check("the files moved with them",
          all(os.path.exists(p) for p in
              (dpp._generated_icon_name("folder", 0, 2),
               dpp._generated_icon_name("label", 0, 3),
               dpp._generated_icon_name("label", 5, 1))))
    check("and none of the old names is left",
          not any(os.path.exists(p) for p in _LEGACY.values()))


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

        # A page of widgets pushes a dozen frames between them, and one
        # scheduled redraw each would put a dozen file reads and resizes a
        # second on the interface thread. They are collected into one pass.
        redrawn.clear()
        for key in range(12):
            panel._images[str(key)] = os.path.join(
                _TMP_HOME, "widget-many-%d.png" % key)
            panel.push_plugin_image(key, frame)
        check("nothing is drawn before the pass runs", not redrawn, redrawn)
        check("and one pass is booked, not twelve", panel._tile_pass_due)
        app.update()
        check("the pass draws every key that changed",
              sorted(redrawn) == list(range(12)), sorted(redrawn))

        # The mark goes up before the pass is booked, not after: Tk runs the
        # callback on its own thread and it can have run and cleared the mark
        # before after() has even returned, and writing the mark afterwards
        # would leave one standing for a pass that is over. Nothing would be
        # drawn again for the rest of the session.
        app.update()
        with panel._tile_lock:      # a live widget may have booked one
            panel._tile_pass_due = False
            panel._tile_dirty.clear()
        redrawn.clear()
        fired = []
        real_after = panel.after

        def after_that_runs_first(ms, cb=None, *a):
            # == and not is: each access binds a fresh method object.
            if cb == panel._draw_dirty_tiles:
                cb()                    # as if Tk had got there first
                fired.append(1)
                return "already-run"
            return real_after(ms, cb, *a)

        panel.after = after_that_runs_first
        try:
            panel._images["4"] = os.path.join(_TMP_HOME, "widget-race.png")
            panel.push_plugin_image(4, frame)
        finally:
            panel.after = real_after
        check("a pass that runs before it is booked leaves no mark",
              bool(fired) and not panel._tile_pass_due,
              "fired=%s due=%s" % (fired, panel._tile_pass_due))

        redrawn.clear()
        panel._images["5"] = os.path.join(_TMP_HOME, "widget-race-2.png")
        panel.push_plugin_image(5, frame)
        app.update()
        check("so the next change is still drawn", 5 in redrawn, redrawn)
        check("and books no further pass", not panel._tile_pass_due)
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

    # ── The generated icons all follow one naming scheme (#95) ───────────────
    # Reported by @FransM: the widget plugins write dp_<plugin>_p1_k2.png and
    # the application wrote dp_label_1_2.png beside them, with folder icons on
    # page 0 dropping the page altogether. The names an old configuration
    # holds have to keep working, so they are renamed and the stored paths
    # follow.
    check("a label icon is named for its page and key",
          dpp._generated_icon_name("label", 3, 7).endswith("dp_label_p3_k7.png"),
          dpp._generated_icon_name("label", 3, 7))
    check("and a folder icon on the main page names its page too",
          panel._folder_icon_name(0, 4).endswith("dp_folder_p0_k4.png"),
          panel._folder_icon_name(0, 4))

    old_label = os.path.join(dpp.CONFIG_DIR, "dp_label_0_10.png")
    old_folder = os.path.join(dpp.CONFIG_DIR, "dp_folder_11.png")
    _Image.new("RGB", (102, 102), (1, 2, 3)).save(old_label)
    _Image.new("RGB", (102, 102), (4, 5, 6)).save(old_folder)
    panel._page_images[0] = dict(panel._page_images.get(0, {}))
    panel._page_images[0]["10"] = old_label
    panel._page_images[0]["11"] = old_folder
    panel._migrate_generated_icon_names()
    check("an old label name is renamed and followed",
          panel._page_images[0]["10"] == dpp._generated_icon_name("label", 0, 10)
          and os.path.exists(panel._page_images[0]["10"]),
          panel._page_images[0]["10"])
    check("so is the page-less folder name",
          panel._page_images[0]["11"] == dpp._generated_icon_name("folder", 0, 11)
          and os.path.exists(panel._page_images[0]["11"]),
          panel._page_images[0]["11"])
    check("and the old files are gone",
          not os.path.exists(old_label) and not os.path.exists(old_folder))

    before = dict(panel._page_images[0])
    panel._migrate_generated_icon_names()
    check("running it again changes nothing", panel._page_images[0] == before)

    # Two files, one name each: what the configuration points at is this
    # key's picture, and a leftover under the new name is not.
    live = os.path.join(dpp.CONFIG_DIR, "dp_label_0_8.png")
    stale = dpp._generated_icon_name("label", 0, 8)
    _Image.new("RGB", (102, 102), (11, 22, 33)).save(live)
    _Image.new("RGB", (102, 102), (99, 88, 77)).save(stale)
    panel._page_images[0]["8"] = live
    panel._migrate_generated_icon_names()
    check("the referenced picture wins over a leftover under the new name",
          _Image.open(panel._page_images[0]["8"]).getpixel((5, 5)) == (11, 22, 33),
          _Image.open(panel._page_images[0]["8"]).getpixel((5, 5)))

    # A folder label that could not be renamed still has to be findable, or
    # the key falls back to the generic icon and the label looks lost.
    legacy = os.path.join(dpp.CONFIG_DIR, "dp_folder_6.png")
    _Image.new("RGB", (102, 102), (3, 3, 3)).save(legacy)
    check("a folder label under its old name is still found",
          panel._folder_icon_drawn(0, 6) == legacy, panel._folder_icon_drawn(0, 6))
    check("and the new name wins when both are there",
          (_Image.new("RGB", (102, 102), (4, 4, 4)).save(
              dpp._generated_icon_name("folder", 0, 6)) or
           panel._folder_icon_drawn(0, 6)) == dpp._generated_icon_name("folder", 0, 6),
          panel._folder_icon_drawn(0, 6))
    check("and a key with neither has none",
          panel._folder_icon_drawn(0, 7) is None, panel._folder_icon_drawn(0, 7))

    # An image the person chose is not ours to rename.
    mine = os.path.join(_TMP_HOME, "my-own-picture.png")
    _Image.new("RGB", (102, 102), (7, 8, 9)).save(mine)
    panel._page_images[0]["9"] = mine
    panel._migrate_generated_icon_names()
    check("an image of your own is left alone",
          panel._page_images[0]["9"] == mine and os.path.exists(mine))

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
