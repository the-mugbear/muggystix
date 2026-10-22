"""Run a Query-page pipeline (v2.509.0): ``query_language`` → one SELECT.

* The dataset (``query_datasets``) gives the rows of ONE project, joined to
  their host.
* Search terms are the Hosts query language: ``host_query_dsl.parse_query`` +
  ``evaluate`` — the Hosts page's own predicates, never a copy.
* ``where`` compares declared columns; ``stats`` / ``top`` group and count in
  SQL; the size of the whole answer rides on it as a window (``count() OVER
  ()``), so "first N of M" is one statement.
* **Every grouped number opens its exact list**: a group's ``drilldown`` is the
  query before the aggregate plus ``| where <col>=<value> AND …`` — equality
  on the very expression the group was made by (``isnull`` for a null key), so
  the drilled list's total IS the group's count (pinned by the tests).

Read-only; the statement timeout is ``get_db``'s.
"""
from __future__ import annotations

import base64
import enum
import ipaddress
import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import Float, String, and_, cast, distinct, func, literal, or_
from sqlalchemy.dialects.postgresql import INET, aggregate_order_by
from sqlalchemy.dialects.postgresql import array_agg as pg_array_agg
from sqlalchemy.orm import Session

from app.db.models_auth import User
from app.services import host_query_dsl as dsl
from app.services.instants import parse_instant
from app.services.query_datasets import DATASETS, Dataset, QueryColumn
from app.services.query_language import (
    BoolOp, Compare, Cond, InList, IsNull, Like, NotCond, Pipeline, QueryError, Top, parse,
)

ROW_CAP = 500
GROUP_CAP = 1000
VALUES_CAP = 20
# The CSV export's listing cap (groups stay at GROUP_CAP).
EXPORT_ROW_CAP = 50_000


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def _dataset(pipe: Pipeline) -> Dataset:
    ds = DATASETS.get(pipe.dataset)
    if ds is None:
        # `port:445` alone is a Hosts-page habit (second review, Q8): say how
        # to write it here.
        hint = (
            " — to filter hosts with the Hosts language, start with `hosts`, e.g. `hosts port:445`"
            if pipe.searches and pipe.searches[0][0].lstrip().startswith(":") else ""
        )
        raise QueryError(
            f"Unknown dataset '{pipe.dataset}'. Datasets: {', '.join(DATASETS)}{hint}", position=pipe.dataset_pos,
        )
    return ds


def _column(ds: Dataset, name: str, pos: int) -> QueryColumn:
    col = ds.columns.get(name)
    if col is None:
        raise QueryError(
            f"Unknown column '{name}' in {ds.name}. Columns: {', '.join(ds.columns)}", position=pos,
        )
    return col


# ---------------------------------------------------------------------------
# Values: a literal in a query → the column's type, and back
# ---------------------------------------------------------------------------

def _parse_time(text: str) -> datetime:
    try:
        return parse_instant(text)
    except ValueError:
        raise ValueError("a date or time like \"2026-10-01\" or \"2026-10-01T12:00:00+00:00\"")


def _coerce(col: QueryColumn, value: object, pos: int) -> object:
    try:
        if col.type == "text":
            if isinstance(value, bool):
                raise ValueError("text")
            return str(value)
        if col.type == "int":
            if isinstance(value, bool) or isinstance(value, float):
                raise ValueError("a whole number")
            return int(value)
        if col.type == "float":
            if isinstance(value, bool):
                raise ValueError("a number")
            return float(value)
        if col.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            raise ValueError("true or false")
        if col.type == "datetime":
            if not isinstance(value, str):
                raise ValueError('a quoted date, like "2026-10-01"')
            return _parse_time(value)
        if col.type == "enum":
            try:
                member = col.enum(str(value).lower())
                return member.value if col.enum_as_text else member
            except ValueError:
                raise ValueError("one of " + ", ".join(f'"{m.value}"' for m in col.enum))
    except ValueError as exc:
        raise QueryError(f"'{col.name}' compares with {exc}", position=pos)
    raise QueryError(f"'{col.name}' cannot be compared", position=pos)  # pragma: no cover


def _plain(value: Any, col: Optional[QueryColumn] = None) -> Any:
    """A database value as the answer carries it (JSON)."""
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and col is not None and col.enum is not None:
        # A value cast to text in an aggregate: the stored NAME.
        member = col.enum.__members__.get(value)
        return member.value if member is not None else value
    return value


