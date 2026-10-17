"""The Query page's language — a pipe query language over a few datasets (v2.509.0).

The owner, 2026-10-16: the teams are used to pipe query languages, so the shape is
a dataset, then commands joined by ``|``::

    observations has:exploit | where severity="critical" | stats dc(host) as hosts by title | sort -hosts | head 20

* ``<dataset>`` — what a row is (``query_datasets``).
* search terms after it (or ``| search <terms>``) are the Hosts query language,
  UNCHANGED (``host_query_dsl``): they always describe the row's HOST, as on
  the Hosts page — one meaning per word.
* ``| where`` compares the dataset's declared COLUMNS.
* ``| stats`` / ``| top`` aggregate; ``| sort``, ``| head`` / ``| limit``,
  ``| fields`` / ``| table`` shape the answer.
* After ``stats`` / ``top`` a command works on the ANSWER (the reading pipe
  query languages share):
  ``| where`` compares its columns (the aggregates by their names — SQL's
  HAVING) and ``| fields`` picks its columns.

This module is the grammar only: text → :class:`Pipeline`.  Names are checked
against the dataset by ``query_service``.  Every error is a
:class:`QueryError` — a :class:`~app.services.host_query_dsl.DSLError`, so the
API's one handler answers it as a 400 with the character ``position``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

from app.services.host_query_dsl import DSLError

MAX_QUERY_LENGTH = 4000
MAX_COMMANDS = 10
MAX_BY = 5
MAX_AGGREGATES = 10
MAX_WHERE_TERMS = 30
MAX_IN_VALUES = 100
# Parentheses and NOTs nested in one `where` — the Hosts language's limit.
# Each level is a Python call (and one in compiling): without a cap a deep
# `((((…` within the length limit was a RecursionError, a 500 (review
# 2026-10-16, N1).
MAX_DEPTH = 10

AGG_FUNCTIONS = {"count", "dc", "distinct_count", "min", "max", "avg", "sum", "values"}
COMMANDS = ("search", "where", "stats", "top", "sort", "head", "limit", "fields", "table")


class QueryError(DSLError):
    """A query that cannot be read.  Answered as a 400 with ``position``."""


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class Compare:
    column: str
    op: str             # = != < <= > >=
    value: object       # str | int | float | bool
    pos: int


@dataclass
class InList:
    column: str
    values: List[object]
    pos: int


@dataclass
class Like:
    column: str
    pattern: str
    pos: int


@dataclass
class IsNull:
    column: str
    negated: bool       # isnotnull
    pos: int


@dataclass
class BoolOp:
    op: str             # and | or
    children: List["Cond"]


@dataclass
class NotCond:
    child: "Cond"


Cond = Union[Compare, InList, Like, IsNull, BoolOp, NotCond]


@dataclass
class Aggregate:
    func: str           # count | dc | min | max | avg | sum | values
    column: Optional[str]
    alias: str
    pos: int


@dataclass
class Stats:
    aggregates: List[Aggregate]
    by: List[str]
    pos: int
    by_pos: List[int] = field(default_factory=list)


@dataclass
class Top:
    columns: List[str]
    limit: int
    pos: int
    columns_pos: List[int] = field(default_factory=list)


@dataclass
class Pipeline:
    dataset: str
    dataset_pos: int
    # (the Hosts-language text, its offset in the whole query)
    searches: List[Tuple[str, int]] = field(default_factory=list)
    wheres: List[Cond] = field(default_factory=list)
    fields: Optional[List[str]] = None
    fields_pos: List[int] = field(default_factory=list)
    aggregate: Optional[Union[Stats, Top]] = None
    # The query up to (not including) the `| stats` / `| top` — the base a
    # group's drill-down adds its `| where` to.
    before_aggregate: Optional[str] = None
    sorts: List[Tuple[str, bool, int]] = field(default_factory=list)   # (column, descending, pos)
    head: Optional[int] = None
    head_pos: int = 0
    # After `stats` / `top` (the usual reading: the command works on the answer's
    # table): `where` keeps groups (SQL's HAVING), `fields` / `table` picks
    # the answer's columns.  Owner, 2026-10-16 (second review Q3).
    having: List[Cond] = field(default_factory=list)
    answer_fields: Optional[List[str]] = None
    answer_fields_pos: List[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Splitting into commands: `|` outside double quotes
# ---------------------------------------------------------------------------

def _segments(text: str) -> List[Tuple[str, int]]:
    """The query cut at each `|` that is not inside a quoted string, with
    each piece's offset."""
    out: List[Tuple[str, int]] = []
    start, i, quoted = 0, 0, False
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quoted:
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif ch == "|" and not quoted:
            out.append((text[start:i], start))
            start = i + 1
        i += 1
    if quoted:
        raise QueryError("A quoted string is not closed", position=len(text))
    out.append((text[start:], start))
    return out


