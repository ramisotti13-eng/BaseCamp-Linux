"""Where a service's own logic runs: one thread, in order, with timers.

Most device logic was written for Tk's main loop: callbacks run one after
another, never at the same time, and a timer is an after(). The services keep
that property by running their logic through a Scheduler instead of calling
it from worker threads directly:

    self.app.scheduler.call_soon(self._on_key, idx)        # from a worker thread
    h = self.app.scheduler.call_later(600, self._fire_single, idx)
    self.app.scheduler.cancel(h)

Under the CustomTkinter interface the scheduler is the Tk main loop itself
(shared/ui/core_bridge.TkScheduler), so nothing about the threading changes
while code moves out of the panels. Without Tk (a web interface, a headless
test) ThreadScheduler gives the same guarantees on a thread of its own.
"""
import heapq
import itertools
import logging
import threading
import time

log = logging.getLogger("basecamp.core.scheduler")


class Handle:
    """A scheduled call; pass it to cancel()."""
    __slots__ = ("cancelled", "native")

    def __init__(self):
        self.cancelled = False
        self.native = None          # e.g. the Tk after-id


class Scheduler:
    """Interface. Callbacks run one at a time, in due order."""

    def call_soon(self, fn, *args):
        return self.call_later(0, fn, *args)

    def call_later(self, delay_ms, fn, *args):
        raise NotImplementedError

    def cancel(self, handle):
        if handle is not None:
            handle.cancelled = True

    def in_scheduler_thread(self):
        raise NotImplementedError

    def start(self):
        pass

    def stop(self):
        pass


class ThreadScheduler(Scheduler):
    """Runs callbacks on a daemon thread of its own.

    An exception in a callback is logged and the thread carries on, as an
    exception in a Tk callback would.
    """

    def __init__(self, name="core-scheduler"):
        self._name = name
        self._cond = threading.Condition()
        self._heap = []
        self._seq = itertools.count()
        self._thread = None
        self._running = False

    def start(self):
        with self._cond:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, name=self._name,
                                            daemon=True)
            self._thread.start()

    def stop(self, timeout=2.0):
        with self._cond:
            self._running = False
            self._cond.notify_all()
            t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout)

    def call_later(self, delay_ms, fn, *args):
        h = Handle()
        due = time.monotonic() + max(0, delay_ms) / 1000.0
        with self._cond:
            heapq.heappush(self._heap, (due, next(self._seq), fn, args, h))
            self._cond.notify()
        return h

    def in_scheduler_thread(self):
        return threading.current_thread() is self._thread

    def pending(self):
        with self._cond:
            return sum(1 for e in self._heap if not e[4].cancelled)

    def _loop(self):
        while True:
            with self._cond:
                while self._running:
                    if self._heap:
                        wait = self._heap[0][0] - time.monotonic()
                        if wait <= 0:
                            break
                        self._cond.wait(wait)
                    else:
                        self._cond.wait()
                if not self._running:
                    return
                _, _, fn, args, h = heapq.heappop(self._heap)
            if h.cancelled:
                continue
            try:
                fn(*args)
            except Exception:
                log.exception("scheduled call %r failed", fn)
