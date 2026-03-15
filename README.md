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

The GUI is split into a persistent **dashboard** at the top and four collapsible sections below:

- **Dashboard** — Live clock display with 24H/12H toggle, language switcher (DE/EN + custom), Analog/Digital display style, splash screen and autostart toggles
- **Monitor Mode** — Start/stop live keyboard display with CPU%, GPU%, RAM%, HDD% and Network MB/s metrics
- **Main Display** — Switch between image and clock mode, upload a custom 240×204 image to the keyboard's main display
- **Numpad Keys** — Assign shell commands and custom 72×72 images (including GIF frame picker) to D1–D4
- **OBS Integration** — Connect to OBS via WebSocket and trigger scene switches, recording or streaming from any D-button

---

## Features

- **Display styles** — Switch between Analog and Digital clock on the keyboard display
- **24H / 12H** — Toggle clock format
- **Monitor mode** — Live metrics on the keyboard display: CPU%, GPU%, RAM%, HDD%, Network MB/s
- **Button actions (D1–D4)** — Assign any shell command to the 4 numpad keys (open apps, scripts, etc.)
- **Image upload (D1–D4)** — Upload custom 72×72 images to each button
- **Main display upload** — Upload a custom 240×204 image to the keyboard's main display
- **OBS integration** — Connect to OBS via WebSocket and trigger scene switches, recording or streaming from D1–D4
- **System tray** — Minimize to tray, runs in the background
- **Internationalization** — UI language switchable at runtime via external JSON files (DE + EN included, add your own)

---

## Requirements

```bash
pip install customtkinter pillow psutil obsws-python pystray
```

> **GPU monitoring** requires `nvidia-smi` (NVIDIA only).

---

## Installation

```bash
git clone https://github.com/Ramisotti/BaseCamp-Linux.git
cd BaseCamp-Linux
pip install customtkinter pillow psutil obsws-python pystray
```

### USB permissions (required, one-time)

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

Arch uses Fish as default shell — switch to bash first:

```bash
bash
```

Then run:

```bash
sudo tee /etc/udev/rules.d/99-mountain-everest-max.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="3282", ATTRS{idProduct}=="0001", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

> Unplug and replug the keyboard. No group changes needed.


---

## Known Issues

### Clock mode after main display image upload

After uploading an image to the main display, switching back to **Clock** mode does not work immediately — the keyboard ignores the mode switch command.

**Workaround:** Unplug and replug the keyboard, then click Clock in the app.

This is a known firmware-level issue with the Mountain Everest Max. A fix is planned for the next release.

---

## Usage

```bash
python3 gui.py
```

The GUI starts with a splash screen and auto-activates Monitor mode. The app minimizes to the system tray when closed.

---

## Installation

### Arch / CachyOS / Manjaro — AUR

```bash
paru -S basecamp-linux
```

The udev rule is installed automatically. Just unplug and replug the keyboard after installation.

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

USB permissions still need to be set up once (see below).

> If you get a FUSE error on startup, add `--appimage-extract-and-run`:
> ```bash
> ./BaseCamp-Linux-x86_64-fedora.AppImage --appimage-extract-and-run
> ```

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
