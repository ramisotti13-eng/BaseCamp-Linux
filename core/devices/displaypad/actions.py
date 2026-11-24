"""What a DisplayPad key does: the action types, building one from an
editor's fields, and running it when the key is pressed.

The catalogue is ids and translation keys; an interface turns the keys into
words (the CustomTkinter panel with app.T). build_action() and
inspector_page_choice() are the parts of the two editors (the button actions
window and the key inspector) that decided what gets stored, out of the
widgets. ActionRunner is what happens on a key press: the action, its "also
on press" chain, the double-click window and the page timeouts, timed on the
core scheduler instead of Tk's after().

Running a macro, a keystroke, a text, a shell command, a URL, OBS or a
plugin's handler goes through an ActionEnv. DispatcherActionEnv hands them
to the application's one key action dispatcher (core/actions.py, the
MacroPad and the Everest Max use it too); the runner keeps only what is the
DisplayPad's own: page switches, 'set_key', chains, double clicks and page
timeouts. An env without dispatch() (a test's recorder) is asked method by
method, as the panel did before 4.0.

One thing changed with the shared dispatcher: a key bound to a type nobody
provides (a plugin that has been removed) does nothing. The DisplayPad used
to run its stored value as a shell command, a value that was written for the
plugin, not for a shell; the MacroPad already refused it.

Moved here from devices/displaypad/panel.py (4.0), unchanged in what it does.
"""
import json
import threading
import unicodedata

ACTION_TYPES = ["none", "shell", "url", "folder", "app", "page", "obs", "macro",
                "keypress", "text", "set_key", "clock", "pc_info"]
# Keys that draw themselves and keep doing so (live.py): a press runs only
# their "also on press" steps.
LIVE_TYPES = ("clock", "pc_info")
# Secondary "also on press" action types (issue #16). A key that otherwise only
# renders a widget (System Monitor, plugin keys) can additionally fire one of
# these on press, so it is no longer a dead key. Kept to the simple free-text
# actions that need just one entry field.
SECONDARY_TYPES = ["none", "keypress", "text", "shell", "url", "page"]

SEPARATOR = "_separator"
SEPARATOR_LABEL = "-- Plugins --"


def label_key(type_id):
    """Translation key of a built-in type's name; None for OBS, whose name is
    "OBS" in every language."""
    return None if type_id == "obs" else f"action_type_{type_id}"


def menu_order(label):
    """Where a label sorts in a menu: by its letters, an umlaut or an accent
    with its base letter rather than after z, case ignored (#109). The same
    as shared.ui_helpers.menu_order, which an interface may pass instead."""
    plain = unicodedata.normalize("NFKD", str(label))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return (plain.casefold(), str(label))


def action_type_entries(translate, plugin_labels=(), include_page=True, sort_key=menu_order):
    """(id, label) pairs of the action menu, in menu order.

    None first, since it is what a key starts as and how one is cleared. Then
    the built-in actions and, below a separator, the plugins', each by name in
    the language the interface is in (#109). They came in the order they had
    been written, which with a dozen plugin actions meant reading the whole
    list to find one.

    Ids and labels come from here together, because the menu is matched back
    to an id by position and two lists kept in step by hand would drift.
    `plugin_labels` is [(id, label)] of the plugins' action types.
    """
    ids = [t for t in ACTION_TYPES if include_page or t != "page"]

    def label(tid):
        key = label_key(tid)
        return "OBS" if key is None else translate(key)

    entries = [(ids[0], label(ids[0]))]
    entries += sorted(((t, label(t)) for t in ids[1:]),
                      key=lambda e: sort_key(e[1]))
    plugin = sorted(plugin_labels or (), key=lambda e: sort_key(e[1]))
    if plugin:
        entries.append((SEPARATOR, SEPARATOR_LABEL))
        entries.extend(plugin)
    return entries


# ── Building an action from an editor's fields ───────────────────────────────

