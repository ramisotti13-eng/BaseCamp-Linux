#!/usr/bin/env python3
"""BaseCamp Linux — multi-device hub GUI.

The entry point of every start (the AppImage imports it through
appentry.py). Since 4.0 run() opens the web interface (ui_web/) and this
file's CustomTkinter interface is the classic one: --classic, or what starts
when the web window cannot open here (ui_web/launch.py).
"""
import sys

# --demo is the web interface on stand-in devices with a throw-away
# configuration (ui_web/demo.py). The configuration directory is fixed when
# shared.config is imported, a few lines down, and this file is the only
# entry the AppImage has, so the switch has to happen before that import.
if "--demo" in sys.argv[1:] and "--classic" not in sys.argv[1:]:
    from ui_web import demo as _demo
    _demo.prepare()

import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image, ImageTk
import subprocess
import datetime
import re
import time
import sys
import os
import json
import math
import colorsys
import psutil
import pwd as _pwd

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

from core import runtime as _runtime
_HERE   = _runtime.HERE
# Process layout lives in core/runtime.py; these names stay because tests and
# the rest of this file use them (tests patch gui._FROZEN).
_FROZEN = _runtime.FROZEN
_interpreter = _runtime.interpreter
_BIN = _runtime.BIN
_RES = _runtime.RES
PYTHON = _runtime.PYTHON
TRAY_HELPER = (os.path.join(_BIN, "basecamp-tray") if _FROZEN
               else os.path.join(_HERE, "tray_helper.py"))

STYLES = {"Analog": "analog", "Digital": "digital"}

# ── Shared modules ─────────────────────────────────────────────────────────────

# Before anything goes online. The AppImage's OpenSSL looks for certificates
# where the machine it was built on keeps them; on Arch, Debian and openSUSE
# that is nowhere, and the update check and the plugin list failed without a
# word. See shared/tls.py.
from shared.tls import ensure_ca_bundle
ensure_ca_bundle()

from shared.config import (
    _real_home, CONFIG_DIR,
    STYLE_FILE, BUTTON_FILE, OBS_FILE, OBS_BACKUP_FILE, MAIN_MODE_FILE,
    AUTOSTART_FILE, SPLASH_FILE, ZONE_FILE, RGB_FILE, PRESET_FILE,
    ICON_LAST_FILE, ICON_LIBRARY_DIR, MAIN_LIBRARY_DIR,
    RGB_PRESETS_FILE,
    load_config, save_config,
    load_style, save_style,
    load_buttons, save_buttons,
    load_obs_config, save_obs_config,
    load_autostart_enabled, save_autostart_enabled,
    load_splash_enabled, save_splash_enabled,
    load_window_geometry, save_window_geometry,
    load_zone_config, save_zone_config, load_zone_colors, save_zone_colors,
    load_rgb_settings, save_rgb_settings,
    load_rgb_config, save_rgb_config,
    _load_per_key, _save_per_key,
    _load_presets, _save_presets,
    _load_icon_last, _save_icon_last,
    _save_to_library, _save_to_main_library,
    _compute_lib_hash, _compute_main_lib_hash,
    _list_library, _list_main_library,
    OBS_INTERNAL_ORDER,
    shipped_path,
    _toolkit_notice_seen, _mark_toolkit_notice_seen,
)
# not os.path.join(_RES, ...): _RES is the frozen bundle, and the language
# files travel with the source overlay. See shipped_path().
LANG_DIR = shipped_path("lang")
from shared.image_utils import image_to_rgb565
import shared.ui as UI
from shared.ui_helpers import (
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER,
    FONT, FONT_BOLD, FONT_SM, FONT_LG,
    ANIM_STEPS, ANIM_MS,
    _rgb_hex, _run_as_sudouser,
    native_open_image, native_open_folder, parse_desktop_apps,
    ColorPickerDialog, pick_color,
    LibraryPickerDialog, pick_library_image, pick_main_library_image,
    MultiUploadDialog,
    CustomRGBWindow, cap_scroll_speed,
    AccordionSection,
    _KB_LAYOUT, _KB_CANVAS_W, _KB_CANVAS_H, _SIDE_SZ, _SIDE_OFFSET,
    _QUICK_COLORS, _SIDE_ZONE_INDICES,
    _KB60_LAYOUT, _KB60_CANVAS_W, _KB60_CANVAS_H, _KB60_NUM_LEDS,
)
from devices.everest_max.panel import EverestMaxPanel
from devices.everest60.panel import Everest60Panel
from devices.makalu67.panel import Makalu67Panel
from devices.displaypad.panel import DisplayPadPanel
from devices.macropad.panel import MacroPadPanel
from devices.obs.panel import OBSPanel
from devices.macros.panel import MacroPanel
from devices.plugins.panel import PluginManagerPanel, PanelNet
from shared.plugins import PluginManager
from shared.plugin_api import PluginContext
from core import CoreApp
from core import i18n as _i18n
from core.platform import usb as _usb
from core.devices.registry import DeviceRegistry
from core.devices import install_all as install_device_services
from shared.ui.core_bridge import TkScheduler, subscribe_tk
from core.updates import UpdateService
from core.settings import SettingsService
from core.obs import ObsService
from core.control import ControlService
from core.plugins import PluginHost
from core.toolkit import toolkit_notice

# ── Keep backward-compatible module-level names used by existing code ──────────

# These were previously defined at module level in gui.py; keep them so that
# any code that imports gui directly still works.
_AUTOSTART_FILE = AUTOSTART_FILE


def _cmd(*args):
    """Build subprocess command for Everest Max controller."""
    return _runtime.controller_cmd("everest_max", *args, frozen=_FROZEN)


def load_lang(code):
    return _i18n.load(LANG_DIR, code)


def default_lang(available):
    """See core.i18n.default_for_env."""
    return _i18n.default_for_env(available)


def available_langs():
    return _i18n.available(LANG_DIR)


# USB presence detection helpers. The logic lives in core/platform/usb.py; these
# stay as module-level names because tests patch them, and each looks the
# others up here at call time so a patch of one is seen by the rest.

def _device_nodes(vid, pid):
    return _usb.device_nodes(vid, pid)


def _device_access_denied(vid, pid):
    return _usb.access_denied(vid, pid, nodes=_device_nodes)


def _describe_node(path):
    return _usb.describe_node(path)


def _check_usb_presence(vid, pid):
    return _usb.usb_present(vid, pid)


def _make_device_registry(core):
    """The DeviceRegistry, with presence and access checks that look up this
    module's functions when called (tests patch them)."""
    return core.add_service(DeviceRegistry(
        core,
        presence=lambda vid, pid: _check_usb_presence(vid, pid),
        access_denied=lambda vid, pid: _device_access_denied(vid, pid),
        describe=lambda path: _describe_node(path)))


# ── Settings dialog ────────────────────────────────────────────────────────────

