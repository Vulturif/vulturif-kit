"""The tagging engine: a WD14 ONNX model turns an image into danbooru tags.

WD14 (SmilingWolf's ``wd-v1-4-*`` / ``wd-swinv2`` / ``eva02``) is the tagger the
whole feature leans on: it outputs thousands of **danbooru-vocabulary** tags
with confidences, and that vocabulary is exactly what the rule34 query syntax is
written against — so ``blonde_hair -bikini`` filtering a local library means the
same thing it means on a booru.

**ONNX only, by design.** The model runs through ``onnxruntime`` — never torch —
so the download stays tens-to-low-hundreds of MB and works on a plain CPU out of
the box. These three heavy imports (``onnxruntime``, ``numpy``, ``PIL``) are made
*inside* the methods that need them, never at module load: a bare
``import vulturif_kit.tagging`` must stay as light as the rest of the kit, and
the deps only exist at all when the app was installed as
``vulturif-kit[tagging]`` (see pyproject).

The numeric glue — reading the label file, turning a probability vector into a
tag list, choosing the rating — is factored into the pure functions
:func:`load_labels` and :func:`select_tags`, which need nothing heavier than the
standard library and so can be unit-tested anywhere. Only :meth:`Tagger.tag`'s
image preprocessing and the ONNX session itself need the extras.

A model bundle is a directory holding ``model.onnx`` and ``selected_tags.csv``;
fetching and verifying that bundle is the downloader's job (``assets.py``), not
this module's.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

MODEL_FILE = "model.onnx"
LABELS_FILE = "selected_tags.csv"

# selected_tags.csv category codes, from the danbooru dumps WD14 was trained on.
CAT_GENERAL = 0
CAT_CHARACTER = 4
CAT_RATING = 9


@dataclass(frozen=True)
class Label:
    """One row of ``selected_tags.csv`` — the tag name and what kind it is."""
    name: str
    category: int


def load_labels(csv_path) -> list:
    """Read ``selected_tags.csv`` into a list of :class:`Label`, in file order
    (the order the model's output vector is in). Pure stdlib."""
    labels = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            try:
                cat = int(row.get("category", CAT_GENERAL))
            except (TypeError, ValueError):
                cat = CAT_GENERAL
            labels.append(Label(name, cat))
    return labels


def select_tags(probs, labels, threshold: float = 0.35,
                rating_prefix: str = "rating:") -> list:
    """Turn a probability vector into ``[(tag, confidence), ...]``, sorted by
    confidence, highest first. Pure: ``probs`` is any sequence of floats, one
    per label, so this is testable without onnxruntime or numpy.

    - general and character tags over ``threshold`` come through as-is;
    - the single highest-scoring **rating** becomes ``rating:<name>`` (e.g.
      ``rating:explicit``) regardless of threshold, so every image gets exactly
      one rating tag — the same shape NudeNet would later contribute.
    """
    out = []
    best_rating = None                 # (confidence, name)
    for prob, label in zip(probs, labels):
        p = float(prob)
        if label.category == CAT_RATING:
            if best_rating is None or p > best_rating[0]:
                best_rating = (p, label.name)
        elif p >= threshold:
            out.append((label.name, p))
    if best_rating is not None:
        out.append((rating_prefix + best_rating[1], best_rating[0]))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


class Tagger:
    """Tag images with a WD14 ONNX model.

    The ONNX session is built lazily on the first :meth:`tag` call, so creating a
    ``Tagger`` is cheap and does not touch onnxruntime — handy for wiring the UI
    before the model bundle is even downloaded.
    """

    def __init__(self, model_dir, *, threshold: float = 0.35,
                 providers=("CPUExecutionProvider",), intra_op_threads: int = 0):
        self.model_dir = Path(model_dir)
        self.threshold = threshold
        self.providers = tuple(providers)
        # Threads one inference may use. 0 = onnxruntime's default (all cores),
        # best when tagging one image at a time; a scan that runs several Taggers
        # in parallel sets this low (e.g. 1–2) so the sessions don't oversubscribe
        # the cores and can actually run at once.
        self.intra_op_threads = int(intra_op_threads)
        self._session = None
        self._labels = None
        self._size = None              # model's square input side, in pixels

    @property
    def ready(self) -> bool:
        """Whether the model bundle's files are present. No heavy import."""
        return ((self.model_dir / MODEL_FILE).is_file()
                and (self.model_dir / LABELS_FILE).is_file())

    def _ensure(self):
        if self._session is not None:
            return
        import onnxruntime                       # lazy: only here

        model = self.model_dir / MODEL_FILE
        if not model.is_file():
            raise FileNotFoundError(f"no {MODEL_FILE} in {self.model_dir}")
        self._labels = load_labels(self.model_dir / LABELS_FILE)
        opts = onnxruntime.SessionOptions()
        if self.intra_op_threads > 0:
            opts.intra_op_num_threads = self.intra_op_threads
        self._session = onnxruntime.InferenceSession(
            str(model), sess_options=opts, providers=list(self.providers))
        # WD14 input is (1, H, W, 3) with H == W; the dim may be a string when
        # the model declares it dynamic, so fall back to the usual 448.
        shape = self._session.get_inputs()[0].shape
        side = shape[1] if len(shape) >= 2 else None
        self._size = side if isinstance(side, int) and side > 0 else 448

    def _preprocess(self, image_path):
        """Load an image and shape it the way WD14 expects: composite onto white,
        pad to a square, resize to the model side, BGR, float32, batched."""
        import numpy as np                        # lazy
        from PIL import Image                      # lazy

        img = Image.open(image_path).convert("RGBA")
        canvas = Image.new("RGBA", img.size, (255, 255, 255, 255))
        canvas.alpha_composite(img)
        img = canvas.convert("RGB")

        w, h = img.size
        side = max(w, h)
        square = Image.new("RGB", (side, side), (255, 255, 255))
        square.paste(img, ((side - w) // 2, (side - h) // 2))
        if side != self._size:
            square = square.resize((self._size, self._size), Image.BICUBIC)

        arr = np.asarray(square, dtype="float32")   # (S, S, 3) RGB
        arr = arr[:, :, ::-1]                         # RGB → BGR
        return arr[None, ...]                         # (1, S, S, 3)

    def tag(self, image_path) -> list:
        """Return ``[(tag, confidence), ...]`` for one image, above the
        configured threshold, plus its one rating tag. Raises if the image can't
        be read or the model is missing."""
        self._ensure()
        batch = self._preprocess(image_path)
        name = self._session.get_inputs()[0].name
        outputs = self._session.run(None, {name: batch})
        probs = outputs[0][0]                          # (1, N) → (N,)
        return select_tags(probs, self._labels, self.threshold)
