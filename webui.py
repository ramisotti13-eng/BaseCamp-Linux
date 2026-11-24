#!/usr/bin/env python3
"""BaseCamp Linux, the new interface: HTML/CSS in a pywebview window over the
core (ui_web/). Since 4.0 it is also what gui.py starts by default; gui.py
falls back to the classic CustomTkinter interface where the web window
cannot open, this entry does not (it says what is missing and stops).

    python3 webui.py              the devices that are plugged in
    python3 webui.py --demo       stand-in devices, a throw-away configuration
    python3 webui.py --debug      with the web inspector

For the page in a browser instead: python3 -m ui_web.devserver [--demo].
"""
import sys

from ui_web.app import main

if __name__ == "__main__":
    sys.exit(main())
