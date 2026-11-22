#!/usr/bin/env python3
"""
Checks the Makalu Max probe without a Makalu Max (#115).

    python3 tools/test_makalu_max_probe.py

The probe is going to someone else's mouse, so what matters most is what it
will not do: every request that leaves it has to be a read. Here a stand-in
mouse answers the way the Windows library expects, records every request,
and the probe's memory dump is compared with what the stand-in holds.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import makalu_max_probe as P                       # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-60s %s" % (name, detail))
    if not ok:
        failures.append(name)


# ── Only reads leave it ──────────────────────────────────────────────────────

for payload, why in (([0xDE, 0x10, 0x00, 0x01], "a memory write"),
                     ([0xC4, 0x00, 0, 0], "commit settings"),
                     ([0xC4, 0x01, 0, 0], "restart"),
                     ([0xC4, 0x02, 0, 0], "the call after a profile write"),
                     ([0xC4, 0x05, 0, 0], "the bootloader"),
                     ([0xC6, 0x01, 0, 0], "a macro write"),
                     ([0xC5, 0x02, 0, 0], "a sensor request that is not a read"),
                     ([0xA1, 0x0C], "a Makalu 67 command")):
    try:
        P.check_allowed(bytes(payload))
        check("refuses %s" % why, False, "was let through")
    except P.RefusedRequest:
        check("refuses %s" % why, True)

for payload in ([0xDD, 0, 1, 0], [0xDF, 0, 1, 0], [0xC5, 0x01, 0], [0xC4, 0x08, 0]):
    try:
        P.check_allowed(bytes(payload))
        ok = True
    except P.RefusedRequest:
        ok = False
    check("lets the read %s through" % P.hexs(payload[:2]), ok)


# ── The descriptor ───────────────────────────────────────────────────────────

# A mouse interface with an unnumbered 8 byte vendor feature report, and a
# second interface with numbered reports.
IF0 = bytes([0x05, 0x01, 0x09, 0x02, 0xA1, 0x01,
             0x75, 0x08, 0x95, 0x04, 0x81, 0x02,           # input, 4 bytes
             0x06, 0x00, 0xFF, 0x75, 0x08, 0x95, 0x08, 0xB1, 0x02,   # feature 8
             0xC0])
IF1 = bytes([0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01,
             0x85, 0x03, 0x75, 0x08, 0x95, 0x20, 0xB1, 0x02,  # feature id 3, 32
             0x85, 0x04, 0x75, 0x08, 0x95, 0x10, 0x81, 0x02,  # input id 4, 16
             0xC0])
d0, d1 = P.parse_descriptor(IF0), P.parse_descriptor(IF1)
check("an unnumbered feature report is found with its size", d0["feature"] == {0: 8},
      d0["feature"])
check("numbered ones by their id", d1["feature"] == {3: 32} and d1["input"] == {4: 16},
      (d1["feature"], d1["input"]))
cands = P.channel_candidates([
    {"interface_number": 1, "reports": d1, "node": "/dev/hidraw9"},
    {"interface_number": 0, "reports": d0, "node": "/dev/hidraw8"},
])
check("only the unnumbered report is a channel, never a numbered one",
      [(e["node"], s) for e, s in cands] == [("/dev/hidraw8", 8)],
      [(e["node"], s) for e, s in cands])


# ── A stand-in mouse ─────────────────────────────────────────────────────────

class FakeMouse:
    """Answers the reads the way the Windows library expects them (Linux
    layout: byte 0 is the report number, 0)."""

    def __init__(self, answer_four=True):
        self.memory = {a: (a * 7 + 3) & 0xFF for a in range(P.MEMORY_END)}
        self.memory[16] = 0x15            # profile 1 of 5
        self.answer_four = answer_four
        self.requests = []
        self.pending = None

    def ioctl(self, fd, request, arg, mutate=False):
        nr = request & 0xFF
        if nr == 0x06:                    # set feature
            payload = bytes(arg[1:])
            self.requests.append(payload)
            self.pending = payload
            return arg
        if nr == 0x07:                    # get feature
            p = self.pending or b""
            out = bytearray(len(arg))
            if p[:1] == bytes([P.READ_4]) and self.answer_four:
                lo, hi = p[1], p[2]
                a = lo | hi << 8
                out[1:5] = bytes([P.ANSWER, 0xC0, lo, hi])
                out[5:9] = bytes(self.memory.get(a + i, 0) for i in range(4))
            elif p[:1] == bytes([P.READ_1]):
                lo, hi, hh = p[1], p[2], p[3]
                a = lo | hi << 8 | hh << 16
                out[1:7] = bytes([P.ANSWER, P.READ_1, lo, hi, hh, self.memory.get(a, 0)])
            elif p[:2] == bytes([P.SENSOR, 0x01]):
                out[1:7] = bytes([P.ANSWER, P.SENSOR, 0x01, p[2], 0, 0x47])
            elif p[:2] == bytes([P.SYSTEM, 0x08]):
                out[1:5] = bytes([P.ANSWER, 0x4C, 0xC4, 0x21 + p[2]])
            arg[:] = out
            return 0
        raise AssertionError("unexpected ioctl %x" % request)


def run(mouse):
    real_ioctl, real_open, real_close = P.fcntl.ioctl, P.os.open, P.os.close
    P.fcntl.ioctl = mouse.ioctl
    P.os.open = lambda path, flags: 99
    P.os.close = lambda fd: None
    try:
        ch = P.Channel("/dev/hidraw-fake", 8)
        memory = P.dump_memory(ch, progress=False)
        fw = P.firmware_version(ch, 0)
        sensor = P.read_sensor(ch, 0)
        return ch, memory, fw, sensor
    finally:
        P.fcntl.ioctl, P.os.open, P.os.close = real_ioctl, real_open, real_close


mouse = FakeMouse()
ch, memory, fw, sensor = run(mouse)
check("the whole settings memory is read", memory == mouse.memory,
      "%d of %d bytes" % (len(memory), len(mouse.memory)))
check("four bytes per request", ch.sent == P.MEMORY_END // 4 + 2, ch.sent)
check("every request that left was a read",
      all(r[0] in P.ALLOWED for r in mouse.requests)
      and all(r[0] not in (0xC4, 0xC5) or r[1] in (0x08, 0x01) for r in mouse.requests),
      sorted({P.hexs(r[:2]) for r in mouse.requests})[:6])
check("each request is the payload size of the report", all(len(r) == 8 for r in mouse.requests))
check("firmware and sensor answers are read", fw[0] == 0x21 and sensor[0] == 0x47,
      (fw[0], sensor[0]))

slow = FakeMouse(answer_four=False)
ch, memory, _fw, _s = run(slow)
expected = {a for start, end in P.REGIONS_1 for a in range(start, end)}
check("a mouse that only answers byte reads gets its regions read that way",
      set(memory) == expected and all(memory[a] == slow.memory[a] for a in memory),
      "%d bytes" % len(memory))
check("and still nothing but reads",
      all(r[0] in P.ALLOWED for r in slow.requests))
check("after giving up on four byte reads within the first few",
      sum(1 for r in slow.requests if r[0] == P.READ_4) <= 40,
      sum(1 for r in slow.requests if r[0] == P.READ_4))

check("firmware is asked only where Windows asks it",
      P.asks_firmware(0x7505) and P.asks_firmware(0xD005) and not P.asks_firmware(0x7500),
      "")
check("the memory listing skips rows it could not read",
      P.memory_lines({0: 1, 1: 2})[0].startswith("0000  01 02 ??"), P.memory_lines({0: 1, 1: 2}))

print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
