"""
Certificate Organization as host provenance (v2.238.0).

A cert's subject Organization is attribution a public CA *validated* before
issuing — it checked that the requester controls the name. That makes it
stronger evidence of who runs a host than a registry record, which is
self-declared, and it costs no external lookup: the value was already in the
``tls_info`` blob every web scan writes, parsed for the self-signed comparison
and then thrown away.

Key nuance the tests pin: only OV/EV certificates carry an Organization. DV
certs — Let's Encrypt and most of the modern web — legitimately have none, so
NULL means "no claim made", never "not the client's".
"""
from datetime import datetime, timezone

import pytest

from app.db import models
from app.services.cert_fields import derive_cert_orgs
from app.services.host_query_dsl import BuildCtx, evaluate, parse_query


def test_reads_an_x509_dn_string():
    subject, issuer = derive_cert_orgs({
        "subject_dn": "CN=www.acme.com, O=Acme Corporation, L=Springfield, C=US",
        "issuer_dn": "CN=R3, O=Let's Encrypt, C=US",
    })
    assert subject == "Acme Corporation"
    assert issuer == "Let's Encrypt"


def test_reads_a_parsed_mapping():
    """Tools disagree on serialisation; both shapes appear in the wild."""
    subject, issuer = derive_cert_orgs({
        "subject": {"common_name": "x", "organization": "Globex Ltd"},
        "issuer": {"organization": "DigiCert Inc"},
    })
    assert subject == "Globex Ltd"
    assert issuer == "DigiCert Inc"


def test_an_escaped_comma_does_not_truncate_the_org():
    """``O=Acme\\, Inc`` is one value, not two RDNs — splitting naively would
    silently attribute the host to "Acme"."""
    subject, _ = derive_cert_orgs({"subject_dn": r"CN=x, O=Acme\, Inc, C=US"})
    assert subject == "Acme, Inc"


def test_a_dv_certificate_yields_no_claim():
    """The common case. Must be None, not an empty string — the column is a
    claim, and absence of a claim is not a negative finding."""
    assert derive_cert_orgs({"subject_dn": "CN=only-cn.example.com"}) == (None, None)
    assert derive_cert_orgs({}) == (None, None)
    assert derive_cert_orgs(None) == (None, None)


@pytest.fixture
def certified_hosts(db_session, test_project):
    """One host with an OV cert naming the client, one with a DV cert."""
    def _host(ip):
        h = models.Host(
            project_id=test_project.id, ip_address=ip, state="up",
            first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
        )
        db_session.add(h)
        db_session.commit()
        db_session.refresh(h)
        return h

    scan = models.Scan(
        project_id=test_project.id, filename="httpx.json",
        scan_type="httpx", tool_name="httpx",
    )
    db_session.add(scan)
    db_session.commit()
    db_session.refresh(scan)

    owned = _host("198.51.100.20")
    dv = _host("198.51.100.21")
    db_session.add_all([
        models.WebInterface(
            scan_id=scan.id, host_id=owned.id, url="https://a.acme.test",
            source="httpx", cert_subject_org="Acme Corporation",
            cert_issuer_org="DigiCert Inc",
        ),
        models.WebInterface(
            scan_id=scan.id, host_id=dv.id, url="https://b.acme.test",
            source="httpx", cert_subject_org=None,
            cert_issuer_org="Let's Encrypt",
        ),
    ])
    db_session.commit()
    return {"owned": owned, "dv": dv}


def test_certorg_filter_finds_the_validated_estate(
    db_session, test_project, test_user, certified_hosts
):
    matched = {
        h.ip_address
        for h in db_session.query(models.Host)
        .filter(models.Host.project_id == test_project.id)
        .filter(evaluate(parse_query('certorg:"Acme"'), BuildCtx(db_session, test_user, test_project.id)))
        .all()
    }
    assert matched == {"198.51.100.20"}, (
        "a DV-certificate host must not match — it made no organisational "
        "claim, which is not the same as claiming someone else"
    )
