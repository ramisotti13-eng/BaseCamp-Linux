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

Without this the app can't talk to the keyboard without `sudo`:

```bash
sudo cp 99-mountain-everest-max.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then unplug and replug the keyboard.

---

## Usage

```bash
python3 gui.py
```

The GUI starts with a splash screen and auto-activates Monitor mode. The app minimizes to the system tray when closed.

---

## AppImage

A self-contained AppImage is available in the releases. No Python installation required.

```bash
chmod +x BaseCamp-Linux-x86_64.AppImage
./BaseCamp-Linux-x86_64.AppImage
```

USB permissions still need to be set up once (see above).

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
