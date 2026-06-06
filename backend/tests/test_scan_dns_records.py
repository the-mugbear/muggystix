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
    # Paginated envelope (CR5-C3): all 6 records, total reported, no more pages.
    assert body["total"] == 6
    assert body["has_more"] is False
    assert len(body["items"]) == 6
    types = sorted(rec["record_type"] for rec in body["items"])
    assert types == ["A", "AAAA", "CNAME", "MX", "NS", "NS"]
    # resolver_name surfaces (the v2.89.0 attribution column).
    assert any(rec["resolver_name"] == "9.9.9.9:53" for rec in body["items"])


def test_total_is_honest_when_results_exceed_one_page(client, db_session, test_project):
    """CR5-C3 — a scan with >page records reports the TRUE total and flags
    has_more, instead of presenting the page size as the count."""
    scan = models.Scan(project_id=test_project.id, filename="big.json", tool_name="dnsx")
    db_session.add(scan)
    db_session.flush()
    for i in range(620):
        db_session.add(models.DNSRecord(
            project_id=test_project.id, scan_id=scan.id,
            domain=f"h{i}.example.com", record_type="A", value=f"10.0.{i // 256}.{i % 256}",
        ))
    db_session.flush()

    r = client.get(_url(test_project.id, scan.id) + "?limit=500")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 620          # honest total, not the page size
    assert len(body["items"]) == 500     # capped at the page limit
    assert body["has_more"] is True

    # A second page returns the remainder.
    r2 = client.get(_url(test_project.id, scan.id) + "?skip=500&limit=500")
    body2 = r2.json()
    assert len(body2["items"]) == 120
    assert body2["has_more"] is False


def test_unknown_scan_404(client, test_project):
    assert client.get(_url(test_project.id, 999999)).status_code == 404
