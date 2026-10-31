"""The signed SHA256SUMS a release must match before an update is taken.

vulturif.com hands out every file's SHA-256, but a hash from the same server
as the file proves nothing once that server is taken over: whoever swaps the
exe swaps its hash too. So every release also carries SHA256SUMS (first line
"<App> v<version>", then "<sha256>  <file name>" per file), signed with an
Ed25519 key that exists only as the UPDATE_SIGNING_KEY secret. The site passes
both on untouched; the updater checks the signature against the pinned public
key and then takes the hashes from the signed text rather than from the
site's own fields.

`manifest()` writes that text (the release CLI calls it), `signed_hashes()`
reads it back, so the format is defined here and nowhere else.

Verification is the RFC 8032 reference algorithm in plain Python: it needs no
secret, so constant time does not matter, and it keeps a native crypto
library out of the one-file exe. It takes a few milliseconds.
"""

import base64
import hashlib
from pathlib import Path


class BadSignature(OSError):
    """A release came with a manifest that is not ours. Never install it."""


_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _add(a, b):
    # Extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, x*y = T/Z.
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, point):
    result = (0, 1, 1, 0)
    while s:
        if s & 1:
            result = _add(result, point)
        point = _add(point, point)
        s >>= 1
    return result


def _equal(a, b):
    return not ((a[0] * b[2] - b[0] * a[2]) % _P
                or (a[1] * b[2] - b[1] * a[2]) % _P)


def _decode(data):
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_BASE = (_GX, _GY, 1, _GX * _GY % _P)


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether `signature` is `public`'s Ed25519 signature of `message`."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a, r = _decode(public), _decode(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(),
                       "little") % _L
    return _equal(_mul(s, _BASE), _add(r, _mul(h, a)))


def manifest(app: str, tag: str, files) -> bytes:
    """SHA256SUMS for `files`: "<app> <tag>", then "<sha256>  <name>" each."""
    lines = [f"{app} {tag}"]
    for name in files:
        lines.append(f"{hashlib.sha256(Path(name).read_bytes()).hexdigest()}  "
                     f"{Path(name).name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def signed_hashes(release: dict, app: str, public_key: str):
    """{file name: sha256} from the release's verified SHA256SUMS.

    None when there is nothing to go by: no key pinned, or a release without
    a manifest (the caller decides what an unsigned release is worth). Raises
    BadSignature when a manifest is there but does not check out, or names
    another app or version than the one offered.
    """
    text, signature = release.get("manifest"), release.get("signature")
    if not public_key or not (text and signature):
        return None
    try:
        sig = base64.b64decode(signature, validate=True)
    except ValueError:
        raise BadSignature("the release signature is not valid base64") from None
    if not verify(base64.b64decode(public_key), text.encode("utf-8"), sig):
        raise BadSignature("the release signature does not match")
    first, *lines = text.splitlines()
    version = (release.get("version") or "").lstrip("v")
    if first.strip() != f"{app} v{version}":
        raise BadSignature(f"the signed manifest is for {first.strip()!r}")
    hashes = {}
    for line in lines:
        digest, _, name = line.strip().partition("  ")
        if name:
            hashes[name] = digest.lower()
    return hashes