class SettingsPanel(ctk.CTkFrame):
    """The settings screen: update, profiles, application, backup, about.

    This used to be a fixed 420x580 modal with six themes stacked in one
    column, and the update flow, the part that actually wants attention,
    sat at the very bottom of it. As a screen each theme is a card and the
    update card is the one that spans the width.
    """
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG)
        self._app = app
        self._build()

    def apply_lang(self):
        """Build the screen again instead of relabelling it widget by widget.

        As a dialog this screen was created fresh every time it was opened, so
        a language change reached it for free. As a screen it is built once and
        kept, and every label on it would stay in the old language. There is no
        device state here to lose, and refresh() puts the two late-arriving
        pieces back, so rebuilding is both the shortest and the safest way to
        keep the cards, their titles and their hints in step.
        """
        for child in self.winfo_children():
            child.destroy()
        self._build()
        try:
            self.refresh()
        except Exception as e:
            print(f"[UI] settings refresh after language change failed: {e}")

    def _build(self):
        app = self._app

        outer = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        outer.pack(fill="both", expand=True)
        cap_scroll_speed(outer)
        grid = ctk.CTkFrame(outer, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=UI.S5, pady=UI.S5)
        grid.grid_columnconfigure(0, weight=1, uniform="set")
        grid.grid_columnconfigure(1, weight=1, uniform="set")

        # ── Update, full width ──
        upd = UI.Card(grid, title=app.T("settings_update_section"))
        upd.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, UI.S3))
        self._update_lbl = ctk.CTkLabel(
            upd.body, text=getattr(app, "_update_message", "")
            or app.T("settings_update_current", ver=APP_VERSION),
            font=(UI.FONT_FAMILY, 11), text_color=FG, anchor="w",
            justify="left", wraplength=520)
        self._update_lbl.pack(side="left", fill="x", expand=True)
        # The button cannot be built here: this screen exists from startup,
        # the update check answers seconds later. As a dialog that was fine
        # because it was created fresh on every open. refresh() creates it.
        self._update_body = upd.body
        self._update_btn = None
        self._maybe_add_update_button()

        # ── Profiles ──
        prof = UI.Card(grid, title=app.T("settings_profiles"))
        prof.grid(row=1, column=0, sticky="nsew", padx=(0, UI.S3), pady=(0, UI.S3))
        profile_row = ctk.CTkFrame(prof.body, fg_color="transparent")
        profile_row.pack(fill="x", pady=2)
        profiles = app._settings.profiles()
        active   = app._settings.active_profile()
        self._profile_combo = ctk.CTkComboBox(
            profile_row, values=profiles or [app.T("settings_profile_none")],
            width=200, height=30, font=(UI.FONT_FAMILY, 11),
            fg_color=BG2, button_color=BLUE, text_color=FG)
        if active and active in profiles:
            self._profile_combo.set(active)
        elif profiles:
            self._profile_combo.set(profiles[0])
        else:
            self._profile_combo.set(app.T("settings_profile_none"))
        self._profile_combo.pack(side="left", padx=(0, 4), fill="x", expand=True)
        UI.GhostButton(profile_row, app.T("settings_profile_load"),
                       self._do_load_profile, width=80,
                       height=UI.CTRL_H_SM).pack(side="left", padx=(UI.S2, 0))
        UI.DangerButton(profile_row, app.T("settings_profile_delete"),
                        self._do_delete_profile, width=80,
                        height=UI.CTRL_H_SM).pack(side="left", padx=(UI.S2, 0))

        save_row = ctk.CTkFrame(prof.body, fg_color="transparent")
        save_row.pack(fill="x", pady=(6, 0))
        self._new_profile_var = ctk.StringVar()
        ctk.CTkEntry(save_row, textvariable=self._new_profile_var,
                     placeholder_text=app.T("settings_profile_name_hint"),
                     height=30, font=(UI.FONT_FAMILY, 11),
                     fg_color=BG2, text_color=FG).pack(
            side="left", padx=(0, 4), fill="x", expand=True)
        UI.GhostButton(save_row, app.T("settings_profile_save"),
                       self._do_save_profile, width=90,
                       height=UI.CTRL_H_SM).pack(side="left", padx=(UI.S2, 0))

        # ── Application: language, autostart, splash, pickers ──
        appc = UI.Card(grid, title=app.T("settings_app_section"))
        appc.grid(row=1, column=1, sticky="nsew", pady=(0, UI.S3))
        lang_row = ctk.CTkFrame(appc.body, fg_color="transparent")
        lang_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(lang_row, text=app.T("settings_language"),
                     font=(UI.FONT_FAMILY, 11), text_color=FG2,
                     anchor="w").pack(side="left")
        lang_names = list(app._avail_langs.values())
        self._lang_combo = ctk.CTkComboBox(
            lang_row, values=lang_names or [""], command=self._do_change_lang,
            width=150, height=UI.CTRL_H_SM, font=(UI.FONT_FAMILY, 11),
            fg_color=BG2, button_color=BLUE, text_color=FG)
        self._lang_combo.set(app._avail_langs.get(app._lang_code, ""))
        self._lang_combo.pack(side="right")

        # Autostart and the splash screen used to live in the keyboard panel
        # header, so they were unreachable for anyone with only a DisplayPad.
        # They are application settings and belong here.
        self._autostart_var = ctk.BooleanVar(value=app._settings.autostart())
        self._splash_var    = ctk.BooleanVar(value=app._settings.splash())
        for key, var, cb in (("settings_autostart", self._autostart_var,
                              self._do_toggle_autostart),
                             ("settings_splash", self._splash_var,
                              self._do_toggle_splash)):
            row = ctk.CTkFrame(appc.body, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=app.T(key), font=(UI.FONT_FAMILY, 11),
                         text_color=FG2, anchor="w").pack(side="left")
            ctk.CTkSwitch(row, text="", variable=var, command=cb,
                          width=40, progress_color=BLUE).pack(side="right")

        pick_row = ctk.CTkFrame(appc.body, fg_color="transparent")
        pick_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(pick_row, text=app.T("settings_picker_section"),
                     font=(UI.FONT_FAMILY, 11), text_color=FG2,
                     anchor="w").pack(side="left")
        UI.GhostButton(pick_row, app.T("settings_picker_reset"),
                       self._do_reset_pickers, width=140,
                       height=UI.CTRL_H_SM).pack(side="right")

        # ── Backup ──
        bak = UI.Card(grid, title=app.T("settings_backup_section"))
        bak.grid(row=2, column=0, sticky="nsew", padx=(0, UI.S3))
        ctk.CTkLabel(bak.body, text=app.T("settings_backup_hint"),
                     font=(UI.FONT_FAMILY, 10), text_color=FG2, anchor="w",
                     justify="left", wraplength=330).pack(fill="x", pady=(0, 8))
        bak_row = ctk.CTkFrame(bak.body, fg_color="transparent")
        bak_row.pack(fill="x")
        UI.GhostButton(bak_row, app.T("settings_backup"), self._do_backup,
                       width=150).pack(side="left", padx=(0, UI.S2))
        UI.GhostButton(bak_row, app.T("settings_restore"), self._do_restore,
                       width=170).pack(side="left")

        # ── About ──
        about = UI.Card(grid, title=app.T("settings_about_section"))
        about.grid(row=2, column=1, sticky="nsew")
        self._about_values = {}
        for key, value in (
                ("settings_about_version", APP_VERSION),
                ("settings_about_install", app._detect_install_type()),
                ("settings_about_config",
                 CONFIG_DIR.replace(os.path.expanduser("~"), "~")),
                ("settings_about_socket", "")):
            row = ctk.CTkFrame(about.body, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=app.T(key), font=(UI.FONT_FAMILY, 11),
                         text_color=FG2, anchor="w").pack(side="left")
            val = ctk.CTkLabel(row, text=str(value), font=(UI.FONT_FAMILY, 11),
                               text_color=FG, anchor="e")
            val.pack(side="right")
            self._about_values[key] = val

        self._status = ctk.CTkLabel(grid, text="", font=(UI.FONT_FAMILY, 11),
                                    text_color=FG2, anchor="w")
        self._status.grid(row=3, column=0, columnspan=2, sticky="ew",
                          pady=(UI.S3, 0))

    def _maybe_add_update_button(self):
        """Add the update button once an update is actually available."""
        app = self._app
        if self._update_btn is not None:
            return
        if (getattr(app, "_update_install_type", "") == "appimage"
                and getattr(app, "_update_url", "")):
            self._update_btn = UI.PrimaryButton(
                self._update_body, app.T("settings_update_button"),
                self._do_update, width=150)
            self._update_btn.pack(side="right")

    def refresh(self):
        """Called every time the screen is shown. Two things are only known
        later than construction: the control socket starts after the UI is
        built, and the update check answers seconds after that."""
        app = self._app
        self._maybe_add_update_button()
        self._about_values["settings_about_socket"].configure(
            text=app.T("state_connected") if getattr(app, "_control_server", None)
            else app.T("state_absent"))
        if getattr(app, "_update_message", ""):
            self._update_lbl.configure(text=app._update_message, text_color=GRN)
        try:
            self._refresh_profile_combo()
        except Exception:
            pass

    def _do_toggle_autostart(self):
        self._app._settings.set_autostart(self._autostart_var.get())

    def _do_toggle_splash(self):
        self._app._settings.set_splash(self._splash_var.get())

    def _refresh_profile_combo(self):
        profiles = self._app._settings.profiles()
        self._profile_combo.configure(
            values=profiles or [self._app.T("settings_profile_none")])
        active = self._app._settings.active_profile()
        if active and active in profiles:
            self._profile_combo.set(active)
        elif profiles:
            self._profile_combo.set(profiles[0])
        else:
            self._profile_combo.set(self._app.T("settings_profile_none"))

    def _do_save_profile(self):
        name = (self._new_profile_var.get() or "").strip()
        if not name:
            self._status.configure(
                text=self._app.T("settings_profile_no_name"), text_color=RED)
            return
        try:
            safe, count = self._app._settings.save_profile(name)
            self._new_profile_var.set("")
            self._refresh_profile_combo()
            self._status.configure(
                text=self._app.T("settings_profile_saved", name=safe, n=count),
                text_color=GRN)
        except Exception as e:
            self._app.toast(
                self._app.T("settings_profile_err", err=str(e)[:60]), kind="bad")

    def _do_load_profile(self):
        name = self._profile_combo.get()
        if name not in self._app._settings.profiles():
            return
        from shared.ui import ask_yes_no
        if not ask_yes_no(self, self._app.T("settings_profiles"),
                          self._app.T("settings_profile_load_confirm", name=name),
                          self._app.T("ui_load"), self._app.T("ui_cancel")):
            return
        try:
            count = self._app._settings.load_profile(name)
            self._app.toast(
                self._app.T("settings_profile_loaded", name=name, n=count),
                kind="ok")
        except Exception as e:
            self._app.toast(
                self._app.T("settings_profile_err", err=str(e)[:60]), kind="bad")

    def _do_delete_profile(self):
        name = self._profile_combo.get()
        if name not in self._app._settings.profiles():
            return
        from shared.ui import ask_yes_no
        if not ask_yes_no(self, self._app.T("settings_profiles"),
                          self._app.T("settings_profile_delete_confirm", name=name),
                          self._app.T("ui_delete"), self._app.T("ui_cancel"),
                          danger=True):
            return
        self._app._settings.delete_profile(name)
        self._refresh_profile_combo()
        self._app.toast(self._app.T("settings_profile_deleted", name=name))

    def _do_change_lang(self, val):
        """Change UI language from the settings dialog (issue #35). Keeps the
        keyboard panel's language combo in sync and persists via the app."""
        try:
            self._app._lang_var.set(val)
        except Exception:
            pass
        self._app._on_lang_change(val)

    def _do_reset_pickers(self):
        self._app._settings.reset_last_dirs()
        self._status.configure(
            text=self._app.T("settings_picker_reset_ok"), text_color=GRN)

    def _do_backup(self):
        from tkinter import filedialog
        st = self._app._settings
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".zip",
            initialdir=st.last_dir("backup", default=os.path.expanduser("~")),
            initialfile=f"basecamp-backup-{ts}.zip",
            filetypes=[("ZIP", "*.zip")],
            title=self._app.T("settings_backup"))
        if not path:
            return
        st.remember_dir("backup", path)
        try:
            count = st.export_backup(path)
            self._status.configure(
                text=self._app.T("settings_backup_ok", n=count), text_color=GRN)
        except Exception as e:
            self._status.configure(
                text=self._app.T("settings_backup_err", err=str(e)[:60]),
                text_color=RED)

    def _do_restore(self):
        from tkinter import filedialog
        from shared.ui import ask_yes_no
        st = self._app._settings
        path = filedialog.askopenfilename(
            parent=self,
            initialdir=st.last_dir("backup", default=os.path.expanduser("~")),
            filetypes=[("ZIP", "*.zip"), ("All", "*.*")],
            title=self._app.T("settings_restore"))
        if not path:
            return
        st.remember_dir("backup", path)
        if not ask_yes_no(self, self._app.T("settings_restore"),
                          self._app.T("settings_restore_confirm"),
                          self._app.T("ui_continue"), self._app.T("ui_cancel"),
                          danger=True, detail=os.path.basename(path)):
            return
        try:
            count = st.import_backup(path)
            self._app.toast(self._app.T("settings_restore_ok", n=count), kind="ok")
        except Exception as e:
            self._app.toast(
                self._app.T("settings_restore_err", err=str(e)[:60]), kind="bad")

    def _do_update(self):
        """Trigger the shared App-level download. UI updates land in our
        local status label / button via the callbacks."""
        app = self._app
        if not getattr(app, "_update_url", "") or not os.environ.get("APPIMAGE"):
            self._status.configure(
                text=app.T("settings_update_no_asset"), text_color=RED)
            return
        if self._update_btn is not None:
            self._update_btn.configure(state="disabled")
        app.run_update_download(
            on_progress=lambda pct: self._status.configure(
                text=app.T("settings_update_downloading", pct=pct), text_color=GRN),
            on_installing=lambda: self._status.configure(
                text=app.T("settings_update_installing"), text_color=GRN),
            on_done=self._on_update_done,
            on_error=self._on_update_error,
        )

    def _on_update_done(self):
        app = self._app
        self._status.configure(text=app.T("settings_update_done"), text_color=GRN)
        if self._update_btn is not None:
            self._update_btn.configure(
                text=app.T("settings_update_restart"),
                state="normal", command=app.restart_after_update,
                fg_color=BLUE, hover_color="#1d4f86")

    def _on_update_error(self, err):
        app = self._app
        self._status.configure(
            text=app.T("settings_update_error", err=err), text_color=RED)
        if self._update_btn is not None:
            self._update_btn.configure(state="normal")


