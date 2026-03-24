# Changelog

## [1.6.0] - 2026-03-25

### Mountain DisplayPad — Full Support

- **Button Images (K1–K12)**: Assign individual 102×102 images or animated GIFs to each of the 12 display buttons
- **Fullscreen Image/GIF**: Upload a single image or animated GIF that spans across all 12 displays as one seamless picture
- **Icon Library**: Built-in library with 39 bundled icons (Media, Social, System, Navigation, Numbers 1–12) plus user-uploaded images — all accessible via a grid picker
- **Fullscreen Library**: Separate library for fullscreen images and GIFs, auto-saves uploaded files for quick reuse
- **Button Actions (K1–K12)**: Assign actions to each button — Shell command, URL, Folder, App, OBS, or Page navigation
- **Multi-Page System**: Create up to 12 sub-pages with customisable folder icons and text labels (DPFolder.png). K1 on sub-pages is always "Back". Fullscreen GIFs work on sub-pages with page navigation still functional underneath
- **Key Event Detection**: Hardware button presses detected via HID (data[0]==0x01 filter, 0.8s debounce). Actions execute during GIF animation by reading key events between frame uploads
- **Icon Rotation**: Rotate all button icons by 0°/90°/180°/270° for mounting the pad in any orientation (e.g. SimRacing setups). Preview thumbnails rotate live in the GUI
- **Device Reconnect**: Automatically re-uploads saved images when the DisplayPad is reconnected or the app restarts
- **Clear All**: Uploads blank (black) images to all buttons on the device, preserving page folder icons
- **Auto-Upload**: Images upload automatically when assigned or when the image dialog is closed — no manual upload button needed
- **GIF Animation**: Supports animated GIFs on individual buttons and fullscreen, with configurable minimum frame time (ms/frame)

### OBS Studio — Global Integration

- **New top-level OBS tab** in the switcher bar (alongside Keyboard, Mouse, DisplayPad)
- OBS connection settings (Host, Port, Password) moved from Keyboard panel to dedicated OBS panel
- Connect & Load Scenes / Disconnect with status indicator
- **OBS switcher button turns green** when connected (visible from any tab)
- **OBS actions available on all devices**: D1–D4 (Keyboard) and K1–K12 (DisplayPad) can be set to OBS type with Scene/Record/Stream selector
- OBS actions execute via `obsws_python` in background threads

### Keyboard (Everest Max) — Improvements

- **OBS section removed** from Keyboard panel (moved to global OBS tab)
- **D1–D4 actions**: Added "OBS" action type with scene/record/stream dropdown
- **Auto-save**: D1–D4 action changes save immediately on type change, browse, or entry edit — green checkmark buttons removed

### UI / UX

- **Simplified DisplayPad layout**: Single scrollable panel (no accordion) with all controls directly visible
- **Simplified OBS layout**: Direct content display without accordion
- **Two-row switcher bar**: Keyboard/Mouse/DisplayPad on top, OBS Studio centered below
- **Emoji-free switcher buttons**: Text-only buttons for better compatibility across platforms
- **Window width** increased to 480px to accommodate 4 tabs
- **Scroll speed** capped in all Library Picker dialogs (consistent with panel scroll behaviour)
- **GIF frame picker skipped** for DisplayPad (device supports animation natively)

### Internationalisation

- Full DE/EN support for all new DisplayPad features (29+ new keys)
- OBS panel and action type labels in both languages
- Page system labels: Page selector, Back button, page name hints

---

## [1.5.1] - 2026-03-22

### Internal

- `mountain-time-sync.py`: fixed slow memory growth in the controller loop — `_handle_btn_resp` was redefined on every iteration (5×/s), creating constant function-object churn; moved to a single definition before the loop
- `mountain-time-sync.py`: RAM and HDD metrics now polled every 2 s instead of every 0.2 s — values change slowly and the reduction in `virtual_memory()` / `disk_usage()` allocation pressure stops Python's memory allocator from retaining freed arenas

---

## [1.5.0] - 2026-03-22

### Makalu 67 Mouse — Button Remap

- New **Button Remap** section in the Makalu 67 panel
- Remap any of the 6 physical buttons (Left, Right, Middle, Back, Forward, DPI+) to a different function
- Categories: Mouse, DPI, Scroll, Sniper
- New **DPI Sniper** function: assign a button to temporarily switch to a lower DPI while held — profile DPI is restored automatically on release (no software polling required, handled by mouse firmware)
- DPI Sniper value is configurable via slider + input field (50–19,000, step 50)
- Left button remap includes a 10-second safety confirmation dialog — automatically reverts if not confirmed
- Assignments are saved to config and restored on next launch