# ---------------------------------------------------------------------------
# Tokens inside one command
# ---------------------------------------------------------------------------

@dataclass
class _Tok:
    kind: str           # WORD STRING NUMBER OP LPAREN RPAREN COMMA EOF
    value: object
    pos: int


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_OPS = ("<=", ">=", "!=", "==", "=", "<", ">")


def _tokens(text: str, base: int) -> List[_Tok]:
    out: List[_Tok] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        pos = base + i
        if ch == '"':
            i += 1
            buf = []
            while i < len(text) and text[i] != '"':
                if text[i] == "\\" and i + 1 < len(text):
                    i += 1
                buf.append(text[i])
                i += 1
            if i >= len(text):
                raise QueryError("A quoted string is not closed", position=pos)
            i += 1
            out.append(_Tok("STRING", "".join(buf), pos))
            continue
        if ch == "(":
            out.append(_Tok("LPAREN", ch, pos))
            i += 1
            continue
        if ch == ")":
            out.append(_Tok("RPAREN", ch, pos))
            i += 1
            continue
        if ch == ",":
            out.append(_Tok("COMMA", ch, pos))
            i += 1
            continue
        op = next((o for o in _OPS if text.startswith(o, i)), None)
        if op:
            out.append(_Tok("OP", "=" if op == "==" else op, pos))
            i += len(op)
            continue
        m = _NUMBER.match(text, i)
        if m and (ch.isdigit() or (ch == "-" and i + 1 < len(text) and text[i + 1].isdigit())):
            raw = m.group(0)
            out.append(_Tok("NUMBER", float(raw) if "." in raw else int(raw), pos))
            i = m.end()
            continue
        if ch in "+-":
            out.append(_Tok("SIGN", ch, pos))
            i += 1
            continue
        m = _WORD.match(text, i)
        if m:
            out.append(_Tok("WORD", m.group(0), pos))
            i = m.end()
            continue
        raise QueryError(f"Unexpected character '{ch}'", position=pos)
    out.append(_Tok("EOF", "", base + len(text)))
    return out


class _Reader:
    def __init__(self, toks: List[_Tok]):
        self.toks, self.i = toks, 0

    def peek(self) -> _Tok:
        return self.toks[self.i]

    def next(self) -> _Tok:
        tok = self.toks[self.i]
        if tok.kind != "EOF":
            self.i += 1
        return tok

    def word(self, what: str) -> _Tok:
        tok = self.next()
        if tok.kind != "WORD":
            raise QueryError(f"Expected {what}", position=tok.pos)
        return tok

    def is_word(self, *values: str) -> bool:
        tok = self.peek()
        return tok.kind == "WORD" and str(tok.value).lower() in values

    def expect(self, kind: str, what: str) -> _Tok:
        tok = self.next()
        if tok.kind != kind:
            raise QueryError(f"Expected {what}", position=tok.pos)
        return tok

    def end(self, command: str) -> None:
        tok = self.peek()
        if tok.kind != "EOF":
            raise QueryError(f"Unexpected '{tok.value}' in `{command}`", position=tok.pos)


