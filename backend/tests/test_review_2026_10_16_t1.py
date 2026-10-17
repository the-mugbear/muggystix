"""Comprehensive review 2026-10-16, batch T1 — small correctness and
security fixes, each pinned here (N9 and N11 are in
``test_totp_replay_and_reauth_throttle.py``).

* N1  the query language caps nesting (a deep `((((…` was a 500).
* N2  a refusal raised while a query's search terms are evaluated is in the
      person's history too.
* A5  the Query page sorts severity by the one ladder, in the same order.
* N3  notifications of one transaction come back newest id first.
* N7  Nessus clips its title, plugin name and host attributes to their columns.
* N8  nmap counts a skipped down host as read, not as processed.
* N10 a webhook's "send test" says one fixed text when the receiver did not answer.
* N12 the agent rate limiter fails open only when its table is missing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api import deps
from app.db import models
from app.db.models import Host
from app.db.models_vulnerability import HostAttribute, Vulnerability, VulnerabilitySeverity
from app.services import webhook_dispatcher
from app.services.query_language import MAX_DEPTH, QueryError, parse
from tests.ingestion_job_harness import run_file


def _url(project, tail=""):
    return f"/api/v1/projects/{project.id}{tail}"


# ---------------------------------------------------------------------------
# N1 — nesting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cond", [
    "(" * (MAX_DEPTH + 1) + "port=1" + ")" * (MAX_DEPTH + 1),
    "NOT " * (MAX_DEPTH + 1) + "port=1",
])
def test_a_where_nested_past_the_cap_is_refused_with_a_reason(cond):
    with pytest.raises(QueryError) as exc:
        parse(f"ports | where {cond}")
    assert "nested too deep" in exc.value.message


def test_nesting_up_to_the_cap_reads_and_a_runaway_is_a_400_not_a_500(client, test_project):
    ok = "(" * MAX_DEPTH + "port=1" + ")" * MAX_DEPTH
    assert parse(f"ports | where {ok}").wheres
    deep = "(" * 1500 + "port=1" + ")" * 1500
    r = client.get(_url(test_project, "/query"), params={"q": f"ports | where {deep}"})
    assert r.status_code == 400 and "nested too deep" in r.json()["detail"]


# ---------------------------------------------------------------------------
# N2 — a refusal raised while the search terms are evaluated
# ---------------------------------------------------------------------------

def test_a_refusal_from_the_hosts_language_is_recorded_too(client, test_project):
    r = client.get(_url(test_project, "/query"), params={"q": "hosts subnet:10.0.5"})
    assert r.status_code == 400 and "subnet:" in r.json()["detail"]
    (entry,) = client.get(_url(test_project, "/query/history")).json()["items"]
    assert entry["q"] == "hosts subnet:10.0.5" and "subnet:" in entry["error"]


# ---------------------------------------------------------------------------
# A5 — the one severity ladder, the same order
# ---------------------------------------------------------------------------

def test_severity_sorts_by_the_one_ladder_in_the_same_order(client, db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="s.nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.flush()
    host = Host(project_id=test_project.id, ip_address="10.42.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    for sev in (VulnerabilitySeverity.LOW, VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.INFO,
                VulnerabilitySeverity.HIGH, VulnerabilitySeverity.MEDIUM):
        db_session.add(Vulnerability(host_id=host.id, scan_id=scan.id, title=f"t-{sev.value}",
                                     severity=sev, source="nessus"))
    db_session.commit()
    q = lambda text: client.get(_url(test_project, "/query"), params={"q": text}).json()  # noqa: E731
    down = q("observations | stats count by severity | sort -severity")
    assert [r[0] for r in down["rows"]] == ["critical", "high", "medium", "low", "info"]
    up = q("observations | stats count by severity | sort severity")
    assert [r[0] for r in up["rows"]] == ["info", "low", "medium", "high", "critical"]


# ---------------------------------------------------------------------------
# N3 — notifications of one transaction
# ---------------------------------------------------------------------------

def test_notifications_of_one_moment_come_back_newest_id_first(db_session, test_user):
    from app.db.models_project import Notification
    from app.services.notification_service import NotificationService

    at = datetime(2026, 10, 16, tzinfo=timezone.utc)
    rows = [Notification(user_id=test_user.id, type="mention", title=f"n{i}", body="m", created_at=at)
            for i in range(4)]
    db_session.add_all(rows)
    db_session.commit()
    listed = NotificationService(db_session).get_notifications(test_user.id, limit=2, offset=0)
    nxt = NotificationService(db_session).get_notifications(test_user.id, limit=2, offset=2)
    ids = [n.id for n in listed + nxt]
    assert ids == sorted((r.id for r in rows), reverse=True)


# ---------------------------------------------------------------------------
# N7 — Nessus values clipped to their columns
# ---------------------------------------------------------------------------

def test_nessus_clips_title_plugin_name_and_attributes_to_their_columns(db_session, test_project, tmp_path):
    long_name = "P" * 600
    long_os = "O" * 600
    path = tmp_path / "long.nessus"
    path.write_text(
        '<?xml version="1.0" ?>\n<NessusClientData_v2>\n<Report name="long">\n'
        '<ReportHost name="10.43.0.1"><HostProperties><tag name="host-ip">10.43.0.1</tag>'
        f'<tag name="operating-system">{long_os}</tag></HostProperties>'
        f'<ReportItem port="80" svc_name="www" protocol="tcp" severity="2" pluginID="99001" pluginName="{long_name}">'
        '<description>d</description><risk_factor>Medium</risk_factor></ReportItem>'
        '</ReportHost>\n</Report>\n</NessusClientData_v2>\n'
    )
    job = run_file(db_session, test_project.id, path)
    assert job.status == "completed", (job.status, job.error_message)
    vuln = db_session.query(Vulnerability).filter(Vulnerability.plugin_id == "99001").one()
    assert vuln.title == long_name[:500] and vuln.source_plugin_name == long_name[:200]
    attr = db_session.query(HostAttribute).filter(HostAttribute.attribute_type == "os_name").one()
    assert attr.value == long_os[:500]


# ---------------------------------------------------------------------------
# N8 — nmap's count of hosts processed
# ---------------------------------------------------------------------------

def test_nmap_counts_only_the_hosts_it_recorded(db_session, test_project, tmp_path):
    from app.parsers.nmap_parser import NmapXMLParser

    down = ''.join(f'<host><status state="down"/><address addr="10.44.0.{i}" addrtype="ipv4"/></host>\n'
                   for i in range(2, 5))
    path = tmp_path / "sweep.xml"
    path.write_text(
        '<?xml version="1.0"?>\n<nmaprun scanner="nmap" start="1700000000">\n'
        '<host><status state="up"/><address addr="10.44.0.1" addrtype="ipv4"/><ports>'
        '<port protocol="tcp" portid="22"><state state="open"/></port></ports></host>\n'
        + down + '</nmaprun>\n'
    )
    parser = NmapXMLParser(db_session)
    parser.parse_file(str(path), "sweep.xml", project_id=test_project.id)
    assert parser.last_parse_stats["summary"] == "1 host"


# ---------------------------------------------------------------------------
# N10 — the webhook test's answer
# ---------------------------------------------------------------------------

def test_a_webhook_test_says_one_fixed_text_whatever_went_wrong(db_session, monkeypatch):
    def refused(*args, **kwargs):
        raise ConnectionRefusedError("[Errno 111] Connection refused: 10.0.0.5:22")

    monkeypatch.setattr(webhook_dispatcher, "_post", refused)
    cfg = SimpleNamespace(id=1, name="w", url="http://10.0.0.5:22/", secret_encrypted=None, project_id=1)
    answer = webhook_dispatcher.WebhookDispatcher(db_session).deliver_test(cfg)
    assert answer == {"ok": False, "error": webhook_dispatcher.WEBHOOK_UNREACHABLE}
    assert "refused" not in answer["error"] and "10.0.0.5" not in answer["error"]


# ---------------------------------------------------------------------------
# N12 — when the rate limiter fails open
# ---------------------------------------------------------------------------

def _db_error(cls, pgcode):
    orig = Exception("boom")
    orig.pgcode = pgcode
    return cls("INSERT INTO agent_rate_buckets", {}, orig)


def test_the_rate_limiter_fails_open_only_for_a_missing_table():
    assert deps._rate_buckets_missing(_db_error(ProgrammingError, "42P01")) is True
    # A busy database is not "no limiter": lock timeout, statement timeout,
    # a lost connection, a permission problem.
    for cls, code in ((OperationalError, "55P03"), (OperationalError, "57014"),
                      (OperationalError, None), (ProgrammingError, "42501")):
        assert deps._rate_buckets_missing(_db_error(cls, code)) is False


def test_a_busy_database_fails_the_agent_call_instead_of_lifting_the_limit(db_session):
    class Busy:
        def execute(self, *args, **kwargs):
            raise _db_error(OperationalError, "55P03")

        def rollback(self):
            pass

    with pytest.raises(OperationalError):
        deps.check_agent_rate_limit(agent=SimpleNamespace(id=1, rate_limit_rpm=60), db=Busy())