### Makalu 67 Mouse — DPI

- DPI settings panel: 5 configurable DPI levels, cycle through them with the DPI button on the mouse
- Reads current DPI values from the mouse on panel open and polls for profile changes every 1.5 seconds
- Reset button restores factory defaults

### Makalu 67 Mouse — Settings

- Mouse settings panel: Polling Rate (125 / 250 / 500 / 1000 Hz), Button Response (debounce 2–12 ms), Angle Snapping (on/off), Lift-Off Distance (Low/High)

### Internationalisation

- Full DE/EN language support for the entire Makalu 67 panel (RGB, Custom RGB, DPI, Settings, Button Remap)
- All section titles, labels, dropdowns, status messages and button grid update live when switching language

### Presets

- 6 built-in color presets ship with the app for both the **keyboard** (Custom RGB) and the **Makalu 67** (Custom RGB): Synthwave, Ocean, Ember, Forest, Arctic, Galaxy
- Presets load automatically on first launch — no setup required

### Internal

- `controller.py`: extracted `_run_cmd()` helper — all HID commands now share a single open/send/get/close pattern instead of duplicating it per function
- `panel.py`: extracted `_fetch_dpi()` helper — `_dpi_load_from_device` and `_dpi_poll` no longer duplicate the subprocess/parse logic
- `panel.py`: removed dead `_REMAP_LABELS` / `_REMAP_LABEL_TO_KEY` class attributes (superseded by i18n translation maps)
- Fixed `rgb code` / `rgb code2` CLI commands in controller.py that would crash at runtime after the `_send_lighting` refactor

---

## [1.4.2] - 2026-03-21 (Beta)

### Makalu 67 Mouse — RGB Control (New Device)
- Full RGB control panel for the Mountain Makalu 67 gaming mouse (VID 0x3282, PID 0x0003)
- Effects: Static, Breathing, RGB Breathing, Rainbow, Responsive, Yeti, Off
- Dual-zone color support for Breathing and Yeti (Zone 1 + Zone 2 colors)
- Speed control: Slow / Medium / Fast (confirmed via USB capture)
- Brightness: 5 levels — 0 / 25 / 50 / 75 / 100 (dropdown, confirmed via USB capture)
- Rainbow direction: ← / → (confirmed via USB capture)
- 12 color presets (standard gaming colors) — click to apply instantly
- All controls push to the mouse immediately without a separate Apply button
- UI shows only the controls relevant to the selected effect

### Keyboard Main Display
- Added **Volume** mode to the display mode selector

### D1–D4 Image Upload
- Fixed upload checksum: was hardcoded `0x6be9`, now correctly computed from pixel data
- Added debug log file at `/tmp/basecamp_d1d4_upload.log` for troubleshooting upload issues

### Internal
- Device code restructured into `devices/everest_max/` and `devices/makalu67/`
- Shared utilities extracted to `shared/` (config, image_utils, ui_helpers)
- Protocol documentation moved to `protocol/`
- README screenshots moved to `docs/`

---

## [1.4.1] - 2026-03-19

### Upload Images & Image Library
- New **Upload Images** dialog (Numpad Keys section): shows D1–D4 as four tiles with thumbnail previews, select images per slot and upload all at once with **Upload All**
- Per-slot **↑** button inside the dialog for uploading a single slot without affecting others
- **Image Library**: every uploaded image is automatically saved as a thumbnail locally — pick previously used images with one click instead of browsing the file system every time
- Library images can be deleted individually via the ✕ button
- The last uploaded image per D-slot is remembered and shown as the tile preview on next open
- **Skip detection**: if the same image is selected again (content unchanged), the slot is skipped — no unnecessary flash write, both in single and multi upload
- **Main display Image Library**: the main display upload now also uses the library picker with thumbnails in the correct 240×204 aspect ratio (stored in `main_library/`)
- Image Library picker opens at the mouse cursor position

---

## [1.4.0] - 2026-03-19

### Custom RGB Mode
- Completely redesigned: new per-key color editor with a full keyboard canvas in a popup window
- Click individual keys to select and color them
- Rubber band (drag) selection across multiple keys
- Ctrl+click and right-click for toggle selection
- Alt+click eyedropper to sample a key's current color
- Ctrl+Z / Undo button (up to 20 steps)
- Side LEDs shown as individual clickable squares around both keyboard and numpad bezels (11 top, 4 right, 12 bottom, 4 left; numpad: 3 top, 4 right, 3 bottom, 4 left)
- Fill selected, fill all, select all, deselect all controls
- Preset system: save, load and delete named color presets
- Built-in **Synthwave** sample preset included
- Section renamed from "Custom RGB Mode (Beta)" to "Custom RGB Mode"

