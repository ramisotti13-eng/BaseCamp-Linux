"""Is a USB device here, and may this user open it?

Linux answers from sysfs and the permissions of the /dev nodes; without sysfs
(Windows) nothing is found, and a Windows port supplies its own functions.
Moved out of gui.py, which keeps ``_check_usb_presence``, ``_device_nodes``,
``_device_access_denied`` and ``_describe_node`` as module-level names because
tests patch them: the wrappers there look each other up at call time.
"""
import os

SYSFS_USB = "/sys/bus/usb/devices"


def device_nodes(vid, pid):
    """The /dev entries a device with this VID:PID is driven through.

    Two kinds: the USB node under /dev/bus/usb, used by the libusb path
    (Everest Max), and the hidraw nodes of its HID interfaces, used by
    everything else. Returns the paths that exist.
    """
    nodes = []
    target_vid, target_pid = f"{vid:04x}", f"{pid:04x}"
    base = SYSFS_USB
    try:
        entries = os.listdir(base)
    except OSError:
        return nodes
    for entry in entries:
        d = f"{base}/{entry}"
        try:
            with open(f"{d}/idVendor") as f:
                if f.read().strip() != target_vid:
                    continue
            with open(f"{d}/idProduct") as f:
                if f.read().strip() != target_pid:
                    continue
            with open(f"{d}/busnum") as f:
                bus = int(f.read().strip())
            with open(f"{d}/devnum") as f:
                dev = int(f.read().strip())
        except (OSError, ValueError):
            continue
        usb_node = f"/dev/bus/usb/{bus:03d}/{dev:03d}"
        if os.path.exists(usb_node):
            nodes.append(usb_node)
        # hidraw nodes live under the interface directories of this device
        for sub in entries:
            if not sub.startswith(entry + ":"):
                continue
            hidraw_dir = f"{base}/{sub}/hidraw"
            try:
                for name in os.listdir(hidraw_dir):
                    node = f"/dev/{name}"
                    if os.path.exists(node):
                        nodes.append(node)
            except OSError:
                # Not every interface is a HID one, and some hang the hidraw
                # directory a level deeper under the hid driver.
                try:
                    for hid_entry in os.listdir(f"{base}/{sub}"):
                        hidraw_dir = f"{base}/{sub}/{hid_entry}/hidraw"
                        if not os.path.isdir(hidraw_dir):
                            continue
                        for name in os.listdir(hidraw_dir):
                            node = f"/dev/{name}"
                            if os.path.exists(node):
                                nodes.append(node)
                except OSError:
                    pass
    return nodes


def access_denied(vid, pid, nodes=None):
    """Nodes of a present device that this user may not read and write.

    A device that has enumerated is visible in sysfs whatever the permissions
    on its /dev entries are, so presence alone said "connected" while every
    action quietly did nothing. That is what a missing or unapplied udev rule
    looks like from the outside (issue #49). Empty list means all good.

    A node that is gone by the time it is checked is not a permission problem
    and must not be reported as one. The DisplayPad re-enumerates by itself,
    so the node listed a moment ago can be the one it has just dropped, and
    os.access() answers False for a path that does not exist. That put a full
    "no permission" notice over a device whose /dev entry was plain 0666 the
    whole time (issue #86).

    `nodes` is the function that lists the nodes (default device_nodes()).
    """
    denied = []
    for node in (nodes or device_nodes)(vid, pid):
        try:
            if os.access(node, os.R_OK | os.W_OK):
                continue
            if not os.path.exists(node):
                continue
        except OSError:
            continue
        denied.append(node)
    return denied


def describe_node(path):
    """Owner, group and mode of a device node, for the log line that says we
    cannot open it. Without this a false report is indistinguishable from a
    real one, and #86 was reported with an `ls` that contradicted us."""
    try:
        import grp
        import pwd
        st = os.stat(path)
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        return "%s (%s:%s %o)" % (path, owner, group, st.st_mode & 0o7777)
    except (OSError, ImportError) as exc:
        return "%s (%s)" % (path, exc.__class__.__name__)


def usb_present(vid, pid):
    """True if a USB device with this VID:PID is present.
    Reads /sys/bus/usb/devices/ directly: no subprocess, no forking.
    """
    try:
        target_vid = f"{vid:04x}"
        target_pid = f"{pid:04x}"
        for entry in os.listdir(SYSFS_USB + "/"):
            base = f"{SYSFS_USB}/{entry}"
            try:
                with open(f"{base}/idVendor") as f:
                    if f.read().strip() != target_vid:
                        continue
                with open(f"{base}/idProduct") as f:
                    if f.read().strip() == target_pid:
                        return True
            except OSError:
                continue
        return False
    except OSError:
        return False
