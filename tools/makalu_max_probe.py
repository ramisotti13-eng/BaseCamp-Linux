#!/usr/bin/env python3
"""
Mountain Makalu Max probe for BaseCamp-Linux (issue #115).

    curl -O https://raw.githubusercontent.com/ramisotti13-eng/BaseCamp-Linux/main/tools/makalu_max_probe.py
    python3 makalu_max_probe.py

Nothing to install: it talks to /dev/hidraw itself and needs no Python
packages. If it says it has no permission, run it once with sudo. Close
BaseCamp Linux first, so the two do not talk to the mouse at once.

It reads and does not write. Every request it sends is checked against a
short list of read requests before it leaves, and anything else is refused
inside this script: no settings, no lighting, no macros, no firmware, no
restart. It goes through /dev/hidraw and never detaches a kernel driver, so
the mouse keeps working while it runs and nothing needs a replug afterwards.
It takes about half a minute.

## What it is for

BaseCamp Linux drives the Makalu Max with the commands of the Makalu 67, and
the Max does not take them: every setting says "Failed". The Windows software
uses a separate library for the Max (Makalu.dll), and that library does not
send commands at all. It reads and writes the mouse's settings memory: one
block for the device, then 256 bytes per profile, holding DPI, lighting,
buttons and the rest.

This probe reads that memory and the USB descriptors, and writes them to one
file. With that file the settings can be laid out against the Windows
library, so the Max support can be written without anyone having to capture
USB traffic.

## What it sends, all of it

    DD lo hi        read four bytes of settings memory at an address
    DF lo hi hh     read one byte of settings memory (only if DD is not answered)
    C5 01 rr        read sensor register rr (registers 0 and 1, the sensor's id)
    C4 08 00 / 02   ask for the firmware versions, only on the device versions
                    the Windows software asks too

Each is a feature report on the interface the Windows software uses, followed
by reading the answer back. Reverse engineered from Mountain Base Camp for
Windows (Makalu.dll: ReadMemData, Read_Fast_MemData, ReadSenMemData,
GetFirmwareVersion). The Windows library's own read routine also repairs
memory it considers blank and commits it; this probe does not do that.
"""
import argparse
import datetime
import fcntl
import json
import os
import platform
import sys
import time

VID = 0x3282
PID = 0x0002              # Makalu Max
HIDRAW_CLASS = "/sys/class/hidraw"

ANSWER = 0xB0
READ_4 = 0xDD
READ_1 = 0xDF
SENSOR = 0xC5
SYSTEM = 0xC4

# The whole of what may leave this script: (command, required second byte).
# None means any second byte (it is an address). C4 and C5 carry several
# requests under one command byte, and only the reading ones are listed:
# C4 00 commits settings, C4 01 restarts the mouse, C4 05 enters the
# bootloader, and none of them can be sent from here.
ALLOWED = {
    READ_4: None,
    READ_1: None,
    SENSOR: 0x01,
    SYSTEM: 0x08,
}

MEMORY_END = 4096          # the highest address the Windows library uses is 4031
REGIONS_1 = ((0, 32), (128, 164), (256, 256 + 5 * 256), (3968, 4096))

report = {
    "probe_version": 1,
    "device": "makalu_max",
    "created": datetime.datetime.now().isoformat(timespec="seconds"),
    "environment": {},
    "usb": {},
    "hidraw": [],
    "channel": None,
    "firmware": {},
    "sensor": {},
    "memory": {},
    "notes": [],
}


def section(title):
    print("\n%s\n%s" % (title, "-" * len(title)))


def hexs(data):
    return " ".join("%02x" % b for b in data) if data else ""


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return b""


# ── The report descriptor ────────────────────────────────────────────────────

