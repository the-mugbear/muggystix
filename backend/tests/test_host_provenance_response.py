"""Provenance must survive response validation (v2.240.1).

The ProvenanceCard was wired into HostInspector and the serializer emitted
both `attributions` and `cert_orgs` from the day it shipped — but the `Host`
response model named neither, so FastAPI stripped them from every response.
The card renders null when both are empty, so it was never visible to anyone:
a feature that shipped, passed its own component tests, and did nothing.

This is the third instance of the same failure this cycle (HostVulnerability
.finding_id, and the emitted-but-undeclared fields before it), so the test
asserts the round trip through the ENDPOINT rather than the serializer — the
serializer was never the thing that was broken.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution


@pytest.fixture
def host(db_session, test_project):
    h = models.Host(
        project_id=test_project.id, ip_address="203.0.113.10", state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db_session.add(h)
    db_session.commit()
    db_session.refresh(h)
    return h


def test_rdap_attribution_reaches_the_client(client, db_session, test_project, host):
    attr = NetworkAttribution(
        project_id=test_project.id, cidr="203.0.113.0/24",
        org_name="Acme Corporation", asn=64501, as_name="ACME-AS",
        country="US", registry="ARIN", handle="NET-203-0-113-0-1",
        source="rdap", looked_up_at=datetime.now(timezone.utc),
    )
    db_session.add(attr)
    db_session.flush()
    db_session.add(HostNetworkAttribution(host_id=host.id, attribution_id=attr.id))
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}")
    assert r.status_code == 200, r.text
    body = r.json()

    assert "attributions" in body, "response_model stripped the provenance"
    assert len(body["attributions"]) == 1
    row = body["attributions"][0]
    assert row["org_name"] == "Acme Corporation"
    assert row["cidr"] == "203.0.113.0/24"
    assert row["asn"] == 64501
    assert row["registry"] == "ARIN"
    # Currency matters — the card warns when registration is stale, which it
    # can only do if the timestamp survives.
    assert row["looked_up_at"] is not None


def test_certificate_org_reaches_the_client(client, db_session, test_project, host):
    scan = models.Scan(
        project_id=test_project.id, filename="httpx.json",
        scan_type="httpx", tool_name="httpx",
    )
    db_session.add(scan)
    db_session.flush()
    db_session.add(models.WebInterface(
        host_id=host.id, scan_id=scan.id, project_id=test_project.id,
        url="https://203.0.113.10",
        cert_subject_org="Acme Corporation", cert_issuer_org="DigiCert Inc",
    ))
    db_session.commit()

    body = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}"
    ).json()

    assert "cert_orgs" in body, "response_model stripped the certificate orgs"
    assert body["cert_orgs"] == [{
        "org": "Acme Corporation",
        "issuer": "DigiCert Inc",
        "url": "https://203.0.113.10",
    }]


def test_a_host_with_no_provenance_returns_empty_lists_not_missing_keys(
    client, test_project, host,
):
    """Absence is normal — internal estates have none of this.

    The card must be able to distinguish "not looked up" from "failed to
    load", so the keys are always present and simply empty.
    """
    body = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}"
    ).json()
    assert body["attributions"] == []
    assert body["cert_orgs"] == []