def _quote(value: Any) -> str:
    """A value written back into a query, read again as the same value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


# ---------------------------------------------------------------------------
# where
# ---------------------------------------------------------------------------

_OPS = {
    "=": lambda c, v: c == v, "!=": lambda c, v: c != v,
    "<": lambda c, v: c < v, "<=": lambda c, v: c <= v,
    ">": lambda c, v: c > v, ">=": lambda c, v: c >= v,
}


def _condition(lookup: Callable[[str, int], QueryColumn], cond: Cond, fold: bool = False):
    """One condition of a `where`.  ``lookup`` names the columns it may
    compare: the dataset's (before an aggregate) or the answer's (after one —
    the aggregates by their names, compared in HAVING).  ``fold`` reads `=`,
    `!=` and `IN` on text ignoring case — only to count what an empty answer
    WOULD have held (`_case_hint`); a query's own `=` is exact."""
    if isinstance(cond, BoolOp):
        parts = [_condition(lookup, c, fold) for c in cond.children]
        return and_(*parts) if cond.op == "and" else or_(*parts)
    if isinstance(cond, NotCond):
        # As the Hosts language reads NOT: what is not known does not match,
        # so its negation holds (`NOT service="ssh"` keeps ports with none).
        return dsl.negate(_condition(lookup, cond.child, fold))
    if isinstance(cond, IsNull):
        col = lookup(cond.column, cond.pos)
        return col.expr.isnot(None) if cond.negated else col.expr.is_(None)
    if isinstance(cond, Like):
        col = lookup(cond.column, cond.pos)
        if col.type != "text":
            raise QueryError(f"like() takes a text column; '{col.name}' is {col.type}", position=cond.pos)
        return col.expr.ilike(cond.pattern)
    if isinstance(cond, InList):
        col = lookup(cond.column, cond.pos)
        values = [_coerce(col, v, cond.pos) for v in cond.values]
        if fold and col.type == "text":
            return func.lower(col.expr).in_([v.lower() for v in values])
        return col.expr.in_(values)
    if isinstance(cond, Compare):
        col = lookup(cond.column, cond.pos)
        if col.type in ("bool", "enum") and cond.op not in ("=", "!="):
            raise QueryError(f"'{col.name}' takes = or != only", position=cond.pos)
        value = _coerce(col, cond.value, cond.pos)
        if col.address and cond.op not in ("=", "!="):
            # An address is ordered as an address (second review, I1).
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise QueryError(
                    f"'{col.name}' {cond.op} compares with an address, like \"10.0.0.9\" — "
                    "for a range use subnet: in the search terms", position=cond.pos,
                )
            return _OPS[cond.op](cast(col.expr, INET), cast(literal(value), INET))
        if fold and col.type == "text" and cond.op in ("=", "!="):
            return _OPS[cond.op](func.lower(col.expr), value.lower())
        return _OPS[cond.op](col.expr, value)
    raise QueryError("Unreadable condition")  # pragma: no cover


def _exact_text(lookup: Callable[[str, int], QueryColumn], cond: Cond) -> Optional[tuple]:
    """The first `col = "text"` (or `col IN (…)`) of a condition: (column
    name, value) — what an empty answer's hint names."""
    if isinstance(cond, BoolOp):
        return next((hit for c in cond.children if (hit := _exact_text(lookup, c))), None)
    if isinstance(cond, Compare) and cond.op == "=" and lookup(cond.column, cond.pos).type == "text":
        return cond.column, str(cond.value)
    if isinstance(cond, InList) and lookup(cond.column, cond.pos).type == "text":
        return cond.column, str(cond.values[0])
    return None


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------

def _search_nodes(ds: Dataset, pipe: Pipeline) -> list:
    """The search terms, parsed by the Hosts language (no database)."""
    nodes = []
    for text, offset in pipe.searches:
        if ds.host_filter == "none":
            raise QueryError(
                f"{ds.name} has no host, so it takes no search terms — compare its columns with | where",
                position=offset,
            )
        try:
            nodes.append(dsl.parse_query(text))
        except dsl.DSLError as exc:
            # The Hosts language's own error, placed in the whole query.
            raise QueryError(exc.message, position=None if exc.position is None else offset + exc.position)
    return nodes


def _filtered(db: Session, project_id: int, user: User, ds: Dataset, plan: "_Plan"):
    """The dataset's rows of one project, with the plan's search terms (which
    may read the database to resolve — `has:eol`, `assigned:me`) and its
    `where` conditions applied."""
    query = ds.base(db, project_id)
    ctx = dsl.BuildCtx(db=db, current_user=user, project_id=project_id)
    if plan.search_nodes:
        # Every search command is about the SAME host: the terms are joined
        # before a row with several hosts (a finding) asks "does one of them
        # match?" — `findings port:445 | search port:22` is ONE host with
        # both, as on `hosts` (second review 2026-10-16, I4: each command was
        # its own EXISTS, so two different hosts satisfied it).
        predicate = and_(*[dsl.evaluate(node, ctx) for node in plan.search_nodes])
        query = query.filter(ds.host_filter(predicate) if callable(ds.host_filter) else predicate)
    for condition in plan.conditions:
        query = query.filter(condition)
    return query


