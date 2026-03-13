#!/usr/bin/env python3
"""Tray icon helper — runs as real user, communicates with main GUI via signals."""
import sys, os, signal, json
import pystray
from PIL import Image

main_pid = int(sys.argv[1])

# Optional 3rd argument: path to lang JSON file
_open_label = "Open"
_quit_label = "Quit"
if len(sys.argv) >= 3:
    lang_file = sys.argv[2]
    try:
        with open(lang_file, encoding="utf-8") as f:
            _lang = json.load(f)
        _open_label = _lang.get("tray_open", _open_label)
        _quit_label = _lang.get("tray_quit", _quit_label)
    except Exception:
        pass

def on_open(icon, item):
    os.kill(main_pid, signal.SIGUSR1)

def on_quit(icon, item):
    os.kill(main_pid, signal.SIGUSR2)
    icon.stop()

img = Image.open(os.path.join(os.path.dirname(__file__), "logo.png")).resize((64, 64), Image.LANCZOS)
menu = pystray.Menu(
    pystray.MenuItem(_open_label, on_open, default=True),
    pystray.MenuItem(_quit_label, on_quit),
)
icon = pystray.Icon("MountainEvMax", img, "Mountain Everest Max", menu)
icon.run()
