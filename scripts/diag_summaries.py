#!/usr/bin/env python3
"""Derived summaries for a collect-logs.sh bundle.

Standard library only — runs on the deployment host with nothing installed.
``collect-logs.sh`` calls it BEFORE the scrubber, and the files it writes are
scrubbed with the rest of the bundle.

    diag_summaries.py request-timing <logs_backend.txt>
    diag_summaries.py sql-statements        (rows on stdin, see below)

**request-timing** reads the backend's access-log lines
(``app/core/request_context.py``)::

    GET /api/v1/projects/14/hosts/ -> 200 41ms req=… db_ms=30 db_n=7 route=/api/v1/projects/{project_id}/hosts/
    SLOW request GET /api/v1/… 1840ms -> 200 req=… db_ms=1700 db_n=9 route=/api/v1/…

and prints, per method + route TEMPLATE: requests, p50 and p95 of the request
time, mean ``db_ms``, mean ``db_n`` and how many ``SLOW request`` lines it had.
Only the template is printed — never the path, which carries ids and whatever
an unmatched request asked for.  A request that matched no route is grouped as
``(unmatched route)``.

**sql-statements** formats ``pg_stat_statements`` rows read from stdin, one per
line, tab-separated: ``calls  total_ms  mean_ms  rows  query``.  The query text
is the NORMALISED one (constants are ``$1``, ``$2`` …), so it holds table and
column names only.  Two belts on top of that: statements other than
SELECT / INSERT / UPDATE / DELETE / WITH / MERGE are left out (Postgres does not
normalise every utility statement — ``ALTER ROLE … PASSWORD '…'`` keeps its
literal), and any quoted literal still present is replaced.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

# The method, then the path (never kept), status, time, and the appended
# fields.  A SLOW line puts the time BEFORE the arrow, so it cannot match.
#
# Both the method and the path are whatever the CLIENT sent, and the path is
# logged percent-decoded — it can hold spaces.  Three things keep a word of
# it out of the report (branch review 2026-10-01):
#   * the method is one of the real HTTP methods ("?" is the middleware's own
#     placeholder), never "any capitals": ``GET /x SECRET y -> 200 …`` used to
#     match again at ``SECRET y`` and print SECRET as the method;
#   * the line is anchored where the message STARTS — the start of the line
#     (the bare message) or the formatter's ``app.access - LEVEL - `` prefix;
#   * the fields are anchored at the END of the line, so text shaped like the
#     fields inside the path (``… route=x``) is path, and the route read is
#     the one the middleware appended.
_METHOD = r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE|CONNECT|\?)"
_START = r"(?:^|app\.access - [A-Z]+ - )"
ACCESS = re.compile(
    _START + _METHOD + r" .*? -> \S+ (\d+)ms req=\S+ db_ms=(\d+) db_n=(\d+) route=(\S+)\s*$"
)
SLOW = re.compile(
    _START + r"SLOW request " + _METHOD + r" .*? \d+ms -> \S+ req=\S+ db_ms=\d+ db_n=\d+ route=(\S+)\s*$"
)
# The line as builds before the timing fields wrote it: nothing after req=.
OLD_ACCESS = re.compile(_START + _METHOD + r" .*? -> \S+ \d+ms req=\S+\s*$")

UNMATCHED = "(unmatched route)"
ROUTE_UNSAFE = re.compile(r"[^A-Za-z0-9_/{}.:\-]")
MAX_ROUTE_CHARS = 120
MAX_ROUTES = 60

MAX_QUERY_CHARS = 400
DML = re.compile(r"^\s*(select|insert|update|delete|with|merge)\b", re.IGNORECASE)
# Everything in a statement that can carry a value, replaced in ONE pass so a
# quote inside a comment (or a comment marker inside a string) cannot change
# what the next alternative sees.  In order:
#   /* … */ and -- comments   free text (an ORM or a person can tag a query
#                             with a host name); pg_stat_statements keeps them
#   $tag$ … $tag$, $$ … $$    dollar-quoted strings
#   E'…' / e'…'               escape strings — ``\'`` does not end one, which
#                             the plain rule below would get wrong
#   '…'                       standard strings ('' is an escaped quote); also
#                             covers the body of B'…', X'…', N'…', U&'…'
# An unterminated one runs to the end of the statement: over-removal is the
# safe direction.
VALUE_BEARING = re.compile(
    r"""
      (?P<comment>   /\*.*?(?:\*/|\Z) | --[^\n]*                          )
    | (?P<dollar>    \$\$ .*? (?:\$\$|\Z)
                   | \$(?P<tag>[A-Za-z_][A-Za-z_0-9]*)\$ .*? (?:\$(?P=tag)\$|\Z) )
    | (?P<escape>    \b[eE]'(?:[^'\\]|\\.|'')*(?:'|\Z)                    )
    | (?P<plain>     '(?:[^']|'')*(?:'|\Z)                                )
    """,
    re.VERBOSE | re.DOTALL,
)
WHITESPACE = re.compile(r"\s+")


def _blank_value(match: re.Match) -> str:
    if match.group("comment") is not None:
        return " "
    if match.group("dollar") is not None:
        return "$$?$$"
    return "'?'"


def strip_values(query: str) -> str:
    """The statement with every comment removed and every string literal
    replaced by ``'?'`` (``$$?$$`` for a dollar-quoted one)."""
    return VALUE_BEARING.sub(_blank_value, query)


def _route_label(route: str) -> str:
    if route == "-":
        return UNMATCHED
    return ROUTE_UNSAFE.sub("?", route)[:MAX_ROUTE_CHARS]


def percentile(sorted_values: list[int], pct: float) -> int:
    """Nearest-rank percentile of an ascending list (no interpolation: the
    answer is always a time one request really took)."""
    if not sorted_values:
        return 0
    rank = max(1, -(-len(sorted_values) * pct // 100))  # ceil
    return sorted_values[int(rank) - 1]


def request_timing(lines, max_routes: int = MAX_ROUTES) -> str:
    durations: dict[tuple[str, str], list[int]] = defaultdict(list)
    db_ms: dict[tuple[str, str], int] = defaultdict(int)
    db_n: dict[tuple[str, str], int] = defaultdict(int)
    slow: dict[tuple[str, str], int] = defaultdict(int)
    old_format = 0
    for line in lines:
        if "route=" not in line:
            if "req=" in line and OLD_ACCESS.search(line):
                old_format += 1
            continue
        m = SLOW.search(line)
        if m:
            slow[(m.group(1), _route_label(m.group(2)))] += 1
            continue
        m = ACCESS.search(line)
        if not m:
            continue
        key = (m.group(1), _route_label(m.group(5)))
        durations[key].append(int(m.group(2)))
        db_ms[key] += int(m.group(3))
        db_n[key] += int(m.group(4))

    out = [
        "=== REQUEST TIMING (backend access log, by route template) ===",
        "Per method + route: requests, p50 / p95 of the whole request (ms), mean time",
        "inside database statements (db_ms), mean statement count (db_n), and how many",
        "were logged as SLOW request (SLOW_REQUEST_MS, default 1000).  request time",
        "minus db_ms is Python.  Sorted by total time; paths are never shown.",
        "",
    ]
    if not durations:
        out.append(
            "No access-log lines carry db_ms= / db_n= / route= — an older build wrote "
            "these logs, or the log window (--since) holds no requests."
        )
        if old_format:
            out.append(f"Access lines in the older format (not summarised): {old_format}")
        return "\n".join(out) + "\n"

    rows = []
    for key, values in durations.items():
        values.sort()
        n = len(values)
        rows.append((sum(values), n, percentile(values, 50), percentile(values, 95),
                     db_ms[key] / n, db_n[key] / n, slow.get(key, 0), key))
    rows.sort(key=lambda r: (-r[0], -r[1], r[7]))
    total_requests = sum(r[1] for r in rows)
    out.append(
        f"Requests summarised: {total_requests} over {len(rows)} route(s); "
        f"SLOW request lines: {sum(slow.values())}"
    )
    if old_format:
        out.append(f"Access lines in the older format (not summarised): {old_format}")
    out.append("")
    out.append(f"{'requests':>8} {'p50_ms':>7} {'p95_ms':>7} {'db_ms':>8} {'db_n':>7} {'slow':>5} {'total_s':>8}  method route")
    for total, n, p50, p95, mean_db, mean_n, n_slow, (method, route) in rows[:max_routes]:
        out.append(
            f"{n:>8} {p50:>7} {p95:>7} {mean_db:>8.1f} {mean_n:>7.1f} {n_slow:>5} {total / 1000:>8.1f}  {method} {route}"
        )
    if len(rows) > max_routes:
        rest = rows[max_routes:]
        out.append(f"… and {len(rest)} more route(s), {sum(r[1] for r in rest)} request(s), "
                   f"{sum(r[0] for r in rest) / 1000:.1f} s in total")
    return "\n".join(out) + "\n"


def clean_query(query: str, max_chars: int = MAX_QUERY_CHARS) -> str | None:
    """One line, bounded, no literal — or ``None`` for a statement that is
    left out (anything that is not plain DML)."""
    # Values and comments go first, so the DML test sees the statement itself
    # (a leading comment is gone) and nothing after it can bring one back.
    stripped = strip_values(query)
    if not DML.match(stripped):
        return None
    text = WHITESPACE.sub(" ", stripped).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …"
    return text


def sql_statements(lines) -> str:
    out = [f"{'calls':>10} {'total_ms':>12} {'mean_ms':>10} {'rows':>12}  query (normalised, first {MAX_QUERY_CHARS} characters)"]
    shown = omitted = unreadable = 0
    for raw in lines:
        raw = raw.rstrip("\n")
        if not raw.strip():
            continue
        parts = raw.split("\t", 4)
        if len(parts) != 5:
            unreadable += 1
            continue
        calls, total_ms, mean_ms, rows, query = parts
        text = clean_query(query)
        if text is None:
            omitted += 1
            continue
        out.append(f"{calls:>10} {total_ms:>12} {mean_ms:>10} {rows:>12}  {text}")
        shown += 1
    if not shown:
        out.append("(no statements recorded yet)")
    if omitted:
        out.append(f"Left out: {omitted} statement(s) that are not SELECT / INSERT / UPDATE / DELETE / WITH / MERGE "
                   "(utility statements can keep a literal).")
    if unreadable:
        out.append(f"Left out: {unreadable} row(s) that could not be read.")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "request-timing":
        with open(argv[2], "r", encoding="utf-8", errors="replace") as src:
            sys.stdout.write(request_timing(src))
        return 0
    if len(argv) == 2 and argv[1] == "sql-statements":
        sys.stdin.reconfigure(errors="replace")
        sys.stdout.write(sql_statements(sys.stdin))
        return 0
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
