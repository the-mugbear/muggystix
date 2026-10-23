"""Comprehensive review 2026-10-16, batch T4 — one implementation each.

* A6  one reading of a typed instant (`instants.parse_instant`): a date, a
      time with an offset, a time without one (UTC) — the same instant on
      every door that takes one.  The agents' `created_after` read a time
      without an offset in the server's zone.
* N15 every CSV row goes through the one formula guard
      (`csv_utils.safe_csv_row`); only a header of constant words is written
      directly.  The names export was safe only because its pattern forbade
      a formula prefix.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import models
from app.services import query_service
from app.services.host_query_dsl import _parse_window
from app.services.instants import parse_instant

UTC_MIDNIGHT = datetime(2026, 10, 8, tzinfo=timezone.utc)
SHAPES = ("2026-10-08", "2026-10-08T00:00:00Z", "2026-10-08T00:00:00", "2026-10-08T02:00:00+02:00")


@pytest.mark.parametrize("text", SHAPES)
def test_one_instant_for_every_spelling_on_every_door(text):
    assert parse_instant(text) == UTC_MIDNIGHT
    assert _parse_window("firstseen", text)[0] == UTC_MIDNIGHT        # the Hosts language
    assert query_service._parse_time(text) == UTC_MIDNIGHT             # the Query page


@pytest.mark.parametrize("text", ["", "yesterday", "2026-13-01"])
def test_what_is_not_an_instant_is_refused(text):
    with pytest.raises(ValueError):
        parse_instant(text)


def test_the_agents_created_after_reads_a_time_without_offset_as_utc(client, db_session, test_project):
    early = models.Scan(project_id=test_project.id, filename="early.xml", tool_name="nmap",
                        created_at=datetime(2026, 10, 7, 23, 0, tzinfo=timezone.utc))
    late = models.Scan(project_id=test_project.id, filename="late.xml", tool_name="nmap",
                       created_at=datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc))
    db_session.add_all([early, late])
    db_session.commit()
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    key = {"X-API-Key": started.json()["api_key"]}

    def names(after):
        r = client.get("/api/v1/agent/assist/scans", headers=key, params={"created_after": after})
        assert r.status_code == 200, r.text
        return sorted(s["filename"] for s in r.json()["items"])

    for spelling in SHAPES:
        assert names(spelling) == ["late.xml"], spelling
    assert client.get("/api/v1/agent/assist/scans", headers=key,
                      params={"created_after": "yesterday"}).status_code == 422


def test_csv_rows_are_guarded():
    """No `.writerow(` outside `csv_utils` but a header of constant words:
    every other row is `safe_csv_row`'s, which neutralises a cell a
    spreadsheet would run as a formula (=, +, -, @, tab, CR)."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    unguarded = []
    for path in app_root.rglob("*.py"):
        if path.name == "csv_utils.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("writerow", "writerows")):
                continue
            arg = node.args[0] if node.args else None
            header = (
                node.func.attr == "writerow" and isinstance(arg, (ast.List, ast.Tuple))
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in arg.elts)
            )
            if not header:
                unguarded.append(f"{path.relative_to(app_root)}:{node.lineno}")
    assert unguarded == [], unguarded


def test_the_names_export_neutralises_a_formula(client, db_session, test_project):
    """The FQDN pattern forbids a formula prefix today; the guard does not
    depend on it — a stored name is written through `safe_csv_row`."""
    from app.services import csv_utils

    assert csv_utils.csv_safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    r = client.get(f"/api/v1/projects/{test_project.id}/names/export", params={"format": "csv"})
    assert r.status_code == 200, r.text
    assert r.text.splitlines()[0] == "fqdn,kind,in_scope,current_ips,last_seen"
