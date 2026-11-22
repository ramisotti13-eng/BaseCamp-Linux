"""Plugin Manager panel -- view, enable, disable, install plugins."""
import json
import os
import shutil
import threading
import urllib.request
import zipfile
import tempfile
import customtkinter as ctk
import shared.ui as UI
from PIL import Image

from shared.ui_helpers import (BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, YLW,
                               BORDER, cap_scroll_speed)
from shared.ui.tokens import FG_FAINT
from shared.config import CONFIG_DIR
from shared.plugins import has_requirement

_PLUGINS_DIR = os.path.join(CONFIG_DIR, "plugins")
_PLUGINS_INDEX_URL = "https://raw.githubusercontent.com/ramisotti13-eng/basecamp-plugins/main/plugins.json"
_REPO_BASE = "https://github.com/ramisotti13-eng/basecamp-plugins/tree/main/"

# Type badge colors
_PLUGIN_LIST_W = 250   # list column beside the detail


def _has_module(name):
    """Is a dependency importable in the interpreter this app runs in?

    Shared with the loader so the dot in the list and the warning on the
    console can never disagree about the same package (#76).
    """
    return has_requirement(name)


_TYPE_COLORS = {
    "panel":   ("#0ea5e9", "#0c4a6e"),
    "service": ("#22c55e", "#14532d"),
    "action":  ("#f59e0b", "#78350f"),
}


def _version_tuple(s):
    """Parse a dotted version like '1.2.3' into a sortable tuple.
    Non-numeric parts become 0 so 'v1.2' parses the same as '1.2'."""
    parts = []
    for p in str(s or "").lstrip("v").split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def fetch_plugin_index(timeout=8):
    """The published plugins, from the index in the plugins repository.

    Used by this screen and by the application's own check at start-up, which
    runs whether or not this screen is ever opened (#103)."""
    req = urllib.request.Request(_PLUGINS_INDEX_URL)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("plugins", []) if isinstance(data, dict) else []


def updates_for(plugins, manifests):
    """The published entries newer than what is installed, by id."""
    out = {}
    for pinfo in plugins or []:
        pid = pinfo.get("id")
        if pid in manifests and _version_tuple(pinfo.get("version", "0")) > \
                _version_tuple(manifests[pid].get("version", "0")):
            out[pid] = pinfo
    return out