# ---------------------------------------------------------------------------
# where
# ---------------------------------------------------------------------------

def _literal(r: _Reader) -> object:
    tok = r.next()
    if tok.kind in ("STRING", "NUMBER"):
        return tok.value
    if tok.kind == "WORD" and str(tok.value).lower() in ("true", "false"):
        return str(tok.value).lower() == "true"
    raise QueryError('Expected a value: a number, true / false or a "quoted string"', position=tok.pos)


class _Where:
    def __init__(self, r: _Reader):
        self.r, self.terms, self.depth = r, 0, 0

    def _deeper(self, tok: _Tok) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise QueryError(f"`where` is nested too deep (max {MAX_DEPTH})", position=tok.pos)

    def parse(self) -> Cond:
        cond = self.or_()
        self.r.end("where")
        return cond

    def or_(self) -> Cond:
        children = [self.and_()]
        while self.r.is_word("or"):
            self.r.next()
            children.append(self.and_())
        return children[0] if len(children) == 1 else BoolOp("or", children)

    def and_(self) -> Cond:
        children = [self.not_()]
        while self.r.is_word("and"):
            self.r.next()
            children.append(self.not_())
        return children[0] if len(children) == 1 else BoolOp("and", children)

    def not_(self) -> Cond:
        if self.r.is_word("not"):
            self._deeper(self.r.next())
            inner = NotCond(self.not_())
            self.depth -= 1
            return inner
        return self.primary()

    def primary(self) -> Cond:
        tok = self.r.peek()
        if tok.kind == "LPAREN":
            self._deeper(self.r.next())
            inner = self.or_()
            self.r.expect("RPAREN", "')'")
            self.depth -= 1
            return inner
        name = self.r.word("a column name, isnull(), isnotnull() or like()")
        self.terms += 1
        if self.terms > MAX_WHERE_TERMS:
            raise QueryError(f"`where` has too many comparisons (max {MAX_WHERE_TERMS})", position=name.pos)
        fn = str(name.value).lower()
        if fn in ("isnull", "isnotnull", "like") and self.r.peek().kind == "LPAREN":
            self.r.next()
            column = self.r.word("a column name")
            if fn == "like":
                self.r.expect("COMMA", "',' and a pattern")
                pattern = self.r.expect("STRING", 'a "quoted" pattern')
                self.r.expect("RPAREN", "')'")
                return Like(str(column.value), str(pattern.value), column.pos)
            self.r.expect("RPAREN", "')'")
            return IsNull(str(column.value), fn == "isnotnull", column.pos)
        if self.r.is_word("in"):
            self.r.next()
            self.r.expect("LPAREN", "'(' and a list of values")
            values = [_literal(self.r)]
            while self.r.peek().kind == "COMMA":
                self.r.next()
                values.append(_literal(self.r))
            self.r.expect("RPAREN", "')'")
            if len(values) > MAX_IN_VALUES:
                raise QueryError(f"`IN` takes at most {MAX_IN_VALUES} values", position=name.pos)
            return InList(str(name.value), values, name.pos)
        op = self.r.next()
        if op.kind != "OP":
            raise QueryError(
                f"Expected a comparison after '{name.value}' (=, !=, <, <=, >, >=, IN)", position=op.pos,
            )
        return Compare(str(name.value), str(op.value), _literal(self.r), name.pos)


# ---------------------------------------------------------------------------
# The other commands
# ---------------------------------------------------------------------------

def _is_limit(r: _Reader) -> bool:
    """`limit=` next (top's option, before or after its columns)."""
    nxt = r.toks[r.i + 1] if r.i + 1 < len(r.toks) else None
    return r.is_word("limit") and nxt is not None and nxt.kind == "OP" and nxt.value == "="


