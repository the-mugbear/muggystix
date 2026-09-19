"""The engagement-wide investigation queue (v2.347.0; design review item 2).

"What should I investigate next?" before anyone has created work for it:
hosts nobody has touched that carry an observed weakness or a relevant
change.  Pins:

* touched hosts (a follow, a note, a plan entry, a finding) never appear;
* the tier is the first one a host qualifies for, stated in words, and the
  queue is ordered by tier then last seen;
* every row carries reasons, the tools that observed it, and a next action
  whose kind matches the tier (plan for exploitable/critical, collect for
  an exposed service with no vulnerability data, inspect for conflicts);
* the workbench carries the block and survives the queue failing.
"""
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models import Annotation, FollowStatus, HostFollow
from app.db.models_confidence import ConflictHistory
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.operations_read_service import compute_investigation_queue


def _host(db, project_id, ip, *, ports=(), first_seen=None, last_seen=None, tool="nmap"):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    # An unspecified host is an OLD host: first_seen defaults to now on the
    # model, which would make every fixture "new this week".
    h.first_seen = first_seen if first_seen is not None else datetime.now(timezone.utc) - timedelta(days=30)
    if last_seen is not None:
        h.last_seen = last_seen
    db.add(h)
    db.flush()
    for p in ports:
        db.add(models.Port(host_id=h.id, port_number=p, protocol="tcp", state="open"))
    scan = models.Scan(project_id=project_id, filename=f"{ip}.xml", tool_name=tool)
    db.add(scan)
    db.flush()
    db.add(models.HostScanHistory(
        host_id=h.id, scan_id=scan.id, state_at_scan="up", discovered_at=datetime.now(timezone.utc),
    ))
    db.flush()
    return h, scan


def _vuln(db, host, scan, severity, exploitable=False):
    db.add(Vulnerability(
        host_id=host.id, scan_id=scan.id, title="v", severity=severity,
        source=VulnerabilitySource.NESSUS, exploitable=exploitable,
    ))
    db.flush()


def test_untouched_hosts_with_reasons_ordered_by_stated_tier(db_session, test_project, test_user):
    pid = test_project.id
    now = datetime.now(timezone.utc)
    # Tier 1: exploitable critical, seen a while ago.
    t1, s1 = _host(db_session, pid, "10.5.0.1", ports=(445,), last_seen=now - timedelta(days=3))
    _vuln(db_session, t1, s1, VulnerabilitySeverity.CRITICAL, exploitable=True)
    _vuln(db_session, t1, s1, VulnerabilitySeverity.HIGH)
    # Tier 2: critical, no exploit.
    t2, s2 = _host(db_session, pid, "10.5.0.2", last_seen=now)
    _vuln(db_session, t2, s2, VulnerabilitySeverity.CRITICAL)
    # Tier 3: exploit on a medium.
    t3, s3 = _host(db_session, pid, "10.5.0.3", last_seen=now, tool="openvas")
    _vuln(db_session, t3, s3, VulnerabilitySeverity.MEDIUM, exploitable=True)
    # Tier 4: RDP open on a host first seen yesterday, no vulnerability data.
    t4, _ = _host(db_session, pid, "10.5.0.4", ports=(3389,), first_seen=now - timedelta(days=1), last_seen=now)
    # Tier 5: scans disagree, nothing else.
    t5, s5 = _host(db_session, pid, "10.5.0.5", last_seen=now)
    db_session.add(ConflictHistory(host_id=t5.id, field_name="os_name", previous_value="a", new_value="b", new_scan_id=s5.id))
    # Not in the queue: an old host with only an ordinary port and no vulns.
    _host(db_session, pid, "10.5.0.6", ports=(8080,), first_seen=now - timedelta(days=30), last_seen=now)
    # Not in the queue: touched hosts, however bad.
    tf, sf = _host(db_session, pid, "10.5.0.7")
    _vuln(db_session, tf, sf, VulnerabilitySeverity.CRITICAL, exploitable=True)
    db_session.add(HostFollow(host_id=tf.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW))
    tn, sn = _host(db_session, pid, "10.5.0.8")
    _vuln(db_session, tn, sn, VulnerabilitySeverity.CRITICAL, exploitable=True)
    db_session.add(Annotation(host_id=tn.id, project_id=pid, body="looked", user_id=test_user.id))
    db_session.commit()

    q = compute_investigation_queue(db_session, test_project, limit=25)

    assert q.untouched_total == 6  # everything but the two touched hosts
    assert q.queue_total == 5
    assert [r.ip_address for r in q.items] == ["10.5.0.1", "10.5.0.2", "10.5.0.3", "10.5.0.4", "10.5.0.5"]
    assert [r.tier for r in q.items] == [1, 2, 3, 4, 5]
    assert q.tiers[0] == "Exploitable critical"

    r1 = q.items[0]
    assert r1.tier_label == "Exploitable critical"
    assert [x.kind for x in r1.reasons] == ["critical_exploitable", "high", "high_value"]
    assert r1.reasons[0].text == "1 critical vulnerability with a known public exploit"
    assert "SMB" in r1.reasons[2].text
    assert r1.evidence.sources == ["nmap"]
    assert r1.evidence.confirmation == "scanner"
    assert r1.next_action.kind == "review"

    assert q.items[2].evidence.sources == ["openvas"]
    assert q.items[2].next_action.kind == "review"

    r4 = q.items[3]
    assert [x.kind for x in r4.reasons] == ["high_value", "new_host"]
    assert r4.reasons[1].text == "First seen 1 day ago"
    assert r4.next_action.kind == "collect"

    r5 = q.items[4]
    assert r5.reasons[0].kind == "conflicts"
    assert r5.next_action.kind == "review"


