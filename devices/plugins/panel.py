"""Plugin Manager panel -- view, enable, disable, install plugins."""
import json
import os
import shutil
import threading
import urllib.request
import zipfile
import tempfile
import customtkinter as ctk
from PIL import Image

from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, YLW, BORDER
from shared.config import CONFIG_DIR

_PLUGINS_DIR = os.path.join(CONFIG_DIR, "plugins")
_PLUGINS_INDEX_URL = "https://raw.githubusercontent.com/ramisotti13-eng/basecamp-plugins/main/plugins.json"
_REPO_BASE = "https://github.com/ramisotti13-eng/basecamp-plugins/tree/main/"

# Type badge colors
_TYPE_COLORS = {
    "panel":   ("#0ea5e9", "#0c4a6e"),
    "service": ("#22c55e", "#14532d"),
    "action":  ("#f59e0b", "#78350f"),
}


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

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(14, 4))

        self._title_lbl = ctk.CTkLabel(
            hdr, text=self.T("pluginmgr_title"),
            font=("Helvetica", 14, "bold"), text_color=FG)
        self._title_lbl.pack(side="left")

        self._count_lbl = ctk.CTkLabel(
            hdr, text="", font=("Helvetica", 11), text_color=FG2)
        self._count_lbl.pack(side="right")

        # Hint
        self._hint_lbl = ctk.CTkLabel(
            self, text=self.T("pluginmgr_hint"),
            font=("Helvetica", 10), text_color=FG2, justify="left")
        self._hint_lbl.pack(fill="x", padx=16, pady=(0, 8))

        # Installed plugins list
        self._list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._list_frame.pack(fill="x", padx=8, pady=(0, 8))

        # Restart hint
        self._restart_lbl = ctk.CTkLabel(
            self, text="", font=("Helvetica", 10, "bold"),
            text_color=YLW)
        self._restart_lbl.pack(fill="x", padx=16, pady=(0, 4))

        # ── Available Plugins (from GitHub) ──────────────────────────────────
        avail_frame = ctk.CTkFrame(self, fg_color=BG2, corner_radius=6)
        avail_frame.pack(fill="x", padx=16, pady=(4, 4))

        avail_hdr = ctk.CTkFrame(avail_frame, fg_color="transparent")
        avail_hdr.pack(fill="x", padx=10, pady=(8, 4))

        self._avail_title = ctk.CTkLabel(
            avail_hdr, text=self.T("pluginmgr_available"),
            font=("Helvetica", 11, "bold"), text_color=FG)
        self._avail_title.pack(side="left")

        self._refresh_btn = ctk.CTkButton(
            avail_hdr, text="\u21BB", font=("Helvetica", 12),
            fg_color="transparent", hover_color=BG3, text_color=FG2,
            width=28, height=24, corner_radius=4,
            command=self._fetch_available)
        self._refresh_btn.pack(side="right")

        self._avail_list = ctk.CTkFrame(avail_frame, fg_color="transparent")
        self._avail_list.pack(fill="x", padx=6, pady=(0, 8))

        self._avail_status = ctk.CTkLabel(
            self._avail_list, text=self.T("pluginmgr_loading"),
            font=("Helvetica", 9), text_color=FG2)
        self._avail_status.pack(pady=8)

        # ── Manual install (collapsed, for advanced users) ───────────────────
        self._manual_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._manual_frame.pack(fill="x", padx=16, pady=(2, 4))

        manual_toggle = ctk.CTkLabel(
            self._manual_frame, text=self.T("pluginmgr_manual_install"),
            font=("Helvetica", 9), text_color=FG2, cursor="hand2")
        manual_toggle.pack(anchor="w")

        self._manual_body = ctk.CTkFrame(self._manual_frame, fg_color="transparent")
        # Initially hidden

        input_row = ctk.CTkFrame(self._manual_body, fg_color="transparent")
        input_row.pack(fill="x", pady=(4, 0))

        self._install_entry = ctk.CTkEntry(
            input_row, placeholder_text=self.T("pluginmgr_install_url"),
            fg_color=BG3, border_color=BORDER, text_color=FG,
            font=("Helvetica", 10), height=28)
        self._install_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._browse_btn = ctk.CTkButton(
            input_row, text=self.T("pluginmgr_install_browse"),
            font=("Helvetica", 9), fg_color=BG3, hover_color=BORDER,
            text_color=FG2, height=28, width=70, corner_radius=4,
            command=self._browse_folder)
        self._browse_btn.pack(side="left", padx=(0, 4))

        self._install_btn = ctk.CTkButton(
            input_row, text=self.T("pluginmgr_install_btn"),
            font=("Helvetica", 10, "bold"),
            fg_color=BLUE, hover_color="#0284c7", text_color=FG,
            height=28, width=80, corner_radius=4,
            command=self._do_install)
        self._install_btn.pack(side="left")

        self._install_status = ctk.CTkLabel(
            self._manual_body, text="", font=("Helvetica", 9), text_color=FG2)
        self._install_status.pack(fill="x", pady=(4, 0))

        self._manual_open = False
        manual_toggle.bind("<Button-1>", lambda e: self._toggle_manual())

        # More plugins link
        self._more_lbl = ctk.CTkLabel(
            self, text=self.T("pluginmgr_more"),
            font=("Helvetica", 9), text_color=FG2)
        self._more_lbl.pack(fill="x", padx=16, pady=(2, 10))

        self._populate()
        # Fetch available plugins in background
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
            ctk.CTkLabel(
                self._list_frame, text=self.T("pluginmgr_empty"),
                font=("Helvetica", 12), text_color=FG2
            ).pack(pady=40)
            self._count_lbl.configure(text="")
            return

        for pid in sorted(manifests.keys()):
            info = manifests[pid]
            self._build_card(pid, info)

        total = len(manifests)
        active = sum(1 for p in manifests if pm.is_loaded(p))
        self._count_lbl.configure(
            text=self.T("pluginmgr_count", total=total, active=active))

    def _build_card(self, pid, info):
        pm = self._app._plugin_manager
        disabled = pm.is_disabled(pid)
        error = pm.get_error(pid)
        is_open = pid in self._expanded

        if disabled:
            accent = FG2
        elif error:
            accent = RED
        else:
            accent = GRN

        card = ctk.CTkFrame(self._list_frame, fg_color=BG3, corner_radius=6,
                            border_width=2, border_color=accent)
        card.pack(fill="x", padx=4, pady=3)

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=(8, 10), pady=(6, 6))

        arrow = "\u25BC" if is_open else "\u25B6"
        arrow_lbl = ctk.CTkLabel(
            hdr, text=arrow, font=("Helvetica", 9), text_color=FG2,
            width=14, cursor="hand2")
        arrow_lbl.pack(side="left", padx=(0, 4))

        icon_img = self._load_icon(pid, info)
        if icon_img:
            icon_lbl = ctk.CTkLabel(hdr, image=icon_img, text="", cursor="hand2")
            icon_lbl.pack(side="left", padx=(0, 6))

        name = info.get("name", pid)
        ver = info.get("version", "")
        name_lbl = ctk.CTkLabel(
            hdr, text=name, font=("Helvetica", 12, "bold"),
            text_color=FG, cursor="hand2")
        name_lbl.pack(side="left")

        if ver:
            ver_lbl = ctk.CTkLabel(
                hdr, text=f"  v{ver}", font=("Helvetica", 10),
                text_color=FG2, cursor="hand2")
            ver_lbl.pack(side="left")

        ptypes = info.get("type", "")
        if isinstance(ptypes, str):
            ptypes = [ptypes] if ptypes else []
        for ptype in ptypes:
            fg_c, bg_c = _TYPE_COLORS.get(ptype, (FG2, BG2))
            badge = ctk.CTkLabel(
                hdr, text=ptype,
                font=("Helvetica", 8, "bold"), text_color=fg_c,
                fg_color=bg_c, corner_radius=6,
                height=16, padx=3, cursor="hand2")
            badge.pack(side="left", padx=(6, 0))

        if disabled:
            btn_text = self.T("pluginmgr_enable")
            btn_color = GRN
            btn_hover = "#16a34a"
            btn_cmd = lambda p=pid: self._enable(p)
        else:
            btn_text = self.T("pluginmgr_disable")
            btn_color = RED
            btn_hover = "#b91c1c"
            btn_cmd = lambda p=pid: self._disable(p)

        toggle_btn = ctk.CTkButton(
            hdr, text=btn_text, font=("Helvetica", 10, "bold"),
            fg_color=btn_color, hover_color=btn_hover, text_color=FG,
            height=24, width=80, corner_radius=4,
            command=btn_cmd)
        toggle_btn.pack(side="right", padx=(8, 0))

        if is_open:
            detail = ctk.CTkFrame(card, fg_color="transparent")
            detail.pack(fill="x", padx=(8, 10), pady=(0, 6))
            self._fill_detail(detail, pid, info, error)

        def toggle_expand(_e=None, p=pid):
            if p in self._expanded:
                self._expanded.discard(p)
            else:
                self._expanded.add(p)
            self._populate()

        for w in (arrow_lbl, name_lbl, hdr):
            w.bind("<Button-1>", toggle_expand)
        if icon_img:
            icon_lbl.bind("<Button-1>", toggle_expand)
        if ver:
            ver_lbl.bind("<Button-1>", toggle_expand)
        for child in hdr.winfo_children():
            if child is not toggle_btn:
                child.bind("<Button-1>", toggle_expand)

        self._rows[pid] = {"card": card, "toggle": toggle_btn}

    def _fill_detail(self, parent, pid, info, error):
        desc = info.get("description", "")
        if desc:
            ctk.CTkLabel(
                parent, text=desc, font=("Helvetica", 10),
                text_color=FG2, anchor="w", justify="left"
            ).pack(fill="x", pady=(0, 4))

        help_text = info.get("help", "")
        if help_text:
            help_frame = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=4)
            help_frame.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(
                help_frame, text=f"\u2139  {help_text}",
                font=("Helvetica", 9), text_color=FG,
                anchor="w", justify="left", wraplength=400
            ).pack(fill="x", padx=8, pady=4)

        author = info.get("author", "")
        if author:
            ctk.CTkLabel(
                parent, text=f"Author: {author}",
                font=("Helvetica", 9), text_color=FG2, anchor="w"
            ).pack(fill="x")

        if error:
            ctk.CTkLabel(
                parent, text=error, font=("Helvetica", 9),
                text_color=RED, anchor="w", wraplength=400, justify="left"
            ).pack(fill="x", pady=(4, 0))

        # Action buttons row
        plugin_path = info.get("_path", "")
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", pady=(6, 0))

        # Reload button (for active plugins)
        pm = self._app._plugin_manager
        if pm.is_loaded(pid):
            ctk.CTkButton(
                btn_row, text=self.T("pluginmgr_reload"),
                font=("Helvetica", 9, "bold"),
                fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                height=22, width=80, corner_radius=4,
                command=lambda p=pid: self._reload(p)
            ).pack(side="left", padx=(0, 6))

        # Uninstall button (only for user-installed plugins, not bundled)
        if plugin_path and _PLUGINS_DIR in plugin_path:
            ctk.CTkButton(
                btn_row, text=self.T("pluginmgr_uninstall"),
                font=("Helvetica", 9, "bold"),
                fg_color="#7f1d1d", hover_color="#991b1b", text_color="#fca5a5",
                height=22, width=90, corner_radius=4,
                command=lambda p=pid: self._uninstall(p)
            ).pack(side="left")

    def _load_icon(self, pid, info):
        if pid in self._icon_cache:
            return self._icon_cache[pid]
        pdir = info.get("_path", "")
        icon_path = os.path.join(pdir, "icon.png")
        if not os.path.isfile(icon_path):
            self._icon_cache[pid] = None
            return None
        try:
            pil_img = Image.open(icon_path).resize((28, 28), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img,
                                   size=(28, 28))
            self._icon_cache[pid] = ctk_img
            return ctk_img
        except Exception:
            self._icon_cache[pid] = None
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
            req = urllib.request.Request(_PLUGINS_INDEX_URL)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            plugins = data.get("plugins", [])
            self.after(0, lambda: self._show_available(plugins))
        except Exception as e:
            self.after(0, lambda: self._show_available_error(str(e)))

    def _show_available(self, plugins):
        self._refresh_btn.configure(state="normal")
        self._available = plugins

        for w in self._avail_list.winfo_children():
            w.destroy()

        pm = self._app._plugin_manager
        installed_ids = set(pm._manifests.keys())

        if not plugins:
            ctk.CTkLabel(
                self._avail_list, text=self.T("pluginmgr_no_available"),
                font=("Helvetica", 9), text_color=FG2
            ).pack(pady=8)
            return

        for pinfo in plugins:
            pid = pinfo.get("id", "")
            name = pinfo.get("name", pid)
            desc = pinfo.get("description", "")
            ver = pinfo.get("version", "")
            author = pinfo.get("author", "")
            is_installed = pid in installed_ids

            row = ctk.CTkFrame(self._avail_list, fg_color=BG3, corner_radius=4)
            row.pack(fill="x", pady=2)

            # Name + version
            ctk.CTkLabel(
                row, text=name, font=("Helvetica", 11, "bold"),
                text_color=FG
            ).pack(side="left", padx=(8, 0), pady=4)

            if ver:
                ctk.CTkLabel(
                    row, text=f"  v{ver}", font=("Helvetica", 9),
                    text_color=FG2
                ).pack(side="left", pady=4)

            if author:
                ctk.CTkLabel(
                    row, text=f"  by {author}", font=("Helvetica", 8),
                    text_color=FG2
                ).pack(side="left", pady=4)

            # Install / Installed button
            if is_installed:
                btn = ctk.CTkButton(
                    row, text=self.T("pluginmgr_installed"),
                    font=("Helvetica", 9), fg_color=BG2, hover_color=BG2,
                    text_color=FG2, height=22, width=80, corner_radius=4,
                    state="disabled")
            else:
                btn = ctk.CTkButton(
                    row, text=self.T("pluginmgr_install_btn"),
                    font=("Helvetica", 9, "bold"),
                    fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                    height=22, width=80, corner_radius=4,
                    command=lambda p=pinfo, b=None: self._install_available(p))
                # Store ref so we can update it
                btn._pinfo = pinfo
            btn.pack(side="right", padx=(4, 8), pady=4)

            # Description below name
            if desc:
                ctk.CTkLabel(
                    row, text=desc, font=("Helvetica", 8),
                    text_color=FG2, anchor="w", wraplength=300
                ).pack(side="left", padx=(12, 4), pady=4)

    def _show_available_error(self, err):
        self._refresh_btn.configure(state="normal")
        for w in self._avail_list.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self._avail_list, text=f"Could not load plugins: {err}",
            font=("Helvetica", 9), text_color=RED
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

    def _browse_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="Select plugin folder")
        if path:
            self._install_entry.delete(0, "end")
            self._install_entry.insert(0, path)

    def _do_install(self):
        src = self._install_entry.get().strip()
        if not src:
            return
        self._install_btn.configure(state="disabled")
        self._install_status.configure(text="Installing...", text_color=YLW)

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

            extracted_root = os.path.join(tmp, f"{repo}-{branch}")
            if subpath:
                plugin_dir = os.path.join(extracted_root, subpath)
            else:
                if os.path.isfile(os.path.join(extracted_root, "plugin.json")):
                    plugin_dir = extracted_root
                else:
                    plugin_dir = None
                    for d in os.listdir(extracted_root):
                        candidate = os.path.join(extracted_root, d)
                        if os.path.isdir(candidate) and os.path.isfile(
                                os.path.join(candidate, "plugin.json")):
                            plugin_dir = candidate
                            break
                    if not plugin_dir:
                        raise FileNotFoundError("No plugin.json found in repository")

            from_browser = pinfo is not None
            self.after(0, lambda: self._install_from_folder(plugin_dir, from_browser))

            def _cleanup():
                try:
                    shutil.rmtree(tmp, ignore_errors=True)
                except Exception:
                    pass
            self.after(5000, _cleanup)

        except Exception as e:
            self.after(0, lambda: self._on_github_fail(str(e), pinfo))

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
