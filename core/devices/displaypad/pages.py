"""The DisplayPad's pages: what each key does and shows, page by page.

PageModel holds the pages (actions, images, fullscreen GIFs, timeouts), the
page on the pad and the live image map of that page, which widget plugins
write their frames into. It reads and writes the configuration through
shared.config, in the 3.x format, and knows nothing about the device or a
user interface: switching, uploading and drawing are its callers' business.

Moved here from devices/displaypad/panel.py (4.0), method by method and
unchanged in what it does; the panel's old attribute and method names are
views onto this object.

Two things stay outside and are handed in:
  translate(key)   the interface language, for the two names stored as data
                   ("Main" for page 0, "Back to Main" for a back key)
  plugin_types()   the action type ids plugins provide (or None when that
                   cannot be told), which decides whose key a frame is

Threads: like the panel before it, the model is not locked. Its owner runs
its methods one at a time (the core scheduler); widget plugins write into
`images` and `page_images` from their own threads, as they always have.
"""
import os
import sys

from PIL import Image

from shared.config import (
    CONFIG_DIR,
    _load_displaypad_buttons, _save_displaypad_buttons,
    _load_displaypad_fullscreen, _save_displaypad_fullscreen,
    _clear_displaypad_fullscreen,
    _load_displaypad_actions, _save_displaypad_actions,
    _load_displaypad_pages, _save_displaypad_pages,
    _load_displaypad_page_timeouts, _save_displaypad_page_timeouts,
    _load_displaypad_page_names, _save_displaypad_page_names,
    _create_displaypad_page, _rename_displaypad_page, _delete_displaypad_page,
)
from core.devices.displaypad import images as dpi
from core.devices.displaypad.actions import LIVE_TYPES
from core.devices.displaypad.protocol import ICON_SIZE, NUM_KEYS

DEFAULT_ACTIONS = [{"type": "none", "action": ""} for _ in range(NUM_KEYS)]

# The interface's words for the two names the model stores as data, when no
# interface is there to say them in its language.
_ENGLISH = {"dp_page_main": "Main", "dp_page_back": "Back to Main"}


def _default_translate(key, **_kw):
    return _ENGLISH.get(key, key)