def build_action(btype, value, sec_type="none", sec_value="", dbl_type="none",
                 dbl_value="", page_target=None, hue_target="", page_exists=None):
    """What the button actions window stores for one key, from its fields:
    the type, the value field, the "also on press" row (#16/#17), the
    double-click row (#47), the page picked for a page key (#30) and, for
    "hue_bri", the light or group picked beside the percentage.

    `page_exists(name)` tells whether a page of that name exists; an "also on
    press" or double-click step to a page that does not is left out, as there
    is nothing to jump to. Returns the keyword arguments for
    save_page_action(): btype, action, extra, target, double.
    """
    action = (value or "").strip()
    # For hue_bri, assemble "target:pct" from combo + entry
    if btype == "hue_bri":
        pct = action.replace("%", "").strip()
        try:
            pct = str(max(0, min(100, int(pct))))
        except ValueError:
            pct = "50"
        action = f"{hue_target}:{pct}" if hue_target else ""
    exists = page_exists or (lambda _name: True)
    # Secondary "also on press" action (issue #16/#17)
    sectype = sec_type or "none"
    secval = (sec_value or "").strip()
    extra = []
    if sectype and sectype != "none":
        step = {"type": sectype, "action": secval}
        if sectype == "page":
            # 'also on press → go to page' (#17): the entry holds a page
            # name, stored directly (#52) -- page_target() resolves it
            # back to an id when the chain actually runs.
            if exists(secval):
                step["target"] = secval
                extra = [step]
            # unresolved / 'New page' name → skip (nothing to jump to)
        elif secval:
            extra = [step]
    # Double-click action (issue #47): authoritative dict from this key's
    # double-click row ({"type":"none"} clears it).
    dbltype = dbl_type or "none"
    dblval = (dbl_value or "").strip()
    double = {"type": "none", "action": ""}
    if dbltype and dbltype != "none":
        step = {"type": dbltype, "action": dblval}
        if dbltype == "page":
            if exists(dblval):
                step["target"] = dblval
                double = step
        elif dblval:
            double = step
    # Primary 'page' action carries the target picked in the combo (#30).
    target = page_target if btype == "page" else None
    return {"btype": btype, "action": action, "extra": extra,
            "target": target, "double": double}


def inspector_page_choice(choice, new_page_label, new_page_caption, page_id_by_name):
    """(value, target) the key inspector stores for a page key: the page
    picked by name, or a new page (minted on save) captioned
    `new_page_caption` when the "new page" entry was picked."""
    if choice == new_page_label:
        return new_page_caption, "new"
    pid = page_id_by_name(choice)
    return choice, (pid if pid is not None else "new")


