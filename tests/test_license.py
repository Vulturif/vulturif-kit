import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import signed_release
from vulturif_kit import LICENSE_PUBLIC_KEYS, PUBLIC_KEYS, App, BadLicense, license as lic
from vulturif_kit.securestore import SecureStore
from vulturif_kit.updater import Updater

NOW = datetime.now(timezone.utc)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_key(keys, days=40, apps=("*",), tier="early", sid=42):
    """A key as vulturif.com's showcase/licenses.py issues it."""
    payload = {"kid": 1, "sid": sid, "ver": 1, "tier": tier, "apps": list(apps),
               "iat": int(NOW.timestamp()),
               "exp": int((NOW + timedelta(days=days)).timestamp())}
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"VLT1-{_b64(data)}.{_b64(keys.private.sign(data))}"


def test_parse_reads_a_signed_key(keys):
    found = lic.parse("  " + make_key(keys) + "\n", keys.public)
    assert (found.supporter, found.tier, found.apps) == (42, "early", ("*",))
    assert 39 < found.days_left() <= 40
    assert found.allows("Leashware") and found.allows("Goonwall")


@pytest.mark.parametrize("bad", ["", "hello", "VLT1-", "VLT1-abc", "VLT1-e30.AAAA"])
def test_parse_refuses_what_is_not_a_key(keys, bad):
    with pytest.raises(BadLicense):
        lic.parse(bad, keys.public)


def test_parse_refuses_a_key_signed_by_someone_else(keys):
    from conftest import Keys
    with pytest.raises(BadLicense, match="not issued"):
        lic.parse(make_key(Keys()), keys.public)
    with pytest.raises(BadLicense, match="cannot check"):
        lic.parse(make_key(keys), ())


def test_tampered_payload_is_refused(keys):
    key = make_key(keys)
    body, sig = key[5:].split(".")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    data["exp"] += 10 ** 8
    forged = f"VLT1-{_b64(json.dumps(data).encode())}.{sig}"
    with pytest.raises(BadLicense):
        lic.parse(forged, keys.public)


def test_what_a_key_allows(keys):
    assert not lic.parse(make_key(keys, days=-1), keys.public).allows("Leashware")
    only = lic.parse(make_key(keys, apps=("leashware",)), keys.public)
    assert only.allows("Leashware") and not only.allows("Goonwall")
    assert not lic.parse(make_key(keys, tier="other"), keys.public).allows("Leashware")


def store(tmp_path, keys):
    return lic.LicenseStore("Demo", tmp_path / "license.dat", keys.public,
                            "https://x/api/license/refresh/", SecureStore(b"test"))


def test_store_keeps_one_checked_key(tmp_path, keys):
    s = store(tmp_path, keys)
    assert s.load() is None and s.current() is None
    with pytest.raises(BadLicense):
        s.set("nonsense")
    assert not s.path.exists()
    s.set(make_key(keys))
    assert s.current().supporter == 42
    s.set(make_key(keys, days=-1))
    assert s.load() is not None and s.current() is None
    s.clear()
    assert s.load() is None


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(self.status_code)


def test_refresh_only_when_due(tmp_path, keys, monkeypatch):
    import requests
    s = store(tmp_path, keys)
    assert s.refresh_if_due() == "none"
    s.set(make_key(keys, days=30))
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("asked too early"))
    assert s.refresh_if_due() == "not_due"


def test_refresh_swaps_in_the_new_key(tmp_path, keys, monkeypatch):
    import requests
    s = store(tmp_path, keys)
    old = make_key(keys, days=3)
    s.set(old)
    fresh = make_key(keys, days=40, sid=42)
    sent = {}

    def post(url, headers, timeout):
        sent.update(url=url, auth=headers["Authorization"])
        return FakeResponse(200, {"key": fresh})

    monkeypatch.setattr(requests, "post", post)
    assert s.refresh_if_due() == "renewed"
    assert sent == {"url": "https://x/api/license/refresh/", "auth": f"Bearer {old}"}
    assert s.load().key == fresh


def test_refresh_keeps_the_key_when_refused_or_offline(tmp_path, keys, monkeypatch):
    import requests
    s = store(tmp_path, keys)
    s.set(make_key(keys, days=3))
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(403))
    assert s.refresh_if_due() == "inactive"
    assert s.current() is not None

    def offline(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "post", offline)
    assert s.refresh_if_due() == "failed"
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, {"key": "junk"}))
    assert s.refresh_if_due() == "failed"
    assert s.current() is not None


class FakeGet:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, url, headers, timeout, params):
        self.calls.append((headers, params))
        return FakeResponse(200, self.answer)


def test_updater_asks_the_early_channel_with_a_valid_key(keys, monkeypatch):
    import requests
    release = signed_release(keys, public_at="2026-12-01T00:00:00+00:00", channel="early")
    get = FakeGet(release)
    monkeypatch.setattr(requests, "get", get)
    updater = Updater("Demo", "1.1.0", "https://x/latest/", keys.public)
    key = make_key(keys)
    update = updater.ask(license=lic.parse(key, keys.public))
    headers, params = get.calls[0]
    assert params == {"channel": "early"}
    assert headers["Authorization"] == f"Bearer {key}"
    assert update.public_at == "2026-12-01T00:00:00+00:00"
    assert update.auth == key and key not in repr(update)


def test_updater_keeps_the_key_to_itself_otherwise(keys, monkeypatch):
    import requests
    get = FakeGet(signed_release(keys))
    monkeypatch.setattr(requests, "get", get)
    updater = Updater("Demo", "1.1.0", "https://x/latest/", keys.public)
    # Public answer to an early question: the download needs no key.
    update = updater.ask(license=lic.parse(make_key(keys), keys.public))
    assert update.public_at is None and update.auth == ""
    # Expired key: not even sent.
    updater.ask(license=lic.parse(make_key(keys, days=-1), keys.public))
    headers, params = get.calls[1]
    assert "Authorization" not in headers and params is None


def test_download_shows_the_key_only_for_early_access(tmp_path, keys, monkeypatch):
    import hashlib
    import requests
    body = b"MZ early"
    seen = []

    class Stream(FakeResponse):
        headers = {"Content-Type": "application/octet-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_content(self, chunk_size):
            yield body

    def get(url, stream, timeout, headers):
        seen.append(headers.get("Authorization"))
        return Stream(200)

    monkeypatch.setattr(requests, "get", get)
    updater = Updater("Demo", "1.1.0", "https://x/latest/", keys.public)
    from vulturif_kit.updater import Asset
    asset = Asset("Demo.exe", "https://x/Demo.exe", len(body), hashlib.sha256(body).hexdigest())
    updater.download(asset, tmp_path / "a.exe", auth="VLT1-key")
    updater.download(asset, tmp_path / "b.exe")
    assert seen == ["Bearer VLT1-key", None]


def test_app_wires_the_license_store(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    kit = App("Leashware", "1.0.0", data_dir_name="ctrlEm", license_keys=("abc",))
    assert kit.license.path == tmp_path / "ctrlEm" / "license.dat"
    assert kit.license.refresh_url == "https://vulturif.com/api/license/refresh/"
    assert kit.license.headers["User-Agent"] == "Leashware/1.0.0"
    assert App("Demo", "1.0.0").license_keys == LICENSE_PUBLIC_KEYS


def test_the_pinned_license_key_is_not_an_update_key():
    assert LICENSE_PUBLIC_KEYS
    assert not set(LICENSE_PUBLIC_KEYS) & set(PUBLIC_KEYS)
