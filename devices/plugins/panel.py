"""Plugin Manager panel -- view, enable, disable installed plugins."""
import os
import customtkinter as ctk
from PIL import Image

from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, YLW, BORDER

# Type badge colors
_TYPE_COLORS = {
    "panel":   ("#0ea5e9", "#0c4a6e"),   # blue fg, blue bg
    "service": ("#22c55e", "#14532d"),    # green fg, green bg
    "action":  ("#f59e0b", "#78350f"),    # amber fg, amber bg
}


class PluginManagerPanel(ctk.CTkFrame):

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app = app
        self._rows = {}  # pid -> dict of widgets
        self._expanded = set()  # pids that are expanded
        self._icon_cache = {}  # pid -> CTkImage
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

        # Plugin list
        self._list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._list_frame.pack(fill="x", padx=8, pady=(0, 8))

        # Restart hint (shown after enable/disable)
        self._restart_lbl = ctk.CTkLabel(
            self, text="", font=("Helvetica", 10, "bold"),
            text_color=YLW)
        self._restart_lbl.pack(fill="x", padx=16, pady=(0, 10))

        self._populate()

    def _populate(self):
        """Build one card per discovered plugin."""
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
        loaded = pm.is_loaded(pid)
        error = pm.get_error(pid)
        is_open = pid in self._expanded

        # Accent color
        if disabled:
            accent = FG2
        elif error:
            accent = RED
        else:
            accent = GRN

        # Card — use border_color for accent
        card = ctk.CTkFrame(self._list_frame, fg_color=BG3, corner_radius=6,
                            border_width=2, border_color=accent)
        card.pack(fill="x", padx=4, pady=3)

        # ── Header (always visible) ──────────────────────────────────────────
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=(8, 10), pady=(6, 6))

        # Expand arrow
        arrow = "\u25BC" if is_open else "\u25B6"
        arrow_lbl = ctk.CTkLabel(
            hdr, text=arrow, font=("Helvetica", 9), text_color=FG2,
            width=14, cursor="hand2")
        arrow_lbl.pack(side="left", padx=(0, 4))

        # Plugin icon
        icon_img = self._load_icon(pid, info)
        if icon_img:
            icon_lbl = ctk.CTkLabel(hdr, image=icon_img, text="", cursor="hand2")
            icon_lbl.pack(side="left", padx=(0, 6))

        # Name + version
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

        # Type badges inline (compact, always visible)
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

        # Toggle button (always visible, right side)
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

        # ── Detail area (only when expanded) ──────────────────────────────────
        if is_open:
            detail = ctk.CTkFrame(card, fg_color="transparent")
            detail.pack(fill="x", padx=(8, 10), pady=(0, 6))
            self._fill_detail(detail, info, error)

        # ── Click binding for expand/collapse ─────────────────────────────────
        def toggle_expand(_e=None, p=pid):
            if p in self._expanded:
                self._expanded.discard(p)
            else:
                self._expanded.add(p)
            self._populate()

        # Bind click on all header widgets (not the toggle button)
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

    def _fill_detail(self, parent, info, error):
        """Build the expanded detail content."""
        # Description
        desc = info.get("description", "")
        if desc:
            ctk.CTkLabel(
                parent, text=desc, font=("Helvetica", 10),
                text_color=FG2, anchor="w", justify="left"
            ).pack(fill="x", pady=(0, 4))

        # Help text
        help_text = info.get("help", "")
        if help_text:
            help_frame = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=4)
            help_frame.pack(fill="x", pady=(0, 4))
            ctk.CTkLabel(
                help_frame, text=f"\u2139  {help_text}",
                font=("Helvetica", 9), text_color=FG,
                anchor="w", justify="left", wraplength=400
            ).pack(fill="x", padx=8, pady=4)

        # Author
        author = info.get("author", "")
        if author:
            ctk.CTkLabel(
                parent, text=f"Author: {author}",
                font=("Helvetica", 9), text_color=FG2, anchor="w"
            ).pack(fill="x")

        # Error detail
        if error:
            ctk.CTkLabel(
                parent, text=error, font=("Helvetica", 9),
                text_color=RED, anchor="w", wraplength=400, justify="left"
            ).pack(fill="x", pady=(4, 0))

    def _load_icon(self, pid, info):
        """Load icon.png from plugin folder if it exists. Returns CTkImage or None."""
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

    # ── Actions ───────────────────────────────────────────────────────────────

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

    # ── i18n ──────────────────────────────────────────────────────────────────

    def apply_lang(self):
        self._title_lbl.configure(text=self.T("pluginmgr_title"))
        self._hint_lbl.configure(text=self.T("pluginmgr_hint"))
        self._populate()
