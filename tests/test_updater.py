import base64
import hashlib
from pathlib import Path

import pytest

from conftest import signed_release
from vulturif_kit import App, BadSignature, signature
from vulturif_kit.updater import Asset, Update, Updater, number, swap


def updater(keys, assets=("Demo.exe",), require=True):
    return Updater("Demo", "1.1.0", "https://x/latest/", keys.public, assets, require)


def test_verify_matches_cryptography(keys):
    message = b"Demo v1.0.0\n"
    sig = base64.b64decode(keys.sign(message.decode()))
    public = base64.b64decode(keys.public)
    assert signature.verify(public, message, sig)
    assert not signature.verify(public, message + b"x", sig)
    assert not signature.verify(public, message, sig[:-1] + bytes([sig[-1] ^ 1]))


def test_manifest_round_trip(tmp_path, keys):
    exe = tmp_path / "Demo.exe"
    exe.write_bytes(b"build")
    text = signature.manifest("Demo", "v1.2.0", [exe]).decode()
    assert text == f"Demo v1.2.0\n{hashlib.sha256(b'build').hexdigest()}  Demo.exe\n"
    release = {"version": "v1.2.0", "manifest": text, "signature": keys.sign(text)}
    assert signature.signed_hashes(release, "Demo", keys.public) == {
        "Demo.exe": hashlib.sha256(b"build").hexdigest()}


def test_signed_release_takes_hashes_from_the_manifest(keys):
    update = updater(keys).newer(signed_release(keys))
    assert update == Update("1.2.0", "notes",
                            (Asset("Demo.exe", "https://x/Demo.exe", 10, "a" * 64),))


def test_not_newer(keys):
    assert updater(keys).newer(signed_release(keys, version="v1.1.0")) is None
    assert updater(keys).newer(signed_release(keys, version="v1.0.9")) is None
    assert number("v1.10.0") > number("v1.9.3")


def test_tampered_or_foreign_manifest_is_refused(keys, ):
    rel = signed_release(keys)
    rel["manifest"] = rel["manifest"].replace("a" * 64, "b" * 64)
    with pytest.raises(BadSignature):
        updater(keys).newer(rel)
    with pytest.raises(BadSignature, match="Other"):
        updater(keys).newer(signed_release(keys, app="Other"))
    other = type(keys)()
    with pytest.raises(BadSignature):
        updater(other).newer(signed_release(keys))


def test_unsigned_only_when_allowed(keys):
    rel = signed_release(keys)
    del rel["manifest"], rel["signature"]
    assert updater(keys).newer(rel) is None
    assert updater(keys, require=False).newer(rel).assets[0].sha256 == "f" * 64


def test_every_shipped_file_must_be_signed(keys):
    rel = signed_release(keys)
    rel["assets"].append({"name": "Host.exe", "url": "u", "size": 1, "sha256": "0"})
    with pytest.raises(BadSignature):
        updater(keys, assets=("Demo.exe", "Host.exe")).newer(rel)
    # A file this app does not ship is none of its business.
    assert updater(keys).newer(rel) is not None


def test_no_main_exe_no_update(keys):
    rel = signed_release(keys, files={"Other.exe": "a" * 64})
    assert updater(keys).newer(rel) is None


def test_targets_keep_the_main_exe_name(tmp_path, keys):
    exe = tmp_path / "Demo (1).exe"
    where = updater(keys, assets=("Demo.exe", "Host.exe")).targets(exe)
    assert where == {"Demo.exe": exe, "Host.exe": tmp_path / "Host.exe"}


def test_swap_is_all_or_nothing(tmp_path):
    a, b = tmp_path / "a.exe", tmp_path / "b.exe"
    a.write_text("old a")
    b.write_text("old b")
    (tmp_path / "a.exe.new").write_text("new a")
    with pytest.raises(OSError):
        swap([(tmp_path / "a.exe.new", a), (tmp_path / "missing.new", b)])
    assert a.read_text() == "old a" and b.read_text() == "old b"

    (tmp_path / "b.exe.new").write_text("new b")
    swap([(tmp_path / "a.exe.new", a), (tmp_path / "b.exe.new", b)])
    assert a.read_text() == "new a" and (tmp_path / "a.exe.old").read_text() == "old a"


class FakeResponse:
    def __init__(self, body: bytes, kind="application/octet-stream"):
        self.body, self.headers = body, {"Content-Type": kind}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.body


def test_apply_downloads_checks_and_swaps(tmp_path, keys, monkeypatch):
    import requests
    body = b"new build"
    exe = tmp_path / "Demo.exe"
    exe.write_bytes(b"old build")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(body))
    good = Update("1.2.0", "", (Asset("Demo.exe", "u", len(body),
                                      hashlib.sha256(body).hexdigest()),))
    assert updater(keys).apply(good, exe=exe) == "1.2.0"
    assert exe.read_bytes() == body

    bad = Update("1.3.0", "", (Asset("Demo.exe", "u", len(body), "0" * 64),))
    with pytest.raises(OSError, match="checksum"):
        updater(keys).apply(bad, exe=exe)
    assert exe.read_bytes() == body

    monkeypatch.setattr(requests, "get",
                        lambda *a, **kw: FakeResponse(b"<html>", "text/html"))
    with pytest.raises(OSError, match="text/html"):
        updater(keys).apply(good, exe=exe)


def test_app_wires_the_endpoints():
    kit = App("Goonwall", "1.0.4", assets=("Goonwall.exe", "GoonwallHost.exe"))
    assert kit.updater.latest_url == "https://vulturif.com/api/goonwall/latest/"
    assert kit.updater.headers == {"User-Agent": "Goonwall/1.0.4"}
    assert kit.analytics.endpoint == "https://vulturif.com/collect/goonwall/"
    assert kit.errors.endpoint == "https://vulturif.com/report/goonwall/"
    assert kit.updater is kit.updater


def test_pinned_key_is_the_one_the_apps_ship():
    # Leashware's and Goonwall's updatesig.py pin this; changing it here
    # would lock every installed copy out of its updates.
    import vulturif_kit
    assert vulturif_kit.PUBLIC_KEY == "jtJ0ZoBR2HTQ+1w2larK2ALUSZHAeSN/W1MGdMSnPXs="
    assert len(base64.b64decode(vulturif_kit.PUBLIC_KEY)) == 32
    assert Path(signature.__file__).exists()
