# Changelog

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