def _sort_key(col: QueryColumn):
    return col.sort_expr if col.sort_expr is not None else col.expr


@dataclass
class _Plan:
    """Everything a query needs that does not need the database (2.510.1,
    external review A4): every name and type is checked here, so saving a
    query (``check``) and running one (``run``) refuse exactly the same
    things.  ``run`` only adds what needs the database — evaluating the
    search terms, and the statement."""
    search_nodes: list
    conditions: list
    # rows | stats
    kind: str
    selected: list
    out_cols: List[Dict[str, str]]
    order: list
    limit: int
    capped: bool
    # A listing's link expressions; a grouping's columns and value columns.
    links: list = field(default_factory=list)
    by: List[QueryColumn] = field(default_factory=list)
    value_cols: List[Optional[QueryColumn]] = field(default_factory=list)
    # After the aggregate: `where` (HAVING) and `fields` (the answer's
    # columns kept, by index).
    having: list = field(default_factory=list)
    keep: Optional[List[int]] = None
    # A listing's order as (expression, descending) — the key a page's
    # `after` continues from (the row's own id last, so it is unique).
    keyset: list = field(default_factory=list)


def _plan_rows(ds: Dataset, pipe: Pipeline, row_cap: int) -> Dict[str, Any]:
    names = pipe.fields or ds.default_fields
    positions = pipe.fields_pos if pipe.fields else [pipe.dataset_pos] * len(names)
    cols = [_column(ds, n, p) for n, p in zip(names, positions)]
    links = ds.link_exprs if ds.link else []
    order = []
    keyset = []
    sorts = pipe.sorts or [(s.lstrip("-"), s.startswith("-"), 0) for s in ds.default_sort]
    for name, desc, pos in sorts:
        key = _sort_key(_column(ds, name, pos))
        order.append(key.desc().nullslast() if desc else key.asc().nullslast())
        keyset.append((key, desc))
    # The row's own id last: a listing is stable from one run to the next.
    order.append(ds.base_id.asc())
    keyset.append((ds.base_id, False))
    return dict(
        keyset=keyset,
        kind="rows",
        selected=[c.expr for c in cols],
        out_cols=[{"name": c.name, "type": c.type} for c in cols],
        order=order,
        limit=min(pipe.head or row_cap, row_cap),
        capped=(pipe.head or 0) > row_cap,
        links=links,
        value_cols=cols,
    )


# ---------------------------------------------------------------------------
# A listing's pages: key-set, never OFFSET (review 2026-10-16, Q5)
# ---------------------------------------------------------------------------