def _download_repo(url):
    """Fetch the repository a plugin URL points into, as one zip.

    Returns (temporary directory, extracted root, path inside the repo). The
    caller removes the directory. All published plugins live in one
    repository, which is what lets an update of several share one download.
    """
    url = url.rstrip("/")
    parts = url.split("github.com/", 1)[1].split("/")
    owner = parts[0]
    repo = parts[1] if len(parts) > 1 else ""
    branch = "main"
    subpath = ""
    if len(parts) > 3 and parts[2] == "tree":
        branch = parts[3]
        subpath = "/".join(parts[4:]) if len(parts) > 4 else ""
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    tmp = tempfile.mkdtemp()
    zip_path = os.path.join(tmp, "repo.zip")
    req = urllib.request.Request(zip_url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        with open(zip_path, "wb") as f:
            f.write(resp.read())
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    return tmp, os.path.join(tmp, f"{repo}-{branch}"), subpath


def _repo_branch_path(url):
    """(repository URL, branch, path inside it) of a plugin URL."""
    url = url.rstrip("/")
    if "/tree/" not in url:
        return url, "main", ""
    repo, rest = url.split("/tree/", 1)
    branch, _, sub = rest.partition("/")
    return repo, branch, sub


def _plugin_dir_in(extracted_root, subpath):
    """Where in an extracted repository the plugin is."""
    if subpath:
        return os.path.join(extracted_root, subpath)
    if os.path.isfile(os.path.join(extracted_root, "plugin.json")):
        return extracted_root
    for d in os.listdir(extracted_root):
        candidate = os.path.join(extracted_root, d)
        if os.path.isdir(candidate) and os.path.isfile(
                os.path.join(candidate, "plugin.json")):
            return candidate
    raise FileNotFoundError("No plugin.json found in repository")


class PluginManagerPanel(ctk.CTkFrame):

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app = app
        self._rows = {}
        self._expanded = set()
        self._icon_cache = {}
        self._available = []  # fetched from plugins.json
        self._build_ui()

    def T(self, key, **kw):
        return self._app.T(key, **kw)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    def _available_info(self, pid):
        """Look up a plugin entry in the cached available list by id."""
        for pinfo in self._available:
            if pinfo.get("id") == pid:
                return pinfo
        return None

    def _has_update(self, pid, installed_info):
        """Return the available pinfo dict if an update is published, else None."""
        avail = self._available_info(pid)
        if not avail:
            return None
        installed_ver = _version_tuple(installed_info.get("version", "0"))
        avail_ver     = _version_tuple(avail.get("version", "0"))
        return avail if avail_ver > installed_ver else None

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        """List on the left, everything about the selected plugin on the right.

        The old screen was a column of cards you had to expand one at a time,
        so the help text, the type badges and the actions were only visible for
        whichever card happened to be open, and the available plugins sat in a
        second list below all of them.
        """
        split = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        split.pack(fill="both", expand=True)
        split.grid_columnconfigure(0, weight=0, minsize=_PLUGIN_LIST_W)
        split.grid_columnconfigure(1, weight=1)
        split.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(split, fg_color=BG, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=(10, 10))
        cap_scroll_speed(left)

        head = ctk.CTkFrame(left, fg_color="transparent")
        head.pack(fill="x", pady=(0, 4))
        self._title_lbl = UI.SectionLabel(head, text=self.T("pluginmgr_installed"))
        self._title_lbl.pack(side="left")
        self._count_lbl = ctk.CTkLabel(head, text="", font=(UI.FONT_FAMILY, 10),
                                       text_color=FG2)
        self._count_lbl.pack(side="right")
        # Shown only while there is something to update (#103): one press for
        # all of them, rather than selecting each plugin to find its button.
        self._update_all_btn = UI.PrimaryButton(left, "", self._update_all,
                                                height=UI.CTRL_H_SM)

        self._list_frame = ctk.CTkFrame(left, fg_color="transparent")
        self._list_frame.pack(fill="x")

        avail_head = ctk.CTkFrame(left, fg_color="transparent")
        avail_head.pack(fill="x", pady=(14, 4))
        self._avail_title = UI.SectionLabel(avail_head,
                                            text=self.T("pluginmgr_available"))
        self._avail_title.pack(side="left")
        self._refresh_btn = UI.GhostButton(avail_head, self.T("pluginmgr_reload"),
                                           self._fetch_available, width=90,
                                           height=UI.CTRL_H_SM)
        self._refresh_btn.pack(side="right")
        self._reg(self._refresh_btn, "pluginmgr_reload")

        self._avail_list = ctk.CTkFrame(left, fg_color="transparent")
        self._avail_list.pack(fill="x")
        self._avail_status = ctk.CTkLabel(
            self._avail_list, text=self.T("pluginmgr_loading"),
            font=(UI.FONT_FAMILY, 10), text_color=FG2, anchor="w")
        self._avail_status.pack(fill="x", pady=4)

        self._restart_lbl = ctk.CTkLabel(left, text="", font=(UI.FONT_FAMILY, 10),
                                         text_color=YLW, anchor="w",
                                         wraplength=_PLUGIN_LIST_W - 20,
                                         justify="left")
        self._restart_lbl.pack(fill="x", pady=(10, 0))

        # ── Manual install, folded away ──
        self._manual_frame = ctk.CTkFrame(left, fg_color="transparent")
        self._manual_frame.pack(fill="x", pady=(12, 0))
        manual_toggle = ctk.CTkLabel(
            self._manual_frame, text=self.T("pluginmgr_manual_install"),
            font=(UI.FONT_FAMILY, 10), text_color=FG2, cursor="hand2", anchor="w")
        self._reg(manual_toggle, "pluginmgr_manual_install")
        manual_toggle.pack(fill="x")
        self._manual_body = ctk.CTkFrame(self._manual_frame, fg_color="transparent")
        self._install_entry = ctk.CTkEntry(
            self._manual_body, placeholder_text=self.T("pluginmgr_install_url"),
            fg_color=BG3, border_color=BORDER, text_color=FG,
            font=(UI.FONT_FAMILY, 10), height=UI.CTRL_H_SM)
        self._install_entry.pack(fill="x", pady=(6, 4))
        btn_row = ctk.CTkFrame(self._manual_body, fg_color="transparent")
        btn_row.pack(fill="x")
        self._browse_btn = UI.GhostButton(btn_row, self.T("pluginmgr_install_browse"),
                                          self._browse_folder, width=110,
                                          height=UI.CTRL_H_SM)
        self._browse_btn.pack(side="left")
        self._install_btn = UI.GhostButton(btn_row, self.T("pluginmgr_install_btn"),
                                           self._do_install, width=110,
                                           height=UI.CTRL_H_SM)
        self._install_btn.pack(side="right")
        self._install_status = ctk.CTkLabel(
            self._manual_body, text="", font=(UI.FONT_FAMILY, 10), text_color=FG2,
            anchor="w", wraplength=_PLUGIN_LIST_W - 20, justify="left")
        self._install_status.pack(fill="x", pady=(4, 0))
        self._manual_open = False
        manual_toggle.bind("<Button-1>", lambda e: self._toggle_manual())

        self._hint_lbl = ctk.CTkLabel(
            left, text=self.T("pluginmgr_hint"), font=(UI.FONT_FAMILY, 9),
            text_color=FG_FAINT, anchor="w", justify="left",
            wraplength=_PLUGIN_LIST_W - 20)
        self._hint_lbl.pack(fill="x", pady=(12, 0))
        self._more_lbl = ctk.CTkLabel(
            left, text=self.T("pluginmgr_more"), font=(UI.FONT_FAMILY, 9),
            text_color=FG_FAINT, anchor="w", justify="left",
            wraplength=_PLUGIN_LIST_W - 20)
        self._more_lbl.pack(fill="x", pady=(4, 0))

        # ── Detail ──
        self._detail = ctk.CTkScrollableFrame(split, fg_color=BG, corner_radius=0)
        self._detail.grid(row=0, column=1, sticky="nsew", padx=12, pady=(10, 10))
        cap_scroll_speed(self._detail)

        self._selected = None
        self._populate()
        self._fetch_available()

    def _toggle_manual(self):
        if self._manual_open:
            self._manual_body.pack_forget()
        else:
            self._manual_body.pack(fill="x")
        self._manual_open = not self._manual_open

    # ── Installed plugins list ───────────────────────────────────────────────

    def _populate(self):
        for w in self._list_frame.winfo_children():
            w.destroy()
        self._rows.clear()

        pm = self._app._plugin_manager
        manifests = pm._manifests

        if not manifests:
            ctk.CTkLabel(self._list_frame, text=self.T("pluginmgr_empty"),
                         font=(UI.FONT_FAMILY, 10), text_color=FG2, anchor="w",
                         justify="left",
                         wraplength=_PLUGIN_LIST_W - 20).pack(fill="x", pady=6)
            self._count_lbl.configure(text="")
            self._render_detail()
            return

        for pid in sorted(manifests.keys()):
            self._list_row(self._list_frame, pid, manifests[pid], installed=True)

        total = len(manifests)
        active = sum(1 for p in manifests if pm.is_loaded(p))
        self._count_lbl.configure(
            text=self.T("pluginmgr_count", total=total, active=active))
        pending = updates_for(self._available, manifests)
        if pending and not getattr(self, "_updating_all", False):
            self._update_all_btn.configure(
                text=self.T("pluginmgr_update_all", n=len(pending)), state="normal")
            self._update_all_btn.pack(fill="x", pady=(0, 6), before=self._list_frame)
        elif not getattr(self, "_updating_all", False):
            self._update_all_btn.pack_forget()
        if self._selected is None or self._selected[0] not in manifests:
            self._select(sorted(manifests)[0], True)
        else:
            self._render_detail()

    def _list_row(self, parent, pid, info, installed):
        """One line: state, name, version. The detail is on the right."""
        pm = self._app._plugin_manager
        selected = self._selected is not None and self._selected[0] == pid
        row = ctk.CTkFrame(parent, fg_color=BG2 if selected else "transparent",
                           corner_radius=5, height=30)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        if installed:
            if pm.is_disabled(pid):
                state = "off"
            elif not pm.is_loaded(pid):
                state = "bad"
            elif any(not _has_module(m) for m in info.get("requires", []) or []):
                state = "warn"
            elif self._has_update(pid, info):
                state = "info"
            else:
                state = "ok"
            # An update used to be worked out here and then drawn as the same
            # green as everything else, so the list never showed it (#103).
            dot = UI.StatusDot(row, state="update" if state == "info" else state,
                               size=7, bg=BG2 if selected else BG)
            dot.pack(side="left", padx=(8, 6))
            dot.bind("<Button-1>", lambda _e, p=pid: self._select(p, True))

        # The icon beside the dot, so the list can be read by picture as well
        # as by name (#79). Small enough not to grow the row.
        icon = self._load_icon(pid, info, size=16)
        if icon is not None:
            ico = ctk.CTkLabel(row, image=icon, text="")
            ico.pack(side="left", padx=(0, 6))
            ico.bind("<Button-1>", lambda _e, p=pid, i=installed: self._select(p, i))

        name = ctk.CTkLabel(row, text=info.get("name", pid),
                            font=(UI.FONT_FAMILY, 11, "bold" if selected else "normal"),
                            text_color=FG if selected else FG2, anchor="w")
        name.pack(side="left", fill="x", expand=True,
                  padx=(8 if not installed else 0, 4))
        newer = self._has_update(pid, info) if installed else None
        if newer:
            ver = ctk.CTkLabel(row, text=self.T("pluginmgr_update_badge",
                                                ver=newer.get("version", "")),
                               font=(UI.FONT_FAMILY, 9, "bold"),
                               text_color=UI.ACCENT)
        else:
            ver = ctk.CTkLabel(row, text=f"v{info.get('version', '')}",
                               font=(UI.FONT_FAMILY, 9), text_color=FG_FAINT)
        ver.pack(side="right", padx=8)
        for w in (row, name, ver):
            w.bind("<Button-1>", lambda _e, p=pid, i=installed: self._select(p, i))
        self._rows[pid] = row

    def _select(self, pid, installed):
        self._selected = (pid, installed)
        self._populate_rows_only()
        self._render_detail()

    def _populate_rows_only(self):
        """Redraw both lists so the selection marker moves, without refetching."""
        for w in self._list_frame.winfo_children():
            w.destroy()
        pm = self._app._plugin_manager
        for pid in sorted(pm._manifests):
            self._list_row(self._list_frame, pid, pm._manifests[pid], True)
        for w in self._avail_list.winfo_children():
            w.destroy()
        for pinfo in self._available:
            pid = pinfo.get("id")
            if pid and pid not in pm._manifests:
                self._list_row(self._avail_list, pid, pinfo, False)

    def _render_detail(self):
        """Everything about the selected plugin, in the room it needs."""
        for w in self._detail.winfo_children():
            w.destroy()
        if self._selected is None:
            ctk.CTkLabel(self._detail, text=self.T("pluginmgr_pick_hint"),
                         font=(UI.FONT_FAMILY, 11), text_color=FG2).pack(pady=40)
            return
        pid, installed = self._selected
        pm = self._app._plugin_manager
        info = pm._manifests.get(pid) if installed else self._available_info(pid)
        if not info:
            return
        error = pm._errors.get(pid) if hasattr(pm, "_errors") else None

        head = ctk.CTkFrame(self._detail, fg_color="transparent")
        head.pack(fill="x")
        # icon.png beside the name. The loader survived the 3.0 redesign but
        # nothing called it any more, so plugins that ship an icon, as the
        # plugin guide tells them to, showed none (#76).
        icon = self._load_icon(pid, info)
        if icon is not None:
            ctk.CTkLabel(head, image=icon, text="").pack(side="left",
                                                         padx=(0, 8))
        ctk.CTkLabel(head, text=info.get("name", pid),
                     font=(UI.FONT_FAMILY, 15, "bold"), text_color=FG,
                     anchor="w").pack(side="left")
        ctk.CTkLabel(head, text=f"  v{info.get('version', '')}",
                     font=(UI.FONT_FAMILY, 11), text_color=FG_FAINT).pack(side="left")
        if installed:
            state = (self.T("pluginmgr_disabled") if pm.is_disabled(pid)
                     else self.T("pluginmgr_active") if pm.is_loaded(pid)
                     else self.T("pluginmgr_not_loaded"))
            UI.StatusPill(head, text=state,
                          state="off" if pm.is_disabled(pid)
                          else "ok" if pm.is_loaded(pid) else "bad").pack(side="right")

        badges = ctk.CTkFrame(self._detail, fg_color="transparent")
        badges.pack(fill="x", pady=(8, 0))
        ptypes = info.get("type", "")
        if isinstance(ptypes, str):
            ptypes = [ptypes] if ptypes else []
        for ptype in ptypes:
            fg_c, bg_c = _TYPE_COLORS.get(ptype, (FG2, BG2))
            ctk.CTkLabel(badges, text=ptype, font=(UI.FONT_FAMILY, 9, "bold"),
                         text_color=fg_c, fg_color=bg_c, corner_radius=6,
                         height=18, padx=8).pack(side="left", padx=(0, 6))

        update_info = self._has_update(pid, info) if installed else None
        if update_info:
            box = ctk.CTkFrame(self._detail, fg_color=BG2, corner_radius=6,
                               border_width=1, border_color=BLUE)
            box.pack(fill="x", pady=(10, 0))
            ctk.CTkLabel(box, text=self.T("pluginmgr_update_to",
                                          ver=update_info.get("version", "")),
                         font=(UI.FONT_FAMILY, 11), text_color=FG,
                         anchor="w").pack(side="left", padx=10, pady=8)
            UI.PrimaryButton(box, self.T("pluginmgr_update_btn"),
                             lambda p=update_info: self._install_available(p),
                             width=130, height=UI.CTRL_H_SM).pack(side="right",
                                                                  padx=10, pady=6)

        body = ctk.CTkFrame(self._detail, fg_color="transparent")
        body.pack(fill="x", pady=(10, 0))
        self._fill_detail(body, pid, info, error)

        actions = ctk.CTkFrame(self._detail, fg_color="transparent")
        actions.pack(fill="x", pady=(14, 0))
        if not installed:
            UI.PrimaryButton(actions, self.T("pluginmgr_install_btn"),
                             lambda i=info: self._install_available(i),
                             width=140).pack(side="left")
            return
        if pm.is_disabled(pid):
            UI.PrimaryButton(actions, self.T("pluginmgr_enable"),
                             lambda p=pid: self._enable(p), width=130).pack(side="left")
        else:
            UI.GhostButton(actions, self.T("pluginmgr_disable"),
                           lambda p=pid: self._disable(p), width=130).pack(side="left")
        UI.GhostButton(actions, self.T("pluginmgr_reload"),
                       lambda p=pid: self._reload(p), width=120).pack(side="left",
                                                                      padx=(8, 0))
        # Bundled plugins live in the app directory and are not ours to
        # remove; only the ones in the user's plugin folder can go.
        if _PLUGINS_DIR in (info.get("_path", "") or ""):
            UI.DangerButton(actions, self.T("pluginmgr_uninstall"),
                            lambda p=pid: self._uninstall(p),
                            width=140).pack(side="right")

    def _fill_detail(self, parent, pid, info, error):
        desc = info.get("description", "")
        if desc:
            ctk.CTkLabel(
                parent, text=desc, font=(UI.FONT_FAMILY, 10),
                text_color=FG2, anchor="w", justify="left"
            ).pack(fill="x", pady=(0, 4))

        help_text = info.get("help", "")
        if help_text:
            # This text was always in the manifest and is the only place that
            # explains which action types to put on a key and what their values
            # mean. It used to be set in 9pt wrapped at 400px inside a 480px
            # window; the screen is wider than that now.
            help_frame = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=5,
                                      border_width=1, border_color=BORDER)
            help_frame.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(
                help_frame, text=help_text,
                font=(UI.FONT_FAMILY, 10), text_color=FG,
                anchor="w", justify="left", wraplength=620
            ).pack(fill="x", padx=10, pady=8)

        # Dependencies were checked at load time and the result went to the
        # console, which is why nobody could tell why a plugin was installed,
        # enabled and doing nothing. It says so here now.
        for pkg in info.get("requires", []) or []:
            ok = _has_module(pkg)
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=(0, 2))
            UI.StatusDot(row, state="ok" if ok else "warn", size=7,
                         bg=BG3).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(
                row,
                text=self.T("pluginmgr_requires_ok", pkg=pkg) if ok
                else self.T("pluginmgr_requires_missing", pkg=pkg),
                font=(UI.FONT_FAMILY, 10), text_color=FG2 if ok else YLW,
                anchor="w", justify="left", wraplength=620).pack(side="left")

        author = info.get("author", "")
        if author:
            ctk.CTkLabel(
                parent, text=self.T("pluginmgr_author", name=author),
                font=(UI.FONT_FAMILY, 9), text_color=FG2, anchor="w"
            ).pack(fill="x")

        # Which copy this is (#75). The pane reads the manifest of the plugin
        # that is installed, which is not necessarily the one the author is
        # editing: nothing overwrites an installed plugin unless its version
        # goes up, so a manifest edited in place without a version bump keeps
        # showing the old name here. Naming the folder makes that answerable.
        path = info.get("_path", "")
        if path:
            home = os.path.expanduser("~")
            if path.startswith(home + os.sep):
                path = "~" + path[len(home):]
            ctk.CTkLabel(
                parent, text=self.T("pluginmgr_path", path=path),
                font=(UI.FONT_FAMILY, 9), text_color=FG_FAINT, anchor="w",
                justify="left", wraplength=620
            ).pack(fill="x")

        if error:
            ctk.CTkLabel(
                parent, text=error, font=(UI.FONT_FAMILY, 9),
                text_color=RED, anchor="w", wraplength=400, justify="left"
            ).pack(fill="x", pady=(4, 0))

        # The actions used to be here, three small buttons at the end of an
        # expanded card. The detail pane owns them now: one row, proper ranks,
        # and it knows whether the plugin is bundled or user-installed.

    def _load_icon(self, pid, info, size=28):
        key = (pid, size)
        if key in self._icon_cache:
            return self._icon_cache[key]
        pdir = info.get("_path", "")
        if not pdir:
            # An entry from the online index, not an installed folder. Without
            # this, join("", "icon.png") would look in the working directory.
            self._icon_cache[key] = None
            return None
        icon_path = os.path.join(pdir, "icon.png")
        if not os.path.isfile(icon_path):
            self._icon_cache[key] = None
            return None
        try:
            pil_img = Image.open(icon_path).resize((size, size), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img,
                                   size=(size, size))
            self._icon_cache[key] = ctk_img
            return ctk_img
        except Exception:
            self._icon_cache[key] = None
            return None

    # ── Enable / Disable ─────────────────────────────────────────────────────

    def _enable(self, pid):
        pm = self._app._plugin_manager
        pm.enable_plugin(pid)
        info = pm._manifests.get(pid, {})
        ptypes = info.get("type", "")
        if isinstance(ptypes, str):
            ptypes = [ptypes]
        if "panel" in ptypes:
            self._restart_lbl.configure(text=self.T("pluginmgr_restart"))
        self._populate()

    def _disable(self, pid):
        pm = self._app._plugin_manager
        if pid in self._app._panels:
            self._app._panels[pid].pack_forget()
            del self._app._panels[pid]
        if pid in self._app._plugin_sw_btns:
            self._app._plugin_sw_btns[pid].destroy()
            del self._app._plugin_sw_btns[pid]
        pm.disable_plugin(pid)
        self._restart_lbl.configure(text=self.T("pluginmgr_restart"))
        self._populate()

    def _reload(self, pid):
        """Reload a plugin: stop, reimport, restart."""
        pm = self._app._plugin_manager
        if pm.reload_plugin(pid):
            self._restart_lbl.configure(text=self.T("pluginmgr_reloaded"))
        else:
            self._restart_lbl.configure(text=self.T("pluginmgr_error"))
        self._populate()

    def _uninstall(self, pid):
        """Remove a plugin from the plugins directory."""
        # Disable first (remove from panels/switcher)
        pm = self._app._plugin_manager
        if pid in self._app._panels:
            self._app._panels[pid].pack_forget()
            del self._app._panels[pid]
        if pid in self._app._plugin_sw_btns:
            self._app._plugin_sw_btns[pid].destroy()
            del self._app._plugin_sw_btns[pid]
        pm.disable_plugin(pid)

        # Delete plugin folder
        plugin_path = os.path.join(_PLUGINS_DIR, pid)
        if os.path.isdir(plugin_path):
            shutil.rmtree(plugin_path)

        # Remove from manager state
        pm._manifests.pop(pid, None)
        pm._instances.pop(pid, None)
        pm._errors.pop(pid, None)
        self._expanded.discard(pid)

        self._restart_lbl.configure(text=self.T("pluginmgr_restart"))
        self._populate()
        # Refresh available list to show Install button again
        self._show_available(self._available)

    # ── Available Plugins Browser ────────────────────────────────────────────

    def _fetch_available(self):
        """Fetch plugins.json from GitHub and build the available list."""
        self._refresh_btn.configure(state="disabled")
        threading.Thread(target=self._do_fetch_available, daemon=True).start()

    def _do_fetch_available(self):
        try:
            plugins = fetch_plugin_index()
            self.after(0, lambda: self._show_available(plugins))
        except Exception as e:
            # Bind e as a default — Python clears the `except` variable at block
            # end, so a deferred lambda referencing it bare raises NameError (3.12+).
            self.after(0, lambda e=e: self._show_available_error(str(e)))

    def _show_available(self, plugins):
        """Available plugins become rows in the same list as the installed
        ones: while looking for something you do not want to care which of
        the two lists it happens to be in."""
        self._available = plugins or []
        pm = self._app._plugin_manager
        for w in self._avail_list.winfo_children():
            w.destroy()
        rows = [p for p in self._available if p.get("id") not in pm._manifests]
        if not rows:
            ctk.CTkLabel(self._avail_list, text=self.T("pluginmgr_no_available"),
                         font=(UI.FONT_FAMILY, 10), text_color=FG2,
                         anchor="w").pack(fill="x", pady=4)
        for pinfo in rows:
            self._list_row(self._avail_list, pinfo["id"], pinfo, installed=False)
        # The sidebar's count. This called a method by a name the application
        # does not have, so the count never reached the sidebar (#103).
        if hasattr(self._app, "_on_plugins_fetched"):
            self._app._on_plugins_fetched(self._available)
        self._populate()

    def _show_available_error(self, err):
        self._refresh_btn.configure(state="normal")
        for w in self._avail_list.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self._avail_list, text=self.T("pluginmgr_fetch_fail", err=err),
            font=(UI.FONT_FAMILY, 9), text_color=RED
        ).pack(pady=8)

    def _install_available(self, pinfo):
        """Install a plugin from the available list."""
        url = pinfo.get("url", "")
        if not url:
            return
        self._restart_lbl.configure(text="")

        # Find and disable the button
        for child in self._avail_list.winfo_children():
            for w in child.winfo_children():
                if isinstance(w, ctk.CTkButton) and hasattr(w, "_pinfo") and w._pinfo is pinfo:
                    w.configure(text="...", state="disabled")
                    break

        threading.Thread(target=self._install_from_github,
                         args=(url, pinfo), daemon=True).start()

    # ── Install logic ────────────────────────────────────────────────────────

    def _update_all(self):
        """Every installed plugin with a newer published version, from one
        download per repository, put in place one after another (#103)."""
        pending = updates_for(self._available, self._app._plugin_manager._manifests)
        if not pending or getattr(self, "_updating_all", False):
            return
        self._updating_all = True
        self._update_all_btn.configure(text=self.T("pluginmgr_installing"),
                                       state="disabled")
        by_source = {}       # (repository, branch) -> entries, one download each
        for pinfo in pending.values():
            url = pinfo.get("url", "")
            if url:
                repo, branch, _sub = _repo_branch_path(url)
                by_source.setdefault((repo, branch), []).append(pinfo)

        def run():
            failed = []
            for plugins in by_source.values():
                try:
                    tmp, root, _sub = _download_repo(plugins[0]["url"])
                except Exception as e:
                    failed.append(str(e))
                    continue
                for pinfo in plugins:
                    try:
                        folder = _plugin_dir_in(
                            root, _repo_branch_path(pinfo["url"])[2])
                    except Exception as e:
                        failed.append(f"{pinfo.get('id')}: {e}")
                        continue
                    self.after(0, lambda d=folder: self._install_from_folder(d, True))
                self.after(5000, lambda t=tmp: shutil.rmtree(t, ignore_errors=True))
            self.after(0, lambda: self._update_all_done(failed))

        threading.Thread(target=run, daemon=True).start()

    def _update_all_done(self, failed):
        self._updating_all = False
        if failed:
            self._restart_lbl.configure(
                text=self.T("pluginmgr_install_fail", err="; ".join(failed)))
        self._populate()

    def _browse_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title=self.T("pluginmgr_pick_folder"))
        if path:
            self._install_entry.delete(0, "end")
            self._install_entry.insert(0, path)

    def _do_install(self):
        src = self._install_entry.get().strip()
        if not src:
            return
        self._install_btn.configure(state="disabled")
        self._install_status.configure(text=self.T("pluginmgr_installing"), text_color=YLW)

        if os.path.isdir(src):
            self._install_from_folder(src)
        elif "github.com" in src:
            threading.Thread(target=self._install_from_github, args=(src,),
                             daemon=True).start()
        else:
            self._install_btn.configure(state="normal")
            self._install_status.configure(
                text=self.T("pluginmgr_install_fail", err="Not a folder or GitHub URL"),
                text_color=RED)

    def _install_from_folder(self, src, from_browser=False):
        try:
            manifest_path = os.path.join(src, "plugin.json")
            if not os.path.isfile(manifest_path):
                msg = self.T("pluginmgr_install_fail", err="No plugin.json found")
                if not from_browser:
                    self._install_status.configure(text=msg, text_color=RED)
                    self._install_btn.configure(state="normal")
                return False

            with open(manifest_path) as f:
                manifest = json.load(f)
            pid = manifest.get("id", "")
            if not pid:
                msg = self.T("pluginmgr_install_fail", err="No id in plugin.json")
                if not from_browser:
                    self._install_status.configure(text=msg, text_color=RED)
                    self._install_btn.configure(state="normal")
                return False

            dest = os.path.join(_PLUGINS_DIR, pid)
            if os.path.exists(dest):
                shutil.rmtree(dest)

            shutil.copytree(src, dest)
            cache = os.path.join(dest, "__pycache__")
            if os.path.isdir(cache):
                shutil.rmtree(cache)

            self._restart_lbl.configure(text=self.T("pluginmgr_install_ok"))
            if not from_browser:
                self._install_status.configure(
                    text=self.T("pluginmgr_install_ok"), text_color=GRN)
                self._install_btn.configure(state="normal")

            self._app._plugin_manager.discover()
            self._populate()
            # Refresh available list to show "Installed"
            self._show_available(self._available)
            return True

        except Exception as e:
            if not from_browser:
                self._install_status.configure(
                    text=self.T("pluginmgr_install_fail", err=str(e)),
                    text_color=RED)
                self._install_btn.configure(state="normal")
            return False

    def _install_from_github(self, url, pinfo=None):
        try:
            tmp, extracted_root, subpath = _download_repo(url)
            plugin_dir = _plugin_dir_in(extracted_root, subpath)

            from_browser = pinfo is not None
            self.after(0, lambda: self._install_from_folder(plugin_dir, from_browser))

            def _cleanup():
                try:
                    shutil.rmtree(tmp, ignore_errors=True)
                except Exception:
                    pass
            self.after(5000, _cleanup)

        except Exception as e:
            # Bind e as a default (see note above) so the deferred lambda keeps it.
            self.after(0, lambda e=e: self._on_github_fail(str(e), pinfo))

    def _on_github_fail(self, err, pinfo=None):
        if pinfo is None:
            self._install_status.configure(
                text=self.T("pluginmgr_install_fail", err=err), text_color=RED)
            self._install_btn.configure(state="normal")
        else:
            self._restart_lbl.configure(
                text=self.T("pluginmgr_install_fail", err=err))

    # ── i18n ──────────────────────────────────────────────────────────────────

    def apply_lang(self):
        self._title_lbl.configure(text=self.T("pluginmgr_title"))
        self._hint_lbl.configure(text=self.T("pluginmgr_hint"))
        self._avail_title.configure(text=self.T("pluginmgr_available"))
        self._install_btn.configure(text=self.T("pluginmgr_install_btn"))
        self._browse_btn.configure(text=self.T("pluginmgr_install_browse"))
        self._more_lbl.configure(text=self.T("pluginmgr_more"))
        self._populate()
        self._show_available(self._available)
