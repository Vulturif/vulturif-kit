"""API keys and logins kept in one encrypted file (DPAPI, see securestore).

Precedence rule: once the store exists it is authoritative and .env is ignored
entirely. .env (or whatever is in the environment) is only ever read to seed
the very first run, so that clearing a field in the settings screen actually
sticks instead of silently reverting to whatever .env still says.

The fields are the app's: (env var, label, help, secret?), which is also the
order its settings screen shows them in. A file that won't decrypt — another
account, another machine, corrupt — is treated as absent rather than as an
error, so it behaves exactly like a first run.
"""

import os
from typing import NamedTuple

from .securestore import SecureStore


class Field(NamedTuple):
    key: str            # also the environment variable that seeds it
    label: str
    help: str = ""
    secret: bool = True


class CredentialStore:
    def __init__(self, path, fields, entropy: bytes | None = None):
        self.path = path
        self.fields = tuple(Field(*f) for f in fields)
        self.keys = tuple(f.key for f in self.fields)
        self._store = SecureStore(entropy)

    def load_stored(self) -> dict | None:
        """Stored credentials, or None when there is no usable store yet.

        None (rather than {}) is what tells `resolve` to fall back to the
        environment. Reads the bare DPAPI blob Leashware wrote before the kit
        too; the next `save_stored` rewrites it in the kit's format.
        """
        data = self._store.read(self.path, raw=True)
        return data if isinstance(data, dict) else None

    def save_stored(self, creds: dict):
        self._store.write(self.path,
                          {k: (creds.get(k) or "") for k in self.keys})

    def exists(self) -> bool:
        return self.load_stored() is not None

    def resolve(self, env=None) -> dict:
        """The credentials the app should actually use."""
        stored = self.load_stored()
        if stored is not None:
            return {k: (stored.get(k) or "") for k in self.keys}
        env = os.environ if env is None else env
        return {k: env.get(k, "") for k in self.keys}
