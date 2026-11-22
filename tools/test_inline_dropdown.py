#!/usr/bin/env python3
"""
Checks the in-window dropdown list against the CustomTkinter it runs with.

    python3 tools/test_inline_dropdown.py

InlineDropdown takes the place of CustomTkinter's DropdownMenu inside every
option menu and combo box (#66), so it has to answer everything those two call
on their menu. CustomTkinter 6.0 started asking the menu whether it is open
and telling it to close, and a source install that pulled 6.0 printed an
AttributeError each time the pointer entered a dropdown (#116).

The first check reads the calls out of the installed CustomTkinter's own
source, so it holds for whatever version comes next. The rest open and close
real dropdowns the way a click does. Run it with each CustomTkinter you care
about; it needs a display.
"""
import os
import re
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("no display, skipping the dropdown checks")
    sys.exit(0)

import customtkinter as ctk                                   # noqa: E402
from customtkinter.windows.widgets import ctk_combobox, ctk_optionmenu  # noqa: E402

from shared.ui import dropdown                                # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-62s %s" % (name, detail), flush=True)
    if not ok:
        failures.append(name)


print("CustomTkinter", ctk.__version__)
check("the list is installed in place of CustomTkinter's menu",
      dropdown.install_inline_dropdown()
      and ctk_optionmenu.DropdownMenu is dropdown.InlineDropdown
      and ctk_combobox.DropdownMenu is dropdown.InlineDropdown)

called = set()
for module in (ctk_optionmenu, ctk_combobox):
    called |= set(re.findall(r"self\._dropdown_menu\.(\w+)", inspect.getsource(module)))
missing = sorted(n for n in called if not hasattr(dropdown.InlineDropdown, n))
check("it has every method the option menu and combo box call",
      called and not missing, "missing %s of %s" % (missing, sorted(called)))

check("nothing is declined with this CustomTkinter", dropdown.DECLINED == (),
      dropdown.DECLINED)

# ── A CustomTkinter that asks for something the list does not have ──────────

import types                                                  # noqa: E402

modules = (ctk_optionmenu, ctk_combobox)
real_calls = dropdown._menu_calls


def back_to_stock():
    for m in modules:
        m.DropdownMenu = m._basecamp_stock_dropdown
        m._basecamp_inline_dropdown = False


back_to_stock()
dropdown._menu_calls = lambda _m: {"open", "cget", "configure", "frobnicate"}
check("a call the list cannot answer is found",
      dropdown.toolkit_gap() == ["frobnicate"], dropdown.toolkit_gap())
took = dropdown.install_inline_dropdown()
check("the list then stays out and CustomTkinter keeps its own menu",
      took is False and all(m.DropdownMenu is m._basecamp_stock_dropdown
                            for m in modules))
check("and what was missing is kept for the notice",
      dropdown.DECLINED == ("frobnicate",), dropdown.DECLINED)
dropdown._menu_calls = real_calls
check("with no source to read (the AppImage) nothing counts as missing",
      dropdown._menu_calls([types.ModuleType("no_source")]) == set())
check("put back once there is nothing missing",
      dropdown.install_inline_dropdown() is True and dropdown.DECLINED == ()
      and all(m.DropdownMenu is dropdown.InlineDropdown for m in modules))

root = ctk.CTk()
root.geometry("400x300")
values = ["Alpha", "Beta", "Gamma"]


def pump():
    for _ in range(5):
        root.update()


def is_shown(widget):
    return widget._dropdown_menu._panel is not None


def run(kind, widget):
    widget.pack(pady=20)
    pump()
    dd = widget._dropdown_menu

    widget._on_enter()                      # the pointer comes over the control
    widget._clicked()
    pump()
    check("%s: a click opens the list" % kind, is_shown(widget))
    check("%s: and it says so" % kind, not hasattr(dd, "is_open") or dd.is_open())

    widget._on_enter()                      # raised AttributeError under 6.0 (#116)
    widget._clicked()
    pump()
    check("%s: a second click on the control closes it" % kind, not is_shown(widget))
    check("%s: and it says so" % kind, not hasattr(dd, "is_open") or not dd.is_open())

    widget._clicked()
    pump()
    check("%s: a third opens it again" % kind, is_shown(widget))

    # Closed without the control being clicked, the pointer still on it:
    # Escape, the wheel, the window moving. The next click has to open it.
    dd.close()
    pump()
    widget._clicked()
    pump()
    check("%s: after Escape the next click opens it" % kind, is_shown(widget))

    if not is_shown(widget):                # the pick below needs an open list
        widget._clicked()
        pump()
    dd._pick(2)
    pump()
    check("%s: picking a row closes it and takes the value" % kind,
          not is_shown(widget) and widget.get() == "Gamma", widget.get())
    widget._clicked()
    pump()
    check("%s: after a pick the next click opens it" % kind, is_shown(widget))
    dd.close()
    widget.pack_forget()


picked = []
run("option menu", ctk.CTkOptionMenu(root, values=values, command=picked.append))
run("combo box", ctk.CTkComboBox(root, values=values, command=picked.append))
check("the commands ran with the picked value", picked == ["Gamma", "Gamma"], picked)

root.destroy()
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