def _columns(r: _Reader, command: str) -> Tuple[List[str], List[int]]:
    """Column names and each one's position — so an unknown one is pointed
    at, not the start of the query (second review 2026-10-16, I3)."""
    first = r.word(f"a column name after `{command}`")
    cols, positions = [str(first.value)], [first.pos]
    while (r.peek().kind == "COMMA" or r.peek().kind == "WORD") and not _is_limit(r):
        if r.peek().kind == "COMMA":
            r.next()
        tok = r.word("a column name")
        cols.append(str(tok.value))
        positions.append(tok.pos)
    return cols, positions


def _stats(r: _Reader, pos: int) -> Stats:
    aggregates: List[Aggregate] = []
    while True:
        name = r.word("an aggregate: count, dc(col), min(col), max(col), avg(col), sum(col), values(col)")
        func = str(name.value).lower()
        if func not in AGG_FUNCTIONS:
            raise QueryError(
                f"Unknown function '{name.value}'. Functions: count, dc, min, max, avg, sum, values",
                position=name.pos,
            )
        if func == "distinct_count":
            func = "dc"
        column: Optional[str] = None
        if r.peek().kind == "LPAREN":
            r.next()
            if r.peek().kind == "RPAREN":
                r.next()
            else:
                column = str(r.word("a column name").value)
                r.expect("RPAREN", "')'")
        if func != "count" and column is None:
            raise QueryError(f"`{func}` needs a column: {func}(column)", position=name.pos)
        default = func if column is None else f"{func}({column})"
        alias = default
        if r.is_word("as"):
            r.next()
            alias = str(r.word("a name after `as`").value)
        aggregates.append(Aggregate(func, column, alias, name.pos))
        if len(aggregates) > MAX_AGGREGATES:
            raise QueryError(f"`stats` takes at most {MAX_AGGREGATES} aggregates", position=name.pos)
        if r.peek().kind == "COMMA":
            r.next()
            continue
        break
    by: List[str] = []
    by_pos: List[int] = []
    if r.is_word("by"):
        r.next()
        by, by_pos = _columns(r, "by")
        if len(by) > MAX_BY:
            raise QueryError(f"`by` takes at most {MAX_BY} columns", position=pos)
    r.end("stats")
    names = [a.alias for a in aggregates] + by
    dup = next((n for n in names if names.count(n) > 1), None)
    if dup:
        raise QueryError(f"'{dup}' names two columns of the answer — rename one with `as`", position=pos)
    return Stats(aggregates, by, pos, by_pos)


def _top(r: _Reader, pos: int) -> Top:
    """`top [limit=N] col, … [limit=N]` — the option is taken on either
    side of the columns (second review 2026-10-16, I5)."""
    limit = 10

    def read_limit() -> int:
        r.next()
        r.expect("OP", "'=' after limit")
        tok = r.expect("NUMBER", "a number after limit=")
        if not isinstance(tok.value, int) or tok.value < 1:
            raise QueryError("limit must be a whole number of at least 1", position=tok.pos)
        return tok.value

    if _is_limit(r):
        limit = read_limit()
    cols, positions = _columns(r, "top")
    if _is_limit(r):
        limit = read_limit()
    if len(cols) > MAX_BY:
        raise QueryError(f"`top` takes at most {MAX_BY} columns", position=pos)
    r.end("top")
    return Top(cols, limit, pos, positions)


def _sort(r: _Reader) -> List[Tuple[str, bool, int]]:
    out = []
    while True:
        desc = False
        if r.peek().kind == "SIGN":
            desc = r.next().value == "-"
        name = r.word("a column name after `sort`")
        out.append((str(name.value), desc, name.pos))
        if r.is_word("desc", "asc"):
            # SQL's word order — here it is a sign (second review, Q3).
            tok = r.peek()
            raise QueryError(
                f"`sort` takes -{name.value} for descending, +{name.value} (or {name.value}) for ascending — "
                f"not '{tok.value}'", position=tok.pos,
            )
        if r.peek().kind == "COMMA":
            r.next()
            continue
        if r.peek().kind in ("WORD", "SIGN"):
            continue
        break
    r.end("sort")
    return out


