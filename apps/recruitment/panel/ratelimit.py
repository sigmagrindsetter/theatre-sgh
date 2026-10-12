"""
Per-user limit on grade saves (REVIEW_PANEL_SPEC.md §5), in process: the service runs one instance
Saves count as distinct (candidate, second) events, so a keepalive flush overlapping a save of the same
card is coalesced with it; autosave (1.5 s debounce) makes at most 40 events a minute
"""
import threading
import time
from collections import OrderedDict, defaultdict

WINDOW = 60   # seconds
EVENTS = 60   # (candidate, second) events per user and window
REPEATS = 2   # requests per event: a save plus the keepalive flush when the card is left

clock = time.monotonic
_events = defaultdict(OrderedDict)  # user id -> {(candidate id, second): requests}, oldest first
_lock = threading.Lock()


def retry_after(uid, cid):
    """None when the save may go ahead (it is then counted), else the seconds until it can."""
    second = int(clock())
    with _lock:
        events = _events[uid]
        while events and next(iter(events))[1] <= second - WINDOW:
            events.popitem(last=False)
        key = (cid, second)
        if key in events:
            if events[key] >= REPEATS:
                return 1
            events[key] += 1
        elif len(events) >= EVENTS:
            return next(iter(events))[1] + WINDOW - second
        else:
            events[key] = 1
        return None