### Color Picker
- Replaced the system color dialog with a custom HSV color wheel
- Circular picker: hue as angle, saturation as radius, brightness as slider
- Before/after preview swatches and hex input field
- Used everywhere colors are picked: Key Color Editor, RGB Lighting, Custom RGB zones

### Bug Fixes
- Fixed: Direction dropdown visible on startup when Static effect was selected
- Fixed: Custom RGB colors not applying to keyboard in AppImage — `basecamp-controller` was not rebuilt with `per-key-rgb` support
- Fixed: Synthwave preset not loading side LED colors — wrong JSON key (`side_leds` → `side`)

---

## [1.3.1] - 2026-03-18

### Numpad Keys — Action Types
- Added action type selector per D-button: Shell, URL, Folder, App, None
- New folder picker: opens native file manager dialog to browse for a folder
- New app picker: searchable list of installed `.desktop` applications
- Actions are saved immediately to config when ✓ is pressed — no restart required
- New **Reset Buttons Flash** button: overwrites all 4 keyboard flash slots with your configured actions — use this after first setup or when switching from Windows Mountain Base Camp, as BaseCamp may have stored its own actions in flash that cause two actions to fire on a single button press

### OBS Integration
- Removed per-button ✓ save button — type and scene changes now save automatically

### Bug Fixes
- Fixed: D4 button press not detected (Write 2/3 in `_write_action` was disabling the flash slot before byte42 could activate)
- Fixed: `XDG_RUNTIME_DIR` not set when launching apps/folders from D-button press as sudo user
- Fixed: Folder/App actions not working on Arch/CachyOS/KDE — controller now auto-detects Wayland vs X11 and sets the correct display environment (`WAYLAND_DISPLAY` or `DISPLAY`)

### Code Quality
- All CLI error messages changed from German to English

---

## [1.3.0] - 2026-03-17

### RGB Lighting
- Fully implemented RGB effects: Wave, Tornado, Tornado Rainbow, Reactive, Yeti, Matrix, Off
- Fixed inverted speed slider (hardware uses 1=fast, 100=slow — now correctly mapped)
- Fixed Tornado and Tornado Rainbow effects not working
- Direction dropdown is now context-sensitive: arrow directions (L→R, T→B, …) for Wave effects, CW/CCW for Tornado effects
- RGB settings (effect, speed, brightness, colors, direction) are now saved to config and restored on next launch

### Custom RGB Mode (Beta)
- New section: zone-based RGB colors for 7 keyboard zones (F Keys, Number Row, QWERTY, Home Row, Shift Row, Bottom Row, Numpad)
- Side ring LED color control (30 LEDs on keyboard, 14 on numpad)
- Brightness slider for all LEDs
- Reset button to restore all zones to default colors
- Zone colors and brightness are saved to config and restored on next launch

### GUI
- Reordered accordion sections: Monitor → Main Display → Numpad Keys → RGB Lighting → Custom RGB Mode → OBS Integration
- OBS Integration moved to the bottom
- All red buttons now have bold black text for better readability
- All colored buttons (blue, green) now use white text instead of near-black
- GIF frame picker cancel button text changed from muted gray to white

### Bug Fixes
- Fixed: switching back to Clock mode after a main display image upload now works correctly
- Fixed: main display stuck on Mountain logo can now be resolved directly in the app via the **Reset Dial Image** button — no Windows required

### Config Persistence
- RGB settings saved to `~/.config/mountain-time-sync/rgb_settings.json`
- Zone colors saved to `~/.config/mountain-time-sync/zone_colors.json`

---

## [1.2.0]

- AUR package for Arch / CachyOS / Manjaro
- Two AppImages: Debian/Ubuntu and Fedora/Nobara builds
- Fixed udev rule: use `MODE=0666` for Arch/CachyOS compatibility

## [1.1.0]

- Main display upload (240×204 image)
- Main display mode switch (Image / Clock)
- Reset Dial Image button
- GIF frame picker for D1–D4 image upload

## [1.0.0]

- Initial release
- Time sync (analog / digital clock)
- Monitor mode: CPU, GPU, RAM, HDD, Network metrics
- D1–D4 button actions and image upload (72×72)
- OBS WebSocket integration
- System tray support
- DE / EN language support
