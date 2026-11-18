"""Local media tagging: tag an image library and filter it with booru syntax.

An optional, lazily-installed add-on (``vulturif-kit[tagging]``). Three pieces:

- :mod:`.query` — parse rule34-style queries once, then :func:`~.query.evaluate`
  them against a local tag set or :func:`~.query.to_dialect` them for a booru.
  Pure stdlib.
- :class:`~.index.TagIndex` — a SQLite index that scans folders incrementally
  and answers those queries. Pure stdlib.
- :class:`~.engine.Tagger` — the WD14 ONNX model that assigns the tags. Its
  heavy deps (``onnxruntime``, ``numpy``, ``PIL``) are imported only when it
  actually tags, so importing this package stays light and the Qt-free rule
  the rest of the kit follows holds here too.

The app wires its own folders, progress and picker on top; none of that is here.
"""

from . import query
from .engine import Label, Tagger, load_labels, select_tags
from .index import IMAGE_EXTS, ScanStats, TagIndex
from .query import evaluate, matches, parse, to_dialect

__all__ = [
    "IMAGE_EXTS", "Label", "ScanStats", "TagIndex", "Tagger",
    "evaluate", "load_labels", "matches", "parse", "query",
    "select_tags", "to_dialect",
]
