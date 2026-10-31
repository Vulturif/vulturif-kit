"""Opt-in usage statistics: which features get used, never what anyone did.

Sent to vulturif.com's collector, which keeps one counter per day, app,
version, Windows release and event name — nothing else, not even the address
a report came from. The line this module draws is the whole point of it:

  - Off unless the user said yes. Off means no counting and no network, and
    nothing leaves before `start()`, which only the app's entry point should
    call — so no window in a test suite can report anything, whatever its
    settings say.
  - Only fixed names leave: "resolve_fail:xhamster", never a file, a title, a
    folder, a search or a link. `track()` checks every name against `_SAFE`,
    which free text cannot pass, so a careless call site drops its event
    rather than leaking it.
  - No identifier of any kind, not even a per-run one: what is never sent
    cannot be kept by mistake. The one thing the app may remember locally is
    the date of the last `active_day` (see `mark_day`), which turns
    "launches" into roughly "people a day" without telling one from another.
  - Counts are summed here and sent in batches, so even the collector sees
    "these features, so many times", not a sequence anyone could replay.
"""

import atexit
import datetime
import platform
import re
import threading

FLUSH_INTERVAL_SEC = 300
TIMEOUT = 8
# On the way out the process waits for the last batch, and a machine without
# network should not keep a closed window's process alive for long.
EXIT_TIMEOUT = 3
# What the collector accepts per report (showcase/views.py) — a batch over
# either is refused whole, so it is split or capped here instead.
MAX_EVENTS = 100
MAX_COUNT = 10_000

# A name with an optional ":kind": letters, digits, underscores. The
# collector holds the same pattern, so nothing that passes here is refused.
# fullmatch, not match: "$" lets a trailing newline through.
_SAFE = re.compile(r"[A-Za-z0-9_]+(:[A-Za-z0-9_]+)?")

APP_LAUNCH = "app_launch"
ACTIVE_DAY = "active_day"           # at most once a day per installation
UPDATE_FAILED = "update_failed"


def event(kind: str, value) -> str:
    """"kind:value" with what `_SAFE` would refuse turned into "_": a speed
    of 1.5 becomes "speed:1_5". Only for values from a fixed set — this makes
    a name pass, it does not make free text safe to send."""
    return f"{kind}:{re.sub(r'[^A-Za-z0-9_]', '_', f'{value}')}"


def safe(name) -> bool:
    return isinstance(name, str) and bool(_SAFE.fullmatch(name))


class Analytics:
    def __init__(self, app: str, version: str, endpoint: str):
        self.app = app
        self.version = version
        # Empty means never send, whatever the user chose.
        self.endpoint = endpoint
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._enabled = False
        self._started = False

    def enabled(self) -> bool:
        return bool(self.endpoint) and self._enabled

    def set_enabled(self, on: bool):
        """Follow the user's answer. Turning it off also forgets whatever was
        counted and not sent yet: no means no from this moment, not from the
        next flush."""
        self._enabled = bool(on)
        if not self._enabled:
            with self._lock:
                self._counts.clear()

    def track(self, name: str, n: int = 1):
        """Count `n` uses of a named feature. Nothing at all unless started
        and opted in; a name that is not a fixed one is dropped, never sent."""
        if not (self._started and self.enabled()) or n <= 0 or not safe(name):
            return
        with self._lock:
            self._counts[name] = min(MAX_COUNT, self._counts.get(name, 0) + int(n))

    def pending(self) -> dict:
        """What would go out with the next batch — for the tests."""
        with self._lock:
            return dict(self._counts)

    def start(self, on: bool):
        """Begin counting and sending in the background. Idempotent.

        Called whatever the answer was, so a yes given later in the settings
        takes effect at once rather than at the next start.
        """
        self.set_enabled(on)
        if self._started:
            return
        self._started = True
        atexit.register(self.flush, EXIT_TIMEOUT)
        threading.Thread(target=self._loop, daemon=True, name="analytics").start()
        self.track(APP_LAUNCH)

    def mark_day(self, last: str | None, today=None) -> str | None:
        """Count this installation as active today, once.

        `last` is the date the app stored the previous time; the answer is
        the date to store now, or None when there is nothing to store. Kept
        by the app, in settings that never leave the machine, and only while
        counting is on — someone who said no has nothing about this in their
        files either.
        """
        if not (self._started and self.enabled()):
            return None
        today = (today or datetime.date.today()).isoformat()
        if last == today:
            return None
        self.track(ACTIVE_DAY)
        return today

    def _loop(self):
        stop = threading.Event()
        while not stop.wait(FLUSH_INTERVAL_SEC):
            self.flush()

    def flush(self, timeout: float = TIMEOUT):
        if not self.enabled():
            return
        with self._lock:
            if not self._counts:
                return
            batch = dict(self._counts)
            self._counts.clear()
        import requests
        # The Windows release and the version, and those only: "Windows 11"
        # identifies nobody, and both say which build a broken site hit.
        base = {"v": self.version,
                "os": f"{platform.system()} {platform.release()}".strip()}
        items = sorted(batch.items())
        for i in range(0, len(items), MAX_EVENTS):
            try:
                requests.post(self.endpoint,
                              json={**base, "events": dict(items[i:i + MAX_EVENTS])},
                              headers={"User-Agent": f"{self.app}/{self.version}"},
                              timeout=timeout)
            except Exception:
                # Statistics must never surface an error or hold anything up.
                # A batch that does not arrive is simply gone, by design.
                pass
