"""No serializer key may be silently dropped by its response model.

Three features shipped broken this cycle in exactly this way, and every one of
them passed its own tests:

  * ``HostVulnerability.finding_id`` — the serializer emitted it, the response
    model didn't declare it, so the "Promoted" badge only survived until the
    page reloaded and operators re-promoted findings.
  * ``attributions`` / ``cert_orgs`` — same shape. The ProvenanceCard was
    imported, rendered, and unit-tested, and had never once appeared for a
    user, because both fields were stripped at the response boundary.

The failure is invisible by construction: FastAPI drops undeclared keys
without warning, the serializer keeps "working", and unit tests that call the
serializer directly — or that render the component with hand-made props — pass
forever.

A union-of-all-models check does NOT catch this. ``finding_id`` was declared on
``Annotation``; it just wasn't on ``HostVulnerability``. The only reliable
comparison is per-endpoint: what the serializer produced versus what the client
actually received.

So this spies on the serializer and diffs its raw output against the parsed
response body. It needs no map to maintain and it cannot drift: any field added
to a serializer without a matching model field fails here immediately.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity


@pytest.fixture
def furnished_host(db_session, test_project):
    """A host carrying one of everything the detail serializer can emit.

    Empty collections would let a dropped nested key hide, so each list must
    have at least one member.
    """
    scan = models.Scan(
        project_id=test_project.id, filename="fixture.xml",
        scan_type="nessus", tool_name="nessus",
    )
    db_session.add(scan)
    db_session.flush()

    host = models.Host(
        project_id=test_project.id, ip_address="203.0.113.40", state="up",
        hostname="host.example", os_name="Linux 5.x", os_family="Linux",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.flush()

    port = models.Port(
        host_id=host.id, port_number=443,
        protocol="tcp", state="open", service_name="https",
    )
    db_session.add(port)
    db_session.flush()

    db_session.add(Vulnerability(
        host_id=host.id, scan_id=scan.id, port_id=port.id,
        title="Some issue", severity=VulnerabilitySeverity.HIGH,
        source="nessus", cve_id="CVE-2024-9999", plugin_id="12345",
    ))

    attr = NetworkAttribution(
        project_id=test_project.id, cidr="203.0.113.0/24",
        org_name="Acme Corporation", asn=64501, registry="ARIN",
        source="rdap", looked_up_at=datetime.now(timezone.utc),
    )
    db_session.add(attr)
    db_session.flush()
    db_session.add(HostNetworkAttribution(host_id=host.id, attribution_id=attr.id))

    db_session.add(models.WebInterface(
        host_id=host.id, scan_id=scan.id, project_id=test_project.id,
        url="https://203.0.113.40",
        cert_subject_org="Acme Corporation", cert_issuer_org="DigiCert Inc",
    ))
    db_session.commit()
    return host


def test_host_detail_response_carries_every_key_the_serializer_produced(
    client, db_session, test_project, furnished_host, monkeypatch,
):
    from app.api.v1.endpoints import hosts as hosts_ep

    real = hosts_ep._serialize_host_detail
    captured: dict = {}

    def _spy(*args, **kwargs):
        produced = real(*args, **kwargs)
        captured.clear()
        captured.update(produced)
        return produced

    monkeypatch.setattr(hosts_ep, "_serialize_host_detail", _spy)

    response = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{furnished_host.id}"
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert captured, "the spy never ran — the endpoint stopped using this serializer"

    dropped = sorted(set(captured) - set(body))
    assert not dropped, (
        "the response model silently dropped serializer output: "
        f"{dropped}. Declare these on the response model (schemas.Host) or "
        "stop emitting them — a key that never reaches the client is a "
        "feature that looks implemented and is not."
    )


def test_nested_vulnerability_rows_carry_every_key_too(
    client, db_session, test_project, furnished_host, monkeypatch,
):
    """The nested case, which is where finding_id was lost.

    A nested model is validated independently of its parent, so the top-level
    check above cannot see a key dropped from a list element.
    """
    from app.api.v1.endpoints import hosts as hosts_ep

    real = hosts_ep._serialize_host_detail
    captured: dict = {}

    def _spy(*args, **kwargs):
        produced = real(*args, **kwargs)
        rows = produced.get("vulnerabilities") or []
        if rows:
            captured.clear()
            captured.update(rows[0])
        return produced

    monkeypatch.setattr(hosts_ep, "_serialize_host_detail", _spy)

    body = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{furnished_host.id}"
    ).json()

    assert captured, "fixture produced no vulnerability row to compare"
    returned = body["vulnerabilities"][0]

    dropped = sorted(set(captured) - set(returned))
    assert not dropped, (
        f"HostVulnerability dropped serializer output: {dropped}"
    )


def test_provenance_collections_are_not_empty_in_this_fixture(
    client, test_project, furnished_host,
):
    """Guards the guard.

    If the fixture stopped producing attributions or cert orgs, the two checks
    above would still pass while covering nothing — which is precisely how the
    ProvenanceCard stayed broken behind green tests.
    """
    body = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{furnished_host.id}"
    ).json()
    assert body["attributions"], "fixture must exercise the attribution path"
    assert body["cert_orgs"], "fixture must exercise the certificate-org path"
    assert body["vulnerabilities"], "fixture must exercise the vulnerability path"
