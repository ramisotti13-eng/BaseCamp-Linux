"""Event bus: services say what happened, interfaces listen.

A topic is a dotted name such as ``displaypad.page_switched``. A subscription
is made to a pattern: the exact topic, a prefix ending in ``.*``
(``displaypad.*`` receives every topic below ``displaypad``), or ``*`` for
everything, which is what a web interface forwarding all events to its page
uses.

Handlers are called as ``handler(topic, payload)`` with the payload as a dict,
on the thread that published. They must return quickly and must not touch a
toolkit directly. An exception in one handler is logged and neither stops the
other handlers nor reaches the publisher: a broken listener must never take a
device worker down with it.
"""
import logging
import threading

log = logging.getLogger("basecamp.core.bus")


def topic_matches(pattern, topic):
    """True if `topic` falls under `pattern` (exact, ``prefix.*`` or ``*``)."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return topic.startswith(pattern[:-1])
    return pattern == topic


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = []         # [(pattern, handler)], in subscription order

    def subscribe(self, pattern, handler):
        """Call `handler(topic, payload)` for every topic matching `pattern`.

        Returns a function that ends the subscription; calling it twice is
        harmless.
        """
        entry = (pattern, handler)
        with self._lock:
            self._subs.append(entry)

        def unsubscribe():
            with self._lock:
                try:
                    self._subs.remove(entry)
                except ValueError:
                    pass
        return unsubscribe

    def publish(self, topic, **payload):
        """Deliver `payload` to every handler subscribed to a matching pattern."""
        with self._lock:
            targets = [h for p, h in self._subs if topic_matches(p, topic)]
        for handler in targets:
            try:
                handler(topic, payload)
            except Exception:
                log.exception("event handler %r failed on %s", handler, topic)

    def subscriber_count(self, topic=None):
        """Number of subscriptions, or of those matching `topic`."""
        with self._lock:
            if topic is None:
                return len(self._subs)
            return sum(1 for p, _ in self._subs if topic_matches(p, topic))