def _encode_key(values: List[Any]) -> str:
    """The last row's sort key as an opaque `after` token."""
    out = []
    for v in values:
        if isinstance(v, datetime):
            out.append(["t", v.isoformat()])
        elif isinstance(v, enum.Enum):
            out.append(["e", v.name])
        elif isinstance(v, Decimal):
            out.append(["n", str(v)])
        else:
            out.append(["v", v])
    return base64.urlsafe_b64encode(json.dumps(out, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_key(token: str, keyset: list) -> List[Any]:
    unreadable = QueryError("`after` is not a place in this listing — start again from its first rows")
    try:
        raw = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (ValueError, TypeError):
        raise unreadable
    if not isinstance(raw, list) or len(raw) != len(keyset):
        raise unreadable
    values = []
    for item, (key, _desc) in zip(raw, keyset):
        if not isinstance(item, list) or len(item) != 2 or item[0] not in ("t", "e", "n", "v"):
            raise unreadable
        tag, value = item
        try:
            if tag == "t":
                value = datetime.fromisoformat(value)
            elif tag == "e":
                value = key.type.enum_class[value]
            elif tag == "n":
                value = Decimal(value)
        except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
            raise unreadable
        if tag == "v" and not (value is None or isinstance(value, (str, int, float, bool))):
            raise unreadable
        values.append(value)
    return values


def _after(keyset: list, values: List[Any]):
    """The rows strictly after the key ``values`` in the listing's order —
    each key ascending or descending, NULLS LAST, as ``_plan_rows`` orders."""
    arms = []
    for i, ((key, desc), value) in enumerate(zip(keyset, values)):
        if value is None:
            continue  # nulls are last: nothing is past a null in this key
        bound = literal(value, type_=key.type)
        beyond = or_(key < bound if desc else key > bound, key.is_(None))
        same = [k.is_(None) if v is None else k == literal(v, type_=k.type)
                for (k, _), v in zip(keyset[:i], values[:i])]
        arms.append(and_(*same, beyond))
    return or_(*arms) if arms else literal(False)


def _run_rows(query, ds: Dataset, plan: _Plan, after: Optional[str] = None,
              paged: bool = True) -> Dict[str, Any]:
    """A listing.  ``after`` continues from the place a previous page's
    ``next_after`` named; ``total`` is still the whole answer and ``start``
    says where this page begins in it.  ``paged`` (no `head`): only then is
    there a next page to name."""
    whole = query
    if after is not None:
        query = query.filter(_after(plan.keyset, _decode_key(after, plan.keyset)))
    keys = [key for key, _ in plan.keyset]
    result = (
        query.with_entities(*plan.selected, *plan.links, *keys, func.count().over().label("_total"))
        .order_by(*plan.order)
        .limit(plan.limit)
        .all()
    )
    remaining = int(result[0]._total) if result else 0
    if after is None:
        total = remaining
    else:
        total = int(whole.with_entities(func.count()).order_by(None).scalar() or 0)
    start = total - remaining
    width = len(plan.selected)
    links_end = width + len(plan.links)
    next_after = None
    if paged and result and remaining > len(result):
        next_after = _encode_key(list(result[-1][links_end:links_end + len(keys)]))
    return {
        "kind": "rows",
        "columns": plan.out_cols,
        "rows": [[_plain(v, c) for v, c in zip(r[:width], plan.value_cols)] for r in result],
        "total": total,
        "limit": plan.limit,
        "capped": plan.capped,
        "drilldowns": None,
        # Each row's page (the host, the finding, the endpoint) — the dataset's.
        "links": [ds.link(tuple(r[width:links_end])) for r in result] if plan.links else None,
        "by": None,
        "truncated_values": [],
        "start": start,
        "next_after": next_after,
    }


# ---------------------------------------------------------------------------
# stats / top
# ---------------------------------------------------------------------------

def _aggregate_expr(ds: Dataset, agg) -> tuple:
    """(expression, answer type, column) for one aggregate."""
    if agg.func == "count":
        if agg.column is None:
            return func.count(), "int", None
        col = _column(ds, agg.column, agg.pos)
        return func.count(col.expr), "int", col
    col = _column(ds, agg.column, agg.pos)
    if agg.func == "dc":
        return func.count(distinct(col.expr)), "int", col
    if agg.func in ("avg", "sum"):
        if not col.numeric:
            raise QueryError(f"{agg.func}() takes a number column; '{col.name}' is {col.type}", position=agg.pos)
        expr = func.avg(cast(col.expr, Float)) if agg.func == "avg" else func.sum(col.expr)
        return expr, "float" if agg.func == "avg" else col.type, col
    if agg.func in ("min", "max"):
        if col.type in ("bool", "enum"):
            raise QueryError(f"{agg.func}() does not take '{col.name}' ({col.type})", position=agg.pos)
        return (func.min(col.expr) if agg.func == "min" else func.max(col.expr)), col.type, col
    if agg.func == "values":
        # The ordered distinct values, nulls last, and only VALUES_CAP + 1 of
        # them leave the database (external review 2026-10-16, A3): the one
        # past the cap says the list was cut.
        text = cast(col.expr, String)
        return pg_array_agg(aggregate_order_by(distinct(text), text.asc().nullslast()))[1:VALUES_CAP + 1], "list", col
    raise QueryError(f"Unknown function '{agg.func}'", position=agg.pos)  # pragma: no cover


def _drilldown(pipe: Pipeline, by: List[QueryColumn], key: List[Any]) -> str:
    terms = []
    for col, value in zip(by, key):
        if value is None:
            terms.append(f"isnull({col.name})")
        else:
            terms.append(f"{col.name}={_quote(_plain(value, col))}")
    return f"{pipe.before_aggregate} | where {' AND '.join(terms)}"


def _answer_column(name: str, expr, type_: str, source: Optional[QueryColumn]) -> QueryColumn:
    """An aggregate as a column a `where` after the aggregate compares.  A
    min / max is its source column's kind (an address stays an address)."""
    if source is not None and source.type == type_:
        return replace(source, name=name, expr=expr, sort_expr=None)
    return QueryColumn(name, expr, type_, "")


def _plan_grouped(ds: Dataset, pipe: Pipeline, top_total=None) -> Dict[str, Any]:
    """``top_total``: what `top`'s percent divides by when a `where` follows
    it — the rows before grouping (a scalar subquery ``run`` passes).  Without
    one the percent is a window over the groups, which HAVING would shrink."""
    agg = pipe.aggregate
    if isinstance(agg, Top):
        by = [_column(ds, n, p) for n, p in zip(agg.columns, agg.columns_pos or [agg.pos] * len(agg.columns))]
        count = func.count()
        if top_total is None:
            # `check` and a `top` with no `where` after it: no subquery needed
            # (literal 1 only lets `check` read the condition's names).
            top_total = literal(1) if pipe.having else func.sum(count).over()
        percent = count * 100.0 / top_total
        selected = [c.expr for c in by] + [count, percent.label("percent")]
        out_cols = [{"name": c.name, "type": c.type} for c in by] + [
            {"name": "count", "type": "int"}, {"name": "percent", "type": "float"},
        ]
        named = {c.name: _sort_key(c) for c in by}
        named["count"] = count
        answer = {c.name: c for c in by}
        answer["count"] = QueryColumn("count", count, "int", "")
        answer["percent"] = QueryColumn("percent", percent, "float", "")
        default_order = [count.desc()] + [_sort_key(c).asc().nullslast() for c in by]
        limit = min(agg.limit, pipe.head or agg.limit, GROUP_CAP)
        value_cols: List[Optional[QueryColumn]] = [None, None]
    else:
        by = [_column(ds, n, p) for n, p in zip(agg.by, agg.by_pos or [agg.pos] * len(agg.by))]
        aggs = [_aggregate_expr(ds, a) for a in agg.aggregates]
        selected = [c.expr for c in by] + [expr for expr, _, _ in aggs]
        out_cols = [{"name": c.name, "type": c.type} for c in by] + [
            {"name": a.alias, "type": t} for a, (_, t, _) in zip(agg.aggregates, aggs)
        ]
        named = {c.name: _sort_key(c) for c in by}
        named.update({a.alias: expr for a, (expr, _, _) in zip(agg.aggregates, aggs)})
        answer = {c.name: c for c in by}
        answer.update({a.alias: _answer_column(a.alias, expr, t, col)
                       for a, (expr, t, col) in zip(agg.aggregates, aggs)})
        default_order = [_sort_key(c).asc().nullslast() for c in by]
        limit = min(pipe.head or GROUP_CAP, GROUP_CAP)
        value_cols = [col for _, _, col in aggs]

    command = "top" if isinstance(agg, Top) else "stats"

    def answer_column(name: str, pos: int) -> QueryColumn:
        col = answer.get(name)
        if col is None:
            raise QueryError(
                f"`where` after `{command}` compares the answer's columns: {', '.join(answer)}", position=pos,
            )
        if col.type == "list":
            raise QueryError(f"'{name}' is a values() list — `where` compares one value", position=pos)
        return col

    # `where` after the aggregate keeps groups (HAVING); the groups' total
    # (the window in `_run_grouped`) is counted after it.
    having = [_condition(answer_column, cond) for cond in pipe.having]

    keep: Optional[List[int]] = None
    if pipe.answer_fields is not None:
        names = [c["name"] for c in out_cols]
        keep = []
        for name, pos in zip(pipe.answer_fields, pipe.answer_fields_pos):
            if name not in names:
                raise QueryError(
                    f"`fields` after `{command}` takes a column of the answer: {', '.join(names)}", position=pos,
                )
            keep.append(names.index(name))

    order = []
    for name, desc, pos in pipe.sorts:
        if name not in named:
            raise QueryError(
                f"`sort` after `{'top' if isinstance(agg, Top) else 'stats'}` takes a column of the answer: "
                f"{', '.join(named)}",
                position=pos,
            )
        key = named[name]
        order.append(key.desc().nullslast() if desc else key.asc().nullslast())
    order += default_order
    return dict(
        kind="stats", selected=selected, out_cols=out_cols, order=order, limit=limit,
        capped=(pipe.head or 0) > GROUP_CAP, by=by, value_cols=value_cols, having=having, keep=keep,
    )


def _project(answer: Dict[str, Any], keep: List[int]) -> Dict[str, Any]:
    """`fields` after the aggregate: the answer's columns it names, in its
    order.  The drill-downs are the rows' own (made from every group column),
    so a group column left out still opens its exact list."""
    columns = [answer["columns"][i] for i in keep]
    names = {c["name"] for c in columns}
    return {
        **answer,
        "columns": columns,
        "rows": [[row[i] for i in keep] for row in answer["rows"]],
        "by": [b for b in answer["by"] if b in names],
        "truncated_values": [cell for cell in answer["truncated_values"] if cell[1] in names],
    }


def _run_grouped(query, pipe: Pipeline, plan: _Plan) -> Dict[str, Any]:
    by = plan.by
    statement = query.with_entities(*plan.selected, func.count().over().label("_groups"))
    if by:
        statement = statement.group_by(*[c.expr for c in by])
    if plan.having:
        statement = statement.having(and_(*plan.having))
    result = statement.order_by(*plan.order).limit(plan.limit).all()

    rows, drilldowns, truncated = [], [], []
    for i, r in enumerate(result):
        key = list(r[:len(by)])
        values = list(r[len(by):-1])
        out = [_plain(v, c) for v, c in zip(key, by)]
        for v, col, spec in zip(values, plan.value_cols, plan.out_cols[len(by):]):
            if spec["type"] == "list":
                items = [_plain(x, col) for x in (v or []) if x is not None]
                if len(items) > VALUES_CAP:
                    # Said, never silent: this cell holds the first VALUES_CAP.
                    truncated.append([i, spec["name"]])
                out.append(items[:VALUES_CAP])
            elif spec["type"] == "float" and v is not None:
                out.append(round(float(v), 2))
            else:
                out.append(_plain(v, col))
        rows.append(out)
        drilldowns.append(_drilldown(pipe, by, key) if by else pipe.before_aggregate)
    total = int(result[0]._groups) if result else 0
    answer = {
        "kind": "stats",
        "columns": plan.out_cols,
        "rows": rows,
        "total": total,
        "limit": plan.limit,
        "capped": plan.capped,
        "drilldowns": drilldowns,
        "links": None,
        # The answer's group columns (the first ones): what a chart's categories are.
        "by": [c.name for c in by],
        # [row, column] of each values() list cut at VALUES_CAP.
        "truncated_values": truncated,
    }
    return _project(answer, plan.keep) if plan.keep is not None else answer


def _case_hint(db: Session, project_id: int, user: User, ds: Dataset, pipe: Pipeline, plan: _Plan) -> Optional[str]:
    """Why an empty answer may be empty: `=` on text is exact and
    case-sensitive.  Counts what the same `where` would
    match ignoring case — said, never done: the query's `=` stays exact, so
    a group's drill-down stays its exact list (owner, 2026-10-16, review Q4)."""
    lookup = lambda name, pos: _column(ds, name, pos)  # noqa: E731
    hit = next((h for cond in pipe.wheres if (h := _exact_text(lookup, cond))), None)
    if hit is None:
        return None
    column, value = hit
    folded = [_condition(lookup, cond, fold=True) for cond in pipe.wheres]
    matches = (
        _filtered(db, project_id, user, ds, replace(plan, conditions=folded))
        .with_entities(func.count()).order_by(None).scalar()
    ) or 0
    if matches:
        return (f"`=` is exact and case-sensitive. Ignoring case, {matches:,} "
                f"{'row' if matches == 1 else 'rows'} would match — like({column}, {_quote(value)}) "
                "compares ignoring case")
    return (f"`=` is exact and case-sensitive. like({column}, {_quote('%' + value + '%')}) "
            "matches part of a value, ignoring case")


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def _plan(text: str, row_cap: int) -> tuple:
    """(pipeline, dataset, plan) — the query read and checked, no database."""
    pipe = parse(text)
    ds = _dataset(pipe)
    search_nodes = _search_nodes(ds, pipe)
    conditions = [_condition(lambda name, pos: _column(ds, name, pos), cond) for cond in pipe.wheres]
    shape = _plan_grouped(ds, pipe) if pipe.aggregate is not None else _plan_rows(ds, pipe, row_cap)
    return pipe, ds, _Plan(search_nodes=search_nodes, conditions=conditions, **shape)


def check(text: str) -> None:
    """Refuse (``QueryError``, a 400 with a position) whatever ``run`` would
    refuse before reading the database: grammar, dataset, every column,
    value, aggregate and sort name.  What saving a query calls — one
    definition of a valid query (external review 2026-10-16, A4)."""
    _plan(text, ROW_CAP)


def _hosts_q(ds: Dataset, pipe: Pipeline) -> Optional[str]:
    """The Hosts-page query whose list is exactly this answer's rows (review
    Q7: `hosts <terms>` ↔ `/hosts?q=<terms>`): a `hosts` listing with no
    `where` — its rows are the hosts its search terms match ("" for every
    host).  None for any other answer."""
    if ds.name != "hosts" or pipe.wheres or pipe.aggregate is not None:
        return None
    terms = [t.strip() for t, _ in pipe.searches if t.strip()]
    if len(terms) <= 1:
        return terms[0] if terms else ""
    # Several search commands are one host's terms, ANDed (as `_filtered` reads them).
    return " AND ".join(f"({t})" for t in terms)


def run(db: Session, project_id: int, user: User, text: str, *, row_cap: Optional[int] = None,
        after: Optional[str] = None) -> Dict[str, Any]:
    """The answer to one query, in one statement.  ``row_cap`` is the most
    rows a listing returns (default ``ROW_CAP``; the CSV export's is larger).
    ``after`` (a listing's ``next_after``) is the next page of it: the rows
    past that place, the cap still the page's size."""
    pipe, ds, plan = _plan(text, ROW_CAP if row_cap is None else row_cap)
    if after is not None:
        if plan.kind != "rows":
            raise QueryError("`after` pages a listing — a stats or top answer has no next page")
        if pipe.head is not None:
            raise QueryError("`after` pages a listing without `head` — `head` already says how many rows")
    query = _filtered(db, project_id, user, ds, plan)
    if isinstance(pipe.aggregate, Top) and pipe.having:
        # `top … | where …`: the percent stays of every row — `top` computes
        # it before the where (the window would be of the kept groups).
        rows_total = query.with_entities(func.count()).order_by(None).statement.correlate(None).scalar_subquery()
        plan = replace(plan, **_plan_grouped(ds, pipe, top_total=rows_total))
    if plan.kind == "stats":
        answer = _run_grouped(query, pipe, plan)
    else:
        answer = _run_rows(query, ds, plan, after=after, paged=pipe.head is None)
    hint = _case_hint(db, project_id, user, ds, pipe, plan) if answer["total"] == 0 else None
    return {"query": text, "dataset": ds.name, **answer, "hint": hint, "hosts_q": _hosts_q(ds, pipe)}


# ---------------------------------------------------------------------------
# The builder's reading of a query (review Q9): the grammar is here, so the
# page's builder asks rather than parsing the text a second time.
# ---------------------------------------------------------------------------

BUILDER_OPS = ("=", "!=", "<", "<=", ">", ">=")


def _builder_conditions(cond, out: list) -> Optional[str]:
    """Flatten an AND of simple comparisons into the builder's rows; the
    reason it cannot, else None."""
    if isinstance(cond, BoolOp):
        if cond.op != "and":
            return "an OR"
        for child in cond.children:
            reason = _builder_conditions(child, out)
            if reason:
                return reason
        return None
    if isinstance(cond, Compare) and cond.op in BUILDER_OPS:
        value = cond.value
        out.append({"column": cond.column, "op": cond.op,
                    "value": ("true" if value else "false") if isinstance(value, bool) else str(value)})
        return None
    if isinstance(cond, Like):
        if "%" not in cond.pattern:
            return "a like() with no %"  # the builder's like matches part of a value
        out.append({"column": cond.column, "op": "like", "value": cond.pattern})
        return None
    if isinstance(cond, IsNull):
        out.append({"column": cond.column, "op": "isnotnull" if cond.negated else "isnull", "value": ""})
        return None
    if isinstance(cond, InList):
        return "an IN list"
    if isinstance(cond, NotCond):
        return "a NOT"
    return "a condition it has no row for"  # pragma: no cover


def builder_shape(text: str) -> Dict[str, Any]:
    """What the page's builder shows for a query: ``{"shape": …}`` when it
    can show all of it, else ``{"shape": None, "reason": …}`` — never a
    builder that silently drops part of the query.  Refuses (``QueryError``)
    whatever ``check`` refuses; reads no database."""
    pipe, _ds, _plan_ = _plan(text, ROW_CAP)

    def cannot(reason: str) -> Dict[str, Any]:
        return {"shape": None, "reason": f"The builder has no place for {reason}; edit the text instead."}

    terms = [t.strip() for t, _ in pipe.searches if t.strip()]
    conditions: list = []
    for cond in pipe.wheres:
        reason = _builder_conditions(cond, conditions)
        if reason:
            return cannot(reason)
    if pipe.fields is not None:
        return cannot("a column list (fields / table)")
    if pipe.having or pipe.answer_fields is not None:
        return cannot("a command after stats or top")
    shape: Dict[str, Any] = {
        "dataset": pipe.dataset,
        "terms": terms[0] if len(terms) == 1 else " AND ".join(f"({t})" for t in terms),
        "conditions": conditions,
        "by": [],
        "measure": {"fn": "count", "column": "", "as": None},
        "most_first": False,
        "head": pipe.head or 0,
    }
    agg = pipe.aggregate
    if agg is None:
        if pipe.sorts:
            return cannot("the order of a listing")
        return {"shape": shape}
    if isinstance(agg, Top):
        if pipe.sorts or pipe.head:
            return cannot("sort or head after top")
        shape.update(by=list(agg.columns), measure={"fn": "top", "column": "", "as": None}, head=agg.limit)
        return {"shape": shape}
    if len(agg.aggregates) != 1:
        return cannot("more than one measure")
    measure = agg.aggregates[0]
    if measure.func == "count" and measure.column is not None:
        return cannot("count(column)")
    shape.update(by=list(agg.by),
                 measure={"fn": measure.func, "column": measure.column or "",
                          "as": None if measure.alias == (measure.func if measure.column is None
                                                          else f"{measure.func}({measure.column})")
                          else measure.alias})
    if pipe.sorts:
        if len(pipe.sorts) != 1 or not pipe.sorts[0][1] or pipe.sorts[0][0] != measure.alias:
            return cannot("an order other than the measure, most first")
        shape["most_first"] = True
    return {"shape": shape}


VALUES_SUGGEST_CAP = 20


def column_values(db: Session, project_id: int, dataset: str, column: str, contains: str = "",
                  limit: int = 12) -> Optional[List[Dict[str, Any]]]:
    """A text column's values in the project that contain ``contains``, most
    rows first — what the editor offers after `where col = "` (v2.512.0).
    None for a column that is not text (an enum's values are the schema's;
    a number is typed).  A typing aid: the dataset's own rows, no search
    terms, no `where`."""
    ds = DATASETS.get(dataset)
    if ds is None:
        raise QueryError(f"Unknown dataset '{dataset}'. Datasets: {', '.join(DATASETS)}")
    col = _column(ds, column, 0)
    if col.type != "text" or col.enum is not None:
        return None
    count = func.count()
    query = ds.base(db, project_id).with_entities(col.expr, count).filter(col.expr.isnot(None))
    if contains:
        pattern = contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(col.expr.ilike(f"%{pattern}%", escape="\\"))
    rows = query.group_by(col.expr).order_by(count.desc(), col.expr.asc()).limit(min(limit, VALUES_SUGGEST_CAP)).all()
    return [{"value": value, "count": int(n)} for value, n in rows]


def to_csv(answer: Dict[str, Any]) -> str:
    """An answer as CSV: the columns, then the rows (a list joined by "; "),
    every cell through the codebase's one formula guard (``csv_utils``) — a
    scanner's title is external text."""
    import csv
    import io

    from app.services.csv_utils import safe_csv_row

    names = [c["name"] for c in answer["columns"]]
    cut = {(i, name) for i, name in answer.get("truncated_values") or []}
    out = io.StringIO()
    writer = csv.writer(out)
    safe_csv_row(writer, names)
    for i, row in enumerate(answer["rows"]):
        cells = []
        for name, value in zip(names, row):
            if isinstance(value, list):
                # A cut values() list says so in the file too.
                value = "; ".join(str(v) for v in value) + ("; …" if (i, name) in cut else "")
            cells.append(value)
        safe_csv_row(writer, cells)
    return out.getvalue()


def schema() -> Dict[str, Any]:
    """What the page's reference and an agent read: datasets, columns,
    commands, functions and the caps."""
    return {
        "datasets": [
            {
                "name": ds.name,
                "description": ds.description,
                "columns": [{"name": c.name, "type": c.type, "description": c.description,
                             **({"values": [m.value for m in c.enum]} if c.enum else {})}
                            for c in ds.columns.values()],
                "default_fields": ds.default_fields,
                "examples": ds.examples,
            }
            for ds in DATASETS.values()
        ],
        "commands": [
            {"name": "search", "syntax": "<dataset> <terms> | search <terms>",
             "description": "The Hosts query language (port:, has:, cve:, site:…) — always about the row's host."},
            {"name": "where", "syntax": 'where <column> = "value" AND …',
             "description": "Compare the dataset's columns: = != < <= > >=, IN (…), like(col, \"%pat%\"), "
                            "isnull(col), isnotnull(col), AND / OR / NOT, parentheses. = is exact and "
                            "case-sensitive; like() ignores case. After stats or top it compares the "
                            "answer's columns: … | stats count by title | where count > 5."},
            {"name": "stats", "syntax": "stats count, dc(col) as name … by col, …",
             "description": "count, dc (distinct count), min, max, avg, sum, values."},
            {"name": "top", "syntax": "top [limit=N] col, …", "description": "Count and percent, most first."},
            {"name": "sort", "syntax": "sort -col, +col",
             "description": "-col sorts descending, +col (or col) ascending."},
            {"name": "head", "syntax": "head N", "description": "The first N (also: limit N)."},
            {"name": "fields", "syntax": "fields col, … (or table)",
             "description": "The columns of a listing — or, after stats or top, the answer's columns to keep."},
        ],
        "caps": {"rows": ROW_CAP, "groups": GROUP_CAP, "values": VALUES_CAP},
    }
