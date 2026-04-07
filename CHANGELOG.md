# Changelog

## [1.7.3-beta] - 2026-04-07

### Everest 60 — Protocol overhaul (thanks to [@FransM](https://github.com/FransM) for reverse-engineering and testing!)

- **SetMode (0x16) fix:** buf[5]=0x01, effect code moved to buf[9] — sent before SendModeDetails now
- **SendModeDetails (0x17) fix:** Correct byte layout for colors, speed, brightness
- **Response verification:** Echo check now reads resp[1] (was resp[0])
- **COLOR_RAINBOW = 0x02** (was 0x01), new **COLOR_DUAL = 0x10** for dual-color effects
- **Tornado direction fix:** CW=0x0A, CCW=0x09 with inversion formula (10-direction)
- **Tornado is single-color only** — removed broken dual-color support for Tornado
- **Breathing, Wave, Reactive, Yeti** now use COLOR_DUAL by default — both colors are sent correctly
- **Custom RGB: LEDIDX hardware mapping** — byte 4 of each color entry is now the physical LED address instead of 0xFF. LED index table reverse-engineered by FransM (WIP, may need refinement)
- **Custom RGB: packet flag fix** — 0x0E = more packets, 0x0A = last packet (was inverted)
- **Custom RGB: mode activation** — `_send_mode(EFFECT_CUSTOM)` is now called before uploading per-key colors

### Everest 60 — Layout fix

- **Removed backtick/tilde key** — does not exist on the Everest 60 (64 keys, was 65)
- **Equal row widths** — all rows now use proportional spacing (`sbet`), fixing rows 2+3 being shorter than the rest

### New features

- **Default presets for Everest 60** — Synthwave, Ocean, Ember, Forest, Arctic, Galaxy (auto-loaded on first use)
- **"Shoreline" preset for Everest Max** — ocean wave gradient from deep navy to bright foam

## [1.7.2-beta] - 2026-04-02

### Everest 60 — Protocol Fixes (thanks to FransM for reverse-engineering)

- **Effect fix:** `0x17` (SendModeDetails) now correctly sends the effect code in buf[5] — was incorrectly set to profile number, which is why only Static worked (Static=0x01 happened to equal profile=1)
- **Mode switch fix:** `0x16` (SetMode) now sends effect code in buf[5] to activate the mode
- **Response verification:** After each `set_report`, verifies `get_report` echoes the command byte — retries up to 3× if device is busy
- **Timing fix:** Added 50ms sleep after `get_feature_report` for device stability
- **Custom RGB fix:** Fixed buffer overflow in per-key mode — `COLORS_PER_PKT` was 56 (colors) but only 14 fit in a 65-byte HID report (14 × 4 bytes = 56 bytes payload). This caused the "Broken pipe" IOCTL error on Apply
- **Dual color support:** Breathing, Wave, and Tornado effects now support a second color — both in the protocol layer and the GUI (Color 2 picker)

---

## [1.7.1-beta] - 2026-03-31

### Bug Fixes

- **Auto-detection fix:** Device detection now runs immediately on startup instead of after 1 second — if only an Everest 60 is connected, the app auto-switches to the Everest 60 panel without requiring a manual change
- **Crash fix (`_rgb_apply_row`):** Fixed a crash on startup where `_rgb_update_controls()` was called before the apply button row was created — reordered initialization to prevent `AttributeError`
- **Everest 60 layout corrected:** Added arrow key cluster — row 4 now has small right shift + ↑ + Del, row 5 has ← ↓ → instead of a wide Ctrl. Total visible keys: 65 (was incorrectly 61)

---

## [1.7.0] - 2026-03-29

### Macro System — New Feature

- **New top-level Macros tab** in the switcher bar — create, edit, and manage macros independently from any device
- **Macro Editor**: Named macros with ordered action sequences, repeat modes (Once / N Times / Toggle), duplicate, delete, export/import as JSON
- **Auto-naming**: New macros get unique names automatically (Macro, Macro 1, Macro 2, …)

### Macro Actions

- **Key Down / Key Up / Key Tap**: Keyboard input simulation with **Rec button** — press Rec, then press any key on your keyboard to capture it
- **Mouse Click**: Left, right, middle, back, forward — with **Rec button** that opens a click-capture dialog (back/forward as quick-pick buttons for side mouse buttons)
- **Mouse Move**: Absolute screen position (x, y)
- **Mouse Path**: Saved mouse movement recordings — record once, reuse in any macro
- **Mouse Scroll**: Up/down with configurable scroll amount
- **Delay**: Configurable wait time in milliseconds
- **Type Text**: Type a string character by character
- **Shell / URL / Folder**: Run commands, open URLs, open folders

### Mouse Recording

