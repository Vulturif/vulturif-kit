import datetime
import os

import pytest

from vulturif_kit import analytics as an
from vulturif_kit.analytics import Analytics
from vulturif_kit.errorreport import ErrorReporter, scrub


@pytest.fixture
def stats():
    s = Analytics("Demo", "1.0.0", "https://x/collect/demo/")
    s._started = True       # start() would spawn the thread and the atexit hook
    s.set_enabled(True)
    return s


def test_nothing_before_start_or_without_yes():
    s = Analytics("Demo", "1.0.0", "https://x/")
    s.set_enabled(True)
    s.track("x")
    assert s.pending() == {}
    s._started = True
    s.set_enabled(False)
    s.track("x")
    assert s.pending() == {}
    assert not Analytics("Demo", "1.0.0", "").enabled()


def test_only_fixed_names(stats):
    for bad in ("two words", "a/b", "https://x", "x\n", "a:b:c", "", "ä"):
        stats.track(bad)
    stats.track("search:pornhub", 2)
    stats.track(an.event("speed", 1.5))
    assert stats.pending() == {"search:pornhub": 2, "speed:1_5": 1}


def test_no_means_no_now(stats):
    stats.track("x")
    stats.set_enabled(False)
    assert stats.pending() == {}


def test_mark_day(stats):
    today = datetime.date(2026, 10, 31)
    assert stats.mark_day(None, today) == "2026-10-31"
    assert stats.mark_day("2026-10-31", today) is None
    assert stats.pending() == {an.ACTIVE_DAY: 1}


def test_flush_batches_without_any_id(stats, monkeypatch):
    import requests
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, json, **kw: sent.append(json))
    for i in range(an.MAX_EVENTS + 5):
        stats.track(f"e{i}")
    stats.flush()
    assert [len(b["events"]) for b in sent] == [an.MAX_EVENTS, 5]
    assert set(sent[0]) == {"v", "os", "events"}
    assert stats.pending() == {}


def test_report_records_and_scrubs(monkeypatch):
    home = os.path.expanduser("~")
    errors = ErrorReporter("Demo", "1.0.0", "https://x/report/demo/", keep=2)
    for i in range(3):
        try:
            raise ValueError(f"{home}\\file{i}.txt")
        except ValueError:
            errors.record(where="job")
    assert errors.recorded() == 2
    details = errors.details()
    assert home not in details and "~\\file2.txt" in details and "file0" not in details
    payload = errors.payload("  it broke ", include_details=False)
    assert payload["description"] == "it broke" and payload["details"] == ""


def test_scrub_user_name(monkeypatch):
    monkeypatch.setenv("USERNAME", "hendrik")
    monkeypatch.setenv("USER", "hendrik")
    assert scrub("D:\\hendrik\\x and hendrikson") == "D:\\<user>\\x and hendrikson"


def test_send_raises_on_429(monkeypatch):
    import requests

    class R:
        status_code = 429

    monkeypatch.setattr(requests, "post", lambda *a, **kw: R())
    with pytest.raises(OSError, match="too many"):
        ErrorReporter("Demo", "1", "https://x/").send("hi")
