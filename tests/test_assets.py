import hashlib

import pytest

from vulturif_kit import App, BadDownload, Download
from vulturif_kit.assets import Downloader


class FakeResponse:
    """A streamed requests response, yielding the body in two chunks."""

    def __init__(self, body: bytes, kind="application/octet-stream"):
        self.body, self.headers = body, {"Content-Type": kind}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        half = len(self.body) // 2
        yield self.body[:half]
        yield self.body[half:]


def pin(body: bytes, **kw) -> Download:
    return Download("https://x/model.bin", hashlib.sha256(body).hexdigest(),
                    len(body), **kw)


def serve(monkeypatch, body: bytes, kind="application/octet-stream"):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(body, kind))


def test_fetch_writes_the_file_when_it_matches(tmp_path, monkeypatch):
    body = b"a neural voice model" * 100
    serve(monkeypatch, body)
    dest = tmp_path / "sub" / "model.bin"      # parent made on the way
    seen = []
    out = Downloader("App/1.0").fetch(pin(body), dest,
                                      on_progress=lambda d, t: seen.append((d, t)))
    assert out == dest
    assert dest.read_bytes() == body
    assert seen[-1] == (len(body), len(body))  # progress ends at 100%
    assert not (tmp_path / "sub" / "model.bin.part").exists()


def test_a_wrong_hash_is_refused_and_leaves_nothing(tmp_path, monkeypatch):
    body = b"tampered bytes"
    serve(monkeypatch, body)
    bad = Download("https://x/model.bin", "0" * 64, len(body))
    dest = tmp_path / "model.bin"
    with pytest.raises(BadDownload, match="checksum"):
        Downloader().fetch(bad, dest)
    assert not dest.exists()
    assert not (tmp_path / "model.bin.part").exists()


def test_a_wrong_size_is_refused_before_hashing(tmp_path, monkeypatch):
    body = b"short"
    serve(monkeypatch, body)
    wrong = Download("https://x/model.bin", hashlib.sha256(body).hexdigest(), 999)
    with pytest.raises(BadDownload, match="incomplete"):
        Downloader().fetch(wrong, tmp_path / "model.bin")


def test_an_html_error_page_is_refused(tmp_path, monkeypatch):
    serve(monkeypatch, b"<html>not found</html>", "text/html; charset=utf-8")
    with pytest.raises(OSError, match="text/html"):
        Downloader().fetch(pin(b"x"), tmp_path / "model.bin")


def test_stop_between_chunks_aborts_and_cleans_up(tmp_path, monkeypatch):
    body = b"half and half"
    serve(monkeypatch, body)
    with pytest.raises(OSError, match="stopped"):
        Downloader().fetch(pin(body), tmp_path / "model.bin", stop=lambda: True)
    assert not (tmp_path / "model.bin.part").exists()


def test_size_may_be_unknown(tmp_path, monkeypatch):
    body = b"unknown length" * 10
    serve(monkeypatch, body)
    item = Download("https://x/model.bin", hashlib.sha256(body).hexdigest())  # size 0
    assert Downloader().fetch(item, tmp_path / "model.bin").read_bytes() == body


def test_have_trusts_only_the_right_size_and_hash(tmp_path):
    body = b"the real file" * 50
    dest = tmp_path / "model.bin"
    dl = Downloader()
    assert dl.have(pin(body), dest) is False       # absent
    dest.write_bytes(body + b"x")
    assert dl.have(pin(body), dest) is False        # wrong size
    dest.write_bytes(b"y" * len(body))
    assert dl.have(pin(body), dest) is False        # right size, wrong hash
    dest.write_bytes(body)
    assert dl.have(pin(body), dest) is True


def test_label_defaults_to_the_url_tail():
    assert Download("https://x/a/model-v1.bin", "0" * 64).label == "model-v1.bin"
    assert Download("https://x/a/model-v1.bin", "0" * 64, name="Voice").label == "Voice"


def test_app_wires_a_downloader_with_the_build_user_agent():
    kit = App("Meatbeat", "0.1.0")
    assert kit.downloads.headers == {"User-Agent": "Meatbeat/0.1.0"}
    assert kit.downloads is kit.downloads        # cached
