"""The rule34 query parser: evaluate locally, and render back to a booru."""

from vulturif_kit.tagging import query
from vulturif_kit.tagging.query import evaluate, matches, parse, to_dialect


# ── evaluate ────────────────────────────────────────────────────────────────

def test_and_requires_every_tag():
    assert matches("blonde_hair 1girl", {"blonde_hair", "1girl", "solo"})
    assert not matches("blonde_hair 1girl", {"blonde_hair"})


def test_not_excludes():
    assert matches("blonde_hair -bikini", {"blonde_hair"})
    assert not matches("blonde_hair -bikini", {"blonde_hair", "bikini"})


def test_or_group_needs_one():
    node = parse("( red_dress ~ blue_dress )")
    assert evaluate(node, {"red_dress"})
    assert evaluate(node, {"blue_dress"})
    assert not evaluate(node, {"green_dress"})


def test_or_inside_and():
    q = "1girl ( red_dress ~ blue_dress ) -bikini"
    assert matches(q, {"1girl", "red_dress"})
    assert not matches(q, {"1girl", "green_dress"})       # no matching dress
    assert not matches(q, {"1girl", "red_dress", "bikini"})  # excluded


def test_top_level_tilde_is_or():
    assert matches("cat ~ dog", {"dog"})
    assert not matches("cat ~ dog", {"fish"})


def test_empty_query_matches_everything():
    assert matches("", {"whatever"})
    assert matches("   ", set())


def test_rating_shorthand_and_full():
    assert matches("rating:e", {"rating:explicit"})
    assert matches("rating:explicit", {"rating:explicit"})
    assert not matches("rating:s", {"rating:explicit"})


def test_score_gate():
    scores = {"blonde_hair": 0.92, "bikini": 0.40}
    assert matches("score:>0.6", {"blonde_hair", "bikini"}, scores)
    assert not matches("score:>0.95", {"blonde_hair", "bikini"}, scores)
    # combined with a tag
    assert matches("blonde_hair score:>0.9", {"blonde_hair", "bikini"}, scores)


def test_case_insensitive_tags():
    assert matches("Blonde_Hair", {"blonde_hair"})


# ── to_dialect: parity with api/booru.py::_normalize_or ─────────────────────

def test_or_group_gelbooru_keeps_parens():
    node = parse("( a ~ b ~ c )")
    assert to_dialect(node, "gelbooru") == "( a ~ b ~ c )"


def test_or_group_danbooru_uses_tildes():
    node = parse("( a ~ b ~ c )")
    assert to_dialect(node, "danbooru") == "~a ~b ~c"


def test_single_alternative_collapses():
    assert to_dialect(parse("( a )"), "gelbooru") == "a"
    assert to_dialect(parse("( a )"), "danbooru") == "a"


def test_plain_and_not_round_trips():
    node = parse("blonde_hair -bikini 1girl")
    assert to_dialect(node, "gelbooru") == "blonde_hair -bikini 1girl"
    assert to_dialect(node, "danbooru") == "blonde_hair -bikini 1girl"


def test_or_in_context_renders_per_dialect():
    node = parse("1girl ( red ~ blue ) -bikini")
    assert to_dialect(node, "gelbooru") == "1girl ( red ~ blue ) -bikini"
    assert to_dialect(node, "danbooru") == "1girl ~red ~blue -bikini"


# ── leniency: half-typed input must not raise ───────────────────────────────

def test_unbalanced_parens_do_not_raise():
    assert matches("( a ~ b", {"a"})          # missing ')'
    assert matches("a )", {"a"})              # stray ')'


def test_trailing_tilde_is_ignored():
    assert matches("a ~", {"a"})


def test_positive_tags_skips_negations_and_scores():
    node = parse("blonde_hair ( red ~ blue ) -bikini score:>0.5")
    assert query.positive_tags(node) == {"blonde_hair", "red", "blue"}
