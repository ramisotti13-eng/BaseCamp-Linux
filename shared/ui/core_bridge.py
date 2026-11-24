"""Bridge between the core and the CustomTkinter interface.

TkScheduler makes the Tk main loop the core's scheduler, so the logic that
moves out of the panels keeps running where it ran before, one callback at a
time on the main thread.

subscribe_tk() delivers core events to a widget on the main thread and ends
the subscription when the widget is destroyed, so a closed screen never gets
a call it cannot handle.

Why calls from other threads go through a queue: tkinter accepts a call such
as after() from another thread only while the main thread sits in
mainloop(). Before that (during start-up, which is when a device is first
found) and while a test drives the loop with update(), the call raises
"main thread is not in main loop" and whatever it was meant to schedule is
lost; that is the traceback of #106. Here a worker thread only puts the call
in a queue, and the main thread moves it into Tk every POLL_MS, so nothing
posted before the loop runs is lost.
"""
import queue
import threading

from core.scheduler import Handle, Scheduler


class TkScheduler(Scheduler):
    POLL_MS = 15

    def __init__(self, root):
        self._root = root
        self._main = threading.current_thread()
        self._inbox = queue.SimpleQueue()
        self._poll_id = None
        self._closed = False
        self._arm_poll()

    # ── main thread only ───────────────────────────────────────────────────
    def _arm_poll(self):
        if self._closed:
            return
        try:
            self._poll_id = self._root.after(self.POLL_MS, self._poll)
        except Exception:
            self._closed = True          # the window is gone

    def _poll(self):
        while True:
            try:
                delay, run, h = self._inbox.get_nowait()
            except queue.Empty:
                break
            self._schedule_here(delay, run, h)
        self._arm_poll()

    def _schedule_here(self, delay, run, h):
        if h.cancelled or self._closed:
            return
        try:
            h.native = self._root.after(delay, run)
        except Exception:
            h.cancelled = True

    # ── any thread ─────────────────────────────────────────────────────────
    def call_later(self, delay_ms, fn, *args):
        h = Handle()

        def run():
            h.native = None
            if not h.cancelled:
                fn(*args)
        delay = max(0, int(delay_ms))
        if self.in_scheduler_thread():
            self._schedule_here(delay, run, h)
        else:
            self._inbox.put((delay, run, h))
        return h

    def cancel(self, handle):
        if handle is None:
            return
        handle.cancelled = True
        if handle.native is not None and self.in_scheduler_thread():
            try:
                self._root.after_cancel(handle.native)
            except Exception:
                pass
            handle.native = None

    def in_scheduler_thread(self):
        return threading.current_thread() is self._main

    def stop(self):
        self._closed = True
        if self._poll_id is not None and self.in_scheduler_thread():
            try:
                self._root.after_cancel(self._poll_id)
            except Exception:
                pass


def subscribe_tk(app, pattern, widget, handler):
    """Call `handler(topic, payload)` on the Tk main thread for each event.

    `app` is the CoreApp (its bus delivers, its scheduler hands over to the
    main thread). Returns the unsubscribe function. The subscription also
    ends by itself when `widget` is destroyed.
    """
    def deliver(topic, payload):
        try:
            alive = widget.winfo_exists()
        except Exception:
            alive = False
        if alive:
            handler(topic, payload)

    def on_event(topic, payload):
        app.scheduler.call_soon(deliver, topic, payload)

    unsubscribe = app.bus.subscribe(pattern, on_event)

    def on_destroy(event):
        if event.widget is widget:
            unsubscribe()
    try:
        widget.bind("<Destroy>", on_destroy, add="+")
    except Exception:
        pass
    return unsubscribe
