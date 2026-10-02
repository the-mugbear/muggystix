"""collect-logs.sh: request timing by route and the top SQL statements.

The bundle carries two derived files (review 2026-10-01):

* ``request_timing.txt`` — the access log's ``db_ms`` / ``db_n`` / ``route``
  fields summarised per route template;
* ``sql_statements.txt`` — the top ``pg_stat_statements`` rows.

Both are written by ``scripts/diag_summaries.py`` (stdlib, host ``python3``)
and then pass through ``scripts/scrub_logs.py`` like every other file.  These
tests pin the parser to the line the middleware REALLY writes, that neither
file can carry a path or a literal, and that the scrubber leaves them readable.
"""
import importlib.util
import logging
import pathlib
import subprocess
import sys

import pytest


def _scripts_dir():
    here = pathlib.Path(__file__).resolve()
    for candidate in (here.parents[1] / "scripts", here.parents[2] / "scripts"):
        if (candidate / "collect-logs.sh").is_file():
            return candidate
    return None


SCRIPTS = _scripts_dir()
pytestmark = pytest.mark.skipif(SCRIPTS is None, reason="scripts/ is not mounted here")


@pytest.fixture(scope="module")
def diag():
    path = SCRIPTS / "diag_summaries.py"
    spec = importlib.util.spec_from_file_location("diag_summaries", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _access(method, path, status, ms, db_ms, db_n, route):
    """One access line as ``docker compose logs --timestamps`` shows it."""
    return (
        "backend-1  | 2026-10-01T22:10:23.746134742Z 2026-10-01 22:10:23,746 - app.access - INFO - "
        f"{method} {path} -> {status} {ms}ms req=c7c83aeb6acb43b2 db_ms={db_ms} db_n={db_n} route={route}\n"
    )


def _slow(method, path, status, ms, db_ms, db_n, route):
    return (
        "backend-1  | 2026-10-01T22:10:24.000000000Z 2026-10-01 22:10:24,000 - app.access - WARNING - "
        f"SLOW request {method} {path} {ms}ms -> {status} req=c7c83aeb6acb43b2 db_ms={db_ms} db_n={db_n} route={route}\n"
    )


HOSTS = "/api/v1/projects/{project_id}/hosts/"


# ---------------------------------------------------------------------------
# request_timing.txt
# ---------------------------------------------------------------------------

def test_the_parser_reads_the_line_the_middleware_writes(diag):
    """The format is taken from the middleware itself, not from a copy of it:
    a reordered or renamed field fails here."""
    import asyncio

    from app.core.request_context import RequestContextMiddleware

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def run():
        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(_message):
            return None

        scope = {"type": "http", "method": "GET", "path": "/api/v1/projects/14/scans/", "headers": []}
        await RequestContextMiddleware(app)(scope, receive, send)

    # The access logger does not propagate, so it gets its own handler here.
    lines = []

    class _Collect(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage() + "\n")

    access = logging.getLogger("app.access")
    handler = _Collect(level=logging.INFO)
    access.addHandler(handler)
    try:
        asyncio.run(run())
    finally:
        access.removeHandler(handler)
    assert lines, "the middleware wrote no access line"

    report = diag.request_timing(lines)
    assert "Requests summarised: 1 over 1 route(s)" in report
    # No router matched in this bare app, so the request has no template —
    # and its path must not be printed in the template's place.
    assert diag.UNMATCHED in report
    assert "/api/v1/projects/14" not in report


def test_per_route_counts_percentiles_means_and_slow_lines(diag):
    lines = [_access("GET", "/api/v1/projects/14/hosts/", 200, ms, 10, 4, HOSTS) for ms in range(1, 101)]
    lines.append(_access("GET", "/api/v1/projects/9/hosts/", 200, 2000, 1810, 6, HOSTS))
    lines.append(_slow("GET", "/api/v1/projects/9/hosts/", 200, 2000, 1810, 6, HOSTS))
    lines.append(_access("GET", "/health", 200, 2, 0, 1, "/health"))
    lines.append("backend-1  | 2026-10-01 22:10:23,746 - app.main - INFO - something else entirely\n")

    report = diag.request_timing(lines)
    rows = [l.split() for l in report.splitlines() if l.rstrip().endswith(HOSTS)]
    assert len(rows) == 1, report
    requests, p50, p95, db_ms, db_n, slow, total_s, method, route = rows[0]
    assert (requests, p50, p95, slow, method, route) == ("101", "51", "96", "1", "GET", HOSTS)
    assert float(db_ms) == pytest.approx((100 * 10 + 1810) / 101, abs=0.05)
    assert float(db_n) == pytest.approx((100 * 4 + 6) / 101, abs=0.05)
    assert float(total_s) == pytest.approx(7.05, abs=0.06)
    # The SLOW line is counted, never counted again as a request.
    assert "Requests summarised: 102 over 2 route(s); SLOW request lines: 1" in report
    # Sorted by total time: the hosts list leads /health.
    assert report.index(HOSTS) < report.index("GET /health")
    # Ids live in the path, and the path is never printed.
    assert "/projects/14/" not in report and "/projects/9/" not in report


def test_an_unmatched_request_never_shows_what_was_asked_for(diag):
    lines = [_access("GET", "/api/v1/agent/acme-corp-secret-route/10.20.30.40", 404, 3, 0, 0, "-")]
    report = diag.request_timing(lines)
    assert f"GET {diag.UNMATCHED}" in report
    assert "acme" not in report and "10.20.30.40" not in report


def test_a_path_with_spaces_cannot_put_a_word_in_the_method_column(diag):
    """The path is logged percent-decoded.  ``GET /x SECRETWORD y -> 200 …``
    used to match a second time at ``SECRETWORD y`` and print SECRETWORD as
    the request's method."""
    lines = [
        _access("GET", "/api/v1/agent/x ACMECORP y", 404, 3, 0, 0, "-"),
        _slow("GET", "/api/v1/agent/x ACMECORP y", 404, 3000, 0, 0, "-"),
    ]
    report = diag.request_timing(lines)
    assert "ACMECORP" not in report
    assert f"GET {diag.UNMATCHED}" in report
    assert "Requests summarised: 1 over 1 route(s); SLOW request lines: 1" in report


def test_a_method_the_client_invented_is_not_printed(diag):
    lines = [_access("ACMEVERB", "/api/v1/x", 405, 3, 0, 0, "-")]
    report = diag.request_timing(lines)
    assert "ACMEVERB" not in report


def test_fields_spelled_inside_the_path_are_not_read_as_the_fields(diag):
    forged = "/x -> 200 1ms req=a db_ms=0 db_n=0 route=/acme-secret"
    lines = [_access("GET", forged, 404, 7, 2, 1, "-")]
    report = diag.request_timing(lines)
    assert "acme" not in report
    assert f"GET {diag.UNMATCHED}" in report
    rows = [l.split() for l in report.splitlines() if l.rstrip().endswith(diag.UNMATCHED)]
    assert rows[0][:3] == ["1", "7", "7"]          # the REAL request's time, not the forged 1ms


def test_logs_from_an_older_build_get_one_line_not_an_empty_table(diag):
    old = (
        "backend-1  | 2026-09-20T10:00:00.000000000Z 2026-09-20 10:00:00,000 - app.access - INFO - "
        "GET /api/v1/projects/14/hosts/ -> 200 41ms req=c7c83aeb6acb43b2\n"
    )
    report = diag.request_timing([old, old])
    assert "an older build wrote these logs" in report
    assert "older format (not summarised): 2" in report
    assert "/projects/14/" not in report


def test_the_route_list_is_capped_and_says_what_it_left_out(diag):
    lines = [
        _access("GET", f"/api/v1/r{i}", 200, 100 - i, 1, 1, f"/api/v1/r{i}") for i in range(10)
    ]
    report = diag.request_timing(lines, max_routes=3)
    assert "GET /api/v1/r0" in report and "GET /api/v1/r2" in report
    assert "GET /api/v1/r3" not in report
    assert "… and 7 more route(s), 7 request(s)" in report


# ---------------------------------------------------------------------------
# sql_statements.txt
# ---------------------------------------------------------------------------

def test_statements_are_one_bounded_line_each(diag):
    long_query = "SELECT hosts_v2.id,\n   hosts_v2.ip_address\tFROM hosts_v2 WHERE " + " AND ".join(
        f"hosts_v2.c{i} = ${i}" for i in range(1, 80)
    )
    one_line = long_query.replace("\n", " ").replace("\t", " ")  # as the SQL collapses it
    report = diag.sql_statements([f"1200\t98000.5\t81.67\t48000\t{one_line}\n"])
    row = report.splitlines()[1]
    assert row.split()[:4] == ["1200", "98000.5", "81.67", "48000"]
    assert "SELECT hosts_v2.id, hosts_v2.ip_address FROM hosts_v2 WHERE" in row
    query = "SELECT" + row.split("  SELECT", 1)[1]
    assert "  " not in query and "\t" not in query
    assert query.endswith(" …")
    assert len(query) <= diag.MAX_QUERY_CHARS + 2


def test_no_literal_and_no_utility_statement_reaches_the_file(diag):
    rows = [
        "5\t10.0\t2.00\t5\tALTER ROLE nmapuser PASSWORD 'hunter2-acme'\n",
        "9\t9.0\t1.00\t9\tSET application_name = 'acme-laptop'\n",
        "3\t6.0\t2.00\t3\tSELECT id FROM hosts_v2 WHERE hostname = 'dc01.acme.corp' AND note = 'it''s'\n",
        "malformed line with no tabs\n",
    ]
    report = diag.sql_statements(rows)
    assert "hunter2" not in report and "acme" not in report and "ALTER ROLE" not in report
    assert "SELECT id FROM hosts_v2 WHERE hostname = '?' AND note = '?'" in report
    assert "Left out: 2 statement(s)" in report
    assert "Left out: 1 row(s) that could not be read" in report


@pytest.mark.parametrize("query, expected", [
    # An escape string: \' does not end it, so the plain-string rule alone
    # stopped early and printed the rest.
    (r"SELECT id FROM hosts_v2 WHERE note = E'it\'s acme-dc01' AND id = $1",
     "SELECT id FROM hosts_v2 WHERE note = '?' AND id = $1"),
    ("SELECT id FROM hosts_v2 WHERE note = $$acme's dc01$$ AND id = $1",
     "SELECT id FROM hosts_v2 WHERE note = $$?$$ AND id = $1"),
    ("SELECT id FROM hosts_v2 WHERE note = $tag$acme $$ dc01$tag$ AND id = $1",
     "SELECT id FROM hosts_v2 WHERE note = $$?$$ AND id = $1"),
    ("SELECT /* asked by acme-laptop */ id FROM hosts_v2 WHERE id = $1",
     "SELECT id FROM hosts_v2 WHERE id = $1"),
    ("/* acme's report */ SELECT id FROM hosts_v2 WHERE hostname = 'dc01.acme.corp'",
     "SELECT id FROM hosts_v2 WHERE hostname = '?'"),
    ("SELECT id FROM hosts_v2 WHERE note = 'a /* not a comment */ acme' AND id = $1",
     "SELECT id FROM hosts_v2 WHERE note = '?' AND id = $1"),
    # Parameters ($1, $2) are not dollar quotes.
    ("SELECT id FROM hosts_v2 WHERE a = $1 AND b = $2", "SELECT id FROM hosts_v2 WHERE a = $1 AND b = $2"),
])
def test_every_kind_of_literal_and_comment_is_removed(diag, query, expected):
    assert diag.clean_query(query) == expected
    assert "acme" not in diag.clean_query(query)


def test_an_unterminated_literal_or_comment_takes_the_rest_of_the_statement(diag):
    assert "acme" not in diag.clean_query("SELECT id FROM t WHERE a = 'acme and more")
    assert "acme" not in diag.clean_query("SELECT id FROM t /* acme and more")
    assert "acme" not in diag.clean_query("SELECT id FROM t WHERE a = $q$acme and more")


def test_a_utility_statement_behind_a_comment_is_still_left_out(diag):
    assert diag.clean_query("/* x */ ALTER ROLE nmapuser PASSWORD 'hunter2'") is None


def test_an_empty_view_says_so(diag):
    assert "(no statements recorded yet)" in diag.sql_statements([])


# ---------------------------------------------------------------------------
# Through the scrubber, and wired into the script
# ---------------------------------------------------------------------------

def test_both_files_survive_the_scrubber_readable(diag, tmp_path):
    """Route templates and normalised SQL name no one: the scrubber must leave
    them as they are — while still replacing a harvested name beside them."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    timing = diag.request_timing(
        [_access("GET", "/api/v1/projects/14/hosts/", 200, 41, 30, 7, HOSTS)] * 3
    )
    statements = diag.sql_statements([
        "1200\t98000.5\t81.67\t48000\tSELECT hosts_v2.id, hosts_v2.ip_address FROM hosts_v2 "
        "WHERE hosts_v2.project_id = $1 AND hosts_v2.hostname ILIKE $2 LIMIT $3\n",
    ])
    (bundle / "request_timing.txt").write_text(timing, encoding="utf-8")
    (bundle / "sql_statements.txt").write_text(statements + "seen on dc01 by Acme Widgets\n", encoding="utf-8")
    terms = tmp_path / "terms.tsv"
    # A date-shaped value (never replaced), a host and a client name.
    terms.write_text("mcp\t2026-10-01\nhost\tdc01\nclient\tAcme Widgets\n", encoding="utf-8")

    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "scrub_logs.py"), str(bundle),
         "--terms", str(terms), "--report", str(tmp_path / "report.txt")],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr

    assert (bundle / "request_timing.txt").read_text(encoding="utf-8") == timing
    scrubbed = (bundle / "sql_statements.txt").read_text(encoding="utf-8")
    assert statements in scrubbed
    assert "dc01" not in scrubbed and "Acme" not in scrubbed


def test_collect_logs_writes_lists_and_guards_both_files():
    script = (SCRIPTS / "collect-logs.sh").read_text(encoding="utf-8")
    for name in ("request_timing.txt", "sql_statements.txt"):
        assert f'"$LOG_DIR/{name}"' in script, f"collect-logs.sh does not write {name}"
        assert f"\n- {name}" in script, f"the bundle's README.txt does not list {name}"
    assert "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" in script
    assert "diag_summaries.py" in script
    # Written before the scrubber runs, so both are scrubbed with the bundle.
    scrub_at = script.index('python3 "$SCRUBBER"')
    assert script.index('"$LOG_DIR/request_timing.txt"') < scrub_at
    assert script.index('"$LOG_DIR/sql_statements.txt"') < scrub_at
    # Statement text is never a source of names to remove: harvesting it would
    # turn table and column names into pseudonyms throughout the bundle.
    harvest = script[script.index("HARVEST=("):script.index("harvest_failed=0")]
    assert "pg_stat_statements" not in harvest
