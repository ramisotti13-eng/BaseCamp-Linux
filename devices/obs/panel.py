"""OBS WebSocket connection panel for BaseCamp Linux hub."""
import threading
import tkinter as tk
import customtkinter as ctk

from shared.config import load_obs_config, save_obs_config
from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, BORDER


class OBSPanel(ctk.CTkFrame):
    """Global OBS WebSocket connection panel."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app = app
        self._scenes = []       # populated after connect
        self._connected = False

        obs_cfg = load_obs_config()
        self._obs_host     = tk.StringVar(value=obs_cfg["host"])
        self._obs_port     = tk.StringVar(value=str(obs_cfg["port"]))
        self._obs_password = tk.StringVar(value=obs_cfg["password"])

        self._build_ui()

    def T(self, key, **kwargs):
        return self._app.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    # ── Public API for other panels ──────────────────────────────────────────

    def get_scenes(self):
        """Return list of scene names (empty if not connected)."""
        return list(self._scenes)

    def is_connected(self):
        return self._connected

    def execute_action(self, action_type, scene=""):
        """Execute an OBS action in a background thread."""
        cfg = self._build_cfg()
        btn = {"type": action_type, "scene": scene}
        threading.Thread(
            target=self._run_obs_action,
            args=(btn, cfg),
            daemon=True).start()

    @staticmethod
    def _run_obs_action(btn_cfg, obs_cfg):
        try:
            import obsws_python as obs
            cl = obs.ReqClient(
                host=obs_cfg["host"], port=obs_cfg["port"],
                password=obs_cfg.get("password", ""), timeout=4)
            t = btn_cfg.get("type", "none")
            if t == "scene":
                cl.set_current_program_scene(btn_cfg["scene"])
            elif t == "record":
                cl.toggle_record()
            elif t == "stream":
                cl.toggle_stream()
            cl.disconnect()
        except Exception:
            pass

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))

        # Cap scroll speed
        _c = scroll._parent_canvas
        _orig = _c.yview
        def _capped(*args):
            if args and args[0] == "scroll":
                n = max(-2, min(2, int(args[1])))
                w = args[2] if len(args) > 2 else "units"
                return _orig("scroll", n, w)
            return _orig(*args)
        _c.yview = _capped

        # Title
        ctk.CTkLabel(scroll, text=self.T("obs_title"),
                     font=("Helvetica", 14, "bold"), text_color=FG,
                     fg_color="transparent").pack(padx=16, pady=(14, 6), anchor="w")

        # ── Connection frame ──
        conn = ctk.CTkFrame(scroll, fg_color=BG3, corner_radius=4)
        conn.pack(fill="x", padx=12, pady=(0, 4))

        row1 = ctk.CTkFrame(conn, fg_color="transparent")
        row1.pack(pady=(8, 2))
        ctk.CTkLabel(row1, text="Host:", text_color=FG2,
                     font=("Helvetica", 11)).pack(side="left")
        ctk.CTkEntry(row1, textvariable=self._obs_host, width=120, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=("Helvetica", 11)).pack(side="left", padx=(2, 8))
        ctk.CTkLabel(row1, text="Port:", text_color=FG2,
                     font=("Helvetica", 11)).pack(side="left")
        ctk.CTkEntry(row1, textvariable=self._obs_port, width=62, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=("Helvetica", 11)).pack(side="left", padx=2)

        row2 = ctk.CTkFrame(conn, fg_color="transparent")
        row2.pack(pady=2)
        self._reg(
            ctk.CTkLabel(row2, text="", text_color=FG2, font=("Helvetica", 11)),
            "obs_password"
        ).pack(side="left")
        ctk.CTkEntry(row2, textvariable=self._obs_password, width=180, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=("Helvetica", 11), show="*").pack(side="left", padx=4)

        btn_row = ctk.CTkFrame(conn, fg_color="transparent")
        btn_row.pack(pady=(6, 8))
        self._reg(
            ctk.CTkButton(btn_row, text="", command=self._connect,
                          fg_color=BLUE, text_color=FG, hover_color="#0884be",
                          font=("Helvetica", 11, "bold"), height=34, corner_radius=6),
            "obs_connect"
        ).pack(side="left")
        self._reg(
            ctk.CTkButton(btn_row, text="", command=self._disconnect,
                          fg_color=RED, text_color=BG, hover_color="#c03030",
                          font=("Helvetica", 11, "bold"), height=34, corner_radius=6),
            "obs_disconnect"
        ).pack(side="left", padx=(6, 0))

        self._status = ctk.CTkLabel(conn, text="", font=("Helvetica", 11),
                                    text_color=FG2, wraplength=300)
        self._status.pack(pady=(0, 8))


    # ── OBS connection ───────────────────────────────────────────────────────

    def _connect(self):
        self._status.configure(text=self.T("obs_connecting"), text_color=BLUE)
        cfg = self._build_cfg()
        save_obs_config(cfg)

        def run():
            import socket
            import obsws_python as obs
            old_timeout = socket.getdefaulttimeout()
            try:
                socket.setdefaulttimeout(4)
                cl   = obs.ReqClient(host=cfg["host"], port=cfg["port"],
                                     password=cfg.get("password", ""), timeout=4)
                resp = cl.get_scene_list()
                scenes = [s["sceneName"] for s in resp.scenes]
                cl.disconnect()
                self._app.after(0, lambda: self._on_connected(scenes))
            except ConnectionRefusedError:
                self._app.after(0, lambda: self._status.configure(
                    text=self.T("obs_unreachable"), text_color=RED))
            except Exception as e:
                msg = str(e) or type(e).__name__
                self._app.after(0, lambda: self._status.configure(
                    text=self.T("obs_error", msg=msg), text_color=RED))
            finally:
                socket.setdefaulttimeout(old_timeout)
        threading.Thread(target=run, daemon=True).start()

    def _disconnect(self):
        self._connected = False
        self._scenes = []
        save_obs_config(self._build_cfg())
        self._status.configure(text=self.T("obs_disconnected"), text_color=FG2)
        # Reset button color unless OBS tab is active
        active = self._app._active_device
        self._app._sw_obs_btn.configure(
            fg_color=BLUE if active == "obs" else BG2,
            text_color=FG if active == "obs" else FG2)

    def _on_connected(self, scenes):
        self._connected = True
        self._scenes = list(scenes)
        save_obs_config(self._build_cfg())
        self._status.configure(
            text=self.T("obs_connected", n=len(scenes)), text_color=GRN)
        self._app._sw_obs_btn.configure(fg_color=GRN, text_color=FG)

    def _build_cfg(self):
        return {
            "host":     self._obs_host.get().strip(),
            "port":     int(self._obs_port.get().strip() or "4455"),
            "password": self._obs_password.get(),
            "buttons": [{"type": "none", "scene": ""} for _ in range(4)]
        }

    def apply_lang(self):
        pass
