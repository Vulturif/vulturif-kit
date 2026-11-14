"""Fetch an optional add-on on demand, verified against a hash the app pins.

Some files are too big to ship inside every build and only some users want
them — a neural voice model, a large asset pack. This downloads one such file
to a destination the app chooses, streaming it to a ``.part`` that only takes
the final name once its size and SHA-256 match what the app pinned. The pinned
hash lives in the app's own source and so travels inside the signed exe, so a
file swapped on the host is refused exactly as a tampered update is.

Kept apart from ``updater.py`` on purpose: the updater swaps the app's own
executables across launches against a *signed release manifest*; this fetches a
single pinned add-on a feature needs, by its own hash, and never touches the
exe. Qt-free: the app's own dialog decides when to fetch and shows progress,
this only knows how.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 60
CHUNK = 1 << 18


class BadDownload(OSError):
    """A fetched file did not match what the app pinned. Never use it."""


@dataclass(frozen=True)
class Download:
    """One pinned file: where to get it and what it must come out as.

    ``sha256`` is the whole guarantee and is always checked. ``size`` is
    optional (0 = unknown); when given it is checked first, so a wrong file
    fails on a cheap stat before it is hashed, and progress can show a total.
    ``name`` is only for messages and defaults to the URL's last segment.
    """

    url: str
    sha256: str
    size: int = 0
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.url.rsplit("/", 1)[-1]


class Downloader:
    """Fetch pinned add-ons. One per app; ``user_agent`` names the build."""

    def __init__(self, user_agent: str = ""):
        self.headers = {"User-Agent": user_agent} if user_agent else {}

    def have(self, item: Download, dest) -> bool:
        """Whether ``dest`` already *is* the pinned file — right size and hash —
        so a half-written or stale file is re-fetched rather than trusted. Reads
        the whole file to hash it, so call it to decide whether to skip a
        download, not on every repaint."""
        dest = Path(dest)
        if not dest.is_file():
            return False
        if item.size and dest.stat().st_size != item.size:
            return False
        return _sha256(dest) == item.sha256.lower()

    def fetch(self, item: Download, dest, on_progress=None, stop=None) -> Path:
        """Stream ``item`` to ``dest`` by way of a ``.part`` that only takes the
        name once size and hash both match — a broken download must never be
        mistaken for the real file. ``on_progress(done, total)`` is called as
        bytes arrive; ``stop()`` is asked between chunks so a closing window
        need not wait for a hundred megabytes.

        Raises :class:`BadDownload` when the bytes do not match the pin, or
        ``OSError`` on a network or file error. Returns ``dest``.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = Path(str(dest) + ".part")
        import requests

        hasher = hashlib.sha256()
        done = 0
        with requests.get(item.url, stream=True, timeout=TIMEOUT,
                          headers=self.headers) as r:
            r.raise_for_status()
            kind = r.headers.get("Content-Type", "")
            if "html" in kind:
                # An error page served at 200 would otherwise be written out as
                # if it were the file and only caught by the size/hash check.
                raise OSError(f"expected a file, got {kind.split(';')[0]}")
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK):
                    if stop and stop():
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, item.size or done)
        if stop and stop():
            part.unlink(missing_ok=True)
            raise OSError("stopped")
        if item.size and part.stat().st_size != item.size:
            part.unlink(missing_ok=True)
            raise BadDownload(f"{item.label} arrived incomplete "
                              f"({done} of {item.size} bytes)")
        if hasher.hexdigest() != item.sha256.lower():
            part.unlink(missing_ok=True)
            raise BadDownload(f"{item.label} does not match its checksum")
        os.replace(part, dest)
        return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()
