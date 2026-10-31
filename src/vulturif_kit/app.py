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
from .credentials import CredentialStore
from .errorreport import ErrorReporter
from .securestore import SecureStore
from .updater import Updater

SITE = "https://vulturif.com"
# From PublicDistribution's scripts/make_signing_key.py — or `vkit keygen`.
# Its private half is UPDATE_SIGNING_KEY, a GitHub Actions secret of each
# app's source repo and nowhere else: someone who takes over vulturif.com
# still cannot sign an update.
PUBLIC_KEY = "jtJ0ZoBR2HTQ+1w2larK2ALUSZHAeSN/W1MGdMSnPXs="


class App:
    def __init__(self, name: str, version: str, slug: str | None = None,
                 assets=None, site: str = SITE, public_key: str = PUBLIC_KEY,
                 require_signature: bool = True, data_dir_name: str | None = None):
        self.name = name
        self.version = version
        self.slug = slug or name.lower()
        self.assets = tuple(assets or (f"{name}.exe",))   # main exe first
        self.site = site.rstrip("/")
        self.public_key = public_key
        self.require_signature = require_signature
        self.data_dir_name = data_dir_name or name

    def url(self, kind: str) -> str:
        return f"{self.site}/{kind}/{self.slug}/"

    @cached_property
    def updater(self) -> Updater:
        return Updater(self.name, self.version, f"{self.site}/api/{self.slug}/latest/",
                       self.public_key, self.assets, self.require_signature)

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