def parse_descriptor(data):
    """Report sizes by kind and report id, out of a HID report descriptor.

    Returns {"feature": {id: bytes}, "input": {...}, "output": {...},
    "usage_pages": [...]}. Sizes are without the report id byte; id 0 means
    the reports on this interface are not numbered.
    """
    sizes = {"input": {}, "output": {}, "feature": {}}
    pages = []
    report_size = report_count = report_id = 0
    stack = []
    index = 0
    while index < len(data):
        prefix = data[index]
        if prefix == 0xFE:                       # long item, skipped whole
            if index + 2 < len(data):
                index += 3 + data[index + 1]
                continue
            break
        size = prefix & 0x03
        size = 4 if size == 3 else size
        value = int.from_bytes(data[index + 1:index + 1 + size], "little")
        tag = prefix & 0xFC
        if tag == 0x04:                          # usage page
            if value not in pages:
                pages.append(value)
        elif tag == 0x74:
            report_size = value
        elif tag == 0x94:
            report_count = value
        elif tag == 0x84:
            report_id = value
        elif tag == 0xA4:                        # push
            stack.append((report_size, report_count, report_id))
        elif tag == 0xB4 and stack:              # pop
            report_size, report_count, report_id = stack.pop()
        elif tag in (0x80, 0x90, 0xB0):
            kind = {0x80: "input", 0x90: "output", 0xB0: "feature"}[tag]
            bits = sizes[kind].get(report_id, 0) + report_size * report_count
            sizes[kind][report_id] = bits
        index += 1 + size
    out = {k: {rid: (bits + 7) // 8 for rid, bits in v.items()}
           for k, v in sizes.items()}
    out["usage_pages"] = ["0x%04X" % p for p in pages]
    return out


# ── Finding the mouse ────────────────────────────────────────────────────────

def hidraw_entries(vid, pid, base=None):
    entries = []
    root = base or HIDRAW_CLASS
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        uevent = read_text(os.path.join(root, name, "device", "uevent"))
        ids = ""
        for line in uevent.splitlines():
            if line.startswith("HID_ID="):
                ids = line.split("=", 1)[1]
        parts = ids.split(":")
        if len(parts) != 3:
            continue
        try:
            if int(parts[1], 16) != vid or int(parts[2], 16) != pid:
                continue
        except ValueError:
            continue
        hid_dir = os.path.realpath(os.path.join(root, name, "device"))
        interface = None
        usb_dir = None
        path = hid_dir
        for _ in range(4):
            path = os.path.dirname(path)
            if interface is None and os.path.exists(os.path.join(path, "bInterfaceNumber")):
                try:
                    interface = int(read_text(os.path.join(path, "bInterfaceNumber")), 16)
                except ValueError:
                    pass
            if usb_dir is None and os.path.exists(os.path.join(path, "idVendor")):
                usb_dir = path
        desc = read_bytes(os.path.join(root, name, "device", "report_descriptor"))
        entries.append({
            "node": "/dev/%s" % name,
            "interface_number": interface,
            "usb_dir": usb_dir,
            "descriptor_hex": desc.hex(),
            "reports": parse_descriptor(desc),
        })
    entries.sort(key=lambda e: (e["interface_number"] is None, e["interface_number"] or 0))
    return entries


def usb_facts(usb_dir):
    """What the USB device says about itself, from sysfs."""
    if not usb_dir:
        return {}
    facts = {k: read_text(os.path.join(usb_dir, k)) for k in
             ("idVendor", "idProduct", "bcdDevice", "manufacturer", "product",
              "speed", "bNumInterfaces", "bMaxPower", "version")}
    interfaces = []
    base = os.path.basename(usb_dir)
    for name in sorted(os.listdir(usb_dir)):
        if not name.startswith(base + ":"):
            continue
        idir = os.path.join(usb_dir, name)
        endpoints = []
        for ep in sorted(os.listdir(idir)):
            if ep.startswith("ep_"):
                edir = os.path.join(idir, ep)
                endpoints.append({k: read_text(os.path.join(edir, k)) for k in
                                  ("bEndpointAddress", "direction", "type",
                                   "wMaxPacketSize", "bInterval")})
        driver = os.path.join(idir, "driver")
        interfaces.append({
            "name": name,
            "class": read_text(os.path.join(idir, "bInterfaceClass")),
            "subclass": read_text(os.path.join(idir, "bInterfaceSubClass")),
            "protocol": read_text(os.path.join(idir, "bInterfaceProtocol")),
            "driver": os.path.basename(os.readlink(driver)) if os.path.islink(driver) else None,
            "endpoints": endpoints,
        })
    facts["interfaces"] = interfaces
    return facts


def channel_candidates(entries):
    """Interfaces with an unnumbered feature report, the one the Windows
    library talks to, best first: interface 0 with 8 bytes, then the rest.

    Only unnumbered feature reports: the library sends everything as report
    0, and a numbered report belongs to something this probe knows nothing
    about, so it is never sent to.
    """
    found = []
    for entry in entries:
        size = entry["reports"]["feature"].get(0)
        if size:
            rank = 0 if (entry["interface_number"] == 0 and size == 8) else 1
            found.append((rank, entry["interface_number"] or 0, entry, size))
    found.sort(key=lambda f: (f[0], f[1]))
    return [(entry, size) for _rank, _if, entry, size in found]


# ── Feature reports through /dev/hidraw ──────────────────────────────────────

def _ioc(nr, size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr


def HIDIOCSFEATURE(size):
    return _ioc(0x06, size)


def HIDIOCGFEATURE(size):
    return _ioc(0x07, size)


class RefusedRequest(Exception):
    pass


def check_allowed(payload):
    """The one gate every request passes. Raises for anything not a read."""
    if len(payload) < 2:
        raise RefusedRequest("empty request")
    cmd = payload[0]
    if cmd not in ALLOWED:
        raise RefusedRequest("command 0x%02X is not a read request" % cmd)
    sub = ALLOWED[cmd]
    if sub is not None and payload[1] != sub:
        raise RefusedRequest("0x%02X 0x%02X is not a read request" % (cmd, payload[1]))


class Channel:
    """Feature report 0 on one hidraw node: set a request, get the answer."""

    def __init__(self, node, size):
        self.node = node
        self.size = size                         # payload bytes, id byte excluded
        self.fd = os.open(node, os.O_RDWR)
        self.sent = 0

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    def ask(self, payload, settle=0.01):
        """`payload` is the request after the report id byte. Returns the
        answer as the Windows library sees it: byte 0 the report id (0),
        then the payload. None when the mouse does not answer."""
        check_allowed(payload)
        out = bytearray(1 + self.size)
        out[1:1 + len(payload)] = payload[:self.size]
        try:
            fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(out)), bytes(out))
        except OSError as exc:
            report["notes"].append("set feature %s: %s" % (hexs(payload[:4]), exc))
            return None
        self.sent += 1
        time.sleep(settle)
        buf = bytearray(1 + self.size)
        try:
            fcntl.ioctl(self.fd, HIDIOCGFEATURE(len(buf)), buf, True)
        except OSError as exc:
            report["notes"].append("get feature after %s: %s" % (hexs(payload[:4]), exc))
            return None
        return bytes(buf)


