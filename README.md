# BaseCamp Linux

**Unofficial Linux companion app for the Mountain Everest Max keyboard.**

Mountain Base Camp is only available on Windows — this project brings display control, button actions, monitor metrics and OBS integration to Linux.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![Platform](https://img.shields.io/badge/Platform-Linux-black) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

- **Display styles** — Switch between Analog and Digital clock on the keyboard display
- **24H / 12H** — Toggle clock format
- **Monitor mode** — Live metrics on the keyboard display: CPU%, GPU%, RAM%, HDD%, Network MB/s
- **Button actions (D1–D4)** — Assign any shell command to the 4 numpad keys (open apps, scripts, etc.)
- **Image upload (D1–D4)** — Upload custom 72×72 images to each button
- **OBS integration** — Connect to OBS via WebSocket and trigger scene switches, recording or streaming from D1–D4
- **System tray** — Minimize to tray, runs in the background
- **Internationalization** — UI language switchable at runtime via external JSON files (DE + EN included, add your own)

---

## Requirements

```bash
pip install pillow psutil obsws-python pystray
```

> **GPU monitoring** requires `nvidia-smi` (NVIDIA only).

---

## Installation

```bash
git clone https://github.com/Ramisotti/BaseCamp-Linux.git
cd BaseCamp-Linux
pip install pillow psutil obsws-python pystray
```

### USB permissions (required, one-time)

Without this the app can't talk to the keyboard without `sudo`:

```bash
sudo tee /etc/udev/rules.d/99-mountain.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="3282", ATTRS{idProduct}=="0001", MODE="0666"
EOF
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

## Adding a language

Copy `lang/en.json` to `lang/xx.json` (e.g. `lang/fr.json`), translate the values, and it will appear automatically in the language dropdown.

---

## Keyboard compatibility

Tested with: **Mountain Everest Max** (VID `0x3282`, PID `0x0001`)

Other Mountain keyboards may work but are untested.

---

## License

MIT
