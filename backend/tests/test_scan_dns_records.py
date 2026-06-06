"""GET /scans/{id}/dns-records — surface every DNS answer a scan produced.

Regression for the dnsx report: a 6-record dnsx file persisted all 6
DNSRecord rows, but only the 2 A/AAAA answers created hosts, so the
operator saw "2 entries" and the CNAME/MX/NS records had no surface.
This endpoint lists them all.
"""
from __future__ import annotations

from app.db import models


def _url(pid, scan_id):
    return f"/api/v1/projects/{pid}/scans/{scan_id}/dns-records"


def _seed(db_session, project_id):
    scan = models.Scan(project_id=project_id, filename="dnsx.json", tool_name="dnsx")
    db_session.add(scan)
    db_session.flush()
    rows = [
        ("A", "example.com", "93.184.216.34", "1.1.1.1:53"),
        ("AAAA", "example.com", "2606:2800:220:1:248:1893:25c8:1946", "1.1.1.1:53"),
        ("CNAME", "www.example.com", "example.com", "8.8.8.8:53"),
        ("MX", "example.com", "0 .", "8.8.8.8:53"),
        ("NS", "example.com", "a.iana-servers.net", "9.9.9.9:53"),
        ("NS", "example.com", "b.iana-servers.net", "9.9.9.9:53"),
    ]
    for rt, domain, value, resolver in rows:
        db_session.add(models.DNSRecord(
            project_id=project_id, scan_id=scan.id, domain=domain,
            record_type=rt, value=value, resolver_name=resolver,
        ))
    db_session.flush()
    return scan


def test_lists_all_dns_records_for_scan(client, db_session, test_project):
    scan = _seed(db_session, test_project.id)
    r = client.get(_url(test_project.id, scan.id))
    assert r.status_code == 200, r.text
    body = r.json()
    # All 6 records returned (not just the 2 that became hosts).
    assert len(body) == 6
    types = sorted(rec["record_type"] for rec in body)
    assert types == ["A", "AAAA", "CNAME", "MX", "NS", "NS"]
    # resolver_name surfaces (the v2.89.0 attribution column).
    assert any(rec["resolver_name"] == "9.9.9.9:53" for rec in body)


def test_unknown_scan_404(client, test_project):
    assert client.get(_url(test_project.id, 999999)).status_code == 404
