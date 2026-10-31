"""Updating the built exe from vulturif.com.

Releases are published to a public `Vulturif/<App>` repo on GitHub and
vulturif.com copies them (its release sync); the exe asks the site, so an
update is counted with the site's downloads and every file comes with its
SHA-256. No token in the exe, nothing to pull out.

How an update lands, across two launches:

1. `check()` (on a worker, once per start of the built exe) asks
   `latest_url` for the newest version and compares it with the app's own.
2. `apply()` downloads the new files next to the running ones, checks each
   one's size and SHA-256, and only then swaps them in by renaming. A signed
   release's hashes are the ones in its signed SHA256SUMS (signature.py), so
   a file swapped on the site is refused. Windows locks a running exe against
   writing and deleting but lets it be renamed: the running file becomes
   `….exe.old`, the download takes its name, and the process carries on
   undisturbed. The new build is what starts next time — or straight away,
   if the app restarts into it.
3. `check()` on the next start deletes the `.old` files.

An app may ship more than one file (Goonwall's GoonwallHost.exe). The first
asset is the main exe and is replaced *where it is and under its own name* —
a browser that saved it as "Goonwall (1).exe" must not end up with a second
copy beside it; every other asset lives beside it under its release name.
All of them swap together or not at all.

Downloads go through the site's download link, which counts them and
redirects to the file. A response that is an HTML page is refused before a
byte is saved — an error page at status 200 would otherwise only be caught
by the size check after it was written out as an exe.

Qt-free: the app's dialog decides when to ask, this only knows how.
"""

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import signature

TIMEOUT = 15
CHUNK = 1 << 18


@dataclass(frozen=True)
class Asset:
    name: str
    url: str            # the site's counting download link
    size: int
    sha256: str | None  # the site always has one; None is still allowed


@dataclass(frozen=True)
class Update:
    version: str        # "1.2.0", without the "v"
    notes: str          # the tag message
    assets: tuple       # Asset, main exe first

    @property
    def size(self) -> int:
        return sum(a.size for a in self.assets)


def number(tag: str) -> tuple:
    """'v1.2.3' -> (1, 2, 3). Both sides three-part, or (1, 2) < (1, 2, 0)
    would offer a version that is already installed."""
    return tuple(int(n) for n in re.findall(r"\d+", tag or ""))