# ── Update-available popup ─────────────────────────────────────────────────────

class UpdateAvailableDialog(ctk.CTkToplevel):
    """Shown once per session when a newer AppImage release is detected.
    Lets the user kick off the download right from the popup so they don't
    have to dig through the settings dialog to find it."""
    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self.title(app.T("update_dialog_title"))
        self.geometry("420x210")
        self.resizable(False, False)
        try:
            self.transient(app)
        except Exception:
            pass

        ctk.CTkLabel(self, text=app.T("update_dialog_title"),
                     font=(UI.FONT_FAMILY, 14, "bold")).pack(pady=(16, 4))
        ctk.CTkLabel(self,
                     text=app.T("update_dialog_body",
                                ver=getattr(app, "_update_version", "")),
                     font=(UI.FONT_FAMILY, 11), wraplength=380,
                     justify="center").pack(pady=(0, 6), padx=12)

        self._status = ctk.CTkLabel(self, text="", font=(UI.FONT_FAMILY, 10),
                                     text_color=FG2, wraplength=380, justify="center")
        self._status.pack(pady=(0, 6))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(pady=(4, 12))
        self._cancel_btn = ctk.CTkButton(
            btns, text=app.T("update_dialog_cancel"), width=130, height=32,
            corner_radius=6, fg_color=BG3, hover_color=BG2,
            command=self.destroy)
        self._cancel_btn.pack(side="left", padx=8)
        self._update_btn = ctk.CTkButton(
            btns, text=app.T("settings_update_button"), width=160, height=32,
            corner_radius=6, fg_color=GRN, hover_color="#1f7a3a",
            command=self._do_update)
        self._update_btn.pack(side="left", padx=8)

    def _do_update(self):
        app = self._app
        if not getattr(app, "_update_url", "") or not os.environ.get("APPIMAGE"):
            self._status.configure(
                text=app.T("settings_update_no_asset"), text_color=RED)
            return
        self._update_btn.configure(state="disabled")
        self._cancel_btn.configure(state="disabled")
        app.run_update_download(
            on_progress=lambda pct: self._status.configure(
                text=app.T("settings_update_downloading", pct=pct), text_color=GRN),
            on_installing=lambda: self._status.configure(
                text=app.T("settings_update_installing"), text_color=GRN),
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _on_done(self):
        app = self._app
        self._status.configure(text=app.T("settings_update_done"), text_color=GRN)
        self._update_btn.configure(
            text=app.T("settings_update_restart"), state="normal",
            command=app.restart_after_update, fg_color=BLUE,
            hover_color="#1d4f86")
        self._cancel_btn.configure(state="normal")

    def _on_error(self, err):
        app = self._app
        self._status.configure(
            text=app.T("settings_update_error", err=err), text_color=RED)
        self._update_btn.configure(state="normal")
        self._cancel_btn.configure(state="normal")


# ── App ────────────────────────────────────────────────────────────────────────

APP_VERSION = "4.0.0-beta.1"

#: Why the web interface could not start, when run() started this one in its
#: place (None otherwise). The window says so once per version.
_WEB_FALLBACK = None

# Window size. The minimum is what the widest screen needs: sidebar plus a
# 6x2 key grid plus the inspector column, measured rather than guessed.
_MIN_W, _MIN_H         = 900, 620
_DEFAULT_W, _DEFAULT_H = 1100, 720
_SIDEBAR_W             = 180


class App(ctk.CTk):
    # VID/PID constants for supported devices
    EVEREST_MAX_VID     = 0x3282
    EVEREST_MAX_PID     = 0x0001
    EVEREST60_VID       = 0x3282
    EVEREST60_PID_ANSI  = 0x0005
    EVEREST60_PID_ISO   = 0x0006
    MAKALU67_VID        = 0x3282
    MAKALU67_PID        = 0x0003
    DISPLAYPAD_VID      = 0x3282
    DISPLAYPAD_PID      = 0x0009
    MACROPAD_VID        = 0x3282
    MACROPAD_PID        = 0x0008

    def __init__(self):
        super().__init__()
        self.title("BaseCamp Linux")
        self.configure(fg_color=BG)
        # The window was nailed to 480x760 and not resizable, which is what
        # pushed every wide thing (the 12-key grid, the keyboard, the macro
        # editor) into a window of its own. It resizes now, remembers the size
        # it was left at, and refuses to go so small that a screen breaks.
        self.resizable(True, True)
        self.minsize(_MIN_W, _MIN_H)
        self.geometry(load_window_geometry() or f"{_DEFAULT_W}x{_DEFAULT_H}")
        self.bind("<Configure>", self._on_window_configure, add="+")
        # An open dropdown must not survive a switch to another program (#66).
        # Bound on the app itself so it does not depend on which screen has
        # been built, and every dialog binds it for its own window.
        UI.bind_dropdown_autoclose(self)
        self._geo_save_id = None

        # Enable drag & drop globally — soft-fails if tkinterdnd2 is missing.
        self._dnd_available = False
        try:
            from tkinterdnd2 import TkinterDnD
            TkinterDnD._require(self)
            self._dnd_available = True
        except Exception:
            pass

        try:
            _icon = ImageTk.PhotoImage(Image.open(
                os.path.join(_RES, "resources", "app_icon_64.png")))
            self.iconphoto(True, _icon)
        except Exception:
            pass

        # The UI-free core (4.0): services, events and commands. Its scheduler
        # is this window's main loop, so the logic that moves out of the
        # panels keeps running on the thread it ran on before. Services are
        # added as they are needed; core.start() comes after the device ones.
        self.core = CoreApp(scheduler=TkScheduler(self))

        # i18n: the strings live in the core's i18n service; _lang, _lang_code
        # and _avail_langs below are views of it for the panels.
        self._i18n_widgets  = []
        self._i18n = self.core.add_service(_i18n.I18nService(
            self.core, LANG_DIR, CONFIG_DIR))
        self._rebuild_obs_type_map()

        # Which devices are here, which we may not open, which keyboard is
        # shown: the registry computes it, _update_device_status renders it.
        # _dev_present, _dev_denied and _kb_panel_id below are views of it.
        self._devices = _make_device_registry(self.core)
        self._devices.set_busy_probe("displaypad", self._displaypad_busy)
        self._devices.add_virtual("obs", self._obs_connected)
        self._devices.add_scan_listener(self._update_device_status)

        # Profiles, backup, autostart, splash; OBS connection and actions.
        self._settings = self.core.add_service(SettingsService(self.core))
        self._obs = self.core.add_service(ObsService(self.core))

        # Update check, download and install; the sidebar text and the popup
        # are drawn here from update.available.
        self._updates = self.core.add_service(
            UpdateService(self.core, APP_VERSION, home=_real_home))
        subscribe_tk(self.core, "update.available", self,
                     self._on_update_available)

        self._lang_var = tk.StringVar()

        self._active_device = None   # "everest_max" | "everest60" | "makalu67" | "displaypad" | "macropad"
        self._panels        = {}     # populated in _build_ui

        install_device_services(self.core)
        # The one key action dispatcher (core/actions.py) every key goes
        # through, MacroPad and DisplayPad alike: OBS and the plugins' action
        # types are this application's, looked up when a key runs.
        self._actions = getattr(self.core.service("macros"), "actions", None)
        if self._actions is not None:
            self._actions.set_obs(self._obs_executor)
            self._actions.set_plugin_lookup(self._plugin_action_handler)
        # The Everest 60 and Makalu services start the controller command
        # lines this application builds, also before (or without) their
        # screens: the registry tells them when the device appears, and the
        # Everest 60 then puts its saved lighting back.
        for svc_name, dev_id in (("everest60", "everest60"), ("makalu", "makalu67")):
            svc = self.core.service(svc_name)
            if svc is not None and hasattr(svc, "use_command"):
                svc.use_command(lambda *a, d=dev_id: self._cmd_for_device(d, *a))
        self.core.start()

        # Plugin system
        self._plugin_manager = PluginManager()
        self._plugin_manager.discover()
        self._plugin_ctx = PluginContext(self, self._plugin_manager)
        self._plugin_manager.load_all(self._plugin_ctx)
        self._plugins = self.core.add_service(PluginHost(
            self.core, self._plugin_manager, net=PanelNet()))
        self.core.bus.subscribe("plugins.updates", self._on_plugins_updates)

        self._build_ui()

        # Populate language combo (now that EverestMaxPanel has created it)
        lang_names   = list(self._avail_langs.values())
        current_name = self._avail_langs.get(self._lang_code, "")
        self._lang_var.set(current_name)
        if hasattr(self, "_everest_panel"):
            if hasattr(self, "_everest_panel"):
                self._everest_panel._lang_combo.configure(values=lang_names)

        self._restore_debounce_id = None
        self._was_withdrawn = False
        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._hide_window)
        self.bind("<Unmap>", lambda e: self._hide_window() if self.state() == "iconic" else None)
        # Recover from display sleep — force refresh only after withdraw/deiconify
        self.bind("<Map>", self._on_window_restore)
        self.after(500, self._start_cpu_auto_clean)
        # Run first device check immediately so the correct panel is shown.
        # The screen itself is opened from _build_ui's deferred callback, which
        # reads the result of this scan (issues #22, #67).
        self._check_devices()
        self._devices.start_polling(5000)
        # Control IPC: lets external software (and the button-action daemon)
        # drive lighting, switch pages and redefine keys (issue #20).
        self._start_control_server()
        # Background update check — non-blocking, only sets a label if newer found
        self._update_message = ""
        self._update_url = ""
        self._update_sha_url = ""
        self._update_version = ""
        self._update_install_type = ""
        # _update_kind = "source" prefers the small overlay tarball (~200 KB
        # for typical patches); "appimage" downloads the full ~250 MB image.
        # Source updates are only chosen when a source-*.tar.gz asset exists
        # on the release, otherwise we fall back to AppImage.
        self._update_kind = ""
        self.after(2000, self._check_for_update)
        # A source install can pull a CustomTkinter newer than this version
        # knows; say so once rather than leave it to the console (#116).
        self.after(1500, self._check_toolkit)
        # Started in place of the web interface: say why, once per version.
        self.after(1800, self._check_web_fallback)
        # Which installed plugins have a newer published version. Asked for
        # here, a little after start and then twice a day: the Plugins screen
        # is only built when someone opens it, so leaving it to that screen
        # meant nobody heard about an update they did not go looking for
        # (#103). The screen reports what it fetches through the same door.
        # (the plugin service does the asking; plugins.updates comes back)
        self._plugin_update_count = 0

    # ── What a key action needs from the application ──────────────────────────

    def _obs_executor(self):
        """OBS for the key action dispatcher: the OBS screen's execute_action
        (it sends the connection settings in its form), else the service's."""
        panel = getattr(self, "_obs_panel", None)
        if panel is not None:
            return panel.execute_action
        return self._obs.execute_action

    def _plugin_action_handler(self, type_id):
        """A plugin's handler for an action type, or None (the plugin manager
        looked up now: a test replaces its types)."""
        pm = getattr(self, "_plugin_manager", None)
        return pm.get_action_handler(type_id) if pm is not None else None

    # ── subprocess command builder ────────────────────────────────────────────

    def _cmd(self, *args):
        """Build subprocess command for Everest Max controller (default device)."""
        return _cmd(*args)

    def _cmd_for_device(self, device_id, *args):
        """Build subprocess command for a specific device controller."""
        return _runtime.controller_cmd(device_id, *args, frozen=_FROZEN)

    # ── Control IPC (issue #20) ─────────────────────────────────────────────────

    def _start_control_server(self):
        """Host the control socket so external programs, the tray helper and
        the button-action daemon can drive the app (lighting, page switch, key
        redefine). The commands are the core's (core/control.py); what only
        this window can do, they ask for with ui.* events."""
        subscribe_tk(self.core, "ui.*", self, self._on_ui_request)
        self._control = self.core.add_service(ControlService(
            self.core, version=APP_VERSION,
            command=lambda dev, *a: self._cmd_for_device(dev, *a),
            screens=self._control_screens,
            active=lambda: self._active_device))
        # None when the socket could not be opened (Settings says so).
        self._control_server = self._control.server

    def _control_screens(self):
        """Every screen there is, built or not (`list`, `page`)."""
        return set(self._panels) | set(getattr(self, "_panel_factories", {}))

    def _on_ui_request(self, topic, payload):
        """What the control socket asks of the window, on the Tk thread."""
        what = topic.rsplit(".", 1)[-1]
        if what == "show":
            self._show_window()
        elif what == "page":
            self._switch_device(payload.get("page"))
        elif what == "quit":
            self._quit()

    def _handle_control(self, obj):
        """Dispatch one control command (see core/control.py). Kept for
        callers from before the move."""
        return self._control.handle(obj)

    def _run_control_cmd(self, cmdline):
        """Run a device-controller command for the control IPC and return output."""
        from core.control import run_controller
        return run_controller(cmdline)

    def _control_set_key(self, obj):
        """Redefine an Everest Max numpad button (issue #18); see
        ControlService.cmd_set_key."""
        return self._control.cmd_set_key(**obj)

    def _on_window_configure(self, event):
        """Remember the window geometry, but only after it stops changing.

        <Configure> fires for every pixel of a drag and for child widgets too,
        so writing on each event would mean hundreds of file writes while
        someone resizes. Debounced by half a second, and only for events that
        are about the window itself.
        """
        if event.widget is not self:
            return
        if self._geo_save_id is not None:
            try:
                self.after_cancel(self._geo_save_id)
            except Exception:
                pass
        self._geo_save_id = self.after(500, self._save_window_geometry)

    def _save_window_geometry(self):
        self._geo_save_id = None
        # A withdrawn or iconified window reports a useless geometry, and
        # saving that would reopen the app somewhere off screen next time.
        try:
            if self.state() != "normal":
                return
        except Exception:
            return
        self._settings.set_window_geometry(self.geometry())

    def _control_dp_state(self):
        """DisplayPad key pages for the `list` reply (ControlService)."""
        return self._control._dp_state()

    def _control_dp_page(self, obj):
        """Switch the DisplayPad's active key page from outside the GUI (#63);
        see ControlService.cmd_dp_page. tools/test_page_hints.py calls it."""
        return self._control.cmd_dp_page(**obj)

    # ── i18n ──────────────────────────────────────────────────────────────────

    @property
    def _lang(self):
        return self._i18n.strings

    @property
    def _lang_code(self):
        return self._i18n.code

    @property
    def _avail_langs(self):
        return self._i18n.avail

    def T(self, key, **kwargs):
        return self._i18n.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        self._i18n_widgets.append((widget, key, attr))
        return widget

    def _rebuild_obs_type_map(self):
        self._obs_type_options = {
            internal: self._lang.get(f"obs_{internal}", internal)
            for internal in OBS_INTERNAL_ORDER
        }
        self._obs_type_display_to_internal = {
            v: k for k, v in self._obs_type_options.items()
        }

    def _load_lang_code(self, code):
        self._i18n.load_code(code)
        self._rebuild_obs_type_map()
        self._apply_lang()

    def _apply_lang(self, only=None):
        """Re-label everything. `only` limits the panel round to one screen,
        which is what a freshly built screen needs: the others already have
        the current language and re-labelling them again is wasted work."""
        for widget, key, attr in self._i18n_widgets:
            try:
                widget.configure(**{attr: self.T(key)})
            except Exception:
                pass
        panels = [only] if only is not None else list(self._panels.values())
        for panel in panels:
            if hasattr(panel, "apply_lang"):
                try:
                    panel.apply_lang()
                except Exception as e:
                    print(f"[UI] apply_lang failed: {e}")
        if only is None:
            # The sidebar entries and the header are written from the language
            # file every time they are refreshed, so re-running that is all a
            # language change needs; registering each of them separately would
            # only duplicate what these two already do.
            for refresh in (self._refresh_sidebar, self._refresh_screen_header):
                try:
                    refresh()
                except Exception as e:
                    print(f"[UI] {refresh.__name__} failed: {e}")

    def _on_lang_change(self, val=None):
        selected_name = val if val is not None else self._lang_var.get()
        code = None
        for c, name in self._avail_langs.items():
            if name == selected_name:
                code = c
                break
        if code is None:
            return
        self._i18n.set_language(code)       # persists, publishes i18n.changed
        self._load_lang_code(code)

    def _pick_gif_frame(self, path, n_frames):
        dlg = ctk.CTkToplevel(self)
        dlg.title(self.T("gif_frame_title", n=n_frames))
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        result    = [0]
        cancelled = [False]

        preview_label = ctk.CTkLabel(dlg, text="", width=144, height=144,
                                      fg_color=BG3)
        preview_label.pack(pady=(12, 2), padx=16)

        info_label = ctk.CTkLabel(dlg, text="", fg_color="transparent",
                                   text_color=FG2, font=(UI.FONT_FAMILY, 11))
        info_label.pack()

        gif_img = Image.open(path)
        _photo  = [None]

        def _update_preview(frame_val):
            try:
                frame_idx = int(float(frame_val))
                gif_img.seek(frame_idx)
                frame    = gif_img.copy().resize((144, 144), Image.LANCZOS).convert("RGB")
                ctk_img  = ctk.CTkImage(light_image=frame, dark_image=frame,
                                         size=(144, 144))
                _photo[0] = ctk_img
                preview_label.configure(image=ctk_img)
                info_label.configure(text=self.T("gif_frame_info",
                                                  frame=frame_idx + 1, total=n_frames))
            except Exception:
                pass

        slider = ctk.CTkSlider(dlg, from_=0, to=n_frames - 1,
                                number_of_steps=n_frames - 1,
                                command=_update_preview,
                                width=200, progress_color=BLUE, button_color=FG,
                                fg_color=BG3)
        slider.set(0)
        slider.pack(pady=(6, 2), padx=16)
        _update_preview(0)

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(pady=(6, 12))

        def _ok():
            result[0] = int(slider.get())
            dlg.destroy()

        def _cancel():
            cancelled[0] = True
            dlg.destroy()

        ctk.CTkButton(btn_row, text=self.T("ui_ok"), command=_ok,
                      fg_color=BLUE, text_color=FG, hover_color="#0884be",
                      font=(UI.FONT_FAMILY, 11, "bold"), height=30, width=70,
                      corner_radius=6).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text=self.T("gif_frame_cancel"), command=_cancel,
                      fg_color=BG3, text_color=FG, hover_color=BG2,
                      font=(UI.FONT_FAMILY, 11), height=30, width=70,
                      corner_radius=6).pack(side="left")

        dlg.wait_window()
        return None if cancelled[0] else result[0]

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Sidebar left, content right. Replaces the old stack of an app header
        # over two rows of coloured pills: those wrapped into a third row as
        # soon as a plugin brought its own panel, and none of the colours meant
        # anything. A vertical list grows without rewrapping.
        self.grid_columnconfigure(0, weight=0, minsize=_SIDEBAR_W)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0, width=_SIDEBAR_W)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        self._sidebar = side

        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=UI.S3, pady=(UI.S4, UI.S2))
        ctk.CTkLabel(brand, text="Base", font=(UI.FONT_FAMILY, 14, "bold"),
                     text_color=FG).pack(side="left")
        ctk.CTkLabel(brand, text="Camp", font=(UI.FONT_FAMILY, 14, "bold"),
                     text_color=BLUE).pack(side="left")

        # Devices. Only what is actually plugged in shows up here, so the list
        # is about this desk and not about the product range.
        self._nav_devices_label = UI.SectionLabel(side, text=self.T("nav_devices"))
        self._reg(self._nav_devices_label, "nav_devices")
        self._nav_devices_label.pack(fill="x", padx=UI.S3, pady=(UI.S3, UI.S1))
        self._nav_devices_box = ctk.CTkFrame(side, fg_color="transparent")
        self._nav_devices_box.pack(fill="x")
        self._nav_empty = ctk.CTkLabel(
            self._nav_devices_box, text=self.T("nav_no_devices"),
            font=(UI.FONT_FAMILY, 10),
            text_color=FG2, anchor="w", justify="left", wraplength=_SIDEBAR_W - 24)
        self._reg(self._nav_empty, "nav_no_devices")

        self._nav_items = {}
        self._nav_items["keyboard"] = UI.NavItem(
            self._nav_devices_box, text=self.T("switcher_keyboard"), state="off",
            command=lambda: self._switch_device(self._kb_panel_id))
        self._reg(self._nav_items["keyboard"], "switcher_keyboard")
        self._nav_items["makalu67"] = UI.NavItem(
            self._nav_devices_box, text=self.T("switcher_mouse"), state="off",
            command=lambda: self._switch_device("makalu67"))
        self._reg(self._nav_items["makalu67"], "switcher_mouse")
        self._nav_items["displaypad"] = UI.NavItem(
            self._nav_devices_box, text="DisplayPad", state="off",
            command=lambda: self._switch_device("displaypad"))
        self._nav_items["macropad"] = UI.NavItem(
            self._nav_devices_box, text="MacroPad", state="off",
            command=lambda: self._switch_device("macropad"))

        self._nav_tools_label = UI.SectionLabel(side, text=self.T("nav_tools"))
        self._reg(self._nav_tools_label, "nav_tools")
        self._nav_tools_label.pack(fill="x", padx=UI.S3, pady=(UI.S4, UI.S1))
        self._nav_tools_box = ctk.CTkFrame(side, fg_color="transparent")
        self._nav_tools_box.pack(fill="x")
        # "Plugins" and "OBS Studio" are the same word in both languages, one a
        # borrowed term and one a product name, so they carry no key.
        for dev_id, label_key, literal in (("macros", "macro_title", None),
                                           ("plugins", None, "Plugins"),
                                           ("obs", None, "OBS Studio")):
            item = UI.NavItem(self._nav_tools_box,
                              text=self.T(label_key) if label_key else literal,
                              command=lambda d=dev_id: self._switch_device(d))
            if label_key:
                self._reg(item, label_key)
            item.pack(fill="x")
            self._nav_items[dev_id] = item

        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.pack(side="bottom", fill="x", pady=(0, UI.S2))
        self._settings_btn = UI.NavItem(
            foot, text=self.T("ui_settings"), command=self._open_settings)
        self._reg(self._settings_btn, "ui_settings")
        self._settings_btn.pack(fill="x")
        self._nav_quit = UI.NavItem(foot, text=self.T("ui_quit"), command=self._quit)
        self._reg(self._nav_quit, "ui_quit")
        self._nav_quit.pack(fill="x")

        # ── Content column: one header strip, then the screen ──
        content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")

        # The header belongs to the screen, not to the panel inside it, so
        # every screen says the same things in the same place: what you are
        # looking at on the left, what state it is in next to it, and later
        # the screen's one primary action on the right.
        head = ctk.CTkFrame(content, fg_color=BG2, corner_radius=0, height=48)
        head.pack(fill="x")
        head.pack_propagate(False)
        self._screen_title = ctk.CTkLabel(
            head, text="", font=(UI.FONT_FAMILY, 14, "bold"), text_color=FG, anchor="w")
        self._screen_title.pack(side="left", padx=(UI.S4, UI.S3))
        self._screen_state = UI.StatusPill(head, text="", state="off")
        self._screen_actions = ctk.CTkFrame(head, fg_color="transparent")
        self._screen_actions.pack(side="right", padx=UI.S4)

        self._panel_area = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
        self._panel_area.pack(fill="both", expand=True)

        # Empty-state overlay shown over a hardware panel when its device isn't
        # connected, instead of a panel full of inert controls (issue #19).
        self._no_device_frame = ctk.CTkFrame(self._panel_area, fg_color=BG)
        _nd_inner = ctk.CTkFrame(self._no_device_frame, fg_color="transparent")
        _nd_inner.place(relx=0.5, rely=0.45, anchor="center")
        self._no_device_title = ctk.CTkLabel(
            _nd_inner, text="", font=(UI.FONT_FAMILY, 16, "bold"), text_color=FG)
        self._no_device_title.pack()
        self._no_device_hint = ctk.CTkLabel(
            _nd_inner, text="", font=(UI.FONT_FAMILY, 11), text_color=FG2,
            wraplength=320, justify="center")
        self._no_device_hint.pack(pady=(6, 0))

        # Two screens are built up front because they work whether or not you
        # are looking at them: the DisplayPad owns the device (key events,
        # uploads, page switches from dp_page) and OBS holds the connection
        # other screens ask about. Everything else is built the first time it
        # is opened.
        #
        # This is where the startup time was: every CustomTkinter widget draws
        # itself with anti-aliased corners on a canvas, and building all eight
        # screens meant about 1400 rounded rectangles and 4000 circles before
        # the window appeared.
        self._obs_panel        = OBSPanel(self._panel_area, self)
        self._displaypad_panel = DisplayPadPanel(self._panel_area, self)
        # Built up front, not on first visit like the other device screens: it
        # owns the MacroPad's command interface, and that interface is what
        # carries key presses. A lazy screen would mean the keys do nothing
        # until someone happens to open it.
        self._macropad_panel   = MacroPadPanel(self._panel_area, self)

        self._panels = {
            "displaypad": self._displaypad_panel,
            "macropad":   self._macropad_panel,
            "obs":        self._obs_panel,
        }
        self._panel_factories = {
            "everest_max": lambda: EverestMaxPanel(self._panel_area, self),
            "everest60":   lambda: Everest60Panel(self._panel_area, self),
            "makalu67":    lambda: Makalu67Panel(self._panel_area, self),
            "macros":      lambda: MacroPanel(self._panel_area, self),
            "plugins":     lambda: PluginManagerPanel(self._panel_area, self),
            "settings":    lambda: SettingsPanel(self._panel_area, self),
        }
        self._panel_attr = {
            "everest_max": "_everest_panel", "everest60": "_everest60_panel",
            "makalu67": "_makalu_panel", "macros": "_macro_panel",
            "plugins": "_plugins_panel", "settings": "_settings_panel",
        }

        # ── Plugin panels ──
        # A plugin that brings its own screen lands under Tools, in the same
        # list as everything else. This is what used to force a third row of
        # pills the moment two of them were installed. Their panels are built
        # on first open like the rest.
        self._plugin_sw_btns = {}
        for pid, info, inst in list(self._plugin_manager.get_panel_plugins()):
            try:
                label = getattr(inst, "panel_label", info.get("name", pid))
                self._panel_factories[pid] = (
                    lambda i=inst: i.create_panel(self._panel_area))
                item = UI.NavItem(self._nav_tools_box, text=label,
                                  command=lambda p=pid: self._switch_device(p))
                item.pack(fill="x")
                self._nav_items[pid] = item
                self._plugin_sw_btns[pid] = item
            except Exception as e:
                print(f"[Plugin] Failed to register panel for {pid}: {e}")

        # Start plugin services after UI is ready
        self.after(100, self._plugin_manager.start_services)

        # Show the first screen once the window itself is up. Building it
        # inline meant the window only appeared when the whole screen was
        # drawn; this way the shell is on screen and the screen fills in.
        #
        # Which screen that is, is decided from the USB scan that __init__ has
        # by then already run, not hardcoded to the keyboard (issue #67): a
        # fixed "everest_max" here overrode the startup choice a moment later,
        # so a desk without a keyboard saw its pad, then "no keyboard", then
        # its pad again.
        self.after(1, self._select_startup_device)

    # ── Device switching ──────────────────────────────────────────────────────

    def _get_panel(self, device_id):
        """The screen for an id, built on first use.

        Building it here rather than at startup is the difference between a
        window that appears in a second and one that takes four, because the
        cost is entirely in drawing widgets nobody has asked for yet.
        """
        panel = self._panels.get(device_id)
        if panel is not None:
            return panel
        factory = self._panel_factories.get(device_id)
        if factory is None:
            return None
        panel = factory()
        self._panels[device_id] = panel
        attr = self._panel_attr.get(device_id)
        if attr:
            setattr(self, attr, panel)
        # A freshly built screen has never seen a language change.
        try:
            self._apply_lang(only=panel)
        except Exception:
            pass
        return panel

    def _switch_device(self, device_id):
        if self._active_device == device_id:
            return
        if self._get_panel(device_id) is None:
            return
        # Hide all panels. A screen that polls something is told it is gone,
        # so nothing keeps reading sensors for a screen nobody is looking at.
        for pid, panel in self._panels.items():
            if pid == self._active_device and hasattr(panel, "on_hide"):
                try:
                    panel.on_hide()
                except Exception:
                    pass
            panel.pack_forget()
        # Show selected panel
        self._panels[device_id].pack(fill="both", expand=True)
        self._active_device = device_id

        panel = self._panels[device_id]
        # Screens fill the header's action slot themselves. Cleared on every
        # switch so a screen never sees another screen's buttons.
        for w in self._screen_actions.winfo_children():
            w.destroy()
        if hasattr(panel, "header_actions"):
            try:
                panel.header_actions(self._screen_actions)
            except Exception as e:
                print(f"[UI] header_actions failed for {device_id}: {e}")
        if hasattr(panel, "refresh"):
            try:
                panel.refresh()
            except Exception as e:
                print(f"[UI] refresh failed for {device_id}: {e}")

        # Update the sidebar selection and the screen header
        self._refresh_sidebar()
        self._refresh_screen_header()

        # Show/hide the "no device connected" overlay for this panel
        self._update_empty_state()

        # Force CTkButtons/widgets to redraw — CTk skips internal canvas
        # draw for widgets that were built while their panel was hidden
        self.after(20, self._redraw_panel_widgets, device_id)

    def _update_empty_state(self):
        """Show the 'no device detected' overlay when the active hardware panel's
        device is not connected; hide it for software panels or present devices.
        Keeps the switcher usable so software tabs stay reachable (issue #19)."""
        if not hasattr(self, "_no_device_frame"):
            return
        active = self._active_device
        show = False
        title_key = "no_device_keyboard"
        if active in ("everest_max", "everest60"):
            show = not (self._dev_present.get("everest_max")
                        or self._dev_present.get("everest60"))
            title_key = "no_device_keyboard"
        elif active == "makalu67":
            show = not self._dev_present.get("makalu67")
            title_key = "no_device_mouse"
        elif active == "displaypad":
            show = not self._dev_present.get("displaypad")
            title_key = "no_device_displaypad"
        elif active == "macropad":
            show = not self._dev_present.get("macropad")
            title_key = "no_device_macropad"
        # Present but not openable is worth its own message: the controls are
        # all there and none of them do anything, which is indistinguishable
        # from the application being broken unless we say so (#49).
        denied = None
        if active in ("everest_max", "everest60"):
            denied = (self._dev_denied.get("everest_max")
                      or self._dev_denied.get("everest60"))
        elif active in ("makalu67", "displaypad", "macropad"):
            denied = self._dev_denied.get(active)
        if not show and denied:
            self._no_device_title.configure(text=self.T("no_access_title"))
            self._no_device_hint.configure(
                text=self.T("no_access_hint", nodes=", ".join(sorted(denied)[:4])))
            self._no_device_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._no_device_frame.tkraise()
            return
        if show:
            self._no_device_title.configure(text=self.T(title_key))
            self._no_device_hint.configure(text=self.T("no_device_hint"))
            self._no_device_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._no_device_frame.tkraise()
        else:
            self._no_device_frame.place_forget()

    def _redraw_panel_widgets(self, device_id):
        """Walk the active panel and force _draw() on all CTk widgets."""
        panel = self._panels.get(device_id)
        if not panel or not panel.winfo_exists():
            return
        self._force_draw_children(panel)

    def _force_draw_children(self, widget):
        """Recursively call _draw() on CTk widgets that have it."""
        if hasattr(widget, "_draw") and callable(widget._draw):
            try:
                widget._draw()
            except Exception:
                pass
        for child in widget.winfo_children():
            self._force_draw_children(child)

    # ── Controller delegation ─────────────────────────────────────────────────

    def _stop_cpu_proc(self):
        """Stop every running CPU monitor. Returns True if one was running.

        Asked before any command that talks to a keyboard: the monitor holds
        that keyboard's USB interface, and a second claim on it fails with
        "Failed to claim interface", which takes the whole command down.

        This used to look at the screen that is open, which was the same thing
        while the colour editors were windows on top of their device screen.
        Since 3.0 they are screens of their own, so from inside the per-key
        editor the lookup found the editor, which owns no monitor, and the
        monitor was left running. Ask the panels that actually have one.
        """
        self._cpu_stopped = []
        for pid, panel in self._panels.items():
            if not hasattr(panel, "_stop_cpu_proc"):
                continue
            try:
                if panel._stop_cpu_proc():
                    self._cpu_stopped.append(pid)
            except Exception as e:
                print(f"[Monitor] could not stop {pid}: {e}")
        return bool(self._cpu_stopped)

    def _start_cpu_auto(self):
        """Restart the monitors _stop_cpu_proc() stopped, and only those."""
        for pid in getattr(self, "_cpu_stopped", []):
            panel = self._panels.get(pid)
            if panel and hasattr(panel, "_start_cpu_auto"):
                try:
                    panel._start_cpu_auto()
                except Exception as e:
                    print(f"[Monitor] could not restart {pid}: {e}")
        self._cpu_stopped = []

    def _start_cpu_auto_clean(self):
        """Monitor Mode at start-up: through the Everest Max's screen when it
        has been built (tests replace its member), else through the service
        whenever an Everest Max is plugged in. It used to start only when the
        keyboard's screen happened to exist half a second after start, which
        an Everest 60 beside it, or a start on another screen, prevented."""
        panel = getattr(self, "_everest_panel", None)
        if panel is not None:
            panel._start_cpu_auto_clean()
            return
        svc = self.core.service("everest_max")
        if svc is not None and self._dev_present.get("everest_max"):
            svc.start_monitor(clean=True)

    # ── USB presence check ────────────────────────────────────────────────────

    def _select_startup_device(self):
        """Open the first connected device. With nothing connected we land on
        Macros, which is the one screen that works without hardware, and the
        sidebar says why the device list is empty (#22).

        This is the only screen switch at startup. It runs from the deferred
        callback in _build_ui, after __init__'s first _check_devices() has
        filled _dev_present, so the first screen drawn is already the right
        one (#67)."""
        self._fall_back_to_present_device()

    def _fall_back_to_present_device(self):
        """Switch to the first device that is actually here, else to Macros."""
        if (self._dev_present.get("everest_max")
                or self._dev_present.get("everest60")):
            self._switch_device(self._kb_panel_id)
            return
        for dev in ("makalu67", "displaypad", "macropad"):
            if self._dev_present.get(dev):
                self._switch_device(dev)
                return
        if self._active_device not in ("macros", "plugins", "obs"):
            self._switch_device("macros")

    # Views of the registry's state; panels, plugins and gui.py read these.
    @property
    def _dev_present(self):
        return self._devices.present

    @property
    def _dev_denied(self):
        """Devices that enumerated but whose /dev nodes we may not open (#49)."""
        return self._devices.denied

    @property
    def _kb_panel_id(self):
        """Which keyboard panel is active."""
        return self._devices.keyboard_id

    def _check_devices(self):
        """One presence and access scan now; the registry repeats it every 5 s
        and calls _update_device_status after each."""
        self._devices.scan()

    def _displaypad_busy(self):
        """True while the application itself is uploading to the DisplayPad.

        Only the upload sessions, which are short and bounded. The device
        worker holds its lock for long stretches while it listens for key
        presses, so waiting on that would switch the check off for good.
        """
        panel = getattr(self, "_displaypad_panel", None)
        if panel is None:
            return False
        return bool(getattr(panel, "_uploading", False)
                    or getattr(panel, "_animating", False))

    def _obs_connected(self):
        return self._obs.is_connected()

    def _update_device_status(self, state=None):
        """Render the registry's state: switcher buttons, screen fall-back,
        sidebar, header, the panels' set_connected, the empty-state overlay."""
        present = self._devices.present
        kb_max_present = present["everest_max"]
        kb_60_present = present["everest60"]
        old_kb_id = self._devices.prev_keyboard_id
        # Auto-switch if viewing a keyboard panel that changed
        if (self._active_device in ("everest_max", "everest60")
                and self._kb_panel_id != old_kb_id):
            self._active_device = None  # force re-switch
            self._switch_device(self._kb_panel_id)
        # Entries carry the model name once we know it, so the list reads
        # "Everest Max" and "Makalu 67" rather than "Keyboard" and "Mouse".
        mouse_label = (getattr(self._makalu_panel, "_model_name", None)
                       if hasattr(self, "_makalu_panel") else None)
        if kb_60_present and hasattr(self, "_everest60_panel"):
            kb_label = getattr(self._everest60_panel, "_model_name", "Everest 60")
        elif kb_max_present:
            kb_label = "Everest Max"
        else:
            kb_label = self.T("switcher_keyboard")
        if hasattr(self, "_nav_items"):
            self._nav_items["keyboard"].set_text(kb_label)
            self._nav_items["makalu67"].set_text(
                mouse_label or self.T("switcher_mouse"))
        # A device can vanish while its screen is open (unplugged, or the pad
        # dropping off the bus). Move to the next one that is here instead of
        # leaving a screen up that talks to nothing.
        if self._active_device in ("everest_max", "everest60") and not (
                kb_max_present or kb_60_present):
            self._fall_back_to_present_device()
        elif (self._active_device in ("makalu67", "displaypad", "macropad")
              and not self._dev_present.get(self._active_device, False)):
            self._fall_back_to_present_device()
        self._refresh_sidebar()
        self._refresh_screen_header()
        # The Everest Max, Everest 60, Makalu and MacroPad services hear about their device
        # from the registry itself (core/devices/registry.wire_presence), and
        # their screens follow the services' presence events.
        # Reflect (dis)connection in the empty-state overlay
        self._update_empty_state()

    def _refresh_sidebar(self):
        """Show the devices that are here, hide the ones that are not, and mark
        the current screen.

        A device that is not plugged in is absent from the list rather than
        greyed out: the list should describe this desk, not the product range.
        The dot is therefore no longer "present or not", it is the device's
        state, so that a pad which enumerated but has not finished its init
        handshake can say so instead of looking identical to a working one.
        """
        if not hasattr(self, "_nav_items"):
            return
        kb_present = (self._dev_present.get("everest_max", False)
                      or self._dev_present.get("everest60", False))
        order = (("keyboard", kb_present),
                 ("makalu67", self._dev_present.get("makalu67", False)),
                 ("displaypad", self._dev_present.get("displaypad", False)),
                 ("macropad", self._dev_present.get("macropad", False)))

        visible = tuple(key for key, present in order if present)
        # Re-pack only when the list really changed. This runs off the device
        # scan every five seconds, and taking every entry out of the box
        # and putting them back each time is visible as a flicker even though
        # nothing moved. Packing an already-packed widget would move it to the
        # end of the box, hence the full rebuild when it does change.
        if visible != getattr(self, "_nav_visible", None):
            for key, _ in order:
                self._nav_items[key].pack_forget()
            for key in visible:
                self._nav_items[key].pack(fill="x")
            if visible:
                self._nav_empty.pack_forget()
            else:
                self._nav_empty.pack(fill="x", padx=UI.S3, pady=(UI.S1, UI.S2))
            self._nav_visible = visible

        for key in visible:
            # A device we cannot open is not a working one, and the dot is
            # the device's state, not its presence (#49).
            denied = (self._dev_denied.get("everest_max")
                      or self._dev_denied.get("everest60")) if key == "keyboard" \
                else self._dev_denied.get(key)
            self._nav_items[key].set_state("warn" if denied else "ok")

        active = self._active_device
        for key, item in self._nav_items.items():
            if key == "keyboard":
                item.set_selected(active in ("everest_max", "everest60"))
            else:
                item.set_selected(active == key)

    # Kept so older call sites keep working while the panels are migrated.
    _refresh_switcher_colors = _refresh_sidebar

    # Literal where both languages agree (a product name, a borrowed term),
    # a language key where they do not.
    _SCREEN_TITLES = {
        "obs": "OBS Studio", "macros": ("key", "macro_title"), "plugins": "Plugins",
        "settings": None,   # filled from the language file at refresh time
    }

    def _refresh_screen_header(self):
        """Name of the current screen on the left, device state beside it.

        Devices carry a state pill, tools do not: a plugin screen has no
        connection to report and an empty pill next to it would only raise the
        question of what it is missing.
        """
        if not hasattr(self, "_screen_title"):
            return
        dev = self._active_device
        if dev in ("everest_max", "everest60"):
            title = self._nav_items["keyboard"]._label.cget("text")
        elif dev in ("makalu67", "displaypad", "macropad"):
            title = self._nav_items[dev]._label.cget("text")
        elif dev == "settings":
            title = self.T("settings_title")
        elif dev in self._SCREEN_TITLES:
            title = self._SCREEN_TITLES[dev]
            if isinstance(title, tuple):
                title = self.T(title[1])
        else:
            item = self._nav_items.get(dev)
            title = item._label.cget("text") if item is not None else ""
        self._screen_title.configure(text=title)

        is_device = dev in ("everest_max", "everest60", "makalu67",
                            "displaypad", "macropad")
        if is_device:
            present = (self._dev_present.get("everest_max") or
                       self._dev_present.get("everest60")) if dev.startswith("everest") \
                else self._dev_present.get(dev, False)
            denied = (self._dev_denied.get("everest_max")
                      or self._dev_denied.get("everest60")) if dev.startswith("everest") \
                else self._dev_denied.get(dev)
            if present and denied:
                # Plugged in and unusable is its own state. Saying "connected"
                # here was the reason a permissions problem looked like the
                # application being broken (#49).
                self._screen_state.set(text=self.T("state_no_access"), state="bad")
                self._screen_state.pack(side="left")
                return
            self._screen_state.set(
                text=self.T("state_connected") if present else self.T("state_absent"),
                state="ok" if present else "off")
            self._screen_state.pack(side="left")
        else:
            self._screen_state.pack_forget()

    # ── Tray / lifecycle ──────────────────────────────────────────────────────

    def _setup_tray(self):
        import signal as _signal
        _signal.signal(_signal.SIGUSR1, lambda *_: self.after(0, self._show_window))
        _signal.signal(_signal.SIGUSR2, lambda *_: self.after(0, self._quit))

        lang_file = os.path.join(LANG_DIR, f"{self._lang_code}.json")
        env = os.environ.copy()
        if os.environ.get("SUDO_USER"):
            user = os.environ["SUDO_USER"]
            uid  = _pwd.getpwnam(user).pw_uid
            env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
            env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
            if _FROZEN:
                cmd = ["sudo", "-u", user, "-E", TRAY_HELPER,
                       str(os.getpid()), lang_file]
            else:
                cmd = ["sudo", "-u", user, "-E", sys.executable, TRAY_HELPER,
                       str(os.getpid()), lang_file]
        else:
            if _FROZEN:
                cmd = [TRAY_HELPER, str(os.getpid()), lang_file]
            else:
                cmd = [_interpreter(), TRAY_HELPER, str(os.getpid()), lang_file]
        # The tray icon is a convenience, not a precondition: a helper that
        # cannot be started used to take the whole application down with a
        # FileNotFoundError out of __init__, so the window never appeared at
        # all (#77). Say what happened and carry on without it.
        self._tray_proc = None
        try:
            if not cmd[0]:
                raise FileNotFoundError("no python interpreter found")
            self._tray_proc = subprocess.Popen(cmd, env=env)
        except Exception as e:
            print(f"[Tray] not started ({type(e).__name__}: {e}); "
                  f"the app runs without a tray icon", flush=True)

    def _on_window_restore(self, event=None):
        """Force UI refresh after withdraw/deiconify (tray restore or display sleep)."""
        if not self._was_withdrawn:
            return
        self._was_withdrawn = False
        if self._restore_debounce_id is not None:
            self.after_cancel(self._restore_debounce_id)
        self._restore_debounce_id = self.after(200, self._do_window_restore)

    def _do_window_restore(self):
        """Actual restore logic, called once after debounce settles."""
        self._restore_debounce_id = None
        try:
            geo = self.geometry()
            self.geometry(geo)
            self.update_idletasks()
            self._refresh_switcher_colors()
            if self._active_device and self._active_device in self._panels:
                panel = self._panels[self._active_device]
                panel.pack_forget()
                panel.pack(fill="both", expand=True)
            self.lift()
        except Exception:
            pass

    def _hide_window(self):
        self._was_withdrawn = True
        self.withdraw()

    def _show_window(self):
        self.deiconify()
        self.lift()

    def _detect_install_type(self):
        """'appimage' | 'arch' | 'debian' | 'source' (see core.updates)."""
        return self._updates.install_type()

    def _check_toolkit(self):
        """Tell a source install once that its CustomTkinter is newer than this
        version handles. The dropdowns then use CustomTkinter's own menus
        (install_inline_dropdown declined), which work, but without the fix
        for #66, and the next CustomTkinter may break something else as well.
        The AppImage carries the CustomTkinter it was tested with, and there is
        no source to compare against in it anyway."""
        from shared.ui import dropdown
        notice = toolkit_notice(APP_VERSION, getattr(ctk, "__version__", "?"),
                                frozen=_FROZEN, declined=dropdown.DECLINED)
        if notice is None:
            return
        ver = notice["version"]
        try:
            UI.ConfirmDialog(
                self, self.T("toolkit_notice_title"),
                self.T("toolkit_notice_body", ver=ver),
                self.T("toolkit_notice_ok"),
                detail=self.T("toolkit_notice_detail", below=notice["below"]))
        except Exception as e:
            print(f"[BaseCamp] CustomTkinter {ver} is newer than this version "
                  f"supports; update BaseCamp or install customtkinter<"
                  f"{notice['below']} ({e})", file=sys.stderr)

    def _check_web_fallback(self):
        """Say once per version why the classic interface came up when the
        web one was asked for (run() noted the reason). An AppImage without
        pywebview is one from before 4.0 that the in-app update brought here:
        only a new AppImage brings the new interface. Anything else is a
        source install missing pywebview or a web view for it."""
        if not _WEB_FALLBACK:
            return
        from ui_web.launch import fallback_notice, has_webview
        from shared.config import _web_notice_seen, _mark_web_notice_seen
        notice = fallback_notice(APP_VERSION, _WEB_FALLBACK, frozen=_FROZEN,
                                 has_webview=has_webview(),
                                 seen=_web_notice_seen, mark=_mark_web_notice_seen)
        if notice is None:
            return
        body = ("web_fallback_body_appimage" if notice["kind"] == "appimage"
                else "web_fallback_body_install")
        try:
            UI.ConfirmDialog(
                self, self.T("web_fallback_title"), self.T(body),
                self.T("web_fallback_ok"),
                detail=self.T("web_fallback_detail", why=notice["why"]))
        except Exception as e:
            print(f"[BaseCamp] the classic interface runs because the new one "
                  f"cannot start: {notice['why']} ({e})", file=sys.stderr)

    def _check_for_update(self):
        """Ask the update service for a newer release, off the interface
        thread. The answer comes back as update.available (fail-quiet on any
        network or parse error)."""
        self._updates.check_async()

    def _on_update_available(self, topic, info):
        ver = info["version"]
        install_type = info["install_type"]
        # Per-distro instruction: the install_type drives which follow-up
        # command the user needs to run.
        hint = self.T(f"settings_update_hint_{install_type}")
        self._update_message = (self.T("settings_update_available", ver=ver)
                                + "\n" + hint)
        # The actionable URL: source if available, else the full AppImage.
        # The popup/button trigger logic only checks _update_url, so this
        # stays transparent.
        self._update_url = info["url"]
        self._update_sha_url = info["sha_url"]
        self._update_kind = info["kind"]
        self._update_version = ver
        self._update_install_type = install_type
        # Say it in the sidebar so the update is visible without opening
        # anything. Works for every install type, source and AUR users see
        # "there is something" even though the button will not install it
        # for them.
        if hasattr(self, "_settings_btn"):
            self._settings_btn.set_text(self.T("ui_update_short", ver=ver))
        # Proactive popup, only for AppImage installs where we can actually
        # do something about it from the GUI. Trigger on _update_url so
        # source-only releases (no AppImage asset) still pop up; without
        # this check the popup never fired for tiny patches.
        if install_type == "appimage" and self._update_url:
            self._show_update_popup()

    def _show_update_popup(self):
        """Open the proactive update popup. Guarded against double-spawn so
        repeated _check_for_update calls don't stack windows."""
        existing = getattr(self, "_update_popup", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.focus()
                    return
            except Exception:
                pass
        self._update_popup = UpdateAvailableDialog(self)

    _UPDATE_ERRORS = {"no_asset": "settings_update_no_asset",
                      "no_checksum": "settings_update_no_checksum",
                      "bad_checksum": "settings_update_bad_checksum"}

    def run_update_download(self, on_progress, on_installing, on_done, on_error):
        """Download and install the update the service found (see
        core.updates.UpdateService.download_and_install). The callbacks fire
        on the Tk thread; on_error gets the translated text."""
        from core.updates import UpdateInfo
        info = UpdateInfo(
            version=getattr(self, "_update_version", ""),
            install_type=getattr(self, "_update_install_type", ""),
            url=getattr(self, "_update_url", ""),
            sha_url=getattr(self, "_update_sha_url", ""),
            kind=getattr(self, "_update_kind", "") or "appimage")

        def failed(code, message):
            key = self._UPDATE_ERRORS.get(code)
            on_error(self.T(key) if key else message)

        self._updates.download_and_install(
            info, on_progress=on_progress, on_installing=on_installing,
            on_done=on_done, on_error=failed)

    def restart_after_update(self):
        """Re-exec the (now updated) AppImage. Kills the tray helper first:
        execv preserves the PID, so the helper would otherwise sit on the new
        process and a second tray would spawn on next startup."""
        tray = getattr(self, "_tray_proc", None)
        if tray is not None and tray.poll() is None:
            try:
                tray.terminate()
                tray.wait(timeout=2)
            except Exception:
                try:
                    tray.kill()
                except Exception:
                    pass
        self._updates.restart()
        self.destroy()

    def _on_plugins_fetched(self, plugins, announce=False):
        """The published plugins have been fetched: the plugin service counts
        the installed ones with a newer version and answers with
        plugins.updates (see _on_plugins_updates)."""
        self._plugins.note_index(plugins, announce=announce)

    def _on_plugins_updates(self, topic, info):
        """Show the count on the Plugins entry in the sidebar and, when the
        service says it is news, say so once (and only to someone who can see
        it, see _announce_plugin_updates). Delivered on the scheduler thread,
        which is the Tk main thread."""
        count = info["count"]
        self._plugin_update_count = count
        item = getattr(self, "_nav_items", {}).get("plugins")
        if item is not None:
            item.set_badge(count)
        if info["announce"]:
            self._announce_plugin_updates(count)

    def _announce_plugin_updates(self, count):
        """Say that plugins have updates, with a button to the Plugins screen.

        Only to someone who can see it. Started by autostart the window sits in
        the tray, and a message shown there now is gone before anybody opens
        it, which is how the 3.2.0 one went unnoticed. It waits for the window
        then, and says the count as it is by that time.
        """
        if self._showing_on_screen():
            self._plugin_notice_waiting = False
            self.toast(self.T("plugin_updates_one") if count == 1
                       else self.T("plugin_updates_many", n=count),
                       kind="info", ms=15000,
                       action=(self.T("plugin_updates_show"),
                               lambda: self._switch_device("plugins")))
            return
        if not getattr(self, "_plugin_notice_waiting", False):
            self._plugin_notice_waiting = True
            self.bind("<Map>", self._plugin_notice_on_map, add="+")

    def _showing_on_screen(self):
        try:
            return bool(self.winfo_viewable()) and self.state() == "normal"
        except tk.TclError:
            return False

    def _plugin_notice_on_map(self, event):
        # Every widget's <Map> passes through the window's bindings; only the
        # window itself coming up counts.
        if event.widget is not self or not getattr(self, "_plugin_notice_waiting", False):
            return
        # Once it is drawn, so the message lands on the screen rather than
        # under it while the window is still being laid out.
        self.after(600, self._plugin_notice_now)

    def _plugin_notice_now(self):
        if not getattr(self, "_plugin_notice_waiting", False):
            return
        count = getattr(self, "_plugin_update_count", 0)
        if not count:
            self._plugin_notice_waiting = False
            return
        if self._showing_on_screen():
            self._announce_plugin_updates(count)

    def toast(self, text, kind="info", ms=3500, action=None):
        """Say something briefly, over the current screen. `action` is
        (label, callback) for a button on the message.

        Panels used to keep a coloured label in their layout for this, which
        took a row permanently and kept the last message on screen for the
        rest of the session.
        """
        if not text:
            return
        try:
            UI.Toast(self._panel_area, text, kind=kind, ms=ms, action=action)
        except Exception:
            pass

    def open_screen(self, screen_id, factory, title=None):
        """Register and show a screen that is built the first time it is used.

        The per-key colour editors are the case this exists for: building them
        at startup would cost a canvas with 126 keys per keyboard for a screen
        most sessions never open.
        """
        if screen_id not in self._panels:
            panel = factory(self._panel_area)
            self._panels[screen_id] = panel
            if title:
                self._SCREEN_TITLES[screen_id] = title
        self._switch_device(screen_id)

    def close_screen(self, screen_id, back_to):
        """Leave a screen opened with open_screen() and go back."""
        self._switch_device(back_to)

    def _open_settings(self):
        """Settings is a screen now, not a modal on top of the app."""
        self._switch_device("settings")

    def _quit(self):
        self.destroy()

    def destroy(self):
        # Signal all background HID threads to stop
        if hasattr(self, "_displaypad_panel"):
            p = self._displaypad_panel
            # First, so no worker starts opening the device while the rest of
            # this runs. The <Destroy> binding that stops the plugin worker
            # only fires inside super().destroy() below, which is after the
            # wait, and the per-upload worker was never told at all: a thread
            # still in libusb_open when the interpreter tears down aborts the
            # process rather than exiting it.
            if hasattr(p, "_closing"):
                p._closing.set()
            if hasattr(p, "_plugin_worker_stop"):
                p._plugin_worker_stop.set()
            if hasattr(p, "_monitor_stop"):
                p._monitor_stop.set()
            if hasattr(p, "_key_stop"):
                p._key_stop.set()
            if hasattr(p, "_anim_stop"):
                p._anim_stop.set()
        # The MacroPad's device thread opens the pad in a loop and was never
        # told to stop, so it kept doing that while the application went away.
        # It talks hidapi rather than libusb, so it is not the abort the
        # DisplayPad's worker could cause, but there is no reason to leave it
        # opening a device nobody is going to read.
        if hasattr(self, "_macropad_panel"):
            try:
                self._macropad_panel._stop_worker()
            except Exception:
                pass
        # Stop Everest panel CPU proc if running
        if hasattr(self, "_everest_panel"):
            if hasattr(self, "_everest_panel") and self._everest_panel._cpu_proc \
                    and self._everest_panel._cpu_proc.poll() is None:
                self._everest_panel._cpu_proc.terminate()
        # None when the tray helper could not be started at all (#77).
        tray = getattr(self, "_tray_proc", None)
        if tray is not None and tray.poll() is None:
            tray.terminate()
        # Stop control IPC server
        control = getattr(self, "_control", None)
        if control is not None:
            control.stop_server()
        # Shutdown plugins
        if hasattr(self, "_plugin_manager"):
            self._plugin_manager.shutdown()
        # Device services stop their workers and release the hardware.
        core = getattr(self, "core", None)
        if core is not None:
            core.stop()
        # Give HID threads time to close their devices before tearing down
        import time
        time.sleep(0.4)
        # A fixed wait is a guess, and a worker that started opening the device
        # just before _closing was set is still inside libusb when it runs out.
        # The upload worker holds this lock for the whole time it owns the
        # device, so taking it is proof that nobody is in there any more.
        # Tearing down while one is aborts the process instead of ending it.
        p = getattr(self, "_displaypad_panel", None)
        lock = getattr(p, "_usb_lock", None) if p is not None else None
        if lock is not None and lock.acquire(timeout=3.0):
            lock.release()
        super().destroy()


# ── Splash screen ─────────────────────────────────────────────────────────────

def show_splash():
    splash = tk.Tk()
    splash.overrideredirect(True)
    img   = Image.open(os.path.join(_RES, "resources", "logo.png")).convert("RGBA")
    img   = img.resize((768, 512), Image.LANCZOS)
    bg    = Image.new("RGBA", img.size, BG)
    bg.paste(img, mask=img.split()[3])
    photo = ImageTk.PhotoImage(bg.convert("RGB"))
    w, h  = img.size
    sw    = splash.winfo_screenwidth()
    sh    = splash.winfo_screenheight()
    splash.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    splash.configure(bg=BG)
    tk.Label(splash, image=photo, bd=0, bg=BG).pack()
    splash.after(3500, splash.destroy)
    splash.mainloop()


def _install_desktop_entry():
    """Install .desktop file and icon to ~/.local/share/ for app menu integration."""
    import shutil
    app_dir       = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    appimage_path = os.environ.get("APPIMAGE", os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))

    icon_src = os.path.join(app_dir, "_internal", "resources", "app_icon_256.png")
    if not os.path.exists(icon_src):
        icon_src = os.path.join(app_dir, "resources", "app_icon_256.png")
    icon_dst = os.path.join(_real_home, ".local", "share", "icons", "hicolor",
                             "256x256", "apps", "basecamp-linux.png")
    os.makedirs(os.path.dirname(icon_dst), exist_ok=True)
    shutil.copy2(icon_src, icon_dst)

    desktop_dir  = os.path.join(_real_home, ".local", "share", "applications")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_path = os.path.join(desktop_dir, "basecamp-linux.desktop")
    with open(desktop_path, "w") as f:
        f.write(f"""[Desktop Entry]
Name=BaseCamp Linux
Comment=Unofficial Linux companion app for the Mountain Everest Max keyboard
Exec="{appimage_path}"
Icon=basecamp-linux
Type=Application
Categories=Utility;
""")
    os.chmod(desktop_path, 0o755)
    print(f"Installed: {desktop_path}")
    print(f"Installed: {icon_dst}")

    # Update the autostart .desktop if it exists (the AppImage may have moved).
    # It starts the web interface (the plain start, which falls back to the
    # classic one where the web window cannot open), unless --classic is given
    # here or the entry already started the classic one.
    if os.path.exists(AUTOSTART_FILE):
        try:
            with open(AUTOSTART_FILE) as f:
                was_classic = "--classic" in f.read()
        except OSError:
            was_classic = False
        ui = "classic" if (was_classic or "--classic" in sys.argv) else "web"
        save_autostart_enabled(True, ui=ui)
        print(f"Updated:   {AUTOSTART_FILE} ({ui} interface)")

    # Refresh desktop cache so the launcher picks up the new .desktop immediately.
    # A glib tool, so it gets the sanitised environment like every other system
    # program we start. With our library paths it loads the bundled glib (#49).
    try:
        from shared.macros import clean_child_env
        subprocess.run(["update-desktop-database", desktop_dir],
                       env=clean_child_env(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass

    print("Done. BaseCamp Linux should now appear in your app menu.")


def run():
    """Real entry point — kept as a function so the AppImage's tiny
    appentry.py shim can call it after wiring up the source-overlay path.
    Running `python gui.py` directly still works because of __main__ below."""
    if "--install" in sys.argv:
        _install_desktop_entry()
        sys.exit(0)
    # CLI client for the control interface (issue #20): forward a JSON command
    # to the already-running GUI and print its reply. Lets scripts drive the app
    # without a separate binary, e.g.:
    #   basecamp --ctl '{"cmd":"rgb","device":"everest60","args":["side-static","255","0","0"]}'
    #   basecamp --ctl '{"cmd":"page","page":"displaypad"}'      # GUI tab
    #   basecamp --ctl '{"cmd":"dp_page","page":"Editor"}'       # DisplayPad key page
    if "--ctl" in sys.argv:
        i = sys.argv.index("--ctl")
        payload = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        from shared.ipc import send_command
        try:
            obj = json.loads(payload) if payload else {"cmd": "ping"}
        except ValueError as e:
            print(json.dumps({"ok": False, "error": f"invalid JSON: {e}"}))
            sys.exit(2)
        # An upload to an Everest Max key takes up to a minute, and a command
        # for the Everest Max waits for Monitor Mode to let go of it first.
        cmd = str(obj.get("cmd", "")).lower() if isinstance(obj, dict) else ""
        wait = 200 if cmd in ("image", "run") else 40
        reply = send_command(obj, timeout=wait)
        print(json.dumps(reply))
        sys.exit(0 if reply.get("ok") else 1)
    # The web interface (ui_web/) is the default since 4.0: the same core in a
    # pywebview window. It checks for a running instance itself. When it
    # cannot open here (no pywebview, or no web view for it) this file's
    # classic interface starts instead, with one line on the console and a
    # hint in its window. That is the case of an AppImage from before 4.0 that
    # the in-app update gave this source: the update brings Python files, not
    # pywebview. --classic skips the question; --web insists on the web
    # interface and says why it cannot start rather than falling back.
    global _WEB_FALLBACK
    if "--classic" in sys.argv:
        if "--demo" in sys.argv:
            print("[BaseCamp] --demo is the new interface's; the classic one has "
                  "no demo mode.", file=sys.stderr, flush=True)
            sys.exit(2)
        # Chosen, so the autostart entry this interface writes starts it too.
        import shared.config as _config
        _config.AUTOSTART_UI = "classic"
    else:
        strict = "--web" in sys.argv
        try:
            from ui_web import launch
            backend, why = launch.ready()
        except Exception as e:       # a source tree without a working ui_web/
            launch = None
            backend, why = None, "ui_web does not load: %s: %s" % (type(e).__name__, e)
        if backend or strict:
            from ui_web.app import main as web_main
            sys.exit(web_main([a for a in sys.argv[1:] if a != "--web"]))
        print(launch.fallback_line(why) if launch else
              "[BaseCamp] The new interface cannot start (%s); starting the classic one." % why,
              file=sys.stderr, flush=True)
        if "--demo" in sys.argv:
            print("[BaseCamp] --demo is the new interface's; the classic one has "
                  "no demo mode.", file=sys.stderr, flush=True)
            sys.exit(2)
        _WEB_FALLBACK = why
    psutil.cpu_percent()
    start_minimized = "--minimized" in sys.argv
    # One instance per session. A second one used to start fully and then fight
    # the first for the keyboard and the pad over USB: both got "Resource busy"
    # or lost the interface outright, and the DisplayPad sat on "Connecting to
    # DisplayPad" forever. Nothing about the launcher tells you the application
    # is already running minimised to the tray, so starting it twice is easy to
    # do by accident. Hand over to the one that is running and leave.
    try:
        from shared.ipc import send_command as _send
        # Ping decides, not "show": an instance from before this version does
        # not know that command and would answer "unknown cmd", which would
        # start a second one against a running application.
        running = _send({"cmd": "ping"}, timeout=1.5).get("ok", False)
        if running and not start_minimized:
            _send({"cmd": "show"}, timeout=1.5)   # best effort, older ones ignore it
    except Exception:
        running = False
    if running:
        if not start_minimized:
            print("[BaseCamp] already running, brought the existing window "
                  "to the front", flush=True)
        sys.exit(0)
    if not start_minimized and load_splash_enabled():
        show_splash()
    app = App()
    if start_minimized:
        app._was_withdrawn = True
        app.withdraw()
    app.mainloop()


if __name__ == "__main__":
    run()
