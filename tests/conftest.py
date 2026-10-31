import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


class Keys:
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)).decode()

    @property
    def secret(self) -> str:
        from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat
        return base64.b64encode(self.private.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()

    def sign(self, text: str) -> str:
        return base64.b64encode(self.private.sign(text.encode())).decode()


@pytest.fixture
def keys():
    return Keys()


def signed_release(keys, app="Demo", version="v1.2.0", files=None, **extra):
    """A /latest/ answer for `files` {name: sha256}, signed with `keys`."""
    files = files or {"Demo.exe": "a" * 64}
    text = f"{app} {version}\n" + "".join(f"{h}  {n}\n" for n, h in files.items())
    return {"version": version, "notes": " notes ",
            "manifest": text, "signature": keys.sign(text),
            "assets": [{"name": n, "url": f"https://x/{n}", "size": 10, "sha256": "f" * 64}
                       for n in files], **extra}