def can_update() -> bool:
    """Only the built exe updates itself — a checkout is updated by git, and
    `sys.executable` there is the venv's Python."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


class Updater:
    def __init__(self, app: str, version: str, latest_url: str,
                 public_key: str, assets=None, require_signature: bool = True):
        self.app = app
        self.version = version
        self.latest_url = latest_url
        self.public_key = public_key
        # False only for the one release that brings signing to an app, so
        # copies already out there can still take a build that went out
        # unsigned; after that an unsigned release is no longer offered.
        self.require_signature = require_signature
        self.assets = tuple(assets or (f"{app}.exe",))
        self.headers = {"User-Agent": f"{app}/{version}"}

    @property
    def main_asset(self) -> str:
        return self.assets[0]

    def targets(self, exe=None) -> dict:
        """Asset name -> the file it replaces."""
        exe = Path(exe or sys.executable)
        return {name: exe if i == 0 else exe.with_name(name)
                for i, name in enumerate(self.assets)}

    def cleanup(self, exe=None):
        """Delete what an earlier `apply()` left: the previous build, and any
        download that never finished."""
        for path in self.targets(exe).values():
            for leftover in (".old", ".new", ".new.part"):
                try:
                    os.remove(str(path) + leftover)
                except OSError:
                    pass    # not there, or still held — the next start retries

    def newer(self, release: dict, current: str | None = None) -> Update | None:
        """The update the site's answer offers over `current`, or None.

        The answer is {"version", "notes", "assets": [{"name", "url", "size",
        "sha256"}]}, plus "manifest" and "signature" for a signed build. None
        too when the release has no main exe: a release mid-sync, or one
        published by hand without it, is not something to offer.

        A signed release's hashes come from its verified SHA256SUMS, not from
        the site's fields. A signature that does not check out raises
        BadSignature; an unsigned release is not offered while
        `require_signature` is on.
        """
        current = self.version if current is None else current
        version = release.get("version") or ""
        if not number(version) or number(version) <= number(current):
            return None
        found = {a.get("name"): a for a in release.get("assets", [])}
        if self.main_asset not in found:
            return None
        signed = signature.signed_hashes(release, self.app, self.public_key)
        if signed is None and self.require_signature:
            return None
        names = [name for name in self.assets if name in found]
        if signed is not None and not all(name in signed for name in names):
            raise signature.BadSignature(
                "a file of the release is not in its signed manifest")
        assets = tuple(
            Asset(name, found[name]["url"], int(found[name]["size"]),
                  signed[name] if signed is not None
                  else found[name].get("sha256") or None)
            for name in names)
        return Update(version.lstrip("v"), (release.get("notes") or "").strip(),
                      assets)

    def check(self, channel: str | None = None) -> Update | None:
        """Ask vulturif.com whether there is something newer. Blocks on the
        network: call it from a worker. Raises when the question could not be
        asked. `channel="beta"` counts pre-releases too."""
        if not can_update():
            return None
        self.cleanup()
        import requests
        r = requests.get(self.latest_url, headers=self.headers, timeout=TIMEOUT,
                         params={"channel": channel} if channel else None)
        r.raise_for_status()
        return self.newer(r.json())

    def download(self, asset: Asset, dest: Path, on_progress=None,
                 offset: int = 0, total: int = 0, stop=None):
        """Stream one asset to `dest`, by way of a `.part` that only takes the
        name once size and hash both match — a broken download must never be
        mistaken for a build. `on_progress(done, total)` counts across all the
        assets of an update, hence `offset` and `total`. `stop()` is asked
        between chunks: a window closing must not wait for fifty megabytes."""
        import requests
        part = Path(str(dest) + ".part")
        hasher = hashlib.sha256()
        done = 0
        with requests.get(asset.url, stream=True, timeout=60,
                          headers=self.headers) as r:
            r.raise_for_status()
            kind = r.headers.get("Content-Type", "")
            if "html" in kind:
                raise OSError(f"expected a file, got {kind.split(';')[0]}")
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK):
                    if stop and stop():
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(offset + done, total or asset.size)
        if stop and stop():
            part.unlink(missing_ok=True)
            raise OSError("stopped")
        if part.stat().st_size != asset.size:
            part.unlink(missing_ok=True)
            raise OSError(f"{asset.name} arrived incomplete "
                          f"({done} of {asset.size} bytes)")
        if asset.sha256 and hasher.hexdigest() != asset.sha256.lower():
            part.unlink(missing_ok=True)
            raise OSError(f"{asset.name} does not match its checksum")
        os.replace(part, dest)

    def apply(self, update: Update, on_progress=None, exe=None, stop=None) -> str:
        """Download `update` and swap it in. Returns the version installed.

        Call from a worker. The running process is untouched either way; on
        failure the current build stays where it was and the next start asks
        again.
        """
        where = self.targets(exe)
        self.cleanup(exe)   # a leftover .old would stand in the way of the rename
        pairs = []
        offset = 0
        for asset in update.assets:
            new = Path(str(where[asset.name]) + ".new")
            self.download(asset, new, on_progress, offset, update.size, stop)
            offset += asset.size
            pairs.append((new, where[asset.name]))
        swap(pairs)
        return update.version


def swap(pairs):
    """Rename each (new, target) into place, keeping the old one as `.old`.

    All or nothing: when one rename fails, everything already swapped is put
    back, so the app is never left half one version and half the next.
    """
    done = []
    try:
        for new, target in pairs:
            old = Path(str(target) + ".old")
            had = target.exists()
            if had:
                os.replace(target, old)
            try:
                os.replace(new, target)
            except OSError:
                if had:
                    os.replace(old, target)
                raise
            done.append((target, old, had))
    except OSError:
        for target, old, had in reversed(done):
            if had:
                os.replace(target, Path(str(target) + ".new"))
                os.replace(old, target)
            else:
                target.unlink(missing_ok=True)
        raise


def strip_bootloader_env():
    """Drop the one-file bootloader's private variables from `os.environ`.

    A one-file build unpacks itself to a temp folder and tells the Python
    inside where through `_MEIPASS2` / `_PYI_*`. A process started from here
    inherits them, believes it is already unpacked and loads its modules out
    of *our* temp folder — which is deleted the moment this process ends, so
    the restarted app dies with "No module named …". Mutates the environment,
    so only call it on the way out.
    """
    for key in [k for k in os.environ
                if k == "_MEIPASS2" or k.startswith("_PYI")]:
        del os.environ[key]
