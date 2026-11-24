"""What the computer is doing: CPU, RAM, disk, network and GPU load.

Two screens show these numbers: the Everest Max's monitor card
(core/devices/everest_max/metrics.py, which also shows the volume) and the
DisplayPad's PC-info keys (core/devices/displaypad/live.py). Both read them
from psutil, through the helpers here.

Nothing here blocks. psutil answers from /proc at once; the one slow source,
`nvidia-smi`, is started in the background and read on the next sample, so a
caller on the core scheduler is never held up by it.

Every reader keeps its own baseline. psutil.cpu_percent(None) measures from
the previous call anywhere in the process, so two screens asking it in turn
would each see the other's short interval; CpuMeter measures from its own
previous reading instead.
"""
import glob
import shutil
import subprocess
import time


def _psutil():
    try:
        import psutil
        return psutil
    except Exception:            # not installed: every value is unknown
        return None


def ram_percent(psutil=None):
    """Memory in use, 0-100."""
    psutil = psutil or _psutil()
    return psutil.virtual_memory().percent


def disk_percent(path="/", psutil=None):
    """How full the disk holding `path` is, 0-100."""
    psutil = psutil or _psutil()
    return psutil.disk_usage(path).percent


class NetRate:
    """Network traffic, sent and received together, in MB/s (10^6 bytes)
    since the previous reading; 0.0 on the first."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._last = None

    def read(self, psutil=None):
        psutil = psutil or _psutil()
        io = psutil.net_io_counters()
        now = self._clock()
        total = io.bytes_sent + io.bytes_recv
        mbs = 0.0
        if self._last:
            dt = max(0.001, now - self._last[0])
            mbs = (total - self._last[1]) / dt / 1e6
        self._last = (now, total)
        return mbs


class CpuMeter:
    """CPU load in percent since this meter's previous reading, worked out
    from psutil.cpu_times() the way psutil.cpu_percent() does it."""

    def __init__(self, psutil=None):
        self._psutil = psutil
        self._last = None
        try:
            self._last = self._times()
        except Exception:
            pass

    def _times(self):
        t = (self._psutil or _psutil()).cpu_times()
        total = sum(t)
        # Guest time is already counted in user time on Linux.
        total -= getattr(t, "guest", 0) + getattr(t, "guest_nice", 0)
        busy = total - t.idle - getattr(t, "iowait", 0)
        return total, busy

    def read(self):
        """0-100, or None before there is an interval to measure."""
        now = self._times()
        last, self._last = self._last, now
        if last is None:
            return None
        d_total = now[0] - last[0]
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, (now[1] - last[1]) / d_total * 100.0))


class GpuReader:
    """GPU load in percent, where the driver tells it without root.

    AMD: /sys/class/drm/card*/device/gpu_busy_percent, read directly.
    NVIDIA: `nvidia-smi`, started in the background and read the next time,
    so each reading is the one from the previous call (two seconds old on
    the DisplayPad). Intel's driver has no such number.
    """

    SYSFS = "/sys/class/drm/card*/device/gpu_busy_percent"
    NVIDIA = ["nvidia-smi", "--query-gpu=utilization.gpu",
              "--format=csv,noheader,nounits"]

    def __init__(self, sysfs_glob=None, which=shutil.which, popen=subprocess.Popen):
        self._glob = sysfs_glob or self.SYSFS
        self._which = which
        self._popen = popen
        self._sysfs = None          # the file that answered, once found
        self._nvidia = None         # path of nvidia-smi, once found
        self._looked = False
        self._proc = None
        self._started = 0.0
        self._value = None

    def _look(self):
        if self._looked:
            return
        self._looked = True
        for path in sorted(glob.glob(self._glob)):
            try:
                with open(path) as f:
                    int(f.read().strip())
                self._sysfs = path
                return
            except (OSError, ValueError):
                continue
        try:
            self._nvidia = self._which("nvidia-smi")
        except Exception:
            self._nvidia = None

    def available(self):
        self._look()
        return bool(self._sysfs or self._nvidia)

    def read(self):
        """0-100, or None when unknown (yet)."""
        self._look()
        if self._sysfs:
            try:
                with open(self._sysfs) as f:
                    return float(int(f.read().strip()))
            except (OSError, ValueError):
                return None
        if self._nvidia:
            self._poll_nvidia()
            return self._value
        return None

    def _poll_nvidia(self):
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                if time.monotonic() - self._started < 5.0:
                    return              # still running: keep the last value
                self._kill(proc)        # hung: give up on this one
            else:
                try:
                    out = proc.stdout.read() if proc.stdout else b""
                    if isinstance(out, bytes):
                        out = out.decode("ascii", "replace")
                    self._value = float(int(out.strip().splitlines()[0]))
                except Exception:
                    self._value = None
                finally:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass
            self._proc = None
        try:
            from core.platform.launcher import clean_child_env
            self._proc = self._popen([self._nvidia] + self.NVIDIA[1:],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL,
                                     env=clean_child_env())
            self._started = time.monotonic()
        except Exception:
            self._proc = None
            self._nvidia = None         # cannot be run: stop trying

    @staticmethod
    def _kill(proc):
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass

    def stop(self):
        """End a query still running."""
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            self._kill(proc)


class SystemSampler:
    """The five numbers of a PC-info key, read together.

    sample(names) -> {name: value or None} for names among cpu, ram, disk,
    net (MB/s) and gpu; only those asked for are read. A value that cannot be
    read is None. `available()` tells which names this computer can answer.
    """

    NAMES = ("cpu", "ram", "disk", "net", "gpu")

    def __init__(self, disk_path="/", psutil=None, gpu=None):
        self._psutil = psutil
        self.disk_path = disk_path
        self._cpu = CpuMeter(psutil) if (psutil or _psutil()) else None
        self._net = NetRate()
        self._gpu = gpu if gpu is not None else GpuReader()
        try:
            self._net.read(self._psutil or _psutil())     # the baseline
        except Exception:
            pass

    def available(self):
        names = ["cpu", "ram", "disk", "net"] if (self._psutil or _psutil()) else []
        try:
            if self._gpu.available():
                names.append("gpu")
        except Exception:
            pass
        return names

    def sample(self, names=NAMES):
        ps = self._psutil or _psutil()
        out = {}
        for name in names:
            try:
                if name == "cpu":
                    out[name] = self._cpu.read() if self._cpu else None
                elif name == "ram":
                    out[name] = ram_percent(ps) if ps else None
                elif name == "disk":
                    out[name] = disk_percent(self.disk_path, ps) if ps else None
                elif name == "net":
                    out[name] = self._net.read(ps) if ps else None
                elif name == "gpu":
                    out[name] = self._gpu.read()
                else:
                    out[name] = None
            except Exception:
                out[name] = None
        return out

    def stop(self):
        try:
            self._gpu.stop()
        except Exception:
            pass

