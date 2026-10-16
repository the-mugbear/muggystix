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

import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import Float, String, and_, cast, distinct, func, or_
from sqlalchemy.orm import Session

from app.db.models_auth import User
from app.services import host_query_dsl as dsl
from app.services.query_datasets import DATASETS, Dataset, QueryColumn
from app.services.query_language import (
    BoolOp, Compare, Cond, InList, IsNull, Like, NotCond, Pipeline, QueryError, Top, parse,
)

ROW_CAP = 500
GROUP_CAP = 1000
VALUES_CAP = 20


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def _dataset(pipe: Pipeline) -> Dataset:
    ds = DATASETS.get(pipe.dataset)
    if ds is None:
        raise QueryError(
            f"Unknown dataset '{pipe.dataset}'. Datasets: {', '.join(DATASETS)}", position=pipe.dataset_pos,
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
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("a date or time like \"2026-10-01\" or \"2026-10-01T12:00:00+00:00\"")
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


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
                return col.enum(str(value).lower())
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


def _condition(ds: Dataset, cond: Cond):
    if isinstance(cond, BoolOp):
        parts = [_condition(ds, c) for c in cond.children]
        return and_(*parts) if cond.op == "and" else or_(*parts)
    if isinstance(cond, NotCond):
        # As the Hosts language reads NOT: what is not known does not match,
        # so its negation holds (`NOT service="ssh"` keeps ports with none).
        return dsl.negate(_condition(ds, cond.child))
    if isinstance(cond, IsNull):
        col = _column(ds, cond.column, cond.pos)
        return col.expr.isnot(None) if cond.negated else col.expr.is_(None)
    if isinstance(cond, Like):
        col = _column(ds, cond.column, cond.pos)
        if col.type != "text":
            raise QueryError(f"like() takes a text column; '{col.name}' is {col.type}", position=cond.pos)
        return col.expr.ilike(cond.pattern)
    if isinstance(cond, InList):
        col = _column(ds, cond.column, cond.pos)
        return col.expr.in_([_coerce(col, v, cond.pos) for v in cond.values])
    if isinstance(cond, Compare):
        col = _column(ds, cond.column, cond.pos)
        if col.type in ("bool", "enum") and cond.op not in ("=", "!="):
            raise QueryError(f"'{col.name}' takes = or != only", position=cond.pos)
        return _OPS[cond.op](col.expr, _coerce(col, cond.value, cond.pos))
    raise QueryError("Unreadable condition")  # pragma: no cover


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------

def _filtered(db: Session, project_id: int, user: User, ds: Dataset, pipe: Pipeline):
    query = ds.base(db, project_id)
    ctx = dsl.BuildCtx(db=db, current_user=user, project_id=project_id)
    for text, offset in pipe.searches:
        try:
            node = dsl.parse_query(text)
        except dsl.DSLError as exc:
            # The Hosts language's own error, placed in the whole query.
            raise QueryError(exc.message, position=None if exc.position is None else offset + exc.position)
        query = query.filter(dsl.evaluate(node, ctx))
    for cond in pipe.wheres:
        query = query.filter(_condition(ds, cond))
    return query


def _sort_key(col: QueryColumn):
    return col.sort_expr if col.sort_expr is not None else col.expr


def _rows(query, ds: Dataset, pipe: Pipeline) -> Dict[str, Any]:
    names = pipe.fields or ds.default_fields
    cols = [_column(ds, n, 0) for n in names]
    order = []
    sorts = pipe.sorts or [(s.lstrip("-"), s.startswith("-"), 0) for s in ds.default_sort]
    for name, desc, pos in sorts:
        key = _sort_key(_column(ds, name, pos))
        order.append(key.desc().nullslast() if desc else key.asc().nullslast())
    # The row's own id last: a listing is stable from one run to the next.
    order.append(ds.base_id.asc())
    limit = min(pipe.head or ROW_CAP, ROW_CAP)
    result = (
        query.with_entities(*[c.expr for c in cols], func.count().over().label("_total"))
        .order_by(*order)
        .limit(limit)
        .all()
    )
    total = int(result[0]._total) if result else 0
    return {
        "kind": "rows",
        "columns": [{"name": c.name, "type": c.type} for c in cols],
        "rows": [[_plain(v, c) for v, c in zip(r[:-1], cols)] for r in result],
        "total": total,
        "limit": limit,
        "capped": (pipe.head or 0) > ROW_CAP,
        "drilldowns": None,
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
        return func.array_agg(distinct(cast(col.expr, String))), "list", col
    raise QueryError(f"Unknown function '{agg.func}'", position=agg.pos)  # pragma: no cover


def _drilldown(pipe: Pipeline, by: List[QueryColumn], key: List[Any]) -> str:
    terms = []
    for col, value in zip(by, key):
        if value is None:
            terms.append(f"isnull({col.name})")
        else:
            terms.append(f"{col.name}={_quote(_plain(value, col))}")
    return f"{pipe.before_aggregate} | where {' AND '.join(terms)}"


def _grouped(query, ds: Dataset, pipe: Pipeline) -> Dict[str, Any]:
    agg = pipe.aggregate
    if isinstance(agg, Top):
        by = [_column(ds, n, agg.pos) for n in agg.columns]
        count = func.count()
        selected = [c.expr for c in by] + [
            count,
            (count * 100.0 / func.sum(count).over()).label("percent"),
        ]
        out_cols = [{"name": c.name, "type": c.type} for c in by] + [
            {"name": "count", "type": "int"}, {"name": "percent", "type": "float"},
        ]
        named = {c.name: _sort_key(c) for c in by}
        named["count"] = count
        default_order = [count.desc()] + [_sort_key(c).asc().nullslast() for c in by]
        limit = min(agg.limit, pipe.head or agg.limit, GROUP_CAP)
        value_cols: List[Optional[QueryColumn]] = [None, None]
    else:
        by = [_column(ds, n, agg.pos) for n in agg.by]
        aggs = [_aggregate_expr(ds, a) for a in agg.aggregates]
        selected = [c.expr for c in by] + [expr for expr, _, _ in aggs]
        out_cols = [{"name": c.name, "type": c.type} for c in by] + [
            {"name": a.alias, "type": t} for a, (_, t, _) in zip(agg.aggregates, aggs)
        ]
        named = {c.name: _sort_key(c) for c in by}
        named.update({a.alias: expr for a, (expr, _, _) in zip(agg.aggregates, aggs)})
        default_order = [_sort_key(c).asc().nullslast() for c in by]
        limit = min(pipe.head or GROUP_CAP, GROUP_CAP)
        value_cols = [col for _, _, col in aggs]

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
    statement = query.with_entities(*selected, func.count().over().label("_groups"))
    if by:
        statement = statement.group_by(*[c.expr for c in by])
    result = statement.order_by(*order).limit(limit).all()

    rows, drilldowns = [], []
    for r in result:
        key = list(r[:len(by)])
        values = list(r[len(by):-1])
        out = [_plain(v, c) for v, c in zip(key, by)]
        for v, col, spec in zip(values, value_cols, out_cols[len(by):]):
            if spec["type"] == "list":
                items = sorted({_plain(x, col) for x in (v or []) if x is not None}, key=str)
                out.append(items[:VALUES_CAP])
            elif spec["type"] == "float" and v is not None:
                out.append(round(float(v), 2))
            else:
                out.append(_plain(v, col))
        rows.append(out)
        drilldowns.append(_drilldown(pipe, by, key) if by else pipe.before_aggregate)
    total = int(result[0]._groups) if result else 0
    return {
        "kind": "stats",
        "columns": out_cols,
        "rows": rows,
        "total": total,
        "limit": limit,
        "capped": (pipe.head or 0) > GROUP_CAP,
        "drilldowns": drilldowns,
    }


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def run(db: Session, project_id: int, user: User, text: str) -> Dict[str, Any]:
    """The answer to one query, in one statement."""
    pipe = parse(text)
    ds = _dataset(pipe)
    if pipe.fields:
        for name in pipe.fields:
            _column(ds, name, pipe.dataset_pos)
    query = _filtered(db, project_id, user, ds, pipe)
    answer = _grouped(query, ds, pipe) if pipe.aggregate is not None else _rows(query, ds, pipe)
    return {"query": text, "dataset": ds.name, **answer}


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
                            "isnull(col), isnotnull(col), AND / OR / NOT, parentheses. = is exact."},
            {"name": "stats", "syntax": "stats count, dc(col) as name … by col, …",
             "description": "count, dc (distinct count), min, max, avg, sum, values."},
            {"name": "top", "syntax": "top [limit=N] col, …", "description": "Count and percent, most first."},
            {"name": "sort", "syntax": "sort -col, +col",
             "description": "-col sorts descending, +col (or col) ascending."},
            {"name": "head", "syntax": "head N", "description": "The first N (also: limit N)."},
            {"name": "fields", "syntax": "fields col, … (or table)", "description": "The columns of a listing."},
        ],
        "caps": {"rows": ROW_CAP, "groups": GROUP_CAP, "values": VALUES_CAP},
    }
