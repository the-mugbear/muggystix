"""Regression tests for ``GET /hosts/{host_id}/dns-records`` (v2.90.0).

Closes the #44.1 → UX phase 3 chain.  The per-row ``resolver_name``
column shipped in v2.89.0; this endpoint surfaces it on the host
detail page.  Tests confirm:
  * IP-matched records (A / PTR-of-this-IP) flow through ``value``.
  * Hostname-matched records (CNAME / MX / NS / TXT / SOA targeting
    the canonical hostname) flow through ``domain``.
  * Per-row ``resolver_name`` is preserved.
  * The aggregate ``resolvers`` + ``record_types`` fields summarize
    the response without forcing the client to do a second pass.
  * 404 on unknown host_id (rather than empty result) so a stale
    deep-link tells the operator the host is gone.
"""
from __future__ import annotations

from app.db import models


def _make_dns_row(
    db_session,
    project,
    *,
    domain: str,
    record_type: str,
    value: str,
    resolver_name: str | None = None,
    ttl: int | None = None,
):
    row = models.DNSRecord(
        project_id=project.id,
        domain=domain,
        record_type=record_type,
        value=value,
        ttl=ttl,
        resolver_name=resolver_name,
    )
    db_session.add(row)
    return row


def test_host_dns_records_404_on_unknown_host(client, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/99999/dns-records")
    assert r.status_code == 404


def test_host_dns_records_matches_by_ip_and_hostname(
    client, db_session, test_project,
):
    """A record matches when its ``value`` is the host's IP OR its
    ``domain`` is the host's hostname.  Records that touch neither
    are excluded — operators don't want every project DNS row on
    every host detail page."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.5",
        hostname="mail.internal",
        state="up",
    )
    db_session.add(host)
    db_session.flush()

    # In-scope rows
    _make_dns_row(
        db_session, test_project,
        domain="mail.internal", record_type="A", value="10.0.0.5",
        resolver_name="1.1.1.1:53", ttl=300,
    )
    _make_dns_row(
        db_session, test_project,
        domain="mail.internal", record_type="PTR", value="10.0.0.5",
        resolver_name="1.1.1.1:53",
    )
    _make_dns_row(
        db_session, test_project,
        domain="mail.internal", record_type="MX", value="10 mailhub.example",
        resolver_name="8.8.8.8:53",
    )
    # Out-of-scope row (different IP, different hostname) — must NOT appear.
    _make_dns_row(
        db_session, test_project,
        domain="www.example.com", record_type="A", value="10.0.0.99",
        resolver_name="1.1.1.1:53",
    )
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/dns-records",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    types = {row["record_type"] for row in body["items"]}
    assert types == {"A", "PTR", "MX"}
    # Aggregate fields summarize for the client.
    assert set(body["resolvers"]) == {"1.1.1.1:53", "8.8.8.8:53"}
    assert set(body["record_types"]) == {"A", "PTR", "MX"}
    # Per-row resolver_name preserved.
    a_row = next(row for row in body["items"] if row["record_type"] == "A")
    assert a_row["resolver_name"] == "1.1.1.1:53"
    assert a_row["ttl"] == 300


def test_host_dns_records_carries_null_resolver(client, db_session, test_project):
    """Pre-v2.89.0 rows (CSV DNSParser, amass) have ``resolver_name=NULL``.
    The response carries the NULL through so the card can render a
    "(resolver unknown)" cell instead of dropping the row."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.6",
        hostname="legacy.internal",
        state="up",
    )
    db_session.add(host)
    db_session.flush()
    _make_dns_row(
        db_session, test_project,
        domain="legacy.internal", record_type="A", value="10.0.0.6",
        resolver_name=None,
    )
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/dns-records",
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["resolver_name"] is None
    # `resolvers` aggregate filters NULL — distinct list of populated
    # resolver names only.
    assert body["resolvers"] == []


def test_host_dns_records_isolation_by_project(client, db_session, test_project):
    """A DNSRecord that belongs to a DIFFERENT project must not appear
    in the response — the multi-project isolation guard.  Same shape
    as the rest of the host-scoped endpoints."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.7",
        hostname="myhost.internal",
        state="up",
    )
    db_session.add(host)
    # Create a row in a different project that would match the IP.
    from app.db.models_project import Project
    other = Project(
        name="other-project", slug="other-project",
        description="isolation guard", is_default=False,
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(
        models.DNSRecord(
            project_id=other.id,
            domain="myhost.internal",
            record_type="A",
            value="10.0.0.7",
            resolver_name="1.1.1.1:53",
        )
    )
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/dns-records",
    )
    body = r.json()
    assert body["total"] == 0


def test_host_dns_records_limit_cap(client, db_session, test_project):
    """``limit`` rejects oversize (cap = 2000)."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.8",
        hostname=None,
        state="up",
    )
    db_session.add(host)
    db_session.flush()
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/{host.id}/dns-records",
        params={"limit": 999_999},
    )
    assert r.status_code == 422
