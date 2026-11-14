"""One app's view of vulturif.com: its name, version and the files it ships.

Every endpoint follows from the slug the site knows the project by, so an app
only says who it is:

    kit = App("Leashware", __version__)
    kit.updater       https://vulturif.com/api/leashware/latest/
    kit.analytics     https://vulturif.com/collect/leashware/
    kit.errors        https://vulturif.com/report/leashware/

The parts are made on first use, so an app that never reports errors never
builds a reporter.
"""

import os
from functools import cached_property
from pathlib import Path

from .analytics import Analytics
from .assets import Downloader
from .credentials import CredentialStore
from .errorreport import ErrorReporter
from .license import LicenseStore
from .securestore import SecureStore
from .updater import Updater

SITE = "https://vulturif.com"
# The keys an update may be signed with, from `vkit keygen`. Each one's
# private half is UPDATE_SIGNING_KEY, a GitHub Actions secret of the apps'
# source repos and nowhere else: someone who takes over vulturif.com still
# cannot sign an update. A signature by any of them is accepted.
#
# More than one only while the key changes. Copies already out there know
# only the keys their build pinned, so:
#   1. ship one release per app, still signed with the old key, whose build
#      pins both (this is where we are: new first, old second);
#   2. once most copies have it, switch UPDATE_SIGNING_KEY to the new key;
#   3. much later, drop the old key here. A copy that skipped step 1's
#      release has to be downloaded by hand once.
PUBLIC_KEYS = (
    "q4Hydg6++n6jwG6OQr94Q+ForxIOiseyGlpLcjUfx2Q=",    # new, from 2026-10-31
    "jtJ0ZoBR2HTQ+1w2larK2ALUSZHAeSN/W1MGdMSnPXs=",    # old, to be dropped
)
# The keys supporter keys may be signed with (license.py). Their private half
# is LICENSE_SIGNING_KEY on vulturif.com, from its scripts/make_license_key.py:
# the site issues the keys, so unlike the update key it has to hold it. Never
# the same pair as PUBLIC_KEYS. Empty: this build takes no supporter keys.
LICENSE_PUBLIC_KEYS = (
    "RQE+gtI2NqoNMHT7TVjpZFp8wX5x8vB1QLTU01LWDwg=",    # from 2026-11-06
)


class App:
    def __init__(self, name: str, version: str, slug: str | None = None,
                 assets=None, site: str = SITE, public_keys=PUBLIC_KEYS,
                 require_signature: bool = True, data_dir_name: str | None = None,
                 license_keys=None):
        self.name = name
        self.version = version
        self.slug = slug or name.lower()
        self.assets = tuple(assets or (f"{name}.exe",))   # main exe first
        self.site = site.rstrip("/")
        self.public_keys = public_keys
        self.require_signature = require_signature
        self.data_dir_name = data_dir_name or name
        self.license_keys = LICENSE_PUBLIC_KEYS if license_keys is None else license_keys

    def url(self, kind: str) -> str:
        return f"{self.site}/{kind}/{self.slug}/"

    @cached_property
    def updater(self) -> Updater:
        return Updater(self.name, self.version, f"{self.site}/api/{self.slug}/latest/",
                       self.public_keys, self.assets, self.require_signature)

    @cached_property
    def license(self) -> LicenseStore:
        """The supporter key, in <data dir>\\license.dat. The same key works in
        every app; each keeps its own copy."""
        return LicenseStore(self.name, self.data_dir / "license.dat", self.license_keys,
                            f"{self.site}/api/license/refresh/",
                            self.store(f"{self.name}/license/1".encode()),
                            user_agent=f"{self.name}/{self.version}")

    def check_update(self, channel: str | None = None) -> "Update | None":
        """Renew the supporter key if it is due, then updater.check() with it.

        The whole update check of an app in one call; on a worker thread, it
        blocks on the network. Without a valid key it is the public check."""
        self.license.refresh_if_due()
        return self.updater.check(channel, self.license.current())

    @cached_property
    def downloads(self) -> Downloader:
        """Fetch optional add-ons the app pins by hash (assets.py). The model
        behind a bundled neural voice, say — too big to ship in every build,
        wanted by only some, so downloaded on demand and checked against a hash
        baked into this signed exe."""
        return Downloader(user_agent=f"{self.name}/{self.version}")

    @cached_property
    def analytics(self) -> Analytics:
        return Analytics(self.name, self.version, self.url("collect"))

    @cached_property
    def errors(self) -> ErrorReporter:
        return ErrorReporter(self.name, self.version, self.url("report"))

    @property
    def data_dir(self) -> Path:
        """%APPDATA%\\<app>, made when first asked for."""
        path = Path(os.environ.get("APPDATA") or Path.home()) / self.data_dir_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def store(self, entropy: bytes | None = None) -> SecureStore:
        """Encrypted JSON files; the default entropy is "<App>/securestore/1"."""
        return SecureStore(entropy if entropy is not None
                           else f"{self.name}/securestore/1".encode())

    def credentials(self, fields, filename: str = "credentials.dat",
                    entropy: bytes | None = None) -> CredentialStore:
        return CredentialStore(self.data_dir / filename, fields, entropy)