- **Rec Mouse** button opens a fullscreen overlay with a screenshot of the desktop as background — see your screen while recording. This is needed because Wayland does not allow apps to track the mouse cursor across the screen; a fullscreen window with a desktop screenshot solves this by receiving mouse motion events while still showing you where you're pointing. The screenshot is taken locally, used only for the overlay background, never sent anywhere, and automatically deleted when recording stops
- **Space to start/stop** recording — no mouse click needed (avoids recording the stop-click position)
- Mouse movement captured via Motion events at ~50ms resolution — works on **X11 and Wayland**
- Recordings saved as reusable JSON files in `~/.config/mountain-time-sync/mouse_recordings/`
- **"Add left click at end"** checkbox (enabled by default) — automatically appends a click at the final position
- Recordings manageable: pick from saved recordings via **"..."** button, delete with **✕** in the picker
- Screenshot tools: `spectacle` (KDE), `grim` (Sway), `gnome-screenshot` (GNOME), `scrot` (X11)

### Macro Assignment

- New **"Macro"** action type available on **D1–D4** (Everest Max) and **K1–K12** (DisplayPad)
- Macro picker dropdown shows all saved macros by name
- Macros execute in a background thread when the assigned button is pressed

### Input Tool Support

- **Auto-detection**: Finds `xdotool` (X11) or `ydotool` (Wayland) automatically
- **ydotool key mapping**: Full Linux input-event-codes mapping for all keys
- **Clear error message** if no input tool is installed — shows install command for Fedora, Debian, and Arch

### Internationalisation

- Full DE/EN support for all Macro features (20+ new translation keys)

---

## [1.6.3-beta] - 2026-03-29

### Mountain Everest 60 Keyboard — Full Support

- **Automatic detection**: Everest 60 ANSI (PID `0x0005`) and ISO (PID `0x0006`) detected automatically on startup — dedicated panel with RGB controls
- **RGB Lighting**: Full effect control — Static, Breathing, Breathing Rainbow, Wave, Wave Rainbow, Tornado, Tornado Rainbow, Reactive, Yeti, Off — with speed, brightness, color pickers and direction
- **Custom RGB Mode**: Per-key color editor with 60% ANSI layout (61 keys) — separate config and presets from Everest Max
- **Keyboard switcher label**: Shows "Everest Max" or "Everest 60" depending on which keyboard is detected (like "Makalu 67" / "Makalu Max" for mouse)
- **Protocol**: Interface 2, magic bytes `0x46 0x23 0xEA`, 65-byte HID Feature Reports — based on OpenRGB reverse-engineering

### Custom RGB Window — Layout Adaptability

- `CustomRGBWindow` now accepts layout parameters — automatically adapts to the connected keyboard:
  - **Everest Max**: Full layout with numpad, nav cluster, and 45 side LEDs
  - **Everest 60**: Compact 60% layout (61 keys, no numpad, no side LEDs, no "Persist to Slot")
- Separate per-key config and presets per keyboard model — settings don't interfere

### USB Access / udev Rules

- Updated `99-mountain.rules` with all supported devices: Everest Max (`0x0001`), Makalu Max (`0x0002`), Makalu 67 (`0x0003`), Everest 60 ANSI (`0x0005`), Everest 60 ISO (`0x0006`), DisplayPad (`0x0009`)
- Added `hidraw` rules for all devices (previously only DisplayPad had hidraw access)
- Updated README installation instructions with complete udev rules

### Build

- Added `everest60-controller` binary to AppImage
- Added `everest60-controller.spec` for PyInstaller builds

---

## [1.6.2-beta] - 2026-03-28

### Makalu Max (PID 0x0002) — Full Support

- **Automatic detection**: App detects Makalu Max and Makalu 67 automatically on startup — same panel, same controls
- **8-button remapping**: Makalu Max supports 8 programmable buttons (vs 6 on Makalu 67); remap and sniper assignments extended accordingly
- **Model name display**: Switcher button and RGB Lighting section header show the detected model name ("Makalu 67" or "Makalu Max")

### DisplayPad — Brightness Control

- **Brightness dropdown** (☀ 0%/25%/50%/75%/100%) added next to the rotation menu — reverse-engineered from USB capture (`12 03 00 00 [%]`)
- Brightness is saved to config and automatically restored on device reconnect or app restart

### UI / UX

- **Device switcher buttons** now turn **green** when the device is connected (instead of always staying gray when not active). Active device stays blue, disconnected stays gray — applies to Keyboard, Mouse, DisplayPad, and OBS
- **DisplayPad busy-at-boot retry**: If the DisplayPad is busy when the app starts (e.g. after autostart), the app retries up to 5× with increasing delays (2 s, 4 s, 6 s, 8 s, 10 s) before giving up

### Build

- Added `makalu-controller` binary to AppImage (was missing — caused errno 2 on Custom RGB in frozen builds)
- Added `build.sh` for reproducible AppImage builds

---

## [1.6.1-beta] - 2026-03-25

### Makalu Max (PID 0x0002) — Initial Support

- Device constants and `detect_model()` added to controller
- Default button layout for Makalu Max defined (`REMAP_DEFAULTS_MAX`)

---

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
