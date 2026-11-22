"""PluginContext — stable API surface for BaseCamp Linux plugins."""
import os
import json

from shared.config import CONFIG_DIR


class PluginContext:
    """Passed to every plugin on instantiation. Wraps app internals."""

    def __init__(self, app, plugin_manager):
        self._app = app
        self._pm = plugin_manager

    # ── i18n ──────────────────────────────────────────────────────────────────

    def T(self, key, **kwargs):
        """Translate a key using the app's current language."""
        return self._app.T(key, **kwargs)

    def register_translations(self, lang_dict):
        """Merge plugin translations into the app's language dict.
        lang_dict: {"en": {"my_key": "Label"}, "de": {"my_key": "Bezeichnung"}}
        """
        code = self._app._lang_code
        if code in lang_dict:
            self._app._lang.update(lang_dict[code])

    # ── Config ────────────────────────────────────────────────────────────────

    @property
    def config_dir(self):
        """Base config directory (~/.config/mountain-time-sync/)."""
        return CONFIG_DIR

    def load_plugin_config(self, plugin_id):
        """Load JSON config from plugins/<id>/config.json. Returns dict or {}."""
        path = os.path.join(CONFIG_DIR, "plugins", plugin_id, "config.json")
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def save_plugin_config(self, plugin_id, data):
        """Save JSON config to plugins/<id>/config.json."""
        pdir = os.path.join(CONFIG_DIR, "plugins", plugin_id)
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, "config.json"), "w") as f:
            json.dump(data, f, indent=2)

    # ── GUI ───────────────────────────────────────────────────────────────────

    @property
    def panel_area(self):
        """The CTkFrame that holds all panels."""
        return self._app._panel_area

    def register_panel(self, plugin_id, label, panel_instance):
        """Register a plugin panel in the app's panel dict."""
        self._app._panels[plugin_id] = panel_instance

    def schedule(self, ms, callback):
        """Schedule a callback on the GUI main thread (wraps app.after())."""
        return self._app.after(ms, callback)

    def schedule_repeat(self, ms, callback):
        """Repeating timer on the GUI thread. Returns cancel function."""
        cancel_id = [None]

        def _loop():
            callback()
            cancel_id[0] = self._app.after(ms, _loop)

        cancel_id[0] = self._app.after(ms, _loop)
        return lambda: self._app.after_cancel(cancel_id[0])

    # ── Device access ─────────────────────────────────────────────────────────

    def get_displaypad(self):
        """Return the DisplayPadPanel instance, or None."""
        return getattr(self._app, "_displaypad_panel", None)

    def get_keyboard_panel(self):
        """Return the active keyboard panel instance."""
        return self._app._panels.get(self._app._kb_panel_id)

    def push_displaypad_image(self, key_index, pil_image):
        """Upload a PIL Image to a DisplayPad button (0-11). Thread-safe.
        The image will be resized to 102x102 and converted to BGR automatically.
        """
        dp = self.get_displaypad()
        if dp and hasattr(dp, "push_plugin_image"):
            dp.push_plugin_image(key_index, pil_image)

    def push_keyboard_dbutton_image(self, button, pil_image):
        """Put a picture on one of the Everest Max's D1-D4 keys (button 0-3).
        Thread-safe, returns at once: True if the picture was taken, False
        when there is no Everest Max or the button is not 0-3.

        Not for animation. The keys keep their pictures in the keyboard's
        flash: each new picture is an erase and a rewrite, takes 45 to 60
        seconds, and pauses Monitor Mode while it runs. A picture the key
        already shows is not written again, and only the latest picture per
        key is written, so push when the content changes (a new track), not
        on a timer.
        """
        panel = getattr(self._app, "_everest_panel", None)
        present = getattr(self._app, "_dev_present", {}).get("everest_max")
        if panel is None or not present or not hasattr(panel, "push_dbutton_image"):
            return False
        return panel.push_dbutton_image(button, pil_image)

    def get_displaypad_current_page(self):
        """Return the DisplayPad's currently active page number (0 = main
        page), or 0 if the DisplayPad isn't connected. Use this instead of
        assuming page 0 so widgets/actions assigned on a sub-page are found.
        """
        dp = self.get_displaypad()
        return getattr(dp, "_current_page", 0) if dp else 0

    def get_displaypad_actions(self, page=None):
        """Return the 12 button actions for a DisplayPad page. Defaults to
        whichever page is currently on screen, so a plugin's "which button
        am I assigned to" lookup works on sub-pages too, not just the main
        page.
        """
        from shared.config import _load_displaypad_actions
        if page is None:
            page = self.get_displaypad_current_page()
        return _load_displaypad_actions(page)

    # ── Action registration ───────────────────────────────────────────────────

    def register_action_type(self, type_id, label, handler, value_options=None):
        """Register a new button action type.
        type_id: internal name (e.g. "led_notify")
        label:   display name (e.g. "LED Notification")
        handler: callable(action_value) — called in a daemon thread on button press
        value_options: optional callable() returning a list of suggested values
                       (strings, or (display_label, value) tuples). When provided,
                       the button-action editor renders an editable dropdown
                       prefilled with those values instead of a plain text entry.
                       Called each time the editor opens so it can reflect
                       live system state (e.g. mounted filesystems).
        """
        self._pm._action_types[type_id] = {
            "label": label,
            "handler": handler,
            "owner": getattr(self._pm, "_loading_pid", None),
            "value_options": value_options,
        }
