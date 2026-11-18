"""The engine's pure glue: label parsing and probability-vector post-processing.

The model inference itself needs onnxruntime + a downloaded model, so it is not
exercised here; these cover the stdlib-only parts that turn a raw output vector
into tags, which is where the logic (thresholds, the single rating tag) lives.
"""

from vulturif_kit.tagging.engine import (Label, Tagger, load_labels,
                                         select_tags)


def test_load_labels_reads_name_and_category(tmp_path):
    csv = tmp_path / "selected_tags.csv"
    csv.write_text(
        "tag_id,name,category,count\n"
        "1,blonde_hair,0,1000\n"
        "2,character_x,4,50\n"
        "3,explicit,9,999\n"
        ",,0,0\n",                               # blank name row is skipped
        encoding="utf-8")
    labels = load_labels(csv)
    assert labels == [Label("blonde_hair", 0), Label("character_x", 4),
                      Label("explicit", 9)]


def test_select_tags_thresholds_and_sorts():
    labels = [Label("blonde_hair", 0), Label("1girl", 0), Label("char_x", 4),
              Label("faint_tag", 0)]
    probs = [0.90, 0.80, 0.70, 0.10]
    out = select_tags(probs, labels, threshold=0.5)
    assert out == [("blonde_hair", 0.90), ("1girl", 0.80), ("char_x", 0.70)]


def test_select_tags_emits_one_rating():
    labels = [Label("1girl", 0), Label("general", 9), Label("questionable", 9),
              Label("explicit", 9)]
    probs = [0.95, 0.10, 0.25, 0.65]
    out = select_tags(probs, labels, threshold=0.5)
    # the highest rating wins regardless of threshold, as rating:<name>
    assert ("rating:explicit", 0.65) in out
    assert sum(1 for t, _ in out if t.startswith("rating:")) == 1
    # still sorted by confidence
    assert out[0] == ("1girl", 0.95)


def test_select_tags_rating_below_threshold_still_wins():
    labels = [Label("safe", 9), Label("explicit", 9)]
    probs = [0.30, 0.20]                          # both under threshold
    out = select_tags(probs, labels, threshold=0.5)
    assert out == [("rating:safe", 0.30)]         # the max rating is always emitted


def test_tagger_ready_needs_both_files(tmp_path):
    t = Tagger(tmp_path)
    assert not t.ready                            # empty dir
    (tmp_path / "model.onnx").write_bytes(b"x")
    assert not t.ready                            # labels still missing
    (tmp_path / "selected_tags.csv").write_text("tag_id,name,category,count\n")
    assert t.ready