# ── The reads, with the answers the Windows library accepts ──────────────────

def read4(ch, address, tries=5):
    lo, hi = address & 0xFF, (address >> 8) & 0xFF
    for _ in range(tries):
        d = ch.ask(bytes([READ_4, lo, hi, 0, 0, 0]))
        if not d:
            continue
        if len(d) >= 8 and d[0] == ANSWER and d[1] == 0xC0 and d[2] == lo and d[3] == hi:
            return d[4:8]
        if len(d) >= 9 and d[1] == ANSWER and d[2] == 0xC0 and d[3] == lo and d[4] == hi:
            return d[5:9]
    return None


def read1(ch, address, tries=5):
    lo, hi, hh = address & 0xFF, (address >> 8) & 0xFF, (address >> 16) & 0xFF
    for _ in range(tries):
        d = ch.ask(bytes([READ_1, lo, hi, hh, 0, 0]))
        if not d:
            continue
        if len(d) >= 7 and d[0] == 0 and d[1] == ANSWER and d[2] == READ_1 \
                and d[3] == lo and d[4] == hi and d[5] == hh:
            return d[6]
        if len(d) >= 5 and d[0] == ANSWER and d[1] == 0xC0 and d[2] == lo and d[3] == hi:
            return d[4]
        if len(d) >= 6 and d[1] == ANSWER and d[2] == 0xC0 and d[3] == lo and d[4] == hi:
            return d[5]
    return None


