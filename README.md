<p align="center">
  <img src="gitlogo.png" alt="BaseCamp Linux" width="480"/>
</p>

# BaseCamp Linux

**Unofficial Linux companion app for the Mountain Everest Max keyboard.**

Mountain Base Camp is only available on Windows — this project brings display control, button actions, monitor metrics and OBS integration to Linux.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![Platform](https://img.shields.io/badge/Platform-Linux-black) ![License](https://img.shields.io/badge/License-GPL%20v3%20%2B%20Non--Commercial-red)

---

## Screenshot

<p align="center">
  <img src="gitgui.png" alt="BaseCamp Linux GUI" width="320"/>
</p>

The GUI is split into a persistent **dashboard** at the top and six collapsible sections below:

- **Dashboard** — Live clock display with 24H/12H toggle, language switcher (DE/EN + custom), Analog/Digital display style, splash screen and autostart toggles
- **Monitor Mode** — Start/stop live keyboard display with CPU%, GPU%, RAM%, HDD% and Network MB/s metrics
- **Main Display** — Switch between image and clock mode, upload a custom 240×204 image to the keyboard's main display
- **Numpad Keys** — Assign actions (Shell, URL, Folder, App) and custom 72×72 images (including GIF frame picker) to D1–D4
- **RGB Lighting** — Control keyboard RGB effects (Wave, Tornado, Reactive, Yeti, Matrix, and more) with speed, brightness, color and direction — settings saved automatically
- **Custom RGB Mode (Beta)** — Set individual colors per keyboard zone (F Keys, Number Row, QWERTY, Home Row, Shift Row, Bottom Row, Numpad) plus the side ring LEDs — colors and brightness saved automatically
- **OBS Integration** — Connect to OBS via WebSocket and trigger scene switches, recording or streaming from any D-button

---

## Features

- **Display styles** — Switch between Analog and Digital clock on the keyboard display
- **24H / 12H** — Toggle clock format
- **Monitor mode** — Live metrics on the keyboard display: CPU%, GPU%, RAM%, HDD%, Network MB/s
- **Button actions (D1–D4)** — Assign Shell commands, URLs, folders or installed apps to D1–D4 — with native folder picker and searchable app picker
- **Image upload (D1–D4)** — Upload custom 72×72 images to each button (GIF frame picker included)
- **Main display upload** — Upload a custom 240×204 image to the keyboard's main display
- **RGB Lighting** — Full RGB effect control: Wave, Tornado, Tornado Rainbow, Reactive, Yeti, Matrix, Off — with speed, brightness, color pickers and direction — settings saved to config
- **Custom RGB Mode (Beta)** — Per-zone keyboard colors (7 zones) + side ring LED color and brightness — saved to config with reset-to-defaults button
- **OBS integration** — Connect to OBS via WebSocket and trigger scene switches, recording or streaming from D1–D4 — settings save automatically on change
- **System tray** — Minimize to tray, runs in the background
- **Internationalization** — UI language switchable at runtime via external JSON files (DE + EN included, add your own)

---

## Known Issues

### Main display stuck on Mountain logo (rare)

In rare cases the main display shows the original Mountain logo and cannot be overwritten with a new image — the upload appears to complete but the logo stays.

**Cause:** The keyboard's internal flash controller gets into a stuck state.

**Fix:** Click **Reset Dial Image** in the Main Display section of the app. This resets the flash controller and clears the stuck state.

---

## Usage

```bash
python3 gui.py
```

The GUI starts with a splash screen and auto-activates Monitor mode. The app minimizes to the system tray when closed.

---

## Installation

### AppImage (Debian, Ubuntu, Mint, Fedora, Nobara)

Self-contained AppImages are available in the [releases](../../releases). No Python installation required.

| File | Distro |
|------|--------|
| `BaseCamp-Linux-x86_64-debian.AppImage` | Debian, Ubuntu, Linux Mint |
| `BaseCamp-Linux-x86_64-fedora.AppImage` | Fedora, Nobara |

```bash
chmod +x BaseCamp-Linux-x86_64-*.AppImage
./BaseCamp-Linux-x86_64-debian.AppImage   # or -fedora
```

To add BaseCamp Linux to your app menu, run it once with `--install`:

```bash
./BaseCamp-Linux-x86_64-fedora.AppImage --install
```

This installs the icon and desktop entry to `~/.local/share/`. After that you can launch it directly from your application launcher.

USB permissions still need to be set up once (see below).

> If you get a FUSE error on startup, add `--appimage-extract-and-run`:
> ```bash
> ./BaseCamp-Linux-x86_64-fedora.AppImage --appimage-extract-and-run
> ```

---

### Arch / CachyOS / Manjaro — AUR

```bash
paru -S basecamp-linux
```

The udev rule is installed automatically. Just unplug and replug the keyboard after installation.

---

### From source

```bash
git clone https://github.com/ramisotti13-eng/BaseCamp-Linux.git
cd BaseCamp-Linux
pip install customtkinter pillow psutil obsws-python pystray
python3 gui.py
```

> **GPU monitoring** requires `nvidia-smi` (NVIDIA only).

---

### USB permissions (required once, AppImage + source installs)

#### Debian / Ubuntu / Linux Mint

```bash
sudo tee /etc/udev/rules.d/99-mountain-everest-max.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="3282", ATTRS{idProduct}=="0001", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG plugdev $USER
```

> Log out and back in after adding the group, then unplug and replug the keyboard.

#### Fedora / Nobara

```bash
sudo tee /etc/udev/rules.d/99-mountain-everest-max.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="3282", ATTRS{idProduct}=="0001", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

> Unplug and replug the keyboard. No group changes needed.

#### Arch / CachyOS / Manjaro

```bash
bash   # switch to bash if using Fish
sudo tee /etc/udev/rules.d/99-mountain-everest-max.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="3282", ATTRS{idProduct}=="0001", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

> Unplug and replug the keyboard. No group changes needed.

---

## Adding a language

Copy `lang/en.json` to `lang/xx.json` (e.g. `lang/fr.json`), translate the values, and it will appear automatically in the language dropdown.

---

## Keyboard compatibility

Tested with: **Mountain Everest Max** (VID `0x3282`, PID `0x0001`)

Other Mountain keyboards may work but are untested.

---

## License

GPL v3 + Non-Commercial — free for personal and open-source use, commercial use prohibited. See [LICENSE](LICENSE) for details.
