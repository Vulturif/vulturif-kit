"""The on-disk tag index: a SQLite database mapping image files to their tags.

A library of tens of thousands of images is far too much to re-tag every time,
so the index is **incremental**. Each scan walks the chosen folders and, for
each image, compares it to what the database already knows: if the file's size
and modification time are unchanged, it is skipped untouched; only a new or
changed file is hashed and re-tagged. A content hash is the final arbiter, so a
file that was merely *touched* (new mtime, same bytes) costs a hash but not a
re-tag.

Tags the user fixed by hand are marked ``manual = 1`` and a re-scan never
clobbers them — only the auto rows from the tagger are replaced.

The tagger is injected, not imported: :meth:`TagIndex.scan` takes any object
with ``.tag(path) -> [(tag, confidence), ...]`` (the WD14 :class:`~.engine.Tagger`
in production, a trivial stub in tests). That keeps this module pure stdlib —
``sqlite3`` and ``hashlib`` only — and testable without onnxruntime.

Filtering reuses :mod:`.query`: the rule34 text is parsed once, the inverted
``tags`` index pre-selects candidate files from the query's positive tags, and
each candidate is confirmed with the exact :func:`~.query.evaluate`.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import query

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})

_CHUNK = 1 << 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY,
    path       TEXT UNIQUE NOT NULL,
    hash       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime      REAL NOT NULL,
    width      INTEGER,
    height     INTEGER,
    scanned_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    tag        TEXT NOT NULL,
    confidence REAL NOT NULL,
    manual     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
"""


@dataclass
class ScanStats:
    """What a :meth:`TagIndex.scan` did, for the progress line and a summary."""
    seen: int = 0          # image files walked
    skipped: int = 0       # unchanged, left alone
    tagged: int = 0        # new or changed, re-tagged
    touched: int = 0       # same bytes, new mtime — hashed but not re-tagged
    failed: int = 0        # could not read / tag
    removed: int = 0       # pruned: gone from disk
    stopped: bool = False  # the stop() callback cut it short