def parse_set_key(action_json, current_page, host_idx):
    """(page, key, type, action) a 'set_key' action redefines (#18), from its
    JSON {"page":P,"key":K,"type":T,"action":A}; page and key default to the
    current page and the key pressed. None when it cannot be read."""
    try:
        d = json.loads(action_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    try:
        page = int(d.get("page", current_page))
        key = int(d.get("key", host_idx))
    except (TypeError, ValueError):
        return None
    return page, key, d.get("type", "none"), d.get("action", "")


# ── Running actions ───────────────────────────────────────────────────────────

class ActionEnv:
    """What running an action needs from outside the DisplayPad. Every
    method may block; ActionRunner decides which run in a thread of their
    own (as the panel did: macros, keystrokes, texts and plugin handlers)."""

    def run_obs(self, action):
        """"scene:<name>", "record" or "stream"."""

    def load_macro(self, macro_id):
        """The stored macro, or None."""
        return None

    def execute_macro(self, macro, stop_event=None):
        """Play a macro; a toggle macro until `stop_event` is set."""

    def keypress(self, combo):
        pass

    def type_text(self, text):
        pass

    def plugin_handler(self, type_id):
        """The handler a plugin registered for this action type, or None."""
        return None

    def run_shell(self, command):
        pass

    def open_target(self, target):
        """Open a URL or a folder with the desktop's handler."""


class DispatcherActionEnv(ActionEnv):
    """Runs the DisplayPad's actions through the shared key action
    dispatcher (core.actions.ActionDispatcher), as the DisplayPad's keys.

    `dispatcher` is the ActionDispatcher, or a callable returning it, looked
    up for every action: the application wires OBS and the plugins into the
    one it owns, and a test may replace it.
    """

    OWNER = "displaypad"

    def __init__(self, dispatcher):
        self._dispatcher = dispatcher

    def dispatcher(self):
        d = self._dispatcher
        return d() if callable(d) and not hasattr(d, "run") else d

    def dispatch(self, type_id, value):
        """Run one action; returns the dispatcher's result code."""
        return self.dispatcher().run(type_id, value, owner=self.OWNER)

    def plugin_handler(self, type_id):
        return self.dispatcher().plugin_handler(type_id)


class ActionRunner:
    """A key press, from the pad to what it does.

    `model` is the PageModel, `scheduler` the core scheduler every method
    here runs on, `env` an ActionEnv. Page switches and 'set_key' edits are
    handed back through `switch_page(page, show_hints)` and
    `set_key(action_json, key)`, scheduled rather than called, as the panel
    did with after(0).
    """

    def __init__(self, model, scheduler, env, switch_page, set_key):
        self.model = model
        self.scheduler = scheduler
        self.env = env
        self.switch_page = switch_page
        self.set_key = set_key
        # Double-click support (issue #47): a key with a 'double' action fires
        # that on a quick second press; a lone press fires the primary only after
        # the window elapses. Keys without a double action stay instant.
        self.dc_timers = {}      # key idx -> pending single-press handle
        self.dc_window = 0.6     # seconds to wait for a second press
        self.dc_antibounce = 0.12  # min gap between the two taps (filters bounce)
        # Per-page auto-timeout (issue #45): jump to a target page after N seconds
        # (mode 'after') or N seconds of no keypress (mode 'idle').
        self.timeout_handle = None
        self.macro_toggle_events = {}   # macro id -> stop Event of a running toggle macro

    # ── A key press ───────────────────────────────────────────────────────────

    def key_pressed(self, idx):
        # A keypress resets the idle auto-timeout for the current page (#45).
        self.note_keypress()
        # Double-click (issue #47): if this key has a 'double' action, delay the
        # primary until the window elapses so a quick second press can trigger the
        # double action instead. Keys with no double action fire instantly.
        dbl = self.model.get_double_action(idx)
        if dbl is None:
            self.fire_chain(idx)
            return
        h = self.dc_timers.pop(idx, None)
        if h is not None:
            # Second press inside the window → double-click action, cancel single.
            self.scheduler.cancel(h)
            self.run_one(dbl.get("type", "none"), dbl.get("action", ""), idx, step=dbl)
        else:
            self.dc_timers[idx] = self.scheduler.call_later(
                int(self.dc_window * 1000), self._dc_fire_single, idx)

    def _dc_fire_single(self, idx):
        """Window elapsed with no second press → treat as a single click."""
        self.dc_timers.pop(idx, None)
        self.fire_chain(idx)

    def cancel_double_clicks(self):
        """Forget pending single presses: they belong to the page being left.
        Left armed, a stale timer would fire the primary against the wrong
        page, or a later single press on the new page would be mistaken for
        its second click (issue #47, surfaced by the #30/#45 page switches)."""
        for h in self.dc_timers.values():
            self.scheduler.cancel(h)
        self.dc_timers.clear()

    def fire_chain(self, idx):
        # Run the primary action, then any chained extras in order (issue #17).
        # Pass the full action dict so a step can carry a page 'target' — this
        # is what lets 'also on press' jump to a page (e.g. press the CPU key →
        # also switch to a per-core page, issue #17).
        primary = self.model.get_action_dict(idx)
        self.run_one(primary.get("type", "none"),
                     primary.get("action", ""), idx, step=primary)
        for step in self.model.get_extra_actions(idx):
            st = step.get("type", "none")
            sa = step.get("action", "")
            if st and st != "none":
                self.run_one(st, sa, idx, step=step)

    def run_one(self, btype, action, idx=0, step=None):
        # Redefine another key on demand (issue #18). action is JSON:
        #   {"page":P,"key":K,"type":T,"action":A}  (page/key optional → current/idx)
        if btype == "set_key" and action:
            self.scheduler.call_soon(self.set_key, action, idx)
            return
        # Page navigation — target any page id (#30 carousel). Legacy actions
        # with no explicit target fall back to the old idx+1 sub-page.
        if btype == "page":
            target = self.model.page_target(step if step is not None else {}, idx)
            self.scheduler.call_soon(self.switch_page, target, True)
            return
        if btype == "back":
            self.scheduler.call_soon(self.switch_page, 0, True)
            return
        if btype in LIVE_TYPES:
            return          # a clock or PC info: it shows, it does nothing
        # Everything else is the shared dispatcher's (core/actions.py).
        dispatch = getattr(self.env, "dispatch", None)
        if dispatch is not None:
            dispatch(btype, action)
            return
        # An env of the 3.x shape, asked method by method.
        # OBS action
        if btype == "obs" and action:
            self.env.run_obs(action)
            return
        # Macro action
        if btype == "macro" and action:
            macro = self.env.load_macro(action)
            if macro:
                if action in self.macro_toggle_events:
                    self.macro_toggle_events[action].set()
                    del self.macro_toggle_events[action]
                else:
                    stop_ev = None
                    if macro.get("repeat_mode") == "toggle":
                        stop_ev = threading.Event()
                        self.macro_toggle_events[action] = stop_ev
                    threading.Thread(
                        target=self.env.execute_macro, args=(macro, stop_ev),
                        daemon=True).start()
            return
        # Keypress / Text action – uses xdotool (X11) or ydotool (Wayland) automatically
        if btype == "keypress" and action:
            threading.Thread(target=self.env.keypress, args=(action,), daemon=True).start()
            return
        if btype == "text" and action:
            threading.Thread(target=self.env.type_text, args=(action,), daemon=True).start()
            return
        # Plugin action types
        handler = self.env.plugin_handler(btype)
        if handler:
            threading.Thread(target=handler, args=(action,), daemon=True).start()
            return
        if btype == "none" or not action:
            return
        # Built-in shell/url/folder/app — dispatch via shared helpers that
        # handle the SUDO_USER / Wayland / X11 environment juggling.
        try:
            if btype in ("url", "folder"):
                self.env.open_target(action)
            else:
                self.env.run_shell(action)
        except Exception:
            pass

    # ── Per-page auto-timeout (issue #45) ─────────────────────────────────────

    def arm_page_timeout(self, page):
        """(Re)start the auto-timeout timer for `page`, cancelling any pending
        one. No-op unless the page has an 'after'/'idle' timeout configured."""
        if self.timeout_handle is not None:
            self.scheduler.cancel(self.timeout_handle)
            self.timeout_handle = None
        to = self.model.page_timeout.get(page)
        if not to:
            return
        secs = int(to.get("seconds", 0) or 0)
        if to.get("mode", "off") in ("after", "idle") and secs > 0:
            self.timeout_handle = self.scheduler.call_later(
                secs * 1000, self._fire_page_timeout, page)

    def _fire_page_timeout(self, page):
        self.timeout_handle = None
        if self.model.current_page != page:
            return
        tgt = self.model.timeout_target(page)
        if tgt != page:
            self.switch_page(tgt, False)

    def note_keypress(self):
        """Idle-mode timeout restarts its countdown on every keypress (#45)."""
        to = self.model.page_timeout.get(self.model.current_page)
        if to and to.get("mode") == "idle" and int(to.get("seconds", 0) or 0) > 0:
            self.arm_page_timeout(self.model.current_page)