def test_limit_keeps_the_true_total(db_session, test_project):
    pid = test_project.id
    for i in range(4):
        h, s = _host(db_session, pid, f"10.6.0.{i + 1}")
        _vuln(db_session, h, s, VulnerabilitySeverity.CRITICAL)
    db_session.commit()
    q = compute_investigation_queue(db_session, test_project, limit=2)
    assert len(q.items) == 2
    assert q.queue_total == 4
    assert q.untouched_total == 4


def test_workbench_carries_the_block(client, db_session, test_project):
    h, s = _host(db_session, test_project.id, "10.7.0.1")
    _vuln(db_session, h, s, VulnerabilitySeverity.CRITICAL, exploitable=True)
    db_session.commit()
    r = client.get(f"/api/v1/projects/{test_project.id}/workbench")
    assert r.status_code == 200, r.text
    block = r.json()["investigate"]
    assert block["queue_total"] == 1
    assert block["items"][0]["host_id"] == h.id
    assert block["items"][0]["next_action"]["kind"] == "review"


def test_taking_a_host_into_review_removes_it_from_the_queue(client, db_session, test_project):
    """The queue only lists hosts nobody is reviewing; marking one In Review
    (the row's Review button) moves it to the caller's personal queue."""
    h, s = _host(db_session, test_project.id, "10.7.0.2")
    _vuln(db_session, h, s, VulnerabilitySeverity.CRITICAL, exploitable=True)
    db_session.commit()
    before = client.get(f"/api/v1/projects/{test_project.id}/workbench").json()
    assert [r["host_id"] for r in before["investigate"]["items"]] == [h.id]

    r = client.post(f"/api/v1/projects/{test_project.id}/hosts/{h.id}/follow", json={"status": "in_review"})
    assert r.status_code in (200, 201), r.text

    after = client.get(f"/api/v1/projects/{test_project.id}/workbench").json()
    assert after["investigate"]["items"] == []
    assert [x["host_id"] for x in after["my_queue"]["items"]] == [h.id]


def test_empty_project_is_empty_not_an_error(db_session, test_project):
    q = compute_investigation_queue(db_session, test_project)
    assert q.items == [] and q.untouched_total == 0 and q.queue_total == 0