def default_res_path():
    """Where resources/ is: beside core/ in a checkout, _MEIPASS when frozen."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    return getattr(sys, "_MEIPASS", here) if getattr(sys, "frozen", False) else here


class SaveResult:
    """What save_page_action() did that its caller may have to show."""
    __slots__ = ("current", "label_drawn")

    def __init__(self, current, label_drawn):
        self.current = current          # the page saved is the one on the pad
        self.label_drawn = label_drawn  # a sub-page key on the pad got a new label


class PageModel:
    def __init__(self, translate=None, plugin_types=None, res_path=None):
        self.translate = translate or _default_translate
        self.plugin_types = plugin_types or (lambda: None)
        self.res_path = res_path or default_res_path()
        # Resource paths for folder/back icons
        self.folder_icon = os.path.join(self.res_path, "resources", "DPFolder.png")
        self.back_icon = os.path.join(self.res_path, "resources", "DPBack.png")
        # Blank placeholder for empty buttons (device keeps old image unless overwritten)
        self.blank_icon = os.path.join(CONFIG_DIR, "dp_blank.png")

        self.current_page = 0
        self.prev_page = 0          # page we switched here from ('prev' target)
        self.page_actions = {}      # page -> [12 action dicts]
        self.page_images = {}       # page -> {str(idx): path}, widget frames included
        self.page_fullscreen = {}   # page -> fullscreen GIF path or None
        self.page_timeout = {}      # page -> {mode, seconds, target} (#45)
        self.images = {}            # the live map of the page on the pad
        self.gif_frames = {}        # idx -> [(bgr, ms)] for the page on the pad
        self.fullscreen_group = set()   # key indices that form a synced fullscreen GIF (3.x tiles)
        self.panel_path = None          # picture/GIF covering the whole panel on the page on the pad
        # ids delete_page() has removed: a switch must never write
        # outgoing-page state back for one of these (see begin_switch)
        self.deleted_page_ids = set()
        self.plugin_frame_keys = {}     # page -> {idx} showing a widget frame

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(self):
        """Read every page from the configuration and bring old ones up to
        date. Leaves page 0 on the pad."""
        self.page_timeout = _load_displaypad_page_timeouts()
        self.current_page = 0
        self.page_actions = {0: _load_displaypad_actions()}
        self.page_images = {0: _load_displaypad_buttons()}
        self.page_fullscreen = {0: _load_displaypad_fullscreen()}
        # Load sub-pages from config. `v` marks the page-model version: v<2 is a
        # pre-#30 page (still using the hard-locked back button) — remember those
        # so migration only touches them and never re-derives a back button the
        # user has since removed.
        legacy_pages = []
        for ps, pdata in _load_displaypad_pages().items():
            p = int(ps)
            self.page_actions[p] = pdata.get("actions", [dict(a) for a in DEFAULT_ACTIONS])
            self.page_images[p] = pdata.get("buttons", {})
            self.page_fullscreen[p] = pdata.get("fullscreen")
            if pdata.get("v", 1) < 2:
                legacy_pages.append(p)
        if not os.path.exists(self.blank_icon):
            Image.new("RGB", (ICON_SIZE, ICON_SIZE), (0, 0, 0)).save(self.blank_icon)

        # ── Page-model migration (#30) ────────────────────────────────────────
        def _back_act():
            return {"type": "page", "action": self.translate("dp_page_back"),
                    "target": 0, "icon": self.back_icon}
        # Convert the old dedicated 'back' action into a normal page→main action
        # everywhere, so it's editable and shows correctly in the dropdown
        # (idempotent — nothing left to convert on the next launch).
        for acts in self.page_actions.values():
            for i, a in enumerate(acts):
                if a.get("type") == "back":
                    acts[i] = _back_act()
        # Only for pre-#30 pages: give an untouched K1 (still the default 'none')
        # the implicit back button the old model provided. v>=2 pages are left
        # as-is so a user CAN remove the back button and have it stay removed.
        for p in legacy_pages:
            acts = self.page_actions.get(p)
            if p != 0 and acts and acts[0].get("type", "none") == "none":
                acts[0] = _back_act()

        self.migrate_generated_icon_names()
        self.redraw_double_captions()

        self.images = dict(self.page_images.get(0, {}))
        # A value that is not a path is dropped rather than carried into the
        # first os.path call, where it would stop the app from starting at all.
        for k, v in list(self.images.items()):
            if v is not None and not isinstance(v, str):
                print(f"[DisplayPad] ignoring non-path image for key {k}: {v!r}")
                self.images[k] = self.blank_icon
        self.gif_frames = {}
        for k, path in self.images.items():
            if path and os.path.exists(path) and path.lower().endswith('.gif'):
                frames = dpi.load_gif_frames(path, self.key_text(0, int(k)))
                if frames:
                    self.gif_frames[int(k)] = frames

        # Inject navigation icons for 'page'/'back' buttons on the start page.
        self.inject_page_icons(0)

        # Restore fullscreen GIF if saved
        fs_path = self.page_fullscreen.get(0)
        if fs_path and os.path.exists(fs_path):
            self.panel_path = fs_path

    # ── Reading actions ───────────────────────────────────────────────────────

    def get_action_dict(self, idx, page=None):
        """Full action dict for button idx on `page` (current page if None)."""
        page = self.current_page if page is None else page
        actions = self.page_actions.get(page, DEFAULT_ACTIONS)
        return actions[idx] if idx < len(actions) else {"type": "none", "action": ""}

    def get_extra_actions(self, idx):
        """Extra action steps for button idx (issue #17). Stored as an `actions`
        list on the button's action dict; empty for the common single-action case.
        K1 on a sub-page is a normal key now (#30), so it too may carry a chain."""
        actions = self.page_actions.get(self.current_page, DEFAULT_ACTIONS)
        if idx < len(actions):
            extra = actions[idx].get("actions")
            if isinstance(extra, list):
                return extra
        return []

    def get_double_action(self, idx):
        """The 'double' action dict for button idx on the current page, or None
        when the key has no double-click action configured (issue #47)."""
        d = self.get_action_dict(idx).get("double")
        if isinstance(d, dict) and d.get("type", "none") not in ("none", ""):
            return d
        return None

    def is_double_key(self, k):
        """Cheap check used by the key listener to pick a per-key debounce: a
        double-click key needs a short anti-bounce so a deliberate second tap
        isn't swallowed by the normal debounce (issue #47)."""
        acts = self.page_actions.get(self.current_page)
        if acts and k < len(acts) and isinstance(acts[k], dict):
            d = acts[k].get("double")
            return isinstance(d, dict) and d.get("type", "none") not in ("none", "")
        return False

    # ── Pages and their names ─────────────────────────────────────────────────

    def page_target(self, act, idx):
        """Resolve a 'page' action's destination page id. Targets are stored
        by NAME (a string, resolved against the page-name registry) so they
        keep working even as page ids get minted/reordered elsewhere and so
        they match what's shown in the page picker (#52). Legacy configs
        stored a raw integer id, which is still accepted here; very old
        main-page-only actions had no target at all and mapped to the fixed
        sub-page idx+1, so that fallback is kept too."""
        t = act.get("target")
        if isinstance(t, str):
            pid = self.page_id_by_name(t)
            if pid is not None:
                return pid
            try:
                return int(t)
            except ValueError:
                return idx + 1
        try:
            return int(t)
        except (TypeError, ValueError):
            return idx + 1

    def _step_target(self, step):
        """Page id an 'also on press' or double-click step goes to, or None."""
        pid = self.page_id_by_name(step.get("target") or "")
        if pid is None:
            try:
                pid = int(step.get("target"))
            except (TypeError, ValueError):
                pid = None
        return pid

    def all_page_ids(self):
        """Every page id that exists or is referenced: 0, any page with stored
        actions/images, every 'page' action target anywhere, and every page
        registered by name even if nothing points at it yet (#52)."""
        ids = {0}
        ids.update(self.page_actions.keys())
        ids.update(self.page_images.keys())
        ids.update(_load_displaypad_page_names().keys())
        for page, acts in self.page_actions.items():
            for i, act in enumerate(acts):
                if act.get("type") == "page":
                    ids.add(self.page_target(act, i))
                # A page targeted only by an 'also on press' chain step or a
                # double-click action was already treated as "referenced" by
                # gc_orphan_pages (so it never gets deleted) — but it was
                # missing here, so it silently fell out of the page picker's
                # option list and got clobbered on reload. Resolve those
                # targets too so such a page always counts as existing.
                for step in (act.get("actions") or []):
                    if isinstance(step, dict) and step.get("type") == "page":
                        pid = self._step_target(step)
                        if pid is not None:
                            ids.add(pid)
                dbl = act.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page":
                    pid = self._step_target(dbl)
                    if pid is not None:
                        ids.add(pid)
        return {p for p in ids if isinstance(p, int) and p >= 0}

    def available_pages(self):
        """The sorted list of page ids that exist. Pages are no longer tied to
        a main-page button slot — any 'page' action on any page can target any
        page id (#30 carousel)."""
        return sorted(self.all_page_ids())

    def mint_page_id(self):
        """Allocate a fresh page id above every existing/referenced one (#30)."""
        return max(self.all_page_ids()) + 1

    def page_id_by_name(self, name):
        """Resolve a page name back to its id, or None if no page has that
        name right now (e.g. it was renamed or deleted elsewhere)."""
        for pid, nm in _load_displaypad_page_names().items():
            if nm == name:
                return pid
        return None

    def unique_page_name(self, name, exclude_id=None):
        """Disambiguate `name` against every other page's name (#55) by
        appending " (2)", " (3)", etc. Without this, two pages could end up
        with the identical name and the page dropdown would show that name
        twice -- looking like a duplicate entry, even though they're two
        distinct, valid pages underneath."""
        existing = {n for pid, n in _load_displaypad_page_names().items()
                    if pid != exclude_id}
        if name not in existing:
            return name
        i = 2
        while f"{name} ({i})" in existing:
            i += 1
        return f"{name} ({i})"

    def _derived_name(self, p):
        """The name page `p` gets when the registry has none yet."""
        if p == 0:
            return self.unique_page_name(self.translate("dp_page_main"), exclude_id=0)
        for page, acts in self.page_actions.items():
            for i, act in enumerate(acts):
                if act.get("type") == "page" and self.page_target(act, i) == p:
                    name = (act.get("action") or "").strip()
                    if name:
                        return self.unique_page_name(name, exclude_id=p)
        return self.unique_page_name(f"Page {p}", exclude_id=p)

    def page_name(self, p):
        """The name of page `p`, without storing anything: the registry's, or
        the one ensure_page_name() would give it."""
        names = _load_displaypad_page_names()
        if p in names:
            return names[p]
        return self._derived_name(p)

    def ensure_page_name(self, p):
        """The persisted name for a page, stored first if it has none yet.

        Page 0 is just another entry in the same registry -- it only happens
        to be the page the app opens on -- defaulting to a translated 'Main'
        the first time (after which it's stored data like any other page and
        can be renamed). A page with no registry entry yet but that's
        targeted by an existing 'page' action (pre-#52 configs) has its
        button-derived label adopted into the registry once, so the name
        becomes durable instead of being re-derived (and able to disappear)
        every time."""
        names = _load_displaypad_page_names()
        if p in names:
            return names[p]
        name = self._derived_name(p)
        names[p] = name
        _save_displaypad_page_names(names)
        return name

    def ensure_page(self, page_id, back_to=0):
        """Create page `page_id` if it doesn't exist, seeding K1 with a back
        button that returns to `back_to`. The back button is a normal 'page'
        action the user can re-target, re-icon or remove (#30)."""
        page_id = int(page_id)
        if page_id == 0 or page_id in self.page_actions:
            return
        acts = [dict(a) for a in DEFAULT_ACTIONS]
        acts[0] = {"type": "page", "action": self.translate("dp_page_back"),
                   "target": self.ensure_page_name(int(back_to)), "icon": self.back_icon}
        self.page_actions[page_id] = acts
        self.page_images.setdefault(page_id, {})
        self.save_sub_pages()

    def create_named_page(self, name):
        """Register a brand-new page that isn't targeted by any button yet
        (#52) -- it exists purely because it's in the name registry, and
        gc_orphan_pages() knows to leave it alone."""
        name = (name or "").strip() or f"Page {self.mint_page_id()}"
        name = self.unique_page_name(name)
        pid = _create_displaypad_page(name, existing_ids=self.all_page_ids())
        self.page_actions.setdefault(pid, [dict(a) for a in DEFAULT_ACTIONS])
        self.page_images.setdefault(pid, {})
        self.save_sub_pages()
        return pid

    def add_page(self, name, back_to):
        """A new page from the tab bar: minted, seeded with a back key to
        `back_to`, and named. Returns its id."""
        pid = self.mint_page_id()
        self.ensure_page(pid, back_to=back_to)
        names = _load_displaypad_page_names()
        names[pid] = self.unique_page_name(name, exclude_id=pid)
        _save_displaypad_page_names(names)
        return pid

    def rename_page(self, page_id, name):
        """Rename a page and update every stored reference to it (#52).
        References are stored by name, so without this cascade a button
        that pointed at the old name would silently stop resolving the
        moment the page got renamed -- that would make name-based
        references *less* durable than plain ids, not more."""
        page_id = int(page_id)
        name = (name or "").strip()
        if not name:
            return
        name = self.unique_page_name(name, exclude_id=page_id)
        old_name = self.ensure_page_name(page_id)
        _rename_displaypad_page(page_id, name)
        if old_name == name:
            return

        def _retarget(step):
            if isinstance(step, dict) and step.get("target") == old_name:
                step["target"] = name
                return True
            return False

        changed_actions = False
        for page, acts in self.page_actions.items():
            for act in acts:
                if not isinstance(act, dict):
                    continue
                if act.get("type") == "page" and _retarget(act):
                    changed_actions = True
                for step in (act.get("actions") or []):
                    if step.get("type") == "page" and _retarget(step):
                        changed_actions = True
                dbl = act.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page" and _retarget(dbl):
                    changed_actions = True
        if changed_actions:
            if 0 in self.page_actions:
                _save_displaypad_actions(self.page_actions[0])
            self.save_sub_pages()

        changed_timeout = False
        for _p, to in (self.page_timeout or {}).items():
            if isinstance(to, dict) and to.get("target") == old_name:
                to["target"] = name
                changed_timeout = True
        if changed_timeout:
            _save_displaypad_page_timeouts(self.page_timeout)

    def find_page_references(self, page_id):
        """Return every place that targets this page by name: a primary
        'page' action, an 'also on press' step, a double-click action, or a
        page-timeout target -- on ANY page, including this one. Used to
        warn before deleting a page that's still pointed at."""
        name = self.ensure_page_name(page_id)
        refs = []
        for from_page, acts in self.page_actions.items():
            for i, act in enumerate(acts or []):
                if not isinstance(act, dict):
                    continue
                if act.get("type") == "page" and act.get("target") == name:
                    refs.append({"from_page": from_page, "key": i, "kind": "action"})
                for step in (act.get("actions") or []):
                    if step.get("type") == "page" and step.get("target") == name:
                        refs.append({"from_page": from_page, "key": i, "kind": "also_on_press"})
                dbl = act.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page" and dbl.get("target") == name:
                    refs.append({"from_page": from_page, "key": i, "kind": "double_click"})
        for from_page, to in (self.page_timeout or {}).items():
            if isinstance(to, dict) and to.get("target") == name:
                refs.append({"from_page": from_page, "key": None, "kind": "timeout"})
        return refs

    def delete_page(self, page_id, switch_to=None):
        """Remove a page's data and its stored file. When it is the page on
        the pad, `switch_to(0)` is called first to leave it. Does NOT clean up
        references to it elsewhere (#54) -- those buttons are left as-is and
        will simply fail to resolve a target next time they're used; the
        delete-page UI is expected to have already warned about that."""
        page_id = int(page_id)
        if page_id == 0:
            return False
        if not _delete_displaypad_page(page_id):
            return False
        # Marks this id so a switch will never write outgoing-page state back
        # for it — needed because a switch isn't always synchronous (it can
        # defer itself via a scheduled retry while an upload or animation
        # runs), so cleanup done only *after* calling it below isn't
        # reliable; see the guard in begin_switch() for the actual fix.
        self.deleted_page_ids.add(page_id)
        self._drop_page(page_id)
        self.page_timeout.pop(page_id, None)
        if self.current_page == page_id:
            if switch_to is not None:
                switch_to(0)
            # Belt-and-suspenders: begin_switch() itself refuses to write
            # outgoing-page state for anything in deleted_page_ids, which is
            # what actually prevents the resurrection regardless of whether
            # the switch above ran synchronously or was deferred. These pops
            # just keep the in-memory dicts tidy for the synchronous case.
            self._drop_page(page_id)
            if self.prev_page == page_id:
                self.prev_page = 0
        self.save_sub_pages()
        _save_displaypad_page_timeouts(self.page_timeout)
        return True

    def _drop_page(self, page_id):
        self.page_actions.pop(page_id, None)
        self.page_images.pop(page_id, None)
        self.page_fullscreen.pop(page_id, None)

    def gc_orphan_pages(self):
        """Drop pages that nothing navigates to and that hold no content — e.g.
        a page minted when the user picked 'New page' but then retargeted the
        button to an existing page (#30). Conservative: never touches page 0, the
        current page, or any page registered by name (#52 -- a person may have
        pre-created it on purpose and not gotten around to using it yet). Also
        keeps any page with an image/fullscreen or a non-default action (the
        auto back button on K1 doesn't count as content)."""
        registered = set(_load_displaypad_page_names().keys())
        referenced = {0, self.current_page} | registered

        def _resolve(tgt, idx=0):
            if isinstance(tgt, str):
                pid = self.page_id_by_name(tgt)
                if pid is not None:
                    return pid
                try:
                    return int(tgt)
                except ValueError:
                    return None
            if isinstance(tgt, int):
                return tgt
            return None

        for page, acts in self.page_actions.items():
            for i, a in enumerate(acts):
                if a.get("type") == "page":
                    referenced.add(self.page_target(a, i))
                # A page targeted only by an 'also on press' chain step (#17) or a
                # double-click action (#47) is still referenced — don't GC it.
                for step in (a.get("actions") or []):
                    if step.get("type") == "page":
                        pid = _resolve(step.get("target"))
                        if pid is not None:
                            referenced.add(pid)
                dbl = a.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page":
                    pid = _resolve(dbl.get("target"))
                    if pid is not None:
                        referenced.add(pid)
        # A page reachable only as an auto-timeout destination is referenced too (#45).
        for _p, to in (self.page_timeout or {}).items():
            pid = _resolve((to or {}).get("target"))
            if pid is not None:
                referenced.add(pid)
        removed = False
        for p in list(self.page_actions.keys()):
            if p in referenced or p == 0:
                continue
            # A widget's last frame is held in memory now (#104), and it says
            # nothing about whether the page has anything on it.
            imgs = {k: v for k, v in (self.page_images.get(p, {}) or {}).items()
                    if v and v != self.blank_icon and dpi.widget_frame_of(v) is None}
            if imgs or self.page_fullscreen.get(p):
                continue
            empty = True
            for i, a in enumerate(self.page_actions.get(p, [])):
                t = a.get("type", "none")
                if t == "none" or (i == 0 and t in ("page", "back")):
                    continue  # default slot or the auto back button
                empty = False
                break
            if empty:
                self.page_actions.pop(p, None)
                self.page_images.pop(p, None)
                self.page_fullscreen.pop(p, None)
                removed = True
        if removed:
            self.save_sub_pages()

    # ── Page timeouts (#45) ───────────────────────────────────────────────────

    def set_page_timeout(self, page, mode, seconds, target):
        """Store a page's auto-timeout. `mode` is off/after/idle, `target` a
        page name or "prev" (stored by name, #52, like every page reference)."""
        if mode == "off":
            self.page_timeout.pop(page, None)
        else:
            self.page_timeout[page] = {"mode": mode, "seconds": seconds, "target": target}
        _save_displaypad_page_timeouts(self.page_timeout)

    def timeout_target(self, page):
        """Page id the timeout of `page` goes to."""
        to = self.page_timeout.get(page) or {}
        tgt = to.get("target", 0)
        if tgt == "prev":
            tgt = self.prev_page
        elif isinstance(tgt, str):
            pid = self.page_id_by_name(tgt)
            tgt = pid if pid is not None else 0
        try:
            return int(tgt)
        except (TypeError, ValueError):
            return 0

    # ── Icons ─────────────────────────────────────────────────────────────────

    def folder_icon_name(self, page, idx):
        """Where the auto folder-label icon for a nav button is written."""
        return dpi.generated_icon_name("folder", page, idx)

    def folder_icon_drawn(self, page, idx):
        """The drawn folder label for this key, whatever it is called, or None.

        Normally the one name. A legacy one only when the rename could not
        happen, which is a config directory that cannot be written to: that
        should not also cost the picture, since the key would silently fall
        back to the generic folder icon and the label would look lost.
        """
        for path in (dpi.generated_icon_name("folder", page, idx),
                     *dpi.legacy_icon_names("folder", page, idx)):
            if os.path.exists(path):
                return path
        return None

    def migrate_generated_icon_names(self):
        """Rename the icons this application drew to the one scheme (#95).

        The page and the key are known from where the entry sits, so the old
        name does not have to be parsed, only recognised. A file that cannot
        be renamed keeps its old name and its stored path, which still works;
        the point of this is a config directory that reads consistently, not
        something anything depends on. Idempotent, so it costs one directory
        walk on every later start and does nothing.
        """
        changed = False
        for page, imgs in self.page_images.items():
            if not isinstance(imgs, dict):
                continue
            for key, path in list(imgs.items()):
                if not isinstance(path, str):
                    continue
                try:
                    idx = int(key)
                except (TypeError, ValueError):
                    continue
                for kind in dpi.ICON_KINDS:
                    if path not in dpi.legacy_icon_names(kind, page, idx):
                        continue
                    new = dpi.generated_icon_name(kind, page, idx)
                    try:
                        if os.path.exists(path):
                            # Unconditionally, even when the new name is
                            # already taken: what the configuration points at
                            # is this key's picture by definition, and a file
                            # left under the new name by an earlier half
                            # finished run is not. Repointing without moving
                            # would have shown that stale one instead.
                            os.replace(path, new)
                    except OSError as e:
                        print(f"[DisplayPad] keeping {path}: {e}")
                        break
                    if os.path.exists(new):
                        imgs[key] = new
                        changed = True
                    break
        if changed:
            _save_displaypad_buttons(
                self.persistable_images(0, self.page_images.get(0, {})))
            self.save_sub_pages()

    def redraw_double_captions(self):
        """Give the double-click caption to labels drawn before it existed.

        A label is drawn when its key is saved, so one saved before #111 kept
        its single line until someone edited the key again. Only the
        application's own label files are touched, in place, so nothing
        stored changes; and a file is written only when its picture does,
        so this costs a start nothing once it has been done.
        """
        for page, actions in list(self.page_actions.items()):
            imgs = self.page_images.get(page, {})
            for idx, act in enumerate(actions):
                if not isinstance(act, dict) or act.get("type") not in ("keypress", "text"):
                    continue
                sub = dpi.double_caption(act.get("double"))
                path = imgs.get(str(idx), "")
                if not sub or path != dpi.generated_icon_name("label", page, idx) \
                        or not os.path.exists(path):
                    continue
                try:
                    dpi.make_label_icon(str(act.get("action", "")).strip(), path, sub,
                                        only_if_changed=True)
                except Exception as e:
                    print(f"[DisplayPad] label for page {page} key {idx + 1}: {e}")

    def is_nav_icon(self, path):
        """True if `path` is an auto-assigned navigation icon (folder/back) — as
        opposed to a user-chosen image we must not clobber."""
        if not path:
            return True
        return (path == self.blank_icon or path == self.back_icon
                or path == self.folder_icon
                or path.startswith(os.path.join(CONFIG_DIR, "dp_folder_")))

    def inject_page_icons(self, page):
        """Give every navigation button on `page` its icon: a custom icon set on
        the action, else the auto folder-label icon, else the shared folder icon
        — or the back icon for a legacy 'back' action. Applies to all pages now
        that any key can navigate (#30)."""
        stored = self.page_images.get(page, {})
        for i, act in enumerate(self.page_actions.get(page, DEFAULT_ACTIONS)):
            t = act.get("type")
            if t in ("page", "back"):
                # This key navigates, so it is ours to draw, not a plugin's.
                self.take_key_back(page, i)
            if t == "page":
                custom = act.get("icon")
                user_img = stored.get(str(i), "")
                if custom and os.path.exists(custom):
                    self.images[str(i)] = custom
                elif (user_img and os.path.exists(user_img)
                      and not self.is_nav_icon(user_img)):
                    # A custom image assigned to this switch button via the image
                    # editor wins over the default folder icon (#30 custom icon).
                    self.images[str(i)] = user_img
                else:
                    labeled = self.folder_icon_drawn(page, i)
                    self.images[str(i)] = labeled or self.folder_icon
            elif t == "back":
                self.images[str(i)] = self.back_icon

    def maybe_auto_label_icon(self, page, idx, btype, action, double=None):
        """Render and assign a text label icon for keypress/text keys (issue
        #31). Only fills an empty slot or replaces a previous auto-icon — a
        user-assigned image is never overwritten. True when a label was drawn
        for a key on a sub-page that is on the pad, which the caller shows and
        uploads (the page 0 path of save_page_action does that anyway)."""
        if btype not in ("keypress", "text") or not (action or "").strip():
            return False
        if page == self.current_page:
            cur = self.images.get(str(idx), "")
        else:
            cur = self.page_images.get(page, {}).get(str(idx), "")
        auto = (cur.startswith(os.path.join(CONFIG_DIR, "dp_label_"))
                or cur.startswith(os.path.join(CONFIG_DIR, "dp_folder_")))
        is_blank = ((not cur) or cur == self.blank_icon or not os.path.exists(cur)
                    or dpi.widget_frame_of(cur) is not None)   # a picture, not an icon
        if not (is_blank or auto):
            return False  # user-assigned image — keep it
        label = action.strip()
        icon_path = dpi.generated_icon_name("label", page, idx)
        try:
            dpi.make_label_icon(label, icon_path, dpi.double_caption(double))
        except Exception:
            return False
        self.page_images.setdefault(page, {})[str(idx)] = icon_path
        self.take_key_back(page, idx)
        if page == self.current_page:
            self.images[str(idx)] = icon_path
            return page != 0
        return False

    # ── Saving a key ──────────────────────────────────────────────────────────

    def save_page_action(self, page, idx, btype, action, extra=None,
                         target=None, icon=None, double=None):
        """Save one button's action and persist it.

        `extra`  — optional secondary "also on press" chain (issue #16/#17);
                   when omitted, any existing chain on the button is preserved.
        `target` — for a 'page' action, the destination page id. None/"new"
                   mints a fresh page (or reuses the button's current target).
        `icon`   — optional custom image path for a 'page' button (#30).
        `double` — optional double-click action dict (issue #47). None preserves
                   any existing one (so runtime set_key edits don't wipe it); a
                   dict sets it; a dict with type 'none' clears it.

        The editor always switches to the page being edited first, so
        `page == current_page` and `images` is this page's live map. Returns
        a SaveResult; showing and uploading the key is the caller's part."""
        actions = self.page_actions.setdefault(
            page, [dict(a) for a in DEFAULT_ACTIONS])
        old = actions[idx] if idx < len(actions) else {}
        if not isinstance(old, dict):
            old = {}
        entry = {"type": btype, "action": action}
        if extra is None:
            if isinstance(old.get("actions"), list) and old["actions"]:
                entry["actions"] = old["actions"]
        elif extra:
            entry["actions"] = list(extra)
        # The key's text over its picture stays with it.
        if isinstance(old.get("text"), dict):
            entry["text"] = old["text"]
        # Double-click action (#47): None = preserve, dict = authoritative.
        if double is None:
            od = old.get("double")
            if isinstance(od, dict) and od.get("type", "none") not in ("none", ""):
                entry["double"] = od
        elif isinstance(double, dict) and double.get("type", "none") not in ("none", ""):
            entry["double"] = double

        # ── 'page' action: resolve/allocate target + custom icon ──────────────
        if btype == "page":
            if target in (None, "", "new"):
                target = (self.page_target(old, idx)
                          if old.get("type") == "page" and old.get("target") not in (None, "")
                          else self.mint_page_id())
            target = int(target)
            self.ensure_page(target, back_to=page)
            entry["target"] = self.ensure_page_name(target)
            keep_icon = icon or (old.get("icon") if old.get("type") == "page" else None)
            if keep_icon:
                entry["icon"] = keep_icon

        actions[idx] = entry
        self.page_images.setdefault(page, {})

        # A key given a different action stops showing what the old one drew.
        # Its frame was kept (it is that widget's key), so moving a key from
        # one widget to another left the first widget's last picture on it,
        # in the editor and on the pad, until the new one drew something:
        # Now Playing with nothing playing never does (#99).
        if btype != old.get("type", "none"):
            shown = (self.images if page == self.current_page
                     else self.page_images[page])
            if dpi.widget_frame_of(shown.get(str(idx))) is not None:
                icon = self.stored_icons(page).get(str(idx))
                if not (icon and dpi.widget_frame_of(icon) is None
                        and os.path.exists(icon)):
                    icon = self.blank_icon
                shown[str(idx)] = icon
                self.take_key_back(page, idx)

        # Auto-generate a label icon for keypress/text keys with no custom image
        # yet, so they show what they do instead of staying blank (issue #31).
        label_drawn = self.maybe_auto_label_icon(page, idx, btype, action,
                                                 entry.get("double"))

        # ── Button image ──────────────────────────────────────────────────────
        # Only the current page owns the live `images` map; a 'set_key'
        # action (#18) can redefine a key on a page the user isn't viewing, so
        # for any other page we edit that page's stored image dict directly and
        # leave the visible page (and its gif/fullscreen state) untouched.
        is_current = (page == self.current_page)
        imgs = self.images if is_current else self.page_images[page]
        if btype == "page":
            icon_path = entry.get("icon")
            existing = imgs.get(str(idx), "")
            if icon_path and os.path.exists(icon_path):
                imgs[str(idx)] = icon_path
            elif existing and os.path.exists(existing) and not self.is_nav_icon(existing):
                pass  # keep an image the user assigned via the editor (#30 custom icon)
            else:
                icon_path = self.folder_icon_name(page, idx)
                dpi.make_folder_icon(self.folder_icon, action, icon_path)
                imgs[str(idx)] = icon_path
            if is_current:
                self.fullscreen_group.discard(idx)
                self.gif_frames.pop(idx, None)
        elif btype == "back":
            imgs[str(idx)] = self.back_icon
        elif btype == "none":
            # Blank the slot, but never wipe a user-assigned decorative image.
            if self.is_nav_icon(imgs.get(str(idx), "")):
                imgs[str(idx)] = self.blank_icon
        elif btype not in ("keypress", "text") and self.is_nav_icon(
                imgs.get(str(idx), "")) and imgs.get(str(idx)) != self.blank_icon:
            # Was a nav button, now a non-visual action — drop the nav icon.
            imgs[str(idx)] = self.blank_icon

        # ── Persist ───────────────────────────────────────────────────────────
        if is_current:
            self.sync_live_images(page)
        if page == 0:
            _save_displaypad_actions(actions)
            _save_displaypad_buttons(
                self.persistable_images(0, self.page_images.get(0, {})))
        else:
            self.save_sub_pages()
        return SaveResult(is_current, label_drawn)

    # ── Plugin frames are not key icons ───────────────────────────────────────
    # A plugin paints a key by writing its frame straight into the live image
    # map (that is how the panel tile and a full page upload pick it up) and
    # into page 0's map, then calling push_plugin_image(). Those two maps are
    # also what we persist, so a frame written a moment after a page switch
    # became the stored icon of whatever key sat there on the new page, and a
    # plugin running on a sub-page overwrote Main's icon for its key index.
    # Both survived the plugin being stopped, because by then they were in
    # displaypad_pages/*.json (#69/#70). A frame is a picture on the device,
    # not a key's icon: it is kept out of everything we store, and the key
    # keeps whatever icon it was given.

    def mark_plugin_frame(self, idx):
        """Note that key `idx` now shows a plugin frame rather than its icon.
        Both maps a plugin writes are marked: the live one, which belongs to
        the page that is up right now, and Main's, which they write directly."""
        for page in (self.current_page, 0):
            self.plugin_frame_keys.setdefault(page, set()).add(int(idx))

    def take_key_back(self, page, idx):
        """This key's image was assigned here, not by a plugin, so it is an
        icon again and gets stored like any other."""
        keys = self.plugin_frame_keys.get(page)
        if keys:
            keys.discard(int(idx))

    def stored_icons(self, page):
        """The icons `page` has on disk, key by key."""
        if page == 0:
            return _load_displaypad_buttons()
        return _load_displaypad_pages().get(str(page), {}).get("buttons") or {}

    def icons_restored(self, page, live, keep=()):
        """`live` with every plugin frame replaced by the icon that key
        actually carries, except on the keys in `keep`.

        The stored config is where that icon comes from: it is the one record
        of it a frame cannot have overwritten, since this is the only place
        that writes it and every assignment here takes the key back first. A
        frame an older version did store there all the same (#104) is not
        taken for an icon; that key has none.

        On a key in `keep` the widget's frame stays, but only its own: one
        drawn for another page or another key is a picture that landed in the
        wrong map, and the key gets its icon back like any other."""
        imgs = dict(live)
        keep = {int(k) for k in keep}
        marked = self.plugin_frame_keys.get(page, set())
        stored = None
        for idx in {int(k) for k in imgs} | set(marked):
            k = str(idx)
            frame = dpi.widget_frame_of(imgs.get(k))
            if idx in keep:
                if frame is None or frame == (page, idx):
                    continue
            elif frame is None and idx not in marked:
                continue                  # the key's own icon
            if stored is None:
                stored = self.stored_icons(page)
            icon = stored.get(k)
            if icon and dpi.widget_frame_of(icon) is None:
                imgs[k] = icon
            else:
                imgs.pop(k, None)
        return imgs

    def persistable_images(self, page, live=None):
        """The map to store for `page` (the current page's live map by
        default). Nothing a plugin painted goes in: the config records what a
        key carries, and a widget frame is a picture on the device."""
        return self.icons_restored(page, self.images if live is None else live)

    def plugin_key_slots(self):
        """Key indexes on the live page that a plugin action type owns.

        The page stamp on a queued frame closes the common case, but not the
        instant between the page changing and a plugin's stop() taking effect:
        a frame composed for the old page but pushed a moment later carries the
        new page's number and would be written (#69/#70). A plugin only ever
        paints a key its own action type sits on, so a key the live page has
        given to a static image or to another action is never a plugin's to
        write, whatever the stamp says.

        Returns None when that cannot be established, and then nothing is
        filtered: refusing to paint is worse than painting once too often.
        """
        try:
            types = self.plugin_types()
            if types is None:
                return None
            types = set(types)
        except Exception:
            return None
        if not types:
            return None
        # The clock and PC-info keys draw like widgets (live.py).
        types |= set(LIVE_TYPES)
        if self.current_page not in self.page_actions:
            return None      # nothing known about this page, so do not filter
        actions = self.page_actions[self.current_page]
        # An empty action list is knowledge, not ignorance: a page where
        # nothing is assigned is exactly the page on which no plugin frame
        # belongs. Treating it like "cannot be established" turned the guard
        # off on the page that needed it most (#70, #82).
        return {i for i, a in enumerate(actions)
                if isinstance(a, dict) and a.get("type") in types}

    def images_for_page(self, page):
        """The map to put on screen for `page`, from what we hold for it.

        A frame is kept on the keys this page gives to a plugin, where the
        widget belongs and where dropping it would flash the static icon
        between the switch and the plugin's next frame. On every other key it
        is somebody else's picture and the key gets its icon back."""
        live = self.page_images.get(page, {})
        owned = self.plugin_key_slots()
        if owned is None:
            # Cannot tell whose key is whose, so every widget keeps its frame;
            # one drawn for another page is still not this page's.
            owned = range(NUM_KEYS)
        return self.icons_restored(page, live, keep=owned)

    def drop_foreign_frame(self, idx, owned=None):
        """Give key `idx` its icon back if the live map holds a widget frame
        that is not this key's on this page.

        Widgets write their frame into the live map themselves, from their
        own thread. One that composed a frame for the page just left and
        wrote it a moment after the switch put it into the new page's map,
        where the editor drew it and the next full upload sent it to the pad:
        a clock and a disk on a page of function keys (#99, #112). The device
        queue already refuses such a frame; this is the same refusal for the
        map. Returns (page, key) of the frame dropped, or None.
        """
        k = str(idx)
        frame = dpi.widget_frame_of(self.images.get(k))
        if frame is None:
            return None
        if frame == (self.current_page, idx):
            if owned is None:
                owned = self.plugin_key_slots()
            if owned is None or idx in owned:
                return None
        icon = self.stored_icons(self.current_page).get(k)
        if not (icon and dpi.widget_frame_of(icon) is None and os.path.exists(icon)):
            icon = self.blank_icon
        self.images[k] = icon
        return frame

    # ── The live page ─────────────────────────────────────────────────────────

    def sync_live_images(self, page=None):
        """Copy the live map into the page's map, widget frames included.

        Leaving them out (as this did until #104) meant a page came back with
        its widget keys showing the icon underneath, and a widget only draws
        when its value changes: a disk that stays as full as it was, or a
        battery that is still charging, left the key black until it did. The
        frame is the last thing that widget drew for this page, which is what
        the key should show until it draws again. Nothing here is stored:
        every write to disk goes through persistable_images, which takes the
        frames out, and images_for_page drops a frame drawn for another page.
        """
        page = self.current_page if page is None else page
        self.page_images[page] = dict(self.images)

    def save_sub_pages(self):
        """Persist every non-main page to displaypad_pages.json. Page ids are no
        longer bounded to 1..12 (a carousel can mint arbitrary ids — #30), so we
        iterate the actual page keys instead of a fixed range."""
        out = {}
        for p in set(self.page_actions) | set(self.page_images):
            if p == 0:
                continue
            out[str(p)] = {
                "v": 2,   # page-model version — >=2 means don't re-derive back (#30)
                "buttons": self.persistable_images(p, self.page_images.get(p, {})),
                "actions": self.page_actions.get(p, [dict(a) for a in DEFAULT_ACTIONS]),
                "fullscreen": self.page_fullscreen.get(p),
            }
        _save_displaypad_pages(out)

    def persist_images(self):
        """Write the current page's images where that page keeps them.

        `images` is the live map of whichever page is showing, not
        necessarily Main's, so which file it goes to depends on the page."""
        if self.current_page == 0:
            _save_displaypad_buttons(self.persistable_images(0))
        else:
            self.sync_live_images()
            self.save_sub_pages()

    def begin_switch(self, page_num):
        """First half of a page switch: put the page being left away and make
        `page_num` the current one. Returns the page left, or None when it was
        just deleted and nothing of it was kept.

        Its state is saved unless it was just deleted via delete_page(). That
        function tries to clean up the entries this writes right after calling
        for the switch, but a switch isn't always synchronous: if an upload or
        an animation was running at the time, the switch was a scheduled retry
        and the real switch — and this save — happens later, *after*
        delete_page() already returned and did its cleanup. That silently
        resurrected the deleted page in memory, invisible until the next
        unrelated save_sub_pages() call (e.g. deleting another page afterward)
        wrote its file back to disk. Guarding it here, at the point the write
        actually happens, is correct regardless of timing."""
        old_page = self.current_page
        kept = None
        if old_page not in self.deleted_page_ids:
            self.prev_page = old_page   # 'previous page' timeout target (#45)
            self.sync_live_images(old_page)
            if self.fullscreen_group:
                self.page_fullscreen.setdefault(old_page, None)  # keep existing path
            kept = old_page
        self.current_page = page_num
        return kept

    def load_page(self, page_num):
        """Second half of a page switch, after the page's widget services were
        brought in line: its live map, navigation icons, blanks, GIF frames
        and fullscreen GIF."""
        self.images = self.images_for_page(page_num)
        self.gif_frames = {}
        self.fullscreen_group = set()
        self.panel_path = None

        # Inject navigation icons for every 'page'/'back' button on this page
        # (any key can navigate now — #30).
        self.inject_page_icons(page_num)

        # Fill empty buttons with blank image (device keeps old image otherwise)
        self.fill_blanks()

        # Load GIF frames for new page
        for k, path in self.images.items():
            if path and os.path.exists(path) and path.lower().endswith('.gif'):
                frames = dpi.load_gif_frames(path, self.key_text(page_num, int(k)))
                if frames:
                    self.gif_frames[int(k)] = frames

        # Restore fullscreen GIF if any
        fs = self.page_fullscreen.get(page_num)
        if fs and os.path.exists(fs):
            self.panel_path = fs      # one picture over the whole panel (service: set_fullscreen)

    def fill_blanks(self):
        """Every key without a picture gets the blank one, so the pad does not
        keep showing what it showed before."""
        for idx in range(NUM_KEYS):
            if str(idx) not in self.images:
                self.images[str(idx)] = self.blank_icon

    def load_fullscreen_gif(self, path, save=True):
        """Split fullscreen GIF into 12 tile frame lists, populate state.
        Fullscreen covers every button — page/back actions still fire on
        key press but visually the whole panel is occupied by the GIF.
        False when it is not an animation."""
        tiles = dpi.split_gif_to_tiles(path)
        if not tiles:
            return False
        self.fullscreen_group = set(range(NUM_KEYS))
        for idx in range(NUM_KEYS):
            self.gif_frames[idx] = self._with_text(self.current_page, idx, tiles[idx])
        if save:
            self.page_fullscreen[self.current_page] = path
            if self.current_page == 0:
                _save_displaypad_fullscreen(path)
            else:
                self.save_sub_pages()
        return True

    def set_panel(self, path):
        """Put `path` (a picture or GIF) over the whole panel on the page on
        the pad, and store it with the page."""
        self.panel_path = path
        self.page_fullscreen[self.current_page] = path
        if self.current_page == 0:
            _save_displaypad_fullscreen(path)
        else:
            self.save_sub_pages()

    def clear_fullscreen(self, page):
        """Forget the fullscreen of a page (stored, and off the page on the
        pad). True if the page had one. The keys keep their own pictures."""
        had = bool(self.page_fullscreen.get(page))
        self.page_fullscreen[page] = None
        if page == self.current_page:
            self.panel_path = None
        if page == self.current_page and self.fullscreen_group:
            self.fullscreen_group = set()
            self.gif_frames = {}
            for k, path in self.images.items():
                if path and os.path.exists(path) and path.lower().endswith('.gif'):
                    frames = dpi.load_gif_frames(path, self.key_text(page, int(k)))
                    if frames:
                        self.gif_frames[int(k)] = frames
        if had:
            if page == 0:
                _clear_displaypad_fullscreen()
            else:
                self.save_sub_pages()
        return had

    def set_button_image(self, key_index, path):
        """Give a key of the page on the pad a picture (or a GIF) and store it.
        Returns the GIF's frames, or None for a still picture."""
        self.images[str(key_index)] = path
        self.take_key_back(self.current_page, key_index)
        self.persist_images()
        frames = (dpi.load_gif_frames(path, self.key_text(self.current_page, key_index))
                  if path.lower().endswith('.gif') else None)
        if frames:
            self.gif_frames[key_index] = frames
        else:
            self.gif_frames.pop(key_index, None)
        return frames

    def clear_key_image(self, idx):
        """Blank one key's picture on the page on the pad, and store that."""
        self.images[str(idx)] = self.blank_icon
        self.take_key_back(self.current_page, idx)
        self.gif_frames.pop(idx, None)
        self.fullscreen_group.discard(idx)
        self.persist_images()

    def clear_page(self):
        """Blank every key of the page on the pad and reset its actions,
        keeping the page keys of the main page so navigation still works."""
        # Set all buttons to blank, but keep page buttons + their actions so
        # navigation between pages still works after a clear.
        page_btns = {}
        kept_page_actions = {}  # idx -> action dict for "page" buttons on main
        cur_actions = self.page_actions.get(self.current_page, DEFAULT_ACTIONS)
        if self.current_page == 0:
            for i, act in enumerate(cur_actions):
                if act.get("type") == "page":
                    labeled = self.folder_icon_drawn(self.current_page, i)
                    page_btns[str(i)] = labeled or self.folder_icon
                    kept_page_actions[i] = dict(act)
        self.images = {str(i): self.blank_icon for i in range(NUM_KEYS)}
        self.images.update(page_btns)
        # Every key on this page is ours again, whatever a plugin was painting.
        self.plugin_frame_keys.pop(self.current_page, None)
        self.gif_frames = {}
        self.fullscreen_group = set()
        self.panel_path = None
        self.page_fullscreen[self.current_page] = None

        # Reset button actions to defaults, but preserve "page" buttons on main
        # and the "back" action on K1 of sub-pages.
        new_actions = [dict(a) for a in DEFAULT_ACTIONS]
        for idx, act in kept_page_actions.items():
            if 0 <= idx < len(new_actions):
                new_actions[idx] = act
        self.page_actions[self.current_page] = new_actions
        return page_btns, new_actions

    def persist_cleared_page(self, page_btns, new_actions):
        """Store what clear_page() left."""
        if self.current_page == 0:
            save_imgs = dict(page_btns)  # only keep page buttons in config
            _save_displaypad_buttons(save_imgs)
            _clear_displaypad_fullscreen()
            _save_displaypad_actions(new_actions)
        else:
            self.page_images[self.current_page] = {}
            self.save_sub_pages()

    def upload_set(self):
        """{key: path or None} the pad is to be sent for the page on it: every
        key with a picture that exists, and the keys of a fullscreen GIF."""
        assigned = {int(k): v for k, v in self.images.items()
                    if v and os.path.exists(v)}
        # Include gif_frames keys not in images (fullscreen GIF loaded without individual paths)
        for k in self.gif_frames:
            if k not in assigned:
                assigned[k] = None
        # A key with text but no picture is sent as a black tile with it.
        for k in range(NUM_KEYS):
            if k not in assigned and self.key_text(self.current_page, k):
                assigned[k] = None
        return assigned

    # ── Text over a key's picture ─────────────────────────────────────────────

    def key_text(self, page, idx):
        """The text overlay stored for a key, or None (older configs have none)."""
        acts = self.page_actions.get(page)
        if acts and 0 <= idx < len(acts) and isinstance(acts[idx], dict):
            return dpi.stored_text_overlay(acts[idx].get("text"))
        return None

    def _with_text(self, page, idx, frames):
        """`frames` ([(bgr, ms)]) with the key's text drawn on each."""
        cfg = self.key_text(page, idx)
        if not cfg or not frames:
            return frames
        return [(dpi.overlay_bgr(bgr, cfg), ms) for bgr, ms in frames]

    def set_key_text(self, page, idx, cfg):
        """Store a key's text overlay (a cleaned dict, or None to take it
        away) with its action."""
        actions = self.page_actions.setdefault(
            page, [dict(a) for a in DEFAULT_ACTIONS])
        while len(actions) <= idx:
            actions.append({"type": "none", "action": ""})
        if not isinstance(actions[idx], dict):
            actions[idx] = {"type": "none", "action": ""}
        if cfg:
            actions[idx]["text"] = cfg
        else:
            actions[idx].pop("text", None)
        if page == 0:
            _save_displaypad_actions(actions)
        else:
            self.save_sub_pages()

    def refresh_key_frames(self, idx):
        """Rebuild the GIF frames of a key of the page on the pad after its
        text changed. Returns the frames, or None if the key shows no GIF."""
        page = self.current_page
        frames = None
        if idx in self.fullscreen_group:
            fs = self.page_fullscreen.get(page)
            tiles = dpi.split_gif_to_tiles(fs) if fs and os.path.exists(fs) else None
            if tiles:
                frames = self._with_text(page, idx, tiles[idx])
        else:
            path = self.images.get(str(idx))
            if path and os.path.exists(path) and path.lower().endswith('.gif'):
                frames = dpi.load_gif_frames(path, self.key_text(page, idx))
        if frames:
            self.gif_frames[idx] = frames
        else:
            self.gif_frames.pop(idx, None)
        return frames

    def still_bgr(self, idx, rotation=0):
        """A key of the page on the pad as a still tile with its text: its
        picture, or black when it has none."""
        cfg = self.key_text(self.current_page, idx)
        path = self.images.get(str(idx))
        if path and os.path.exists(path):
            return dpi.image_to_bgr(path, rotation, cfg)
        return dpi.rotate_bgr(dpi.blank_bgr_with_text(cfg), rotation)
