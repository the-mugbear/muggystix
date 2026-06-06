"""Unit tests for the /hosts boolean query DSL parser + validator.

These exercise the pure layer (``tokenize`` → ``parse_query`` → AST +
guardrails) with no database — evaluation against SQLAlchemy is covered
by the integration tests in ``test_host_query_dsl_filter.py``.
"""
from __future__ import annotations

import pytest

from app.services.host_query_dsl import (
    And,
    DSLError,
    FieldNode,
    MAX_DEPTH,
    MAX_LEAVES,
    Not,
    Or,
    Term,
    count_leaves,
    parse_query,
    schema,
    tokenize,
)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def test_quotes_and_escapes():
    toks = tokenize(r'note:"a \"quoted\" \\ slash"')
    quoted = [t for t in toks if t.type == "QUOTED"]
    assert quoted[0].value == 'a "quoted" \\ slash'


def test_hyphenated_value_is_one_token():
    node = parse_query("cve:CVE-2021-44228")
    assert isinstance(node, FieldNode)
    assert node.values == ["CVE-2021-44228"]


def test_keyword_after_colon_is_a_value():
    # AND/OR/NOT are only operators in operator position.
    node = parse_query("service:and")
    assert isinstance(node, FieldNode) and node.values == ["and"]


def test_quoted_keyword_is_a_term():
    node = parse_query('"AND"')
    assert isinstance(node, Term) and node.text == "AND"


# ---------------------------------------------------------------------------
# Precedence + semantics
# ---------------------------------------------------------------------------

def test_precedence_or_binds_loosest():
    # a OR b AND c  ==  a OR (b AND c)
    node = parse_query("aaa OR bbb AND ccc")
    assert isinstance(node, Or)
    assert isinstance(node.children[0], Term)
    assert isinstance(node.children[1], And)


def test_parens_override_precedence():
    node = parse_query("(aaa OR bbb) AND ccc")
    assert isinstance(node, And)
    assert isinstance(node.children[0], Or)


def test_implicit_and_by_adjacency():
    node = parse_query("port:80 port:443")
    assert isinstance(node, And)
    assert all(isinstance(c, FieldNode) and c.name == "port" for c in node.children)
    assert [c.values for c in node.children] == [["80"], ["443"]]


def test_comma_within_field_is_or():
    node = parse_query("port:80,443")
    assert isinstance(node, FieldNode)
    assert node.values == ["80", "443"]


def test_top_level_comma_is_or():
    node = parse_query("port:80 , state:up")
    assert isinstance(node, Or)


def test_not_negation():
    node = parse_query("NOT tag:test")
    assert isinstance(node, Not)
    assert isinstance(node.child, FieldNode)


def test_case_insensitive_operators():
    assert isinstance(parse_query("aaa or bbb"), Or)
    assert isinstance(parse_query("aaa And bbb"), And)


# ---------------------------------------------------------------------------
# Errors (each raises DSLError, never bubbles a 500-class exception)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad,frag", [
    ("port:", "value"),
    ("note:ab", "characters"),
    ("(a AND b", "parentheses"),
    ("a AND", "end"),
    ("foo bar)", "')'"),
    ('cve:"unterminated', "Unterminated"),
    ("frob:x", "Unknown field"),
    ("", "Empty"),
    ("port:80,", "end"),  # trailing spaced/dangling comma → dangling OR
    # RV-9 — sub-trigram lengths rejected (tech: now trgm; bare terms floored).
    ("tech:a", "characters"),
    ("ab", "characters"),
])
def test_errors_raise_dslerror(bad, frag):
    with pytest.raises(DSLError) as ei:
        parse_query(bad)
    assert frag.lower() in ei.value.message.lower()


@pytest.mark.parametrize("bad,frag", [
    ("ssh", "number"),       # RV-5 — non-numeric port
    ("99999", "range"),      # RV-5 — out of 0..65535
    ("-1", "number"),        # negative isn't isdigit()
])
def test_port_builder_rejects_invalid(bad, frag):
    """RV-5 — port: validation lives in the build phase (like risk:/scan:),
    surfaced as a 400 via evaluate(); the error path never touches ctx.db
    so a None ctx is fine here."""
    from app.services.host_query_dsl import _b_port
    with pytest.raises(DSLError) as ei:
        _b_port(None, [bad])
    assert frag.lower() in ei.value.message.lower()


def test_error_carries_position():
    with pytest.raises(DSLError) as ei:
        parse_query("a AND )")
    assert ei.value.position is not None


def test_unknown_field_position_points_at_field():
    with pytest.raises(DSLError) as ei:
        parse_query("frobnicate:x")
    assert ei.value.position == 0


# ---------------------------------------------------------------------------
# Guardrails / DoS caps
# ---------------------------------------------------------------------------

def test_leaf_cap():
    too_many = " ".join(f"port:{n}" for n in range(MAX_LEAVES + 5))
    with pytest.raises(DSLError) as ei:
        parse_query(too_many)
    assert "many terms" in ei.value.message.lower()


def test_depth_cap():
    deep = "(" * (MAX_DEPTH + 2) + "a" + ")" * (MAX_DEPTH + 2)
    with pytest.raises(DSLError) as ei:
        parse_query(deep)
    assert "deep" in ei.value.message.lower()


def test_length_cap():
    with pytest.raises(DSLError) as ei:
        parse_query("a" * 5000)
    assert "too long" in ei.value.message.lower()


def test_injection_chars_do_not_break_parser():
    # %, _, ', ; , ) inside a quoted value are literal content, not SQL.
    node = parse_query("note:\"100% off'; DROP TABLE hosts; --\"")
    assert isinstance(node, FieldNode)
    assert node.values[0].startswith("100% off")


# ---------------------------------------------------------------------------
# count_leaves + schema
# ---------------------------------------------------------------------------

def test_count_leaves():
    assert count_leaves(parse_query("aaa AND bbb OR ccc")) == 3
    assert count_leaves(parse_query("NOT (port:80 OR port:443)")) == 2


def test_schema_lists_fields_and_examples():
    s = schema()
    names = {f["name"] for f in s["fields"]}
    assert {"port", "cve", "has", "tag", "note"} <= names
    assert "nse" not in names  # cut from v1
    assert s["examples"], "expected curated example queries"
    # Every example must itself parse.
    for ex in s["examples"]:
        parse_query(ex["q"])
