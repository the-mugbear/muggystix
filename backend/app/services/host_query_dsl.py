"""Boolean query DSL for /hosts — lexer, parser, AST, evaluator.

Turns a power-search string such as::

    port:80 port:443 AND NOT tag:test
    cve:CVE-2021-44228 OR vuln:"log4j"
    (has:critical OR risk:80) AND has:web

into a single SQLAlchemy ``ColumnElement`` that is appended to the
existing filtered-host query as one ``.filter()``.

Pipeline: ``tokenize`` → ``parse`` (recursive descent → AST) → ``validate``
(allowlist + guardrails) → ``evaluate`` (AST → SQLAlchemy).  Every field
builder delegates to :mod:`app.services.host_query_predicates`, so the DSL
and the legacy filter panel share one predicate implementation.

Grammar (EBNF)::

    query     := or_expr
    or_expr   := and_expr ( ("OR" | ",") and_expr )*   # top-level comma = OR
    and_expr  := not_expr ( ("AND")? not_expr )*        # adjacency = implicit AND
    not_expr  := "NOT"? primary
    primary   := "(" or_expr ")" | predicate
    predicate := WORD ":" value_list | WORD | QUOTED    # bare word/quote = free-text term
    value_list:= value ("," value)*                     # comma inside a field = OR-within-field
    value     := WORD | QUOTED

Operators are case-insensitive.  ``AND``/``OR``/``NOT`` are keywords only
in operator position — a quoted token or a value after ``:`` is always
literal, so ``service:and`` and ``note:"and then"`` work.  ``-`` is *not*
a negation operator (values in this domain are full of hyphens); use
``NOT``.

Errors never 500: every failure raises :class:`DSLError` (carrying a
character ``position`` where known), which a FastAPI handler maps to 400.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Callable, List, Optional, Sequence, Union

from sqlalchemy import and_, false, not_, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import FollowStatus
from app.db.models_auth import User
from app.services import host_query_predicates as P
from app.services.host_query_common import escape_like  # noqa: F401  (parity w/ predicates)


# ---------------------------------------------------------------------------
# Limits (guardrails — all enforced before any DB work)
# ---------------------------------------------------------------------------

MAX_INPUT_LENGTH = 2000
MAX_TOKENS = 400
MAX_LEAVES = 30
MAX_DEPTH = 10
MIN_TRGM_LEN = 3


class DSLError(Exception):
    """A query that can't be parsed/validated.  Always surfaced as HTTP 400.

    ``position`` is a 0-based character offset into the original query
    string when known (so the UI can underline the offending token), else
    ``None``.
    """

    def __init__(self, message: str, position: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.position = position


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

# Single-character structural tokens.
_SPECIALS = {"(": "LPAREN", ")": "RPAREN", ",": "COMMA", ":": "COLON"}
# Characters that terminate a bare word.
_WORD_BREAK = set(' \t\r\n(),:"')


@dataclass
class Token:
    type: str           # LPAREN RPAREN COMMA COLON QUOTED WORD EOF
    value: str
    start: int
    end: int


def tokenize(text: str) -> List[Token]:
    """Lex ``text`` into tokens.  ``QUOTED`` tokens carry the unescaped
    content; ``WORD`` tokens carry the raw word (keyword classification is
    the parser's job, since it's context-sensitive)."""
    if len(text) > MAX_INPUT_LENGTH:
        raise DSLError(
            f"Query too long ({len(text)} chars, max {MAX_INPUT_LENGTH})",
            position=MAX_INPUT_LENGTH,
        )

    tokens: List[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch in _SPECIALS:
            tokens.append(Token(_SPECIALS[ch], ch, i, i + 1))
            i += 1
            continue
        if ch == '"':
            start = i
            i += 1
            buf: List[str] = []
            closed = False
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    # Only \" and \\ are meaningful escapes; anything else
                    # keeps the backslash literally so users don't have to
                    # double-escape Windows paths in, say, note:"C:\\x".
                    if nxt in '"\\':
                        buf.append(nxt)
                        i += 2
                        continue
                    buf.append(c)
                    i += 1
                    continue
                if c == '"':
                    closed = True
                    i += 1
                    break
                buf.append(c)
                i += 1
            if not closed:
                raise DSLError("Unterminated quoted string", position=start)
            tokens.append(Token("QUOTED", "".join(buf), start, i))
            continue
        # Bare word: run until a break char.
        start = i
        while i < n and text[i] not in _WORD_BREAK:
            i += 1
        tokens.append(Token("WORD", text[start:i], start, i))

        if len(tokens) > MAX_TOKENS:
            raise DSLError(f"Query too complex (max {MAX_TOKENS} tokens)", position=start)

    tokens.append(Token("EOF", "", n, n))
    return tokens


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class And:
    children: List["Node"]


@dataclass
class Or:
    children: List["Node"]


@dataclass
class Not:
    child: "Node"


@dataclass
class FieldNode:
    name: str
    values: List[str]
    pos: int


@dataclass
class Term:
    text: str
    pos: int


Node = Union[And, Or, Not, FieldNode, Term]

_KEYWORDS = {"AND", "OR", "NOT"}


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    # -- token helpers --------------------------------------------------
    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _is_keyword(self, tok: Token, kw: str) -> bool:
        return tok.type == "WORD" and tok.value.upper() == kw

    def _starts_primary(self, tok: Token) -> bool:
        if tok.type in ("LPAREN", "QUOTED"):
            return True
        # A bare word starts a primary unless it's an operator keyword.
        return tok.type == "WORD" and tok.value.upper() not in _KEYWORDS

    # -- grammar --------------------------------------------------------
    def parse(self) -> Node:
        if self.peek().type == "EOF":
            raise DSLError("Empty query")
        node = self.parse_or()
        if self.peek().type != "EOF":
            tok = self.peek()
            raise DSLError(f"Unexpected '{tok.value or tok.type}'", position=tok.start)
        return node

    def parse_or(self) -> Node:
        children = [self.parse_and()]
        while True:
            tok = self.peek()
            if self._is_keyword(tok, "OR") or tok.type == "COMMA":
                self.advance()
                children.append(self.parse_and())
            else:
                break
        return children[0] if len(children) == 1 else Or(children)

    def parse_and(self) -> Node:
        children = [self.parse_not()]
        while True:
            tok = self.peek()
            if self._is_keyword(tok, "AND"):
                self.advance()
                children.append(self.parse_not())
            elif self._starts_primary(tok):
                # Adjacency = implicit AND.
                children.append(self.parse_not())
            else:
                break
        return children[0] if len(children) == 1 else And(children)

    def parse_not(self) -> Node:
        if self._is_keyword(self.peek(), "NOT"):
            self.advance()
            return Not(self.parse_not())
        return self.parse_primary()

    def parse_primary(self) -> Node:
        tok = self.peek()
        if tok.type == "LPAREN":
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise DSLError(f"Query nested too deep (max {MAX_DEPTH})", position=tok.start)
            self.advance()
            node = self.parse_or()
            if self.peek().type != "RPAREN":
                raise DSLError("Unbalanced parentheses — expected ')'", position=self.peek().start)
            self.advance()
            self.depth -= 1
            return node
        if tok.type == "RPAREN":
            raise DSLError("Unexpected ')'", position=tok.start)
        if tok.type == "COLON":
            raise DSLError("Unexpected ':'", position=tok.start)
        if tok.type == "QUOTED":
            self.advance()
            return Term(tok.value, tok.start)
        if tok.type == "WORD":
            # Operator keyword in primary position = a dangling operator.
            if tok.value.upper() in _KEYWORDS:
                raise DSLError(f"Unexpected operator '{tok.value}'", position=tok.start)
            self.advance()
            if self.peek().type == "COLON":
                return self._parse_field(tok)
            return Term(tok.value, tok.start)
        raise DSLError("Unexpected end of query", position=tok.start)

    def _parse_field(self, name_tok: Token) -> FieldNode:
        self.advance()  # consume COLON
        tok = self.peek()
        if tok.type not in ("WORD", "QUOTED"):
            raise DSLError(f"Expected a value after '{name_tok.value}:'", position=tok.start)
        val_tok = self.advance()
        values: List[str] = [val_tok.value]
        # Comma is an OR-within-field separator only when it's *tight* —
        # directly adjacent to the value on both sides (``port:80,443``).
        # A spaced comma (``port:80 , state:up``) is a top-level OR and is
        # left for ``parse_or`` to handle, removing the ambiguity between
        # the two comma meanings.
        while True:
            comma = self.peek()
            if comma.type != "COMMA" or comma.start != val_tok.end:
                break
            nxt = self.tokens[self.pos + 1]
            if nxt.type not in ("WORD", "QUOTED") or nxt.start != comma.end:
                break
            self.advance()      # comma
            val_tok = self.advance()
            values.append(val_tok.value)
        return FieldNode(name_tok.value.lower(), values, name_tok.start)


# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------

@dataclass
class BuildCtx:
    db: Session
    current_user: User
    project_id: int


Builder = Callable[[BuildCtx, List[str]], ColumnElement]


@dataclass
class FieldSpec:
    name: str
    builder: Builder
    aliases: List[str] = dc_field(default_factory=list)
    # Drives frontend autocomplete: which /hosts/filters/data array (if
    # any) supplies value suggestions, or "enum"/"free".
    value_source: str = "free"
    trgm: bool = False
    enum_values: List[str] = dc_field(default_factory=list)


_HAS_KEYWORDS = {
    "web": lambda ctx: P.has_web_interface_predicate(ctx.db),
    "notes": lambda ctx: P.has_notes_predicate(ctx.db),
    "exploit": lambda ctx: P.has_exploit_predicate(ctx.db),
    "tested": lambda ctx: P.has_test_execution_predicate(ctx.db),
    "open_ports": lambda ctx: P.has_open_ports_predicate(ctx.db),
    "critical": lambda ctx: P.severity_predicate(ctx.db, ["CRITICAL"]),
    "high": lambda ctx: P.severity_predicate(ctx.db, ["HIGH"]),
    "medium": lambda ctx: P.severity_predicate(ctx.db, ["MEDIUM"]),
    "low": lambda ctx: P.severity_predicate(ctx.db, ["LOW"]),
}

_FOLLOW_VALUES = {s.value for s in FollowStatus} | {"none", "in_review_any"}


def _b_subnet(ctx: BuildCtx, values: List[str]) -> ColumnElement:
    pred = P.subnet_predicate(values)
    return pred if pred is not None else false()


def _b_risk(ctx: BuildCtx, values: List[str]) -> ColumnElement:
    scores = []
    for v in values:
        if not v.lstrip("-").isdigit():
            raise DSLError(f"risk: expects a number, got '{v}'")
        scores.append(int(v))
    return or_(*[P.risk_predicate(ctx.db, s) for s in scores])


def _b_scan(ctx: BuildCtx, values: List[str]) -> ColumnElement:
    ids = []
    for v in values:
        if not v.isdigit():
            raise DSLError(f"scan: expects a numeric id, got '{v}'")
        ids.append(int(v))
    return P.scan_predicate(ctx.db, ids)


def _b_port(ctx: BuildCtx, values: List[str]) -> ColumnElement:
    # RV-5 — validate here so a non-numeric value (``port:ssh``) is a 400,
    # not a silent broadening.  Pre-fix the leaf dropped non-numeric values
    # and an empty int list left the port subquery unfiltered → matched
    # every host with any port.
    ports = []
    for v in values:
        s = str(v).strip()
        if not s.isdigit():
            raise DSLError(f"port: expects a number, got '{v}' (try service: for a name)")
        n = int(s)
        if not (0 <= n <= 65535):
            raise DSLError(f"port: out of range 0-65535, got '{v}'")
        ports.append(n)
    if not ports:
        raise DSLError("port: requires at least one numeric value")
    return P.port_predicate(ctx.db, ports)


def _b_follow(ctx: BuildCtx, values: List[str]) -> ColumnElement:
    preds = []
    for v in values:
        if v not in _FOLLOW_VALUES:
            raise DSLError(f"Unknown follow status '{v}'")
        preds.append(P.follow_predicate(ctx.db, v, ctx.current_user))
    return or_(*preds)


def _b_assigned(ctx: BuildCtx, values: List[str]) -> ColumnElement:
    preds = []
    for v in values:
        pred = P.assigned_predicate(ctx.db, v, ctx.current_user)
        if pred is None:
            raise DSLError(f"Invalid assigned value '{v}' (use me, any, or a user id)")
        preds.append(pred)
    return or_(*preds)


def _b_has(ctx: BuildCtx, values: List[str]) -> ColumnElement:
    preds = []
    for v in values:
        key = v.lower()
        if key not in _HAS_KEYWORDS:
            raise DSLError(
                f"Unknown has: value '{v}' (one of: {', '.join(sorted(_HAS_KEYWORDS))})"
            )
        preds.append(_HAS_KEYWORDS[key](ctx))
    return or_(*preds)


_FIELD_SPECS: List[FieldSpec] = [
    FieldSpec("state", lambda c, v: P.state_predicate(v), value_source="enum",
              enum_values=["up", "down", "unknown"]),
    FieldSpec("ip", lambda c, v: P.ip_predicate(v)),
    FieldSpec("hostname", lambda c, v: P.hostname_predicate(v), aliases=["host"]),
    FieldSpec("os", lambda c, v: P.os_predicate(v), value_source="os"),
    FieldSpec("port", _b_port, value_source="port"),
    FieldSpec("service", lambda c, v: P.service_predicate(c.db, v), aliases=["svc"],
              value_source="service"),
    FieldSpec("portstate", lambda c, v: P.portstate_predicate(c.db, v), value_source="enum",
              enum_values=["open", "closed", "filtered"]),
    FieldSpec("subnet", _b_subnet, aliases=["cidr"], value_source="cidr"),
    # RV-9 — trgm=True gives tech: the same MIN_TRGM_LEN guard as the other
    # ILIKE/trigram fields, so `tech:a` no longer forces a leading-wildcard
    # scan below the 3-char index threshold.
    FieldSpec("tech", lambda c, v: P.tech_predicate(c.db, v), value_source="tech", trgm=True),
    FieldSpec("tag", lambda c, v: P.tag_predicate_by_name(c.db, v, c.project_id),
              value_source="tag"),
    FieldSpec("label", lambda c, v: P.label_predicate_by_name(c.db, v, c.project_id),
              value_source="label"),
    FieldSpec("risk", _b_risk),
    FieldSpec("follow", _b_follow, value_source="enum", enum_values=sorted(_FOLLOW_VALUES)),
    FieldSpec("assigned", _b_assigned),
    FieldSpec("scan", _b_scan, value_source="scan"),
    FieldSpec("has", _b_has, value_source="enum", enum_values=sorted(_HAS_KEYWORDS)),
    FieldSpec("cve", lambda c, v: P.cve_predicate(c.db, v), trgm=True),
    FieldSpec("vuln", lambda c, v: P.vuln_predicate(c.db, v), trgm=True),
    FieldSpec("header", lambda c, v: P.header_predicate(c.db, v), trgm=True),
    FieldSpec("webtitle", lambda c, v: P.webtitle_predicate(c.db, v), trgm=True),
    FieldSpec("note", lambda c, v: P.note_predicate(c.db, v), trgm=True),
]

# name/alias -> spec
FIELD_BUILDERS: dict[str, FieldSpec] = {}
for _spec in _FIELD_SPECS:
    FIELD_BUILDERS[_spec.name] = _spec
    for _alias in _spec.aliases:
        FIELD_BUILDERS[_alias] = _spec


# ---------------------------------------------------------------------------
# Validation walk
# ---------------------------------------------------------------------------

def _validate(node: Node) -> None:
    """Single post-parse walk: field allowlist, trgm min-length, leaf cap."""
    leaves = 0

    def walk(n: Node) -> None:
        nonlocal leaves
        if isinstance(n, (And, Or)):
            for c in n.children:
                walk(c)
        elif isinstance(n, Not):
            walk(n.child)
        elif isinstance(n, FieldNode):
            leaves += 1
            spec = FIELD_BUILDERS.get(n.name)
            if spec is None:
                raise DSLError(f"Unknown field '{n.name}'", position=n.pos)
            if spec.trgm:
                for v in n.values:
                    if len(v) < MIN_TRGM_LEN:
                        raise DSLError(
                            f"'{n.name}:' needs at least {MIN_TRGM_LEN} characters "
                            f"(got '{v}')",
                            position=n.pos,
                        )
        elif isinstance(n, Term):
            leaves += 1
            # RV-9 — a bare free-text term shorter than the trigram index
            # threshold forces a leading-wildcard seq scan across many
            # columns.  Require MIN_TRGM_LEN, matching the trgm fields.
            if len(n.text.strip()) < MIN_TRGM_LEN:
                raise DSLError(
                    f"Search term '{n.text}' needs at least {MIN_TRGM_LEN} characters",
                    position=n.pos,
                )
        if leaves > MAX_LEAVES:
            raise DSLError(f"Query has too many terms (max {MAX_LEAVES})")

    walk(node)


def count_leaves(node: Node) -> int:
    if isinstance(node, (And, Or)):
        return sum(count_leaves(c) for c in node.children)
    if isinstance(node, Not):
        return count_leaves(node.child)
    return 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_query(q: str) -> Node:
    """Tokenize, parse, and validate ``q`` → AST root.  Raises
    :class:`DSLError` on any problem; converts a runaway parse
    (``RecursionError``) into a clean 400 rather than a 500."""
    try:
        tokens = tokenize(q)
        node = _Parser(tokens).parse()
    except DSLError:
        raise
    except RecursionError:
        raise DSLError("Query nested too deep")
    except ValueError as exc:  # defensive: int()/lookup slips
        raise DSLError(str(exc))
    _validate(node)
    return node


def evaluate(node: Node, ctx: BuildCtx) -> ColumnElement:
    """AST → SQLAlchemy boolean expression."""
    if isinstance(node, And):
        return and_(*[evaluate(c, ctx) for c in node.children])
    if isinstance(node, Or):
        return or_(*[evaluate(c, ctx) for c in node.children])
    if isinstance(node, Not):
        return not_(evaluate(node.child, ctx))
    if isinstance(node, FieldNode):
        spec = FIELD_BUILDERS[node.name]  # presence guaranteed by _validate
        return spec.builder(ctx, node.values)
    if isinstance(node, Term):
        # Free-text term — reuse the legacy search semantics so the bare-word
        # power-search behaves exactly like the panel's quick-search box.
        from app.services.host_query import build_search_predicate
        return build_search_predicate(ctx.db, node.text)
    raise DSLError("Unevaluatable query node")  # pragma: no cover


def schema() -> dict:
    """Field + example catalogue for the frontend (autocomplete, help
    popover, template gallery).  Single source of truth = the registry."""
    seen = set()
    fields = []
    for spec in _FIELD_SPECS:
        if spec.name in seen:
            continue
        seen.add(spec.name)
        fields.append({
            "name": spec.name,
            "aliases": spec.aliases,
            "value_source": spec.value_source,
            "trgm": spec.trgm,
            "enum_values": spec.enum_values,
        })
    return {"fields": fields, "examples": EXAMPLES}


# Curated starter queries surfaced in the template gallery.
EXAMPLES: List[dict] = [
    {"label": "Open 80 AND 443", "q": "port:80 port:443"},
    {"label": "Untested criticals", "q": "has:critical AND NOT has:tested"},
    {"label": "Log4Shell-exposed web", "q": 'cve:CVE-2021-44228 OR vuln:"log4j"'},
    {"label": "Exploitable, high risk", "q": "has:exploit AND risk:80"},
    {"label": "Windows RDP, not tagged test", "q": "os:windows port:3389 AND NOT tag:test"},
    {"label": "nginx servers", "q": "header:nginx OR tech:nginx"},
]
