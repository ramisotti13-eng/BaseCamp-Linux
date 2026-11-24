"""OBS WebSocket connection panel for BaseCamp Linux hub."""
import tkinter as tk
import customtkinter as ctk
import shared.ui as UI

from shared.config import load_obs_config
from shared.ui.core_bridge import subscribe_tk
from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, BORDER


class OBSPanel(ctk.CTkFrame):
    """Global OBS WebSocket connection panel."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app = app
        # The connection, scene list and actions live in the core's OBS
        # service; this panel is its form and its status line.
        self._svc = app.core.service("obs")

        obs_cfg = load_obs_config()
        self._obs_host     = tk.StringVar(value=obs_cfg["host"])
        self._obs_port     = tk.StringVar(value=str(obs_cfg["port"]))
        self._obs_password = tk.StringVar(value=obs_cfg["password"])

        self._build_ui()
        subscribe_tk(app.core, "obs.changed", self, self._on_obs_changed)

    def T(self, key, **kwargs):
        return self._app.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    # ── Public API for other panels ──────────────────────────────────────────

    def get_scenes(self):
        """Return list of scene names (empty if not connected)."""
        return self._svc.get_scenes()

    def is_connected(self):
        return self._svc.is_connected()

    def execute_action(self, action_type, scene=""):
        """Execute an OBS action in a background thread."""
        self._svc.execute_action(action_type, scene, cfg=self._build_cfg())

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))

        from shared.ui_helpers import cap_scroll_speed
        cap_scroll_speed(scroll)

        # The screen header says OBS Studio, so no second title here. One
        # card, because 188 lines of connection settings do not fill a screen.
        card = UI.Card(scroll, title=self.T("obs_connection"))
        self._reg(card, "obs_connection")
        card.pack(fill="x", padx=16, pady=(12, 4))
        conn = card.body

        row1 = ctk.CTkFrame(conn, fg_color="transparent")
        row1.pack(pady=(8, 2))
        ctk.CTkLabel(row1, text=self.T("obs_host_label"), text_color=FG2,
                     font=(UI.FONT_FAMILY, 11)).pack(side="left")
        ctk.CTkEntry(row1, textvariable=self._obs_host, width=120, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=(UI.FONT_FAMILY, 11)).pack(side="left", padx=(2, 8))
        ctk.CTkLabel(row1, text=self.T("obs_port_label"), text_color=FG2,
                     font=(UI.FONT_FAMILY, 11)).pack(side="left")
        ctk.CTkEntry(row1, textvariable=self._obs_port, width=62, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=(UI.FONT_FAMILY, 11)).pack(side="left", padx=2)

        row2 = ctk.CTkFrame(conn, fg_color="transparent")
        row2.pack(pady=2)
        self._reg(
            ctk.CTkLabel(row2, text="", text_color=FG2, font=(UI.FONT_FAMILY, 11)),
            "obs_password"
        ).pack(side="left")
        ctk.CTkEntry(row2, textvariable=self._obs_password, width=180, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=(UI.FONT_FAMILY, 11), show="*").pack(side="left", padx=4)

        btn_row = ctk.CTkFrame(conn, fg_color="transparent")
        btn_row.pack(pady=(6, 8))
        self._reg(
            UI.PrimaryButton(btn_row, "", self._connect, width=140),
            "obs_connect"
        ).pack(side="left")
        self._reg(
            UI.GhostButton(btn_row, "", self._disconnect, width=120),
            "obs_disconnect"
        ).pack(side="left", padx=(6, 0))

        self._status = ctk.CTkLabel(conn, text="", font=(UI.FONT_FAMILY, 11),
                                    text_color=FG2, wraplength=300)
        self._status.pack(pady=(0, 8))


    # ── OBS connection ───────────────────────────────────────────────────────

    def _connect(self):
        self._status.configure(text=self.T("obs_connecting"), text_color=BLUE)
        self._svc.connect(self._build_cfg())

    def _disconnect(self):
        self._svc.disconnect(self._build_cfg())

    def _on_obs_changed(self, topic, p):
        status = p["status"]
        if status == "connected":
            self._status.configure(
                text=self.T("obs_connected", n=len(p["scenes"])), text_color=GRN)
        elif status == "disconnected":
            self._status.configure(text=self.T("obs_disconnected"), text_color=FG2)
        elif status == "unreachable":
            self._status.configure(text=self.T("obs_unreachable"), text_color=RED)
        elif status == "error":
            self._status.configure(
                text=self.T("obs_error", msg=p["message"]), text_color=RED)
        if status in ("connected", "disconnected"):
            # The sidebar reads the presence of OBS from the device table.
            self._app._dev_present["obs"] = p["connected"]
            self._app._refresh_sidebar()

    def _build_cfg(self):
        return self._svc.build_config(self._obs_host.get(), self._obs_port.get(),
                                      self._obs_password.get())

    def apply_lang(self):
        pass
