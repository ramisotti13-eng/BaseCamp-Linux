#!/usr/bin/env python3
"""
Checks the Everest controller hands interface 3 back to the kernel.

    python3 tools/test_everest_release.py

The application stops a monitor with Popen.terminate(), which is SIGTERM, and
Python's default handling for that ends the process where it stands. The
`finally` that releases the claimed interface never ran, so usbhid never got
interface 3 back and the keyboard kept it detached until it was replugged.

Worse, the old release only reattached when the same run had been the one to
detach, so once a killed run had leaked the interface every later run saw
nothing to hand back and left it that way for good.

No keyboard needed: the release path is checked against a stand-in device, and
the signal handling in a child process that claims nothing.
"""
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import usb.util                                   # noqa: E402
import emax_controller as E                       # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-52s %s" % (name, detail))
    if not ok:
        failures.append("%s: %s" % (name, detail))


class FakeDev:
    """Enough of a pyusb device for the release path."""

    def __init__(self, driver_active=True):
        self.driver_active = driver_active
        self.attached = []

    def is_kernel_driver_active(self, iface):
        return self.driver_active

    def detach_kernel_driver(self, iface):
        self.driver_active = False

    def attach_kernel_driver(self, iface):
        self.attached.append(iface)
        self.driver_active = True


_claimed = []
usb.util.claim_interface = lambda dev, i: _claimed.append(i)
usb.util.release_interface = lambda dev, i: None
usb.util.dispose_resources = lambda dev: None

# The ordinary round trip.
dev = FakeDev(driver_active=True)
E._claim(dev)
check("claiming detaches the kernel driver", dev.driver_active is False)
E._release(dev)
check("releasing hands interface 3 back", dev.attached == [E.INTERFACE],
      "attached=%s" % dev.attached)

# The state a killed run leaves behind: no driver to detach, and the old code
# then remembered there was nothing to give back.
dev = FakeDev(driver_active=False)
E._claim(dev)
E._release(dev)
check("a leaked interface is healed, not kept", dev.attached == [E.INTERFACE],
      "attached=%s" % dev.attached)

# The main display upload resets the port itself and says so.
dev = FakeDev(driver_active=True)
E._claim(dev)
dev._no_reattach = True
E._release(dev)
check("an explicit opt-out is respected", dev.attached == [],
      "attached=%s" % dev.attached)

# SIGTERM has to arrive as the stop Ctrl-C already was.
child = textwrap.dedent("""
    import os, signal, sys
    sys.path.insert(0, %r)
    import emax_controller as E
    signal.signal(signal.SIGTERM, E._exit_on_term)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except KeyboardInterrupt:
        print("KeyboardInterrupt")
        sys.exit(0)
    print("nothing raised")
    sys.exit(1)
""" % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
done = subprocess.run([sys.executable, "-c", child], capture_output=True,
                      text=True, timeout=30)
check("SIGTERM raises KeyboardInterrupt", done.returncode == 0,
      done.stdout.strip() or done.stderr.strip()[-80:])

# And main() is where that handler goes on, so every mode gets it.
import inspect                                    # noqa: E402
src = inspect.getsource(E.main)
check("main installs the SIGTERM handler",
      "signal.signal(signal.SIGTERM" in src)

print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
