"""Rule34-style tag queries: parse once, then match locally or render for a booru.

The booru search box and the local media library speak the same little query
language — ``blonde_hair -bikini ( red_dress ~ blue_dress )`` — but need two
different things from it. A remote booru matches server-side, so the app only
has to *render* the query into that site's dialect (what
``api/booru.py::_normalize_or`` does today with a regex). The local library has
no server, so the same query must be *evaluated* against the tags this machine
assigned to one image.

So the design is: parse the text **once** into a small AST (:func:`parse`), then
either :func:`evaluate` it against one image's tags, or :func:`to_dialect` it
back to a remote query string. One grammar, two backends, no second place for
the syntax to drift.

Grammar (whitespace-separated terms, implicitly AND-ed):

    seq     -> or_chain*                 # a space means AND
    or_chain-> term ( '~' term )*        # a '~' means OR
    term    -> '-'? factor               # a leading '-' negates
    factor  -> '(' seq ')' | atom        # parens group, so an OR can sit in an AND
    atom    -> SCORE | TAG
    SCORE   -> 'score' OP NUMBER         # score:>0.6   (local only)
    OP      -> '>' | '>=' | '<' | '<=' | '='
    TAG     -> any run of non-space, non-'(' , non-')' , non-'~' chars

Top-level ``a ~ b`` is an OR too; parentheses only make the grouping explicit
and let an OR sit inside an AND. ``rating:explicit`` (and the shorthands
``rating:s`` / ``q`` / ``e`` / ``g``) is sugar for the plain tag
``rating:explicit`` — the WD14 tagger and NudeNet both emit rating as a tag, so
it needs no node of its own.

The parser is deliberately lenient: unbalanced parentheses, a trailing ``~`` or
a stray ``)`` never raise — a search box must do *something* with half-typed
input, not throw. An empty query is a vacuous AND that matches everything.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass


# ── the AST ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class And:
    """All parts must match. Empty parts → matches everything."""
    parts: tuple


@dataclass(frozen=True)
class Or:
    """At least one part must match. Empty parts → matches nothing."""
    parts: tuple


@dataclass(frozen=True)
class Not:
    """The child must *not* match."""
    child: object


@dataclass(frozen=True)
class Tag:
    """A literal tag; matches when the image has it. Names are lower-cased so
    ``Blonde_Hair`` and ``blonde_hair`` are one tag."""
    name: str


@dataclass(frozen=True)
class Score:
    """A confidence gate: matches when *some* tag on the image has a confidence
    satisfying ``op`` vs ``value`` (e.g. ``score:>0.6`` → a tag over 0.6). Local
    only — a remote booru has no per-tag confidence."""
    op: str
    value: float


# Booru rating shorthands → the full word the taggers emit. 's' is deliberately
# "safe": rule34/gelbooru (the sites this syntax comes from) rate safe, not the
# danbooru "sensitive". Full words pass through untouched.
_RATING = {"s": "safe", "g": "general", "q": "questionable", "e": "explicit"}

_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt,
        "<=": operator.le, "=": operator.eq}

_NUM = re.compile(r"(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)$")


# ── tokenizer ──────────────────────────────────────────────────────────────

_SPECIAL = frozenset("()~")


def _tokenize(text: str) -> list:
    """Split into word tokens plus the single-character specials ``( ) ~``.

    A ``-`` is an ordinary word character, so ``-bikini`` is one token; the
    parser peels the leading ``-`` off. ``score:>0.6`` is one token too — none
    of ``: > . =`` is special."""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c in _SPECIAL:
            tokens.append(c)
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in _SPECIAL:
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


class _Stream:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self):
        t = self.peek()
        self.i += 1
        return t


# ── parser ─────────────────────────────────────────────────────────────────

def parse(text: str):
    """Parse rule34-style query ``text`` into an AST. Never raises."""
    return _parse_seq(_Stream(_tokenize(text or "")))


def _parse_seq(s: _Stream):
    """An AND-sequence of OR-chains, up to end-of-input or a closing ``)``."""
    parts = []
    while True:
        t = s.peek()
        if t is None or t == ")":
            break
        parts.append(_parse_or(s))
    return _mk_and(parts)


def _parse_or(s: _Stream):
    alts = [_parse_term(s)]
    while s.peek() == "~":
        s.next()                       # consume '~'
        if s.peek() is None or s.peek() == ")":
            break                      # trailing '~' — ignore it
        alts.append(_parse_term(s))
    return _mk_or(alts)


def _parse_term(s: _Stream):
    t = s.peek()
    if t == "-":                       # a lone '-' token: negate the next factor
        s.next()
        return _mk_not(_parse_factor(s))
    if isinstance(t, str) and t.startswith("-") and len(t) > 1:
        s.next()
        return _mk_not(_atom(t[1:]))
    return _parse_factor(s)


def _parse_factor(s: _Stream):
    t = s.peek()
    if t == "(":
        s.next()
        inner = _parse_seq(s)
        if s.peek() == ")":
            s.next()                   # balanced; a missing ')' is tolerated
        return inner
    s.next()
    if t is None:
        return And(())
    return _atom(t)


def _atom(tok: str):
    low = tok.lower()
    if low.startswith("score:"):
        m = _NUM.match(tok[6:])
        if m:
            return Score(m.group(1) or ">=", float(m.group(2)))
        return Tag(low)                # 'score:foo' isn't numeric → literal tag
    if low.startswith("rating:"):
        val = low[7:]
        return Tag("rating:" + _RATING.get(val, val))
    return Tag(low)


# ── AST constructors that flatten and simplify ─────────────────────────────

def _mk_and(parts):
    flat = []
    for p in parts:
        if isinstance(p, And):
            flat.extend(p.parts)       # (a (b c)) → (a b c)
        else:
            flat.append(p)
    if len(flat) == 1:
        return flat[0]
    return And(tuple(flat))


def _mk_or(alts):
    flat = []
    for a in alts:
        if isinstance(a, Or):
            flat.extend(a.parts)
        else:
            flat.append(a)
    if len(flat) == 1:
        return flat[0]
    return Or(tuple(flat))


def _mk_not(child):
    if isinstance(child, Not):
        return child.child             # --a → a
    return Not(child)


# ── backend 1: evaluate locally ────────────────────────────────────────────

def evaluate(node, tags, scores=None) -> bool:
    """Does one image match? ``tags`` is its tag set (any iterable, lower-cased
    by the caller — the index stores them lower), ``scores`` an optional
    ``{tag: confidence}`` map used only by ``score:`` gates."""
    if not isinstance(tags, (set, frozenset)):
        tags = set(tags)
    return _ev(node, tags, scores or {})


def _ev(node, tags, scores) -> bool:
    if isinstance(node, And):
        return all(_ev(p, tags, scores) for p in node.parts)   # empty → True
    if isinstance(node, Or):
        return any(_ev(p, tags, scores) for p in node.parts)   # empty → False
    if isinstance(node, Not):
        return not _ev(node.child, tags, scores)
    if isinstance(node, Tag):
        return node.name in tags
    if isinstance(node, Score):
        op = _OPS.get(node.op, operator.ge)
        return any(op(c, node.value) for c in scores.values())
    return True


def matches(text: str, tags, scores=None) -> bool:
    """Parse ``text`` and evaluate it in one call (convenience for the index)."""
    return evaluate(parse(text), tags, scores)


def positive_tags(node) -> set:
    """The literal tags that appear outside any negation — what the index can
    use to pre-select candidate files before the exact :func:`evaluate` pass.
    A query with none (only negations / score gates) returns an empty set, and
    the caller then has to consider every file."""
    found: set = set()
    _collect_positive(node, found)
    return found


def _collect_positive(node, out):
    if isinstance(node, Tag):
        out.add(node.name)
    elif isinstance(node, (And, Or)):
        for p in node.parts:
            _collect_positive(p, out)
    # Not and Score contribute no required-present tag


# ── backend 2: render for a remote booru ───────────────────────────────────

def to_dialect(node, dialect: str = "gelbooru") -> str:
    """Render ``node`` back to a booru query string. The gelbooru family
    (gelbooru, rule34, safebooru, xbooru) keeps OR groups parenthesised as
    ``( a ~ b )``; the danbooru lineage (danbooru, e621, moebooru) wants
    ``~a ~b`` with no parens. This is the drop-in for
    ``BooruClient._normalize_or`` — same output for the OR-group forms it
    handled, plus it understands the whole grammar."""
    return _render(node, dialect).strip()


def _render(node, dialect) -> str:
    if isinstance(node, And):
        return " ".join(r for r in (_render(p, dialect) for p in node.parts) if r)
    if isinstance(node, Or):
        rendered = [r for r in (_render(p, dialect) for p in node.parts) if r]
        if not rendered:
            return ""
        if len(rendered) == 1:
            return rendered[0]
        if dialect == "gelbooru":
            return "( " + " ~ ".join(rendered) + " )"
        return " ".join("~" + r for r in rendered)
    if isinstance(node, Not):
        inner = _render(node.child, dialect)
        return "-" + inner if inner else ""
    if isinstance(node, Tag):
        return node.name
    if isinstance(node, Score):
        return f"score:{node.op}{_num(node.value)}"
    return ""


def _num(v: float) -> str:
    return "%g" % v
