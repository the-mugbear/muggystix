"""Review 2026-11-20, batch 3 — remediation knows an endpoint can be a NAME at
a host (C1, U8).

One finding can have several named endpoints on one address
(``uq_finding_host_name``), each with its own contact, deadline and state.
The list, the reminder and the alert named only the address, and the contact
document matched its systems by address — so every endpoint at that address
took the first row's contact and deadline, and another contact's endpoint was
printed in this contact's document.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Notification
from app.db.models_remediation import RemediationPolicy
from app.services import remediation_alerts, remediation_report, remediation_service

HERE = Path(__file__).resolve()
ROOT = next(
    (p for p in (Path("/app/report-templates"), HERE.parents[2] / "report-templates")
     if (p / "contact-report" / "template.json").is_file()),
    None,
)
needs_templates = pytest.mark.skipif(ROOT is None, reason="report-templates/ is not mounted here")
TODAY = datetime.now(timezone.utc).date()


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def base(project) -> str:
    return f"/api/v1/projects/{project.id}/remediation"


@pytest.fixture(autouse=True)
def installation(db_session, monkeypatch):
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                     days_low=120, due_soon_days=7))
    db_session.commit()
    if ROOT is not None:
        monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(ROOT))


@pytest.fixture
def two_names(client, db_session, test_project):
    """One load balancer, two sites on it, one finding on both: the shop is
    Roger's and overdue, the blog is Jane's and assigned yesterday."""
    host = models.Host(project_id=test_project.id, ip_address="10.61.0.9", hostname="lb01", state="up")
    shop = models.DNSName(project_id=test_project.id, fqdn="shop.example.test")
    blog = models.DNSName(project_id=test_project.id, fqdn="blog.example.test")
    finding = Finding(project_id=test_project.id, title="Outdated TLS library", severity="high",
                      status="confirmed", source="manual", recommendation="Upgrade the library.")
    db_session.add_all([host, shop, blog, finding])
    db_session.flush()
    on_shop = FindingHost(finding_id=finding.id, host_id=host.id, name_id=shop.id, host_status="open")
    on_blog = FindingHost(finding_id=finding.id, host_id=host.id, name_id=blog.id, host_status="open")
    db_session.add_all([on_shop, on_blog])
    db_session.commit()
    answer = client.post(f"{base(test_project)}/apply", json={"overwrite": True, "rows": [
        {"finding_host_id": on_shop.id, "contact_email": "roger@testdomain.com", "contact_name": "Roger Smith",
         "notified_on": day(42)},
        {"finding_host_id": on_blog.id, "contact_email": "jane@testdomain.com", "notified_on": day(1)},
    ]})
    assert answer.status_code == 200, answer.text
    return {"shop": on_shop.id, "blog": on_blog.id}


def test_a_row_says_which_name_it_is_and_the_search_finds_it_by_that_name(client, test_project, two_names):
    rows = client.get(base(test_project)).json()["items"]
    assert {row["finding_host_id"]: row["name"] for row in rows} == {
        two_names["shop"]: "shop.example.test", two_names["blog"]: "blog.example.test"}
    assert all(row["ip_address"] == "10.61.0.9" and row["name_id"] is not None for row in rows)

    found = client.get(base(test_project), params={"q": "shop.example"}).json()
    assert [row["finding_host_id"] for row in found["items"]] == [two_names["shop"]]
    assert found["total"] == 1


def test_an_endpoint_on_the_host_itself_has_no_name(client, db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.61.0.10", hostname="files01", state="up")
    finding = Finding(project_id=test_project.id, title="SMB signing not required", severity="high",
                      status="confirmed", source="manual")
    db_session.add_all([host, finding])
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    db_session.commit()

    (row,) = client.get(base(test_project)).json()["items"]
    assert (row["name"], row["name_id"], row["hostname"]) == (None, None, "files01")


def test_the_reminder_names_the_name_the_contact_fixes(client, test_project, two_names):
    message = client.get(f"{base(test_project)}/follow-up",
                         params={"contact_email": "roger@testdomain.com"}).json()
    assert "shop.example.test (10.61.0.9)" in message["text"]
    assert "blog.example.test" not in message["text"]          # Jane's


def test_the_one_wording_of_an_endpoint():
    label = remediation_service.endpoint_label
    assert label("10.0.0.1") == "10.0.0.1"
    assert label("10.0.0.1", "files01") == "10.0.0.1 (files01)"
    assert label("10.0.0.1", "lb01", "shop.example.test") == "shop.example.test (10.0.0.1)"


def test_an_alert_names_the_name_too(db_session, test_project, two_names):
    remediation_alerts.sweep(db_session)
    db_session.commit()
    bodies = [n.body for n in db_session.query(Notification)
              .filter(Notification.source_type == "remediation_overdue").all()]
    assert bodies and all("shop.example.test (10.61.0.9)" in body for body in bodies)


@needs_templates
def test_each_contacts_document_lists_their_own_named_endpoint_and_not_the_others(
        db_session, test_project, two_names):
    rogers = remediation_report.build_dataset(db_session, test_project.id, "roger@testdomain.com")
    (finding,) = rogers["findings"]
    (endpoint,) = finding["affected"]                 # by address it listed both, with the first row's deadline
    assert (endpoint["name"], endpoint["address"], endpoint["deadline_state"]) == (
        "shop.example.test", "10.61.0.9", "overdue")
    assert rogers["remediation"]["not_listed"] == 0   # it went negative

    janes = remediation_report.build_dataset(db_session, test_project.id, "jane@testdomain.com")
    (finding,) = janes["findings"]
    (endpoint,) = finding["affected"]
    assert endpoint["name"] == "blog.example.test" and endpoint["deadline_state"] != "overdue"
