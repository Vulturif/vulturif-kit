"""Error reports to vulturif.com, for the app's "Send error report…".

Nothing leaves without the person pressing Send in the app's dialog, which
should show exactly what goes: what they typed, a way to reach them if they
want an answer, the version and "Windows 11", and — only if they leave it
ticked — the last few errors this run recorded (`details()`).

Errors are recorded in memory only, never written to disk, and gone at quit:
uncaught exceptions on any thread (the hooks `install()` sets, called from
the entry point alone) and whatever the app hands to `record()`, such as a
failed background job. Before they are shown, the home folder becomes "~"
and the user name "<user>" — a path under C:\\Users\\<name> is a name. A
file's path elsewhere can still be in an error message, which is why the
dialog shows the text and leaves the choice to the person.

The site (PublicDistribution's `showcase.views.report`) keeps the report for
the admin and stores nothing about the sender beyond it.
"""

import os
import platform
import re
import sys
import threading
import time
import traceback
from collections import deque

TIMEOUT = 20
KEEP = 10               # errors kept, newest last
MAX_DETAILS = 30_000    # characters sent; the site keeps 40 000


def os_name() -> str:
    return f"{platform.system()} {platform.release()}".strip()


def scrub(text: str) -> str:
    """Put "~" for the home folder and "<user>" for the user name."""
    home = os.path.expanduser("~")
    if home and home != "~":
        for form in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
            text = re.sub(re.escape(form), "~", text, flags=re.IGNORECASE)
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(user) > 2:   # two letters would eat half of every word
        text = re.sub(rf"(?<![\w.]){re.escape(user)}(?![\w])", "<user>", text,
                      flags=re.IGNORECASE)
    return text


class ErrorReporter:
    def __init__(self, app: str, version: str, endpoint: str, keep: int = KEEP):
        self.app = app
        self.version = version
        self.endpoint = endpoint
        self._errors = deque(maxlen=keep)
        self._lock = threading.Lock()
        self._installed = False

    def record(self, exc: BaseException | None = None, where: str = ""):
        """Keep one error for a later report. In an `except` block `exc` may
        be left out: the one being handled is taken."""
        exc = exc or sys.exc_info()[1]
        if exc is None:
            return
        text = "".join(traceback.format_exception(exc)).rstrip()
        head = time.strftime("%H:%M:%S") + (f" in {where}" if where else "")
        with self._lock:
            self._errors.append(f"[{head}]\n{text}")

    def recorded(self) -> int:
        with self._lock:
            return len(self._errors)

    def details(self) -> str:
        """What "Include technical details" sends, already scrubbed. Cut from
        the front: the newest errors matter most."""
        with self._lock:
            text = "\n\n".join(self._errors)
        return scrub(text)[-MAX_DETAILS:]

    def payload(self, description: str, contact: str = "",
                include_details: bool = True) -> dict:
        """Exactly what `send` posts — for the dialog to show beforehand."""
        return {"v": self.version, "os": os_name(),
                "description": description.strip(),
                "details": self.details() if include_details else "",
                "contact": contact.strip()}

    def send(self, description: str, contact: str = "",
             include_details: bool = True):
        """Post one report. Blocks on the network: call it from a worker.
        Raises when it did not arrive."""
        import requests
        r = requests.post(self.endpoint,
                          json=self.payload(description, contact, include_details),
                          headers={"User-Agent": f"{self.app}/{self.version}"},
                          timeout=TIMEOUT)
        if r.status_code == 429:
            raise OSError("too many reports today — please try again tomorrow")
        r.raise_for_status()

    def install(self):
        """Record uncaught exceptions too, then hand them on as before.
        Idempotent."""
        if self._installed:
            return
        self._installed = True
        before = sys.excepthook
        before_thread = threading.excepthook

        def hook(kind, value, tb):
            self.record(value, "main thread")
            before(kind, value, tb)

        def thread_hook(args):
            if args.exc_value is not None:
                self.record(args.exc_value, getattr(args.thread, "name", "thread"))
            before_thread(args)

        sys.excepthook = hook
        threading.excepthook = thread_hook
