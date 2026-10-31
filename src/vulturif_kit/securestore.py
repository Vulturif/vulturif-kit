"""Small JSON files that are not readable as text.

On Windows that is DPAPI (CryptProtectData), which is the right tool for
exactly this: the key is the user's own login, managed by Windows, so there is
no key file lying next to the data to give it away. Another account on the
same PC, or anyone who copies the file to another machine, gets bytes they
cannot read. The flip side is the same sentence read the other way: the files
do not travel. Copied to a new PC they read as nothing, and the app starts
over.

Elsewhere there is no DPAPI, and the data is only compressed — not readable at
a glance, but not protected either. The apps are Windows apps; this keeps them
running on other systems rather than pretending to secure them.

The entropy is mixed into the encryption, so another program running as the
same user cannot decrypt the blobs by simply handing them to DPAPI. It is part
of the key, not a label: every app keeps the one it started with for ever
(Goonwall's still says VideoSelector), or what is on disk turns unreadable.
"""

import json
import os
import sys
import zlib
from pathlib import Path

MAGIC_DPAPI = b"VSEC1\n"    # DPAPI blob follows
MAGIC_PLAIN = b"VSZ1\n"     # zlib-compressed JSON follows (non-Windows)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def _in_blob(data: bytes):
        # The buffer is returned too: the blob only points at it, and it has
        # to outlive the call.
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data: bytes, entropy: bytes | None) -> bytes:
        src, _keep = _in_blob(data)
        # None, not b"": Leashware's credentials were written with no entropy
        # at all, and that is what reads them back.
        ent, _keep2 = _in_blob(entropy) if entropy is not None else (None, None)
        out = _Blob()
        if not fn(ctypes.byref(src), None,
                  ctypes.byref(ent) if ent is not None else None, None, None,
                  _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise OSError(ctypes.FormatError())
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def protect(data: bytes, entropy: bytes | None = None) -> bytes:
        """The raw DPAPI blob for `data`. Raises OSError."""
        return _call(_crypt32.CryptProtectData, data, entropy)

    def unprotect(blob: bytes, entropy: bytes | None = None) -> bytes:
        """Back from `protect`. Raises OSError for another user's blob."""
        return _call(_crypt32.CryptUnprotectData, blob, entropy)

    HAS_ENCRYPTION = True
else:
    def protect(data: bytes, entropy: bytes | None = None) -> bytes:
        raise OSError("no DPAPI on this platform")

    unprotect = protect
    HAS_ENCRYPTION = False


class SecureStore:
    def __init__(self, entropy: bytes | None):
        self.entropy = entropy

    def dumps(self, obj) -> bytes:
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        if HAS_ENCRYPTION:
            return MAGIC_DPAPI + protect(data, self.entropy)
        return MAGIC_PLAIN + zlib.compress(data)

    def loads(self, data: bytes, raw: bool = False):
        """Decode what dumps() wrote. Raises ValueError for anything else.

        `raw` also takes a bare DPAPI blob without the magic line, which is
        what Leashware's credentials.dat was before the kit.
        """
        if data.startswith(MAGIC_DPAPI):
            blob = data[len(MAGIC_DPAPI):]
        elif data.startswith(MAGIC_PLAIN):
            try:
                return json.loads(zlib.decompress(data[len(MAGIC_PLAIN):]).decode("utf-8"))
            except zlib.error as e:
                raise ValueError(str(e)) from e
        elif raw and HAS_ENCRYPTION:
            blob = data
        else:
            raise ValueError("not a securestore file")
        try:
            plain = unprotect(blob, self.entropy)
        except OSError as e:     # another user's, or another machine's
            raise ValueError(f"cannot decrypt: {e}") from e
        return json.loads(plain.decode("utf-8"))

    def read(self, path, legacy=None, raw: bool = False):
        """The object stored at `path`, or None if there is none or it is
        unreadable.

        `legacy` names the plain JSON file an older version kept instead. If
        only that exists it is read, rewritten encrypted at `path` and deleted
        — the point of encrypting is lost while a readable copy is still lying
        around.
        """
        path = Path(path)
        if path.exists():
            try:
                return self.loads(path.read_bytes(), raw=raw)
            except (OSError, ValueError):
                return None
        if legacy is None or not Path(legacy).exists():
            return None
        legacy = Path(legacy)
        try:
            obj = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            self.write(path, obj)
            legacy.unlink()
        except OSError:
            pass        # still usable this run; migration is retried next start
        return obj

    def write(self, path, obj):
        """Store `obj` at `path`, atomically. Raises OSError on failure."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(self.dumps(obj))
        os.replace(tmp, path)       # never a half-written file
