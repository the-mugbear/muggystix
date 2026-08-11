"""Certificate expiry and self-signed state must reach the client (v2.244.0).

``WebInterface.cert_not_after`` and ``cert_self_signed`` have been written by
the httpx parser since it landed and read by **nothing** — no serializer
emitted them, so the two facts an operator actually acts on ("this expires in
nine days", "this is self-signed") were invisible everywhere in the product.

The subtler half is the query. The host-detail fetch filtered
``cert_subject_org IS NOT NULL``, which is correct for the organisation half
and wrong for expiry: a DV certificate carries no organizationName, so every
Let's Encrypt host — the common case on the public internet — was dropped
before its expiry could be read.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models


def _web_iface(db, project, host, **cert):
    scan = models.Scan(
        project_id=project.id, filename="httpx.jsonl",
        scan_type="httpx", tool_name="httpx",
    )
    db.add(scan)
    db.flush()
    row = models.WebInterface(
        host_id=host.id, scan_id=scan.id, project_id=project.id,
        url=f"https://{host.ip_address}", **cert,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def host(db_session, test_project):
    h = models.Host(
        project_id=test_project.id, ip_address="203.0.113.77", state="up",
    )
    db_session.add(h)
    db_session.commit()
    return h


def _detail(client, project, host):
    r = client.get(f"/api/v1/projects/{project.id}/hosts/{host.id}")
    assert r.status_code == 200, r.text
    return r.json()


def test_expiry_and_self_signed_reach_the_client(
    client, db_session, test_project, host,
):
    expires = datetime.now(timezone.utc) + timedelta(days=9)
    _web_iface(
        db_session, test_project, host,
        cert_subject_org="Acme Corporation",
        cert_not_after=expires,
        cert_self_signed=False,
    )
    body = _detail(client, test_project, host)

    assert body["cert_status"], "expiry/self-signed never reached the response"
    entry = body["cert_status"][0]
    assert entry["self_signed"] is False
    assert entry["not_after"] is not None
    assert entry["subject_org"] == "Acme Corporation"


def test_a_dv_certificate_still_reports_its_expiry(
    client, db_session, test_project, host,
):
    """The query bug.

    A DV cert has no organizationName. Filtering the host-detail fetch on
    ``cert_subject_org IS NOT NULL`` dropped the row entirely, so the most
    common certificate on the internet reported no expiry at all.
    """
    _web_iface(
        db_session, test_project, host,
        cert_subject_org=None,               # DV: no O= field
        cert_not_after=datetime.now(timezone.utc) + timedelta(days=3),
        cert_self_signed=False,
    )
    body = _detail(client, test_project, host)

    assert body["cert_status"], (
        "a DV certificate reported no expiry — the row was filtered out "
        "before the expiry field could be read"
    )
    assert body["cert_status"][0]["not_after"] is not None
    # It correctly contributes no organisation claim.
    assert body["cert_orgs"] == []


def test_a_self_signed_certificate_is_reported_as_such(
    client, db_session, test_project, host,
):
    _web_iface(
        db_session, test_project, host,
        cert_subject_org="Internal CA",
        cert_not_after=datetime.now(timezone.utc) + timedelta(days=365),
        cert_self_signed=True,
    )
    body = _detail(client, test_project, host)
    assert body["cert_status"][0]["self_signed"] is True


def test_a_host_with_no_certificate_reports_an_empty_list(
    client, db_session, test_project, host,
):
    """Absence must be an empty list, not a missing key — the card renders
    off this and a missing key would read as a loading state."""
    body = _detail(client, test_project, host)
    assert body["cert_status"] == []
    assert body["cert_orgs"] == []
