"""The SQLite tag index: incremental scanning, manual overrides, querying."""

import os
import threading

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


def test_move_keeps_tags_without_retagging(tmp_path):
    # A file that reappears at a new path with the same bytes is recognised by
    # its content hash: the row follows it, tags and all, and the tagger is not
    # run again.
    src = tmp_path / "sub"
    src.mkdir()
    p = _img(src, "a.jpg", b"the only pixels")
    tagger = StubTagger(default=(("solo", 0.9),))
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        assert tagger.calls == 1
        idx.set_manual(p, "favourite", True)

        moved = tmp_path / "renamed.jpg"            # same bytes, new path
        p.rename(moved)
        stats = idx.scan(tmp_path, tagger)

        assert stats.moved == 1
        assert stats.tagged == 0
        assert stats.removed == 0                   # the old row was not pruned
        assert idx.count() == 1
        tags = idx.tags_for(moved)
        assert "solo" in tags and "favourite" in tags
        assert idx.tags_for(p) == {}                # nothing left at the old path
    assert tagger.calls == 1                         # the tagger never ran again


def test_duplicate_is_not_treated_as_a_move(tmp_path):
    # Two files with identical bytes, both present: the second is a genuine
    # duplicate, not a move, so it is tagged on its own path.
    _img(tmp_path, "a.jpg", b"same bytes")
    tagger = StubTagger(default=(("solo", 0.9),))
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        _img(tmp_path, "b.jpg", b"same bytes")      # duplicate appears
        stats = idx.scan(tmp_path, tagger)
        assert stats.moved == 0
        assert stats.tagged == 1
        assert idx.count() == 2


def test_ambiguous_hash_falls_back_to_tagging(tmp_path):
    # Two identical files already indexed; one vanishes and a third identical
    # file appears elsewhere. Which "moved" can't be told, so no row is
    # repointed — the new path is tagged and the vanished one is pruned.
    a = _img(tmp_path, "a.jpg", b"identical")
    _img(tmp_path, "b.jpg", b"identical")
    tagger = StubTagger(default=(("solo", 0.9),))
    with TagIndex(":memory:") as idx:
        idx.scan(tmp_path, tagger)
        assert idx.count() == 2
        a.unlink()
        _img(tmp_path, "c.jpg", b"identical")       # new identical file
        stats = idx.scan(tmp_path, tagger)
        assert stats.moved == 0
        assert stats.tagged == 1
        assert stats.removed == 1
        assert idx.count() == 2


def test_parallel_move_keeps_tags(tmp_path):
    for i in range(6):
        _img(tmp_path, f"{i}.jpg", data=f"pixels-{i}".encode())
    p0 = tmp_path / "0.jpg"
    with TagIndex(tmp_path / "x.db") as idx:
        idx.scan(tmp_path, _mk(), workers=3)
        idx.set_manual(p0, "keeper", True)
        p0.rename(tmp_path / "0-moved.jpg")
        stats = idx.scan(tmp_path, _mk(), workers=3)
        assert stats.moved == 1
        assert stats.removed == 0
        assert idx.count() == 6
        assert "keeper" in idx.tags_for(tmp_path / "0-moved.jpg")


def test_scan_on_a_worker_thread(tmp_path):
    # The GUI creates the index on one thread (a dialog) and scans on another (a
    # worker); sqlite forbids sharing a connection across threads unless the index
    # is thread-safe. This reproduces that exact arrangement.
    _img(tmp_path, "a.jpg")
    _img(tmp_path, "b.jpg")
    idx = TagIndex(tmp_path / "idx.db")                 # created on the main thread
    tagger = StubTagger({"a.jpg": [("solo", 0.9)], "b.jpg": [("duo", 0.8)]})
    err = []

    def work():
        try:
            idx.scan(tmp_path, tagger)                  # run on another thread
        except Exception as e:                           # pragma: no cover
            err.append(e)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert not err, err
    assert idx.count() == 2                             # read back on the main thread
    assert idx.query("solo") == [str(tmp_path / "a.jpg")]
    idx.close()


def test_concurrent_query_during_scan(tmp_path):
    # A query firing while a scan is mid-run must not blow up on the shared
    # connection. The stub blocks briefly per file so the two genuinely overlap.
    for i in range(6):
        _img(tmp_path, f"{i}.jpg")
    idx = TagIndex(tmp_path / "idx.db")
    gate = threading.Event()

    class SlowTagger:
        def tag(self, path):
            gate.set()
            return [("solo", 0.9)]

    err = []

    def work():
        try:
            idx.scan(tmp_path, SlowTagger())
        except Exception as e:                           # pragma: no cover
            err.append(e)

    t = threading.Thread(target=work)
    t.start()
    gate.wait(2)
    for _ in range(20):
        idx.query("solo")                               # hammer reads during the scan
    t.join()
    assert not err, err
    assert idx.count() == 6
    idx.close()


def _mk(default=(("solo", 0.9), ("rating:explicit", 0.7))):
    return StubTagger(default=default)


def test_parallel_scan_matches_sequential(tmp_path):
    for i in range(12):
        _img(tmp_path, f"{i}.jpg", data=f"pixels-{i}".encode())
    with TagIndex(tmp_path / "seq.db") as a:
        sa = a.scan(tmp_path, _mk())
    with TagIndex(tmp_path / "par.db") as b:
        sb = b.scan(tmp_path, _mk(), workers=4, make_tagger=_mk)   # own tagger per worker
    assert sa.tagged == 12 and sb.tagged == 12
    with TagIndex(tmp_path / "par.db") as b:
        assert b.count() == 12
        assert sorted(os.path.basename(p) for p in b.query("solo")) == \
               sorted(os.path.basename(p) for p in TagIndex(tmp_path / "seq.db").query("solo"))


def test_parallel_rescan_skips_and_keeps_manual(tmp_path):
    for i in range(6):
        _img(tmp_path, f"{i}.jpg", data=f"pixels-{i}".encode())
    p0 = tmp_path / "0.jpg"
    with TagIndex(tmp_path / "x.db") as idx:
        idx.scan(tmp_path, _mk(), workers=3)
        idx.set_manual(p0, "keeper", True)
        stats = idx.scan(tmp_path, _mk(), workers=3)         # nothing changed
        assert stats.skipped == 6 and stats.tagged == 0
        assert "keeper" in idx.tags_for(p0)


def test_persists_to_disk(tmp_path):
    _img(tmp_path, "a.jpg")
    db = tmp_path / "data" / "tags.db"
    with TagIndex(db) as idx:
        idx.scan(tmp_path, StubTagger())
    assert db.is_file()
    with TagIndex(db) as idx:                     # reopen
        assert idx.count() == 1