def parse(text: str) -> Pipeline:
    """Text → :class:`Pipeline`.  Grammar and command order only; names are
    the dataset's to check."""
    if not text or not text.strip():
        raise QueryError("Write a query: a dataset (hosts, ports, observations), then | commands", position=0)
    if len(text) > MAX_QUERY_LENGTH:
        raise QueryError(f"Query too long ({len(text)} characters, max {MAX_QUERY_LENGTH})",
                         position=MAX_QUERY_LENGTH)
    segments = _segments(text)
    if len(segments) - 1 > MAX_COMMANDS:
        raise QueryError(f"Too many commands (max {MAX_COMMANDS})", position=segments[MAX_COMMANDS + 1][1])

    first, first_at = segments[0]
    m = re.match(r"\s*([A-Za-z_]+)", first)
    if not m:
        raise QueryError("Start with a dataset: hosts, ports or observations", position=first_at)
    pipe = Pipeline(dataset=m.group(1).lower(), dataset_pos=first_at + m.start(1))
    rest = first[m.end():]
    if rest.strip():
        pipe.searches.append((rest, first_at + m.end()))

    # Each command's place: shaping the rows (search / where / fields), then
    # one aggregate, then sort / head.
    stage = 0
    for seg, at in segments[1:]:
        cm = re.match(r"\s*([A-Za-z_]+)", seg)
        if not cm:
            raise QueryError("Expected a command after '|'", position=at)
        command, body_at = cm.group(1).lower(), at + cm.end()
        body = seg[cm.end():]
        cpos = at + cm.start(1)
        if command not in COMMANDS:
            raise QueryError(f"Unknown command '{cm.group(1)}'. Commands: {', '.join(COMMANDS)}", position=cpos)
        if command == "search":
            if stage > 0:
                raise QueryError("`search` comes before stats, top, sort and head", position=cpos)
            if not body.strip():
                raise QueryError("`search` needs terms", position=cpos)
            pipe.searches.append((body, body_at))
            continue
        r = _Reader(_tokens(body, body_at))
        if command in ("where", "fields", "table") and stage > 1:
            # After head it would act on the first N only — not what it reads as.
            raise QueryError(f"`{command}` comes before head", position=cpos)
        if command == "where":
            if pipe.aggregate is not None:
                pipe.having.append(_Where(r).parse())
            elif stage > 0:
                raise QueryError("`where` comes before sort and head", position=cpos)
            else:
                pipe.wheres.append(_Where(r).parse())
        elif command in ("fields", "table"):
            if pipe.aggregate is not None:
                if pipe.answer_fields is not None:
                    raise QueryError("One `fields` / `table` after `stats` or `top`", position=cpos)
                pipe.answer_fields, pipe.answer_fields_pos = _columns(r, command)
            elif stage > 0:
                raise QueryError(f"`{command}` comes before sort and head", position=cpos)
            else:
                pipe.fields, pipe.fields_pos = _columns(r, command)
            r.end(command)
        elif command in ("stats", "top"):
            if stage > 0:
                raise QueryError("One `stats` or `top` per query, before sort and head", position=cpos)
            if pipe.fields is not None:
                raise QueryError(f"`fields` / `table` before `{command}` has nothing to choose: the aggregate "
                                 f"makes the columns — put it after `{command}` to pick the answer's",
                                 position=cpos)
            pipe.before_aggregate = text[:at - 1].rstrip()
            pipe.aggregate = _stats(r, cpos) if command == "stats" else _top(r, cpos)
            stage = 1
        elif command == "sort":
            if stage > 1:
                raise QueryError("`sort` comes before head", position=cpos)
            pipe.sorts = _sort(r)
            stage = 1 if stage == 0 and pipe.aggregate is None else max(stage, 1)
        else:  # head / limit
            tok = r.expect("NUMBER", f"a number after `{command}`")
            r.end(command)
            if not isinstance(tok.value, int) or tok.value < 1:
                raise QueryError(f"`{command}` takes a whole number of at least 1", position=tok.pos)
            pipe.head, pipe.head_pos = tok.value, tok.pos
            stage = 2
    return pipe
