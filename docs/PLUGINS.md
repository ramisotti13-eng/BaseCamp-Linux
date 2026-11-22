# BaseCamp Linux -- Plugin Development Guide

This guide explains how to write plugins for BaseCamp Linux. Plugins can add
new panels to the GUI, register custom button actions for the DisplayPad and
Everest Max, or run background services.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Plugin Structure](#plugin-structure)
3. [Manifest (plugin.json)](#manifest)
4. [Plugin Types](#plugin-types)
   - [Panel Plugin](#panel-plugin)
   - [Action Plugin](#action-plugin)
   - [Service Plugin](#service-plugin)
   - [Combined Plugins](#combined-plugins)
5. [Plugin API Reference](#plugin-api-reference)
   - [i18n (Translations)](#i18n-translations)
   - [Config (Load/Save)](#config-loadsave)
   - [GUI (Panels, Timers)](#gui-panels-timers)
   - [Device Access](#device-access)
   - [DisplayPad Integration](#displaypad-integration)
   - [Everest Max D1-D4 keys](#everest-max-d1-d4-keys)
   - [Action Registration](#action-registration)
6. [UI Styling](#ui-styling)
7. [Thread Safety](#thread-safety)
8. [Dependencies](#dependencies)
9. [Debugging](#debugging)
10. [Examples](#examples)
    - [Hello World (Panel)](#example-hello-world)
    - [Notification Sound (Action)](#example-notification-sound)
    - [LED API Server (Service)](#example-led-api-server)
    - [Now Playing (Panel + Service + Action + DisplayPad)](#example-now-playing)

---

## Quick Start

1. Create a folder in `~/.config/mountain-time-sync/plugins/`:

```
mkdir -p ~/.config/mountain-time-sync/plugins/my_plugin
```

2. Create `plugin.json`:

```json
{
  "id": "my_plugin",
  "name": "My Plugin",
  "version": "1.0",
  "author": "Your Name",
  "description": "What this plugin does",
  "type": "panel",
  "entry": "__init__"
}
```

3. Create `__init__.py` with a `Plugin` class:

```python
import customtkinter as ctk

class Plugin:
    panel_id = "my_plugin"
    panel_label = "My Plugin"

    def __init__(self, ctx):
        self.ctx = ctx

    def create_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="#1a1a2e", corner_radius=0)
        ctk.CTkLabel(frame, text="It works!",
                     font=("Helvetica", 16, "bold"),
                     text_color="#e0e0e0").pack(pady=40)
        return frame
```

4. Restart BaseCamp Linux -- your plugin tab appears in the switcher bar.

You can manage plugins (enable/disable) from the **Plugins** tab in the app.

---

## Plugin Structure

```
~/.config/mountain-time-sync/plugins/
  my_plugin/
    plugin.json          # Manifest (required)
    __init__.py          # Plugin class (required)
    icon.png             # Plugin icon, shown in Plugin Manager (optional, any size)
    panel.py             # Additional modules (optional)
    config.json          # Plugin config, managed by ctx.save_plugin_config()
    lang/                # Optional i18n files
      en.json
      de.json
    resources/           # Optional images, data files
```

The plugin directory name does not matter -- the `id` field in `plugin.json`
is the unique identifier.

**Plugin icon:** If you place an `icon.png` in your plugin folder, the Plugin
Manager will display it as a 28x28 icon next to your plugin name. Any size
works -- it will be resized automatically. Square images work best.

**Plugin Manager appearance:** Each plugin is shown as a card with a colored
left accent stripe (green = active, gray = disabled, red = error). The plugin
types (`panel`, `service`, `action`) are displayed as colored badges. All
information comes from your `plugin.json` manifest.

---

## Manifest

The `plugin.json` file describes your plugin to the loader.

| Field         | Type            | Required | Description                          |
|---------------|-----------------|----------|--------------------------------------|
| `id`          | string          | yes      | Unique identifier (lowercase, no spaces) |
| `name`        | string          | yes      | Display name                         |
| `version`     | string          | yes      | Version string (e.g. "1.0")         |
| `author`      | string          | no       | Author name                          |
| `description` | string          | no       | Short description                    |
| `type`        | string or list  | yes      | Plugin type(s): `"panel"`, `"action"`, `"service"` |
| `entry`       | string          | no       | Python module name (default: `"__init__"`) |
| `requires`    | list of strings | no       | Python packages needed (informational) |
| `default_disabled` | boolean    | no       | If `true`, plugin starts disabled on fresh installs |

### Type field

A plugin can have one type or multiple:

```json
"type": "panel"
"type": "service"
"type": ["panel", "service"]
"type": ["panel", "action"]
```

---

## Plugin Types

Every plugin must define a `Plugin` class in its entry module. The class
receives a `PluginContext` object (`ctx`) in `__init__`. What methods the
class implements depends on the plugin type.

### Panel Plugin

Adds a new tab to the switcher bar in the GUI.

**Required attributes/methods:**

| Name            | Type     | Description                          |
|-----------------|----------|--------------------------------------|
| `panel_id`      | str      | Internal ID for the panel            |
| `panel_label`   | str      | Label shown on the switcher button   |
| `create_panel(parent)` | method | Returns a `ctk.CTkFrame`     |

```python
import customtkinter as ctk

class Plugin:
    panel_id = "my_panel"
    panel_label = "My Panel"

    def __init__(self, ctx):
        self.ctx = ctx

    def create_panel(self, parent):
        """Called once during app startup. Return a CTkFrame."""
        frame = ctk.CTkFrame(parent, fg_color="#1a1a2e", corner_radius=0)
        # Build your UI inside the frame
        ctk.CTkLabel(frame, text="Hello").pack(pady=20)
        return frame
```

The `parent` argument is the app's panel area (a `CTkFrame`). Your frame will
be shown/hidden automatically when the user clicks the switcher button.

### Action Plugin

Registers new button action types that appear in the DisplayPad (K1-K12) and
Everest Max (D1-D4) action type dropdowns.

```python
class Plugin:
    def __init__(self, ctx):
        ctx.register_action_type(
            type_id="play_sound",
            label="Play Sound",
            handler=self.on_press
        )

    def on_press(self, action_value):
        """Called in a daemon thread when a button with this type is pressed.
        action_value is whatever the user typed in the action field."""
        import subprocess
        subprocess.Popen(["paplay", action_value])
```

After registration, "Play Sound" appears in the type dropdown. The user can
type a sound file path in the action field. When they press the button, your
`on_press` handler runs.

**Important:** The handler runs in a background thread, not the GUI thread.
If you need to update the GUI from the handler, use `ctx.schedule()`.

### Service Plugin

Runs a background thread that starts with the app and stops on shutdown.

```python
import threading

class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()

    def start(self):
        """Called after the GUI is ready."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Called when the app is closing."""
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            # Do background work
            self._stop.wait(10)  # sleep 10 seconds between iterations
```

`start()` is called about 100ms after the main window is built.
`stop()` is called during `App.destroy()`, before the GUI is torn down.

### Combined Plugins

A plugin can combine multiple types. For example, a plugin that has both a
settings panel and a background service:

```json
"type": ["panel", "service"]
```

```python
class Plugin:
    panel_id = "my_service"
    panel_label = "My Service"

    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()

    def create_panel(self, parent):
        # Settings UI
        frame = ctk.CTkFrame(parent, ...)
        return frame

    def start(self):
        # Background work
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()
```

---

## Plugin API Reference

The `PluginContext` object (`ctx`) is passed to your `Plugin.__init__`. It
provides a stable interface to the app's internals.

### i18n (Translations)

```python
# Get a translated string
text = ctx.T("my_key")
text = ctx.T("greeting", name="World")  # with format args

# Register translations (call in __init__)
ctx.register_translations({
    "en": {"my_key": "Hello", "greeting": "Hello {name}!"},
    "de": {"my_key": "Hallo", "greeting": "Hallo {name}!"},
})
```

The app currently supports `"en"` (English) and `"de"` (German). Your plugin
keys are merged into the global translation dict. Use a prefix to avoid
collisions (e.g. `"myplugin_title"` instead of `"title"`).

### Config (Load/Save)

```python
# Load plugin config (returns {} if no config exists)
cfg = ctx.load_plugin_config("my_plugin")

# Save plugin config
ctx.save_plugin_config("my_plugin", {"api_key": "...", "interval": 60})

# Base config directory path
path = ctx.config_dir  # ~/.config/mountain-time-sync/
```

Config is stored as `plugins/<id>/config.json`. Use `load_plugin_config` /
`save_plugin_config` rather than managing files directly.

### GUI (Panels, Timers)

```python
# The panel area frame (parent for plugin panels)
parent = ctx.panel_area

# Register a panel manually (normally done automatically for panel plugins)
ctx.register_panel("my_id", "My Label", my_frame)

# Schedule a one-shot callback on the GUI thread
ctx.schedule(1000, lambda: print("1 second later"))

# Schedule a repeating callback (returns a cancel function)
cancel = ctx.schedule_repeat(5000, my_update_function)
# Later: cancel() to stop
```

**All GUI updates must happen on the main thread.** If your code runs in a
background thread (service plugin, action handler), wrap GUI calls in
`ctx.schedule()`:

```python
def _run(self):
    result = do_heavy_work()
    # Update GUI safely:
    self.ctx.schedule(0, lambda: self.label.configure(text=result))
```

### Device Access

```python
# Get the DisplayPad panel instance (or None if not available)
dp = ctx.get_displaypad()

# Get the active keyboard panel (Everest Max or Everest 60)
kb = ctx.get_keyboard_panel()
```

These return the panel objects directly. You can call their public methods.
Be aware that device panels may not be connected -- check before calling
device-specific methods.

### DisplayPad Integration

Plugins can render live images onto DisplayPad buttons:

```python
from PIL import Image, ImageDraw, ImageFont

# Push a 102x102 image to button K12 (index 11)
img = Image.new("RGB", (102, 102), (20, 20, 40))
draw = ImageDraw.Draw(img)
draw.text((10, 40), "Hello!", fill=(224, 224, 224))
ctx.push_displaypad_image(11, img)
```

**`ctx.push_displaypad_image(key_index, pil_image)`**

| Parameter   | Type       | Description                         |
|-------------|------------|-------------------------------------|
| `key_index` | int (0-11) | Button index (K1=0, K12=11)        |
| `pil_image` | PIL Image  | Any size -- resized to 102x102 automatically |

The method is **thread-safe** and returns immediately: it hands the image to a
queue that a single long-lived worker thread drains, and that worker is the only
thread that talks to the pad, so pushes from several plugins can never collide
on the USB device. Call it from your own thread; you do **not** need
`ctx.schedule()` for this, only for touching GUI widgets. The DisplayPad does
not need to be in animation mode.

A frame is stamped with the page that was on the pad when you pushed it. If the
pad switches page before the queue is drained, that frame is dropped instead of
being painted over the new page's icons.

**Paint only keys your action type sits on.** A frame for a key that the live
page has given to something else, a plain image or another action, is dropped:
a page switch and a plugin's own thread cannot be ordered against each other, so
a frame arriving a moment too late would otherwise overwrite the new page's
icon. Find your keys the way the shipped plugins do, by walking
`ctx.get_displaypad_actions()` for your own type, and this never gets in your
way.

**A frame is never stored as the key's icon.** Several plugins write their
frame into the panel's image maps as well, so that the key grid in the app and
a full page re-upload show the widget rather than the icon underneath it. That
still works, but those maps are also what the app saves, so such a frame used
to end up in `displaypad_pages/*.json` as the key's icon and outlive the plugin
that drew it: the pad then showed a stopped widget's last picture on a key that
had been given to something else entirely (#69). The panel now keeps every
frame it is handed out of what it stores, and the key keeps the icon it was
given.

**Tips:**
- Only re-upload when content actually changes (not every poll cycle) --
  each upload takes ~200ms of USB time
- Use `ImageFont.truetype()` with DejaVu Sans for text rendering with full
  Unicode support (umlauts, CJK, etc.)
- Button layout (6 columns x 2 rows):
  ```
  K1  K2  K3  K4  K5  K6
  K7  K8  K9  K10 K11 K12
  ```
- If the DisplayPad is not connected, the upload silently fails (no crash)
- If a GIF animation is running, the image is automatically queued into the
  animation loop (no "Resource busy" errors)
- Combine with `register_action_type()` to let users assign a button press
  action (e.g. play/pause) to the same button that shows the live widget

**Auto-detect which button to use:**

When your plugin registers an action type, users assign it to a button in the
DisplayPad action config. Your plugin can read the config to find which button
it should render the widget on:

```python
def _find_my_button(self):
    """Find which DisplayPad button has our action type assigned."""
    actions = self.ctx.get_displaypad_actions()   # the page on the pad now
    for i, act in enumerate(actions):
        if act.get("type") == "my_action_type_id":
            return i
    return None  # not assigned to any button on this page
```

Ask the context, not `shared.config._load_displaypad_actions()`: that one
answers for the main page unless given a page, so a widget assigned on any
other page was never found (#82).

This way the user controls where the widget appears by assigning the action
type in the DisplayPad settings, and the plugin automatically renders there.

**Preview tile in the DisplayPad panel:**

When a plugin action type is assigned to a button and no image is set, the
DisplayPad panel automatically shows a small preview tile with the action
label and a blue border. This lets users see at a glance which buttons have
plugin actions assigned.

### Everest Max D1-D4 keys

```python
ctx.push_keyboard_dbutton_image(0, cover)   # D1, a PIL Image of any size
```

**`ctx.push_keyboard_dbutton_image(button, pil_image)`** puts a picture on one
of the four keys above the Everest Max numpad (button 0-3 for D1-D4). It is
thread-safe and returns at once: `True` if the picture was taken, `False` when
no Everest Max is connected or the button is out of range.

These keys are not a screen. They keep their picture in the keyboard's flash,
so every new picture is an erase and a rewrite, takes 45 to 60 seconds, and
pauses Monitor Mode while it runs (it starts again afterwards if it was
running). Base Camp for Windows does the same for its Spotify covers. So:

- push when the content changes, a new track say, never on a timer
- a picture the key already shows is not written again, and while an upload
  runs only the latest picture per key is kept, so pushing the same thing
  twice costs nothing
- it is not for animation or anything live

### Action Registration

```python
ctx.register_action_type(
    type_id="my_action",      # internal name, stored in config
    label="My Action",        # shown in the dropdown
    handler=my_handler_fn     # callable(action_value: str)
)
```

The `type_id` is stored in the user's button config JSON. Keep it stable
across versions -- changing it will break existing button assignments.

The `handler` receives the action value string from the config. It runs in a
daemon thread.

---

## UI Styling

To match the app's look, use these color constants (import from
`shared.ui_helpers`):

```python
from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER
```

| Constant | Hex       | Usage                        |
|----------|-----------|------------------------------|
| `BG`     | `#0e0e1a` | Main background              |
| `BG2`    | `#16162a` | Card/section background      |
| `BG3`    | `#222244` | Elevated elements, buttons   |
| `FG`     | `#e0e0e0` | Primary text                 |
| `FG2`    | `#707090` | Secondary/dimmed text        |
| `BLUE`   | `#0ea5e9` | Accent, active states        |
| `YLW`    | `#f5c542` | Warnings, highlights         |
| `GRN`    | `#22c55e` | Success, connected status    |
| `RED`    | `#dc2626` | Errors, destructive actions  |
| `BORDER` | `#2a2a4a` | Input borders                |

**Font conventions:**
- Headings: `("Helvetica", 14, "bold")`
- Labels: `("Helvetica", 11)`
- Small text: `("Helvetica", 10)`

**Panel dimensions:**
- Width: 480px (fixed window width)
- Use `pack(fill="x", padx=16)` for content sections
- Cards: `ctk.CTkFrame(parent, fg_color=BG3, corner_radius=6)`

---

## Thread Safety

The GUI toolkit (Tkinter/CustomTkinter) is **not thread-safe**. Rules:

1. **Never** call widget methods from a background thread
2. Use `ctx.schedule(0, callback)` to run code on the GUI thread
3. Action handlers and service `_run()` methods execute in background threads
4. `create_panel()` and `__init__()` run on the GUI thread

This is about **widgets**, not about the DisplayPad. `push_displaypad_image()`
is safe to call from your own thread and does not need `ctx.schedule()`; a
plugin that only paints keys, like `dp_pipe_text`, never has to schedule
anything. Wrap in `ctx.schedule()` only what touches the interface.

```python
# WRONG - will crash or corrupt state
def on_press(self, action_value):
    self.my_label.configure(text="Pressed!")  # background thread!

# CORRECT
def on_press(self, action_value):
    self.ctx.schedule(0, lambda: self.my_label.configure(text="Pressed!"))
```

---

## Dependencies

The `requires` field in `plugin.json` is **informational only**. The plugin
loader checks if listed packages are importable and prints a warning if not,
but does not install them automatically.

```json
"requires": ["requests", "websocket-client"]
```

If your plugin needs external packages, document the install command:

```
pip install requests websocket-client
```

Name the packages the way pip does. The loader maps the ones whose import name
differs (`Pillow` imports as `PIL`, `opencv-python` as `cv2`) before checking,
so `"requires": ["Pillow"]` no longer reports a package that is right there.

Packages bundled with the AppImage (available without extra install):
- `customtkinter`, `Pillow`, `psutil`, `hid`, `pyusb`
- Standard library: `json`, `threading`, `subprocess`, `socket`, `os`, etc.

Anything **not** in that list only works on a source install. The AppImage
brings its own Python and cannot see packages installed with the system pip, so
a plugin that needs `opencv-python` (like `dp_video`) will not run there no
matter what you install.

---

## Debugging

Plugins print to stdout/stderr. When running from the terminal:

```bash
python3 gui.py
```

You will see:
```
[Plugin] Loaded: My Plugin v1.0
[Plugin] Started service: My Plugin
```

If a plugin fails to load, you get:
```
[Plugin] Failed to load my_plugin: ImportError: No module named 'requests'
```

**Tips:**
- Add `print()` statements with `flush=True` in your plugin code
- Errors in `__init__` or `create_panel` prevent the plugin from loading but
  do not crash the app
- Errors in `start()` are caught and logged
- Errors in action handlers are caught by the threading system

To test your plugin module standalone:

```python
python3 -c "
import sys; sys.path.insert(0, '/path/to/mountain-time-sync')
from shared.plugins import PluginManager
from shared.plugin_api import PluginContext

pm = PluginManager()
pm.discover()
print('Found:', list(pm._manifests.keys()))
"
```

---

## Examples

### Example: Hello World

The simplest possible panel plugin.

**`~/.config/mountain-time-sync/plugins/hello_world/plugin.json`:**
```json
{
  "id": "hello_world",
  "name": "Hello World",
  "version": "1.0",
  "author": "BaseCamp Linux",
  "description": "Example plugin - shows a simple panel with a greeting",
  "type": "panel",
  "entry": "__init__"
}
```

**`~/.config/mountain-time-sync/plugins/hello_world/__init__.py`:**
```python
"""Hello World -- example plugin for BaseCamp Linux."""
import customtkinter as ctk


class Plugin:
    panel_id = "hello_world"
    panel_label = "Hello"

    def __init__(self, ctx):
        self.ctx = ctx
        ctx.register_translations({
            "en": {"hello_greeting": "Hello from a Plugin!",
                   "hello_desc": "This is an example plugin panel.\n"
                                 "Plugins live in ~/.config/mountain-time-sync/plugins/"},
            "de": {"hello_greeting": "Hallo vom Plugin!",
                   "hello_desc": "Dies ist ein Beispiel-Plugin-Panel.\n"
                                 "Plugins liegen in ~/.config/mountain-time-sync/plugins/"},
        })

    def create_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="#1a1a2e", corner_radius=0)

        ctk.CTkLabel(
            frame, text=self.ctx.T("hello_greeting"),
            font=("Helvetica", 16, "bold"), text_color="#e0e0e0"
        ).pack(pady=(40, 10), padx=16, anchor="w")

        ctk.CTkLabel(
            frame, text=self.ctx.T("hello_desc"),
            font=("Helvetica", 12), text_color="#888888", justify="left"
        ).pack(pady=(0, 20), padx=16, anchor="w")

        return frame
```

---

### Example: Notification Sound

An action plugin that plays a sound when a button is pressed.

**`plugin.json`:**
```json
{
  "id": "play_sound",
  "name": "Play Sound",
  "version": "1.0",
  "type": "action",
  "description": "Play a sound file when a button is pressed"
}
```

**`__init__.py`:**
```python
import subprocess

class Plugin:
    def __init__(self, ctx):
        ctx.register_action_type(
            type_id="play_sound",
            label="Play Sound",
            handler=self.on_press
        )

    def on_press(self, action_value):
        """action_value = path to a .wav or .ogg file."""
        if action_value:
            try:
                subprocess.Popen(["paplay", action_value],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                print("[PlaySound] paplay not found, trying aplay")
                subprocess.Popen(["aplay", action_value],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
```

**Usage:** In the DisplayPad or Everest Max button config, select "Play Sound"
from the type dropdown, then enter the path to a sound file
(e.g. `/home/user/sounds/notification.ogg`).

---

### Example: LED API Server

A service plugin that exposes keyboard LED control via a Unix socket. External
scripts can connect and send JSON commands to control the LEDs.

This one also exists as a folder you can copy straight into your plugins
directory, together with the client script that drives it:
[`docs/examples/led_api`](examples/led_api).

**`plugin.json`:**
```json
{
  "id": "led_api",
  "name": "LED API",
  "version": "1.0",
  "type": "service",
  "description": "Control keyboard LEDs via Unix socket"
}
```

**`__init__.py`:**
```python
import json
import os
import socket
import threading

SOCKET_PATH = os.path.expanduser(
    "~/.config/mountain-time-sync/led_api.sock")


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        # Unlink socket to unblock accept()
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(SOCKET_PATH)
            sock.close()
        except Exception:
            pass
        try:
            os.unlink(SOCKET_PATH)
        except Exception:
            pass

    def _serve(self):
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCKET_PATH)
        srv.listen(1)
        srv.settimeout(1.0)
        print(f"[LED API] Listening on {SOCKET_PATH}", flush=True)

        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            try:
                data = conn.recv(4096).decode()
                cmd = json.loads(data)
                result = self._handle(cmd)
                conn.sendall(json.dumps(result).encode())
            except Exception as e:
                conn.sendall(json.dumps({"ok": False, "error": str(e)}).encode())
            finally:
                conn.close()

        srv.close()
        try:
            os.unlink(SOCKET_PATH)
        except Exception:
            pass

    def _handle(self, cmd):
        action = cmd.get("cmd")
        if action == "get_status":
            kb = self.ctx.get_keyboard_panel()
            return {"ok": True, "keyboard_connected": kb is not None}
        if action == "set_brightness":
            # Example: adjust brightness via the keyboard panel
            percent = cmd.get("percent", 100)
            print(f"[LED API] Set brightness to {percent}%", flush=True)
            return {"ok": True}
        return {"ok": False, "error": f"Unknown command: {action}"}
```

**Companion CLI (`cli.py` -- not loaded by the app, run manually):**
```python
#!/usr/bin/env python3
"""CLI tool to send commands to the LED API plugin."""
import json
import os
import socket
import sys

SOCKET_PATH = os.path.expanduser(
    "~/.config/mountain-time-sync/led_api.sock")

def send(cmd):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    sock.sendall(json.dumps(cmd).encode())
    resp = json.loads(sock.recv(4096).decode())
    sock.close()
    return resp

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: cli.py get-status | set-brightness <percent>")
        sys.exit(1)
    if sys.argv[1] == "get-status":
        print(send({"cmd": "get_status"}))
    elif sys.argv[1] == "set-brightness":
        print(send({"cmd": "set_brightness", "percent": int(sys.argv[2])}))
```

---

### Example: Now Playing

The most complete example -- a panel + service + action plugin that shows
what's playing in your browser, with DisplayPad live widget. This plugin
ships with BaseCamp Linux in `~/.config/mountain-time-sync/plugins/now_playing/`.

**Features:**
- GUI panel with title card, progress bar, play/pause, volume/mute
- Live widget on DisplayPad K12 showing title + status
- "Now Playing (Play/Pause)" action type for button assignment
- Uses `playerctl` (MPRIS) for media info and `pactl` for volume control

**`plugin.json`:**
```json
{
  "id": "now_playing",
  "name": "Now Playing",
  "version": "1.0",
  "type": ["panel", "service", "action"],
  "description": "Shows what's playing in your browser via MPRIS"
}
```

**Key patterns demonstrated in `__init__.py`:**

```python
class Plugin:
    panel_id = "now_playing"
    panel_label = "Now Playing"

    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()

        # Register translations
        ctx.register_translations({
            "en": {"np_title": "NOW PLAYING", ...},
            "de": {"np_title": "AKTUELLE WIEDERGABE", ...},
        })

        # Register action type (play/pause on button press)
        ctx.register_action_type(
            type_id="now_playing",
            label="Now Playing (Play/Pause)",
            handler=lambda val: _playerctl("play-pause")
        )

    def create_panel(self, parent):
        # Build GUI with info card, progress bar, controls, volume slider
        ...

    def start(self):
        # Poll loop: update GUI + render DisplayPad button
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        while not self._stop.is_set():
            info = _get_media_info()
            # Update GUI (must run on main thread)
            self.ctx.schedule(0, lambda i=info: self._update_ui(i))
            # Update DisplayPad (runs in poll thread, upload is thread-safe)
            self._update_displaypad(info)
            self._stop.wait(2)

    def _find_my_button(self):
        """Auto-detect which DisplayPad button has our action assigned."""
        from shared.config import _load_displaypad_actions
        actions = _load_displaypad_actions()
        for i, act in enumerate(actions):
            if act.get("type") == "now_playing":
                return i
        return None

    def _update_displaypad(self, info):
        # Render 102x102 image with Pillow
        img = Image.new("RGB", (102, 102), (16, 16, 36))
        draw = ImageDraw.Draw(img)
        draw.text((6, 8), info["title"], fill=(224, 224, 224), font=font)
        # Push to the button the user assigned the action to
        key = self._find_my_button()
        if key is not None:
            self.ctx.push_displaypad_image(key, img)
```

See the full source in `~/.config/mountain-time-sync/plugins/now_playing/__init__.py`.

---

## Plugin Lifecycle Summary

```
App starts
  |
  +-- PluginManager.discover()       # Reads plugin.json from each folder
  +-- PluginManager.load_all(ctx)    # Imports modules, calls Plugin(ctx)
  |     |
  |     +-- Plugin.__init__(ctx)     # Register translations, actions
  |
  +-- App._build_ui()
  |     |
  |     +-- Plugin.create_panel()    # Panel plugins: build GUI
  |
  +-- PluginManager.start_services() # Service plugins: start()
  |
  ... app runs ...
  |
  +-- App.destroy()
        |
        +-- PluginManager.shutdown() # All plugins: stop()
```

---

## Tips

- **Prefix your translation keys** with your plugin ID to avoid conflicts
  (e.g. `sysmon_title` not `title`)
- **Use daemon threads** (`daemon=True`) so they don't block app shutdown
- **Catch exceptions** in your handlers -- uncaught errors in threads are
  silently lost
- **Test without the full app** by importing `PluginManager` and
  `PluginContext` directly (see Debugging section)
- **Keep it simple** -- the plugin system is designed for straightforward
  extensions, not a full framework
