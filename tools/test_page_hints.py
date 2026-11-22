#!/usr/bin/env python3
"""
Checks the DisplayPad page box: a page's note and its keys' hints (#93).

    python3 tools/test_page_hints.py

It needs a display. A DisplayPad plugged into the machine is hidden from the
application, and Monitor Mode is kept from starting, so no device is touched.
The configuration goes to a temporary directory.
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("no display, skipping the page box checks")
    sys.exit(0)

_TMP_HOME = tempfile.mkdtemp(prefix="basecamp-page-hints-test-")
os.environ["HOME"] = _TMP_HOME
_RUN = tempfile.mkdtemp(prefix="hints-", dir="/tmp")
os.environ["XDG_RUNTIME_DIR"] = _RUN

import tkinter as tk                                  # noqa: E402

import gui                                            # noqa: E402
from devices.displaypad import panel as dpp           # noqa: E402
from devices.everest_max import panel as emp          # noqa: E402
from shared import hid_compat                         # noqa: E402
from shared.config import (_load_displaypad_page_hints,   # noqa: E402
                           _save_displaypad_page_hints,
                           _load_hint_box_settings, _save_hint_box_settings)

emp.EverestMaxPanel._start_cpu_auto = lambda self: None
emp.EverestMaxPanel._start_cpu_auto_clean = lambda self: None
emp.EverestMaxPanel._toggle_cpu = lambda self: None
_real_enum = hid_compat.enumerate
hid_compat.enumerate = lambda vid=0, pid=0, *a, **k: (
    [] if (vid, pid) == (dpp.VID, dpp.PID) else _real_enum(vid, pid, *a, **k))

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-60s %s" % (name, detail), flush=True)
    if not ok:
        failures.append(name)


app = gui.App()
app.geometry("1200x800")


def pump(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def texts(widget):
    out = []
    for child in widget.winfo_children():
        try:
            out.append(str(child.cget("text")))
        except (tk.TclError, ValueError, AttributeError):
            pass
        out.extend(texts(child))
    return out


def checks():
    panel = app._displaypad_panel
    app._switch_device("displaypad")
    pump()

    # Two pages: one with a note and hints, one without anything.
    edit = panel._mint_page_id()
    panel._ensure_page(edit, back_to=0)
    names = dpp._load_displaypad_page_names()
    names[edit] = "Editor"
    dpp._save_displaypad_page_names(names)
    plain = panel._mint_page_id()
    panel._ensure_page(plain, back_to=0)
    _save_displaypad_page_hints(edit, note="Shortcuts while the editor is open",
                                keys={"1": "Save", "2": "Find"})

    # They outlive everything else that writes the page.
    panel._switch_to_page(edit)
    pump()
    panel._save_page_action(edit, 1, "keypress", "ctrl+s")
    panel._save_sub_pages()
    panel._rename_page(edit, "Editor") if hasattr(panel, "_rename_page") else None
    h = _load_displaypad_page_hints(edit)
    check("a page's note and hints survive saving its keys",
          h["note"].startswith("Shortcuts") and h["keys"] == {"1": "Save", "2": "Find"}, h)

    # The inspector writes a key's hint, and to the key it was loaded for.
    panel._select_key(3)
    panel._insp_hint_var.set("Replace")
    panel._select_key(4)               # no focus change, as with a click on a tile
    h = _load_displaypad_page_hints(edit)["keys"]
    check("a hint typed in the inspector is kept when another key is chosen",
          h.get("3") == "Replace" and "4" not in h, h)
    panel._insp_hint_var.set("Go to line")
    panel._switch_to_page(plain)        # the field still holds key 5's text
    pump()
    h_edit = _load_displaypad_page_hints(edit)["keys"]
    h_plain = _load_displaypad_page_hints(plain)["keys"]
    check("and lands on its own page when the page changes under it",
          h_edit.get("4") == "Go to line" and not h_plain, (h_edit, h_plain))
    panel._save_page_action(edit, 3, "shell", "true")
    check("a hint outlives its key being given another action",
          _load_displaypad_page_hints(edit)["keys"].get("3") == "Replace")

    # A switch somebody made brings the box up.
    focus_before = app.focus_get()
    panel._switch_to_page(edit, show_hints=True)
    pump(0.5)
    box = panel._hint_box
    check("a switch made by a key brings the page's box up",
          box is not None and box.winfo_viewable() and box.page == edit)
    shown = texts(box) if box else []
    check("with the page's name, its note and its keys' hints",
          "Editor" in shown and any(t.startswith("Shortcuts") for t in shown)
          and "Save" in shown and "Go to line" in shown, shown[:8])
    inside = app.focus_get()
    check("and it takes no focus",
          inside is None or not str(inside).startswith(str(box)),
          (focus_before, inside))

    # One the application made takes it down again.
    panel._switch_to_page(plain)
    pump()
    check("a switch the application made closes it", panel._hint_box is None)

    panel._switch_to_page(plain, show_hints=True)
    pump()
    check("a page with nothing to say stays silent", panel._hint_box is None)

    # Asked for again on the page it is on: shown again.
    panel._switch_to_page(edit)
    pump()
    panel._switch_to_page(edit, show_hints=True)
    pump()
    check("pressing the page's key again shows it again", panel._hint_box is not None)

    # A page key on the pad, through the action it runs.
    panel._hint_box.close()
    panel._switch_to_page(plain)
    pump()
    panel._run_one_action("page", "Editor", 0,
                          {"type": "page", "action": "Editor", "target": "Editor"})
    pump(0.5)
    check("a page key on the pad brings it up",
          panel._hint_box is not None and panel._hint_box.page == edit)

    # The tray and scripts come through the control socket.
    panel._hint_box.close()
    panel._switch_to_page(plain)
    pump()
    app._control_dp_page({"cmd": "dp_page", "page": "Editor"})
    pump(0.5)
    check("a page switch from the tray or a script brings it up",
          panel._hint_box is not None and panel._hint_box.page == edit)
    panel._hint_box.close()
    panel._switch_to_page(plain)
    pump()
    app._control_dp_page({"cmd": "dp_page", "page": "Editor", "hints": False})
    pump(0.5)
    check("unless the script says hints: false", panel._hint_box is None)

    # Closing by itself, but not while it is being read.
    _save_hint_box_settings(timeout=1)
    panel._switch_to_page(edit, show_hints=True)
    pump(0.3)
    box = panel._hint_box
    box._set_hover(True)
    pump(1.4)
    check("it stays while the pointer is over it", panel._hint_box is box)
    box._set_hover(False)
    pump(1.4)
    check("and closes by itself after the set seconds", panel._hint_box is None)
    _save_hint_box_settings(timeout=0)

    # Told to stay quiet.
    panel._switch_to_page(edit, show_hints=True)
    pump()
    panel._hint_box._not_this_session()
    panel._switch_to_page(plain); pump()
    panel._switch_to_page(edit, show_hints=True); pump()
    check("not again this session means not again this session",
          panel._hint_box is None)
    panel._hint_pages_quiet.clear()
    panel._switch_to_page(edit, show_hints=True); pump()
    panel._hint_box._never()
    check("never for this page is stored with the page",
          _load_displaypad_page_hints(edit)["show"] is False)
    panel._switch_to_page(plain); pump()
    panel._switch_to_page(edit, show_hints=True); pump()
    check("and holds", panel._hint_box is None)

    # The note window writes the note, the switch and the seconds.
    dlg = dpp.PageNoteDialog(panel, edit)
    pump()
    dlg._text.delete("1.0", "end")
    dlg._text.insert("1.0", "New note")
    dlg._show.set(True)
    dlg._secs.set("7")
    dlg._save()
    pump()
    h = _load_displaypad_page_hints(edit)
    check("the note window saves the note and the switch",
          h["note"] == "New note" and h["show"] is True, h)
    check("and the seconds", _load_hint_box_settings()["timeout"] == 7)

    # A deleted page takes its box with it.
    panel._switch_to_page(edit, show_hints=True); pump()
    panel._delete_page(edit)
    pump()
    check("deleting a page closes its box", panel._hint_box is None)


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
shutil.rmtree(_TMP_HOME, ignore_errors=True)
shutil.rmtree(_RUN, ignore_errors=True)
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