def _sha256(path, chunk=_CHUNK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class TagIndex:
    """A SQLite-backed tag index. Use as a context manager, or remember to
    :meth:`close`."""

    def __init__(self, db_path):
        self.path = str(db_path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # The scan runs on a background thread while queries come from the GUI
        # thread, so one connection has to cross threads. sqlite forbids that by
        # default; ``check_same_thread=False`` allows it and ``_lock`` serialises
        # every access so the threads never touch the connection at once. WAL
        # lets a query read while a scan is mid-write (ignored for ``:memory:``).
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("PRAGMA foreign_keys = ON")
            self.db.execute("PRAGMA busy_timeout = 5000")
            if self.path != ":memory:":
                self.db.execute("PRAGMA journal_mode = WAL")
            self.db.executescript(_SCHEMA)
            self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        with self._lock:
            self.db.close()

    # ── scanning ───────────────────────────────────────────────────────────

    def scan(self, roots, tagger, *, workers=1, make_tagger=None,
             exts=IMAGE_EXTS, prune=True, on_progress=None, stop=None) -> ScanStats:
        """Index every image under ``roots`` (a path or an iterable of paths),
        tagging new or changed files with ``tagger``.

        ``on_progress(stats, path)`` is called as each file is handled and
        ``stop()`` is polled between files, so a closing window need not wait for
        a whole library. With ``prune`` on, rows for files that no longer exist
        **under the scanned roots** are deleted.

        With ``workers`` > 1 the slow part — hashing and tagging — runs in a
        thread pool while every database write stays on this thread (so the db
        is single-writer and uncontended). ``make_tagger``, a zero-arg callable
        returning a fresh :class:`~.engine.Tagger`, gives each worker its own
        ONNX session so the inferences genuinely run at once; without it the one
        ``tagger`` is shared across the pool (less speed-up, but still overlaps
        hashing with inference).
        """
        if isinstance(roots, (str, os.PathLike)):
            roots = [roots]
        roots = [Path(r) for r in roots]
        stats = ScanStats()
        exts = frozenset(e.lower() for e in exts)
        seen_paths = set()

        if workers and workers > 1:
            self._scan_parallel(roots, exts, tagger, workers, make_tagger,
                                stats, seen_paths, on_progress, stop)
        else:
            for path in self._walk(roots, exts):
                if stop and stop():
                    stats.stopped = True
                    break
                seen_paths.add(str(path))
                stats.seen += 1
                try:
                    self._index_one(path, tagger, stats)
                except Exception:
                    stats.failed += 1
                if on_progress:
                    on_progress(stats, path)

        if prune and not stats.stopped:
            stats.removed = self._prune(roots, seen_paths)
        with self._lock:
            self.db.commit()
        return stats

    def _scan_parallel(self, roots, exts, tagger, workers, make_tagger,
                       stats, seen_paths, on_progress, stop):
        """Hash + tag in a thread pool; keep every db write on this thread."""
        from concurrent.futures import ThreadPoolExecutor

        tl = threading.local()

        def worker_tagger():
            tg = getattr(tl, "tg", None)
            if tg is None:
                tg = tl.tg = (make_tagger() if make_tagger else tagger)
            return tg

        def heavy(path, row):
            # Pool thread: hash, then tag only if the bytes changed. No db here.
            digest = _sha256(path)
            if row is not None and row["hash"] == digest:
                return digest, None                 # touched: same bytes
            if stop and stop():
                return digest, "stopped"
            return digest, worker_tagger().tag(path)

        pending = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for path in self._walk(roots, exts):
                if stop and stop():
                    stats.stopped = True
                    break
                seen_paths.add(str(path))
                stats.seen += 1
                st = path.stat()
                with self._lock:
                    row = self.db.execute(
                        "SELECT id, hash, size, mtime FROM files WHERE path = ?",
                        (str(path),)).fetchone()
                # Fast path: unchanged → skip without hashing or a worker.
                if row and row["size"] == st.st_size and row["mtime"] == st.st_mtime:
                    stats.skipped += 1
                    if on_progress:
                        on_progress(stats, path)
                    continue
                pending.append((pool.submit(heavy, path, row), path, row))
                # Keep at most ~2x workers in flight so a huge library is not all
                # hashed into memory ahead of the single writer.
                if len(pending) >= workers * 2:
                    self._collect(pending.pop(0), stats, on_progress)
            for item in pending:
                self._collect(item, stats, on_progress)

    def _collect(self, item, stats: ScanStats, on_progress):
        fut, path, row = item
        try:
            digest, pairs = fut.result()
        except Exception:
            stats.failed += 1
        else:
            if pairs != "stopped":
                self._write_result(path, row, digest, pairs, stats)
        if on_progress:
            on_progress(stats, path)

    def _write_result(self, path, row, digest, pairs, stats: ScanStats):
        """Write one worker's result to the db (this thread only)."""
        st = path.stat()
        now = time.time()
        with self._lock:
            if pairs is None:                       # touched: refresh stat, keep tags
                self.db.execute(
                    "UPDATE files SET size = ?, mtime = ?, scanned_at = ? WHERE id = ?",
                    (st.st_size, st.st_mtime, now, row["id"]))
                stats.touched += 1
                return
            if row:
                file_id = row["id"]
                self.db.execute(
                    "UPDATE files SET hash = ?, size = ?, mtime = ?, scanned_at = ? "
                    "WHERE id = ?",
                    (digest, st.st_size, st.st_mtime, now, file_id))
                self.db.execute(
                    "DELETE FROM tags WHERE file_id = ? AND manual = 0", (file_id,))
            else:
                cur = self.db.execute(
                    "INSERT INTO files (path, hash, size, mtime, scanned_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(path), digest, st.st_size, st.st_mtime, now))
                file_id = cur.lastrowid
            manual = {r["tag"] for r in self.db.execute(
                "SELECT tag FROM tags WHERE file_id = ? AND manual = 1", (file_id,))}
            self.db.executemany(
                "INSERT OR IGNORE INTO tags (file_id, tag, confidence, manual) "
                "VALUES (?, ?, ?, 0)",
                [(file_id, t.lower(), float(c)) for t, c in pairs
                 if t.lower() not in manual])
            stats.tagged += 1

    def _walk(self, roots, exts):
        for root in roots:
            if root.is_file():
                if root.suffix.lower() in exts:
                    yield root
                continue
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    if os.path.splitext(n)[1].lower() in exts:
                        yield Path(dirpath) / n

    def _index_one(self, path, tagger, stats: ScanStats):
        st = path.stat()
        key = str(path)
        with self._lock:
            row = self.db.execute(
                "SELECT id, hash, size, mtime FROM files WHERE path = ?", (key,)
            ).fetchone()

        # Fast path: known file, same size and mtime → assume unchanged.
        if row and row["size"] == st.st_size and row["mtime"] == st.st_mtime:
            stats.skipped += 1
            return

        digest = _sha256(path)                   # slow I/O — kept outside the lock

        if row and row["hash"] == digest:
            # Same bytes, only the mtime moved: refresh the stat, keep the tags.
            with self._lock:
                self.db.execute(
                    "UPDATE files SET size = ?, mtime = ?, scanned_at = ? WHERE id = ?",
                    (st.st_size, st.st_mtime, time.time(), row["id"]))
            stats.touched += 1
            return

        pairs = tagger.tag(path)                 # the slow part (inference) — also
        now = time.time()                        # outside the lock, so a query can
        with self._lock:                         # run against the db between files
            if row:
                file_id = row["id"]
                self.db.execute(
                    "UPDATE files SET hash = ?, size = ?, mtime = ?, scanned_at = ? "
                    "WHERE id = ?",
                    (digest, st.st_size, st.st_mtime, now, file_id))
                # Replace auto rows only; manual corrections survive the re-scan.
                self.db.execute(
                    "DELETE FROM tags WHERE file_id = ? AND manual = 0", (file_id,))
            else:
                cur = self.db.execute(
                    "INSERT INTO files (path, hash, size, mtime, scanned_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, digest, st.st_size, st.st_mtime, now))
                file_id = cur.lastrowid

            manual = {r["tag"] for r in self.db.execute(
                "SELECT tag FROM tags WHERE file_id = ? AND manual = 1", (file_id,))}
            self.db.executemany(
                "INSERT OR IGNORE INTO tags (file_id, tag, confidence, manual) "
                "VALUES (?, ?, ?, 0)",
                [(file_id, t.lower(), float(c)) for t, c in pairs
                 if t.lower() not in manual])
        stats.tagged += 1

    def _prune(self, roots, seen_paths) -> int:
        roots_s = [str(r) for r in roots]
        removed = 0
        with self._lock:
            rows = self.db.execute("SELECT id, path FROM files").fetchall()
            for r in rows:
                p = r["path"]
                if p in seen_paths:
                    continue
                if any(p == rs or p.startswith(rs + os.sep) for rs in roots_s):
                    self.db.execute("DELETE FROM files WHERE id = ?", (r["id"],))
                    removed += 1
        return removed

    # ── manual corrections ─────────────────────────────────────────────────

    def set_manual(self, path, tag, present: bool = True):
        """Record a hand correction: add ``tag`` to the image, or remove it.
        Marked ``manual = 1`` so the next scan leaves it be."""
        tag = tag.lower()
        with self._lock:
            row = self.db.execute(
                "SELECT id FROM files WHERE path = ?", (str(path),)).fetchone()
            if not row:
                raise KeyError(f"not indexed: {path}")
            if present:
                self.db.execute(
                    "INSERT INTO tags (file_id, tag, confidence, manual) "
                    "VALUES (?, ?, 1.0, 1) "
                    "ON CONFLICT(file_id, tag) DO UPDATE SET manual = 1",
                    (row["id"], tag))
            else:
                # A manual removal is a manual row the query layer treats as
                # absent; record the override as confidence 0.
                self.db.execute(
                    "INSERT INTO tags (file_id, tag, confidence, manual) "
                    "VALUES (?, ?, 0.0, 1) "
                    "ON CONFLICT(file_id, tag) DO UPDATE SET manual = 1, confidence = 0.0",
                    (row["id"], tag))
            self.db.commit()

    # ── reading ────────────────────────────────────────────────────────────

    def tags_for(self, path) -> dict:
        """``{tag: confidence}`` for one file. A manual removal (confidence 0) is
        excluded so it reads as absent."""
        with self._lock:
            row = self.db.execute(
                "SELECT id FROM files WHERE path = ?", (str(path),)).fetchone()
            if not row:
                return {}
            return {r["tag"]: r["confidence"] for r in self.db.execute(
                "SELECT tag, confidence FROM tags WHERE file_id = ? AND confidence > 0",
                (row["id"],))}

    def query(self, text: str) -> list:
        """Every indexed path matching the rule34 query ``text``, as a sorted
        list. Pre-selects candidates from the query's positive tags via the
        inverted index, then confirms each with the exact evaluator."""
        node = query.parse(text)
        wanted = query.positive_tags(node)

        with self._lock:
            if wanted:
                marks = ",".join("?" * len(wanted))
                rows = self.db.execute(
                    f"SELECT DISTINCT file_id FROM tags "
                    f"WHERE confidence > 0 AND tag IN ({marks})", tuple(wanted))
                candidates = [r["file_id"] for r in rows]
            else:
                candidates = [r["id"] for r in
                              self.db.execute("SELECT id FROM files")]

            out = []
            for file_id in candidates:
                rows = self.db.execute(
                    "SELECT tag, confidence FROM tags WHERE file_id = ? AND confidence > 0",
                    (file_id,)).fetchall()
                tags = {r["tag"] for r in rows}
                scores = {r["tag"]: r["confidence"] for r in rows}
                if query.evaluate(node, tags, scores):
                    p = self.db.execute(
                        "SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
                    if p:
                        out.append(p["path"])
        out.sort()
        return out

    def all_tags(self, prefix: str = "", limit: int = 50) -> list:
        """``[(tag, count), ...]`` over the library, most common first — for tag
        autocomplete. ``prefix`` filters to tags starting with it."""
        with self._lock:
            if prefix:
                rows = self.db.execute(
                    "SELECT tag, COUNT(*) c FROM tags WHERE confidence > 0 AND tag LIKE ? "
                    "GROUP BY tag ORDER BY c DESC LIMIT ?",
                    (prefix.lower() + "%", limit)).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT tag, COUNT(*) c FROM tags WHERE confidence > 0 "
                    "GROUP BY tag ORDER BY c DESC LIMIT ?", (limit,)).fetchall()
        return [(r["tag"], r["c"]) for r in rows]

    def count(self) -> int:
        """How many files are indexed."""
        with self._lock:
            return self.db.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