def read_sensor(ch, register, tries=5):
    for _ in range(tries):
        d = ch.ask(bytes([SENSOR, 0x01, register, 0, 0, 0, 0]))
        if not d:
            continue
        if len(d) >= 7 and d[0] == 0 and d[1] == ANSWER and d[2] == SENSOR \
                and d[3] == 0x01 and d[4] == register:
            return d[6], hexs(d)
        if len(d) >= 4 and d[0] == ANSWER and d[1] == 0xC2 and d[2] == register:
            return d[3], hexs(d)
        if len(d) >= 5 and d[1] == ANSWER and d[2] == 0xC2 and d[3] == register:
            return d[4], hexs(d)
    return None, None


def firmware_version(ch, which):
    for _ in range(5):
        d = ch.ask(bytes([SYSTEM, 0x08, which, 0]))
        if d and len(d) >= 5 and d[1] == ANSWER and (d[2], d[3]) in (
                (0x4C, 0xC4), (0x55, 0xAA), (0xAA, 0x55)):
            return d[4], hexs(d)
        time.sleep(0.1)
    return None, None


def asks_firmware(bcd):
    """Whether the Windows library asks this device version for firmware.
    It does for 0xD005 and for anything from 0x7505 up, and not otherwise."""
    return bcd == 0xD005 or bcd >= 0x7505


