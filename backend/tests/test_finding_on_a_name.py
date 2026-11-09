"""A finding on a NAME, written by hand (2.538.0): ``POST /findings`` with
``name_id``.

An engagement against web targets by host name has findings on the NAME, not
the bare address.  The endpoint stays (host, name) — the name hangs on the
address it resolves to — and the host page's Add finding and the Names page's
action send the host and one of its current names.  The rule is the host
test's (``target_fqdn``): the name must be observed at that host.  ONE
implementation answers both doors, ``dns_name_service.name_observed_at_host``,
so a name a test may aim at is exactly a name a finding may be on.
"""
import uuid

import pytest

from app.db import models
from app.services.dns_name_service import NAME_NOT_AT_HOST, record_observation


@pytest.fixture
def host(db_session, test_project):
    row = models.Host(project_id=test_project.id, ip_address="10.46.0.1", state="up")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def other_host(db_session, test_project):
    row = models.Host(project_id=test_project.id, ip_address="10.46.0.2", state="up")
    db_session.add(row)
    db_session.commit()
    return row


def _findings(project):
    return f"/api/v1/projects/{project.id}/findings"


def _name_at(db_session, project, fqdn, ip):
    """A name observed resolving to ``ip`` (an A record), as an import writes it."""
    record_observation(db_session, project_id=project.id, name=fqdn, record_type="A", value=ip)
    db_session.commit()
    return db_session.query(models.DNSName).filter(
        models.DNSName.project_id == project.id, models.DNSName.fqdn == fqdn,
    ).one()


def _body(host, **extra):
    return {"title": "Weak TLS on the portal", "severity": "medium", "host_ids": [host.id], **extra}


def test_a_finding_can_be_written_on_a_name_observed_at_the_host(client, db_session, test_project, host):
    name = _name_at(db_session, test_project, "portal.example.org", host.ip_address)
    r = client.post(_findings(test_project), json=_body(host, name_id=name.id))
    assert r.status_code == 201, r.text
    endpoints = [(h["host_id"], h["name_id"], h["fqdn"]) for h in r.json()["hosts"]]
    assert endpoints == [(host.id, name.id, "portal.example.org")]
    # The same finding read back carries the named endpoint.
    again = client.get(f"{_findings(test_project)}/{r.json()['id']}").json()
    assert [(h["name_id"], h["fqdn"]) for h in again["hosts"]] == [(name.id, "portal.example.org")]


def test_without_a_name_the_endpoint_is_the_bare_host(client, test_project, host):
    r = client.post(_findings(test_project), json=_body(host))
    assert r.status_code == 201, r.text
    assert [(h["host_id"], h["name_id"]) for h in r.json()["hosts"]] == [(host.id, None)]


def test_the_name_must_be_observed_at_that_host_the_host_tests_rule(client, db_session, test_project, host, other_host):
    """A name that resolves elsewhere is refused with the host test's own
    words: both doors ask ``name_observed_at_host``."""
    name = _name_at(db_session, test_project, "elsewhere.example.org", other_host.ip_address)
    r = client.post(_findings(test_project), json=_body(host, name_id=name.id))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == NAME_NOT_AT_HOST

    t = client.post(f"/api/v1/projects/{test_project.id}/host-tests", json={"tests": [dict(
        request_key=str(uuid.uuid4()), host_id=host.id, tool="curl", description="Check the vhost",
        rationale="The name is served here", target_fqdn="elsewhere.example.org",
    )]})
    assert t.status_code == 422, t.text
    assert t.json()["detail"] == r.json()["detail"]

    # On the host it resolves to, both are accepted.
    ok = client.post(_findings(test_project), json=_body(other_host, name_id=name.id))
    assert ok.status_code == 201, ok.text
    test_ok = client.post(f"/api/v1/projects/{test_project.id}/host-tests", json={"tests": [dict(
        request_key=str(uuid.uuid4()), host_id=other_host.id, tool="curl", description="Check the vhost",
        rationale="The name is served here", target_fqdn="elsewhere.example.org",
    )]})
    assert test_ok.status_code == 201, test_ok.text
    assert test_ok.json()["items"][0]["target_fqdn"] == "elsewhere.example.org"


def test_a_name_names_the_endpoint_on_exactly_one_host(client, db_session, test_project, host, other_host):
    name = _name_at(db_session, test_project, "portal.example.org", host.ip_address)
    two = client.post(_findings(test_project), json={**_body(host, name_id=name.id), "host_ids": [host.id, other_host.id]})
    assert two.status_code == 422, two.text
    assert "exactly one host_id" in two.json()["detail"]
    none = client.post(_findings(test_project), json={**_body(host, name_id=name.id), "host_ids": []})
    assert none.status_code == 422, none.text
    assert client.get(_findings(test_project)).json()["total"] == 0


def test_an_unknown_name_or_another_projects_name_is_refused(client, db_session, test_project, host):
    from app.db.models_project import Project

    other = Project(name="Other", slug="other-names")
    db_session.add(other)
    db_session.flush()
    foreign_host = models.Host(project_id=other.id, ip_address=host.ip_address, state="up")
    db_session.add(foreign_host)
    db_session.commit()
    foreign = _name_at(db_session, other, "theirs.example.org", host.ip_address)

    for name_id in (foreign.id, 987654321):
        r = client.post(_findings(test_project), json=_body(host, name_id=name_id))
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == NAME_NOT_AT_HOST
    assert client.get(_findings(test_project)).json()["total"] == 0


def test_a_wildcard_is_never_an_endpoint(client, db_session, test_project, host):
    record_observation(db_session, project_id=test_project.id, name="*.example.org", record_type="A", value=host.ip_address)
    db_session.commit()
    wildcard = db_session.query(models.DNSName).filter(
        models.DNSName.project_id == test_project.id, models.DNSName.kind == "wildcard",
    ).first()
    assert wildcard is not None, "the fixture should have recorded a wildcard name"
    r = client.post(_findings(test_project), json=_body(host, name_id=wildcard.id))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == NAME_NOT_AT_HOST
