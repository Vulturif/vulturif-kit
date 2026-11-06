"""Supporter keys: early access to new releases, checked offline.

vulturif.com issues a key to everyone who supports the apps (its
showcase/licenses.py has the format):

    VLT1-<base64url(payload JSON)>.<base64url(Ed25519 signature)>

    {"apps": ["*"], "exp": ..., "iat": ..., "kid": 1, "sid": 42,
     "tier": "early", "ver": 1}

The app checks the signature against LICENSE_PUBLIC_KEYS (app.py), with the
same plain-Python Ed25519 as the updates (signature.py), so a key works
without asking the site. What it unlocks is the early-access channel of the
updater; the site checks the key again before it hands out such a release.

A key runs out after about 40 days. `refresh_if_due()`, once a start, swaps
it for a fresh one while there are less than RENEW_DAYS left; the site only
does that while the supporter is paid up. A supporter who stops simply gets
no new key, and the app quietly goes back to the normal releases. Nothing is
ever switched off: a build already installed keeps working.

Qt-free like the rest: the app's settings dialog shows and sets the key.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import signature

PREFIX = "VLT1-"
TIMEOUT = 15
# Renew when fewer days than this are left on the key.
RENEW_DAYS = 10


class BadLicense(ValueError):
    """Not a supporter key: mistyped, cut off, or not signed by vulturif.com."""


@dataclass(frozen=True)
class License:
    key: str
    supporter: int
    tier: str
    apps: tuple
    issued: datetime
    expires: datetime

    @property
    def expired(self) -> bool:
        return self.expires <= datetime.now(timezone.utc)

    def days_left(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (self.expires - now) / timedelta(days=1)

    def allows(self, app: str, tier: str = "early") -> bool:
        """Whether this key, while it is valid, unlocks `tier` in `app`."""
        return (not self.expired and self.tier == tier
                and ("*" in self.apps or app.lower() in self.apps))


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse(key: str, public_keys) -> License:
    """The license in `key`, signature checked; an expired one too.

    Raises BadLicense for anything that is not a key one of `public_keys`
    signed. Whitespace around it (from copying) does not matter.
    """
    key = (key or "").strip()
    if not key.startswith(PREFIX) or len(key) > 1000:
        raise BadLicense("not a supporter key (they start with VLT1-)")
    body, _, sig = key[len(PREFIX):].partition(".")
    try:
        data, sig = _unb64(body), _unb64(sig)
    except (ValueError, binascii.Error) as e:
        raise BadLicense("the key is incomplete; copy all of it") from e
    if not signature.keys(public_keys):
        raise BadLicense("this build cannot check supporter keys")
    if len(sig) != 64 or not signature.verify_any(public_keys, data, sig):
        raise BadLicense("this key was not issued by vulturif.com")
    try:
        payload = json.loads(data)
        return License(
            key=key,
            supporter=int(payload["sid"]),
            tier=str(payload["tier"]),
            apps=tuple(str(a).lower() for a in payload["apps"]),
            issued=datetime.fromtimestamp(int(payload["iat"]), timezone.utc),
            expires=datetime.fromtimestamp(int(payload["exp"]), timezone.utc),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise BadLicense("not a supporter key") from e


class LicenseStore:
    """The one key an app keeps, encrypted (securestore) in its data folder."""

    def __init__(self, app: str, path, public_keys, refresh_url: str, store,
                 user_agent: str = ""):
        self.app = app
        self.path = Path(path)
        self.public_keys = public_keys
        self.refresh_url = refresh_url
        self.store = store
        self.headers = {"User-Agent": user_agent or app}

    def load(self) -> License | None:
        """The stored key, expired or not; None when there is none (or it is broken)."""
        saved = self.store.read(self.path)
        if not isinstance(saved, dict):
            return None
        try:
            return parse(saved.get("key", ""), self.public_keys)
        except BadLicense:
            return None

    def current(self) -> License | None:
        """The stored key if it unlocks early access in this app right now."""
        license = self.load()
        return license if license and license.allows(self.app) else None

    def set(self, key: str) -> License:
        """Check `key` and keep it. Raises BadLicense (nothing is stored then)."""
        license = parse(key, self.public_keys)
        self.store.write(self.path, {"key": license.key})
        return license

    def clear(self):
        self.path.unlink(missing_ok=True)

    def refresh_if_due(self, now: datetime | None = None) -> str:
        """Renew the key when it is about to run out. Blocks on the network.

        Returns what happened: "none" (no key), "not_due", "renewed",
        "inactive" (the site will not renew it: support ended; the key stays
        until it expires) or "failed" (offline and the like; next start tries
        again). Never raises.
        """
        license = self.load()
        if license is None:
            return "none"
        if license.days_left(now) >= RENEW_DAYS:
            return "not_due"
        try:
            import requests
            r = requests.post(self.refresh_url, timeout=TIMEOUT, headers={
                **self.headers, "Authorization": f"Bearer {license.key}"})
            if r.status_code == 403:
                return "inactive"
            r.raise_for_status()
            self.set(r.json()["key"])
        except Exception:
            return "failed"
        return "renewed"