def dump_memory(ch, progress=True):
    """The settings memory, four bytes at a time; one at a time where the
    mouse does not answer the four byte read."""
    memory = {}
    missing = []
    for address in range(0, MEMORY_END, 4):
        data = read4(ch, address)
        if data is None:
            missing.append(address)
            if len(missing) == 8 and not memory:
                break           # not answered from the start: no use going on
        else:
            for i, b in enumerate(data):
                memory[address + i] = b
        if progress and address % 256 == 0:
            print("\r  reading settings memory %3d%%" % (address * 100 // MEMORY_END),
                  end="", flush=True)
    if progress:
        print("\r  reading settings memory 100%")
    if not memory or len(missing) * 4 >= MEMORY_END // 2:
        # The four byte read is not what this firmware answers: the regions
        # the Windows library reads, one byte each.
        report["notes"].append("four byte reads unanswered, read byte by byte")
        for start, end in REGIONS_1:
            for address in range(start, end):
                value = read1(ch, address)
                if value is not None:
                    memory[address] = value
    return memory


def memory_lines(memory):
    lines = []
    for row in range(0, MEMORY_END, 16):
        cells = [("%02x" % memory[a]) if a in memory else "??"
                 for a in range(row, row + 16)]
        if all(c == "??" for c in cells):
            continue
        lines.append("%04x  %s" % (row, " ".join(cells)))
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--vid", type=lambda v: int(v, 0), default=VID)
    parser.add_argument("--pid", type=lambda v: int(v, 0), default=PID,
                        help="0x0002 for the Makalu Max")
    parser.add_argument("--out", default=None, help="where to write the report")
    parser.add_argument("--dry-run", action="store_true",
                        help="only look at the descriptors, send nothing")
    args = parser.parse_args()

    print("Mountain Makalu Max probe for BaseCamp-Linux.")
    report["environment"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "vid": "0x%04X" % args.vid,
        "pid": "0x%04X" % args.pid,
        "euid": os.geteuid(),
        "dry_run": args.dry_run,
    }

    section("Mouse")
    entries = hidraw_entries(args.vid, args.pid)
    if not entries:
        print("  no Makalu Max found (VID 0x%04X PID 0x%04X)." % (args.vid, args.pid))
        print("  Plug it in with its cable and try again.")
        return 1
    report["usb"] = usb_facts(entries[0]["usb_dir"])
    bcd = int(report["usb"].get("bcdDevice") or "0", 16)
    print("  %s, device version %04x" % (report["usb"].get("product") or "?", bcd))
    for entry in entries:
        feats = ", ".join("id %d: %d bytes" % (rid, size)
                          for rid, size in sorted(entry["reports"]["feature"].items()))
        print("  interface %-2s %-14s feature reports: %s"
              % (entry["interface_number"], entry["node"], feats or "none"))
    report["hidraw"] = [{k: v for k, v in e.items() if k != "usb_dir"} for e in entries]

    candidates = channel_candidates(entries)
    if not candidates:
        print("\n  none of its interfaces has the feature report the Windows")
        print("  software talks to, so nothing is sent. Please attach the file")
        print("  anyway: the descriptors in it are what is needed next.")
        report["notes"].append("no unnumbered feature report")
    if args.dry_run:
        if candidates:
            print("\n  would talk to %s (%d bytes); --dry-run, nothing sent"
                  % (candidates[0][0]["node"], candidates[0][1]))
        candidates = []

    ch = None
    for entry, size in candidates:
        try:
            trial = Channel(entry["node"], size)
        except PermissionError:
            print("\n  no permission for %s. Run it once as: sudo python3 %s"
                  % (entry["node"], os.path.basename(sys.argv[0])))
            return 1
        except OSError as exc:
            report["notes"].append("open %s: %s" % (entry["node"], exc))
            continue
        if read4(trial, 0, tries=3) is not None or read1(trial, 0, tries=3) is not None:
            ch = trial
            report["channel"] = {"node": entry["node"], "payload_bytes": size,
                                 "interface_number": entry["interface_number"]}
            break
        trial.close()
        report["notes"].append("no answer on %s" % entry["node"])

    if candidates and ch is None:
        print("\n  the mouse did not answer the memory read on any interface.")
        print("  Please attach the file anyway, the descriptors are in it.")

    if ch is not None:
        print("\n  talking to %s" % ch.node)
        try:
            section("Firmware and sensor")
            if asks_firmware(bcd):
                for label, which in (("main", 0x00), ("receiver", 0x02)):
                    ver, raw = firmware_version(ch, which)
                    report["firmware"][label] = {"version": ver, "reply": raw}
                    print("  firmware %-9s %s" % (label, ver if ver is not None else "(no answer)"))
            else:
                print("  firmware: not asked; Windows does not ask this device version")
            for register in (0, 1):
                value, raw = read_sensor(ch, register)
                report["sensor"]["reg%d" % register] = {"value": value, "reply": raw}
                print("  sensor register %d  %s" % (register,
                      "0x%02X" % value if value is not None else "(no answer)"))

            section("Settings memory")
            memory = dump_memory(ch)
            report["memory"] = {"bytes_read": len(memory),
                                "hex": memory_lines(memory)}
            if 16 in memory:
                print("  active profile %d of %d" % (memory[16] >> 4, memory[16] & 0x0F))
            print("  %d of %d bytes read, %d requests sent"
                  % (len(memory), MEMORY_END, ch.sent))
        finally:
            ch.close()

    out = args.out or ("makalu-max-probe-%s.json"
                       % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
    print("\nWritten: %s" % os.path.abspath(out))
    print("Please attach that file to issue #115.")
    print("It holds the mouse's USB descriptors and its stored settings,")
    print("nothing from your computer beyond the Python and kernel version.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
