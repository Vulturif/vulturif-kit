"""The SQLite tag index: incremental scanning, manual overrides, querying."""

import os

from vulturif_kit.tagging.index import TagIndex


class StubTagger:
    """Stands in for the WD14 engine: returns per-file tags and counts calls so
    a test can prove a scan skipped a file instead of re-tagging it."""

    def __init__(self, by_name=None, default=(("solo", 0.9),)):
        self.by_name = by_name or {}
        self.default = list(default)
        self.calls = 0

    def tag(self, path):
        self.calls += 1
        return self.by_name.get(os.path.basename(str(path)), self.default)


def _img(dirpath, name, data=b"pixels"):
    p = dirpath / name
    p.write_bytes(data)
    return p


def test_first_scan_tags_everything(tmp_path):
    _img(tmp_path, "a.jpg")
    _img(tmp_path, "b.png")
    _img(tmp_path, "notes.txt")                 # not an image
    tagger = StubTagger()
    with TagIndex(":memory:") as idx:
        stats = idx.scan(tmp_path, tagger)
        assert stats.seen == 2                   # the .txt is skipped by extension
        assert stats.tagged == 2
        assert idx.count() == 2
    assert tagger.calls == 2


def test_rescan_skips_unchanged(tmp_path):
    _img(tmp_path, "a.jpg")
    tagger = StubTagger()
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        assert tagger.calls == 1
        stats = idx.scan(tmp_path, tagger)       # nothing changed
        assert stats.skipped == 1
        assert stats.tagged == 0
    assert tagger.calls == 1                      # the tagger was not run again


def test_touch_without_edit_is_not_retagged(tmp_path):
    p = _img(tmp_path, "a.jpg")
    tagger = StubTagger()
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        st = p.stat()
        os.utime(p, (st.st_atime + 100, st.st_mtime + 100))   # same bytes, new mtime
        stats = idx.scan(tmp_path, tagger)
        assert stats.touched == 1
        assert stats.tagged == 0
    assert tagger.calls == 1


def test_edit_retags(tmp_path):
    p = _img(tmp_path, "a.jpg", b"pixels")
    tagger = StubTagger()
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        p.write_bytes(b"different and longer pixels")          # real content change
        stats = idx.scan(tmp_path, tagger)
        assert stats.tagged == 1
    assert tagger.calls == 2


def test_manual_tag_survives_rescan(tmp_path):
    p = _img(tmp_path, "a.jpg")
    tagger = StubTagger(default=(("solo", 0.9),))
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        idx.set_manual(p, "favourite", True)
        p.write_bytes(b"changed so it re-tags")
        idx.scan(tmp_path, tagger)
        tags = idx.tags_for(p)
        assert "favourite" in tags               # manual row kept
        assert "solo" in tags                     # auto rows refreshed


def test_manual_removal_reads_as_absent(tmp_path):
    p = _img(tmp_path, "a.jpg")
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, StubTagger(default=(("solo", 0.9), ("blurry", 0.8))))
        idx.set_manual(p, "blurry", False)
        assert "blurry" not in idx.tags_for(p)
        assert "blurry" not in idx.query("blurry")


def test_query_filters_the_library(tmp_path):
    _img(tmp_path, "one.jpg")
    _img(tmp_path, "two.jpg")
    _img(tmp_path, "three.jpg")
    tagger = StubTagger({
        "one.jpg":   [("blonde_hair", 0.9), ("1girl", 0.8)],
        "two.jpg":   [("blonde_hair", 0.9), ("bikini", 0.7)],
        "three.jpg": [("black_hair", 0.9), ("1girl", 0.8)],
    })
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        hits = idx.query("blonde_hair -bikini")
        assert [os.path.basename(h) for h in hits] == ["one.jpg"]
        both = idx.query("1girl")
        assert sorted(os.path.basename(h) for h in both) == ["one.jpg", "three.jpg"]


def test_prune_drops_vanished_files(tmp_path):
    p = _img(tmp_path, "gone.jpg")
    _img(tmp_path, "stays.jpg")
    tagger = StubTagger()
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        assert idx.count() == 2
        p.unlink()
        stats = idx.scan(tmp_path, tagger)
        assert stats.removed == 1
        assert idx.count() == 1


def test_persists_to_disk(tmp_path):
    _img(tmp_path, "a.jpg")
    db = tmp_path / "data" / "tags.db"
    with TagIndex(db) as idx:
        idx.scan(tmp_path, StubTagger())
    assert db.is_file()
    with TagIndex(db) as idx:                     # reopen
        assert idx.count() == 1
