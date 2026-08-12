"""
RDAP ingest → per-host network attribution (v2.237.0).

Scope validation today is self-referential: ``out_of_scope_only`` means "no
HostSubnetMapping row", i.e. it checks hosts against CIDRs someone typed into
the scope. If a client hands over a range they don't own, nothing can tell.
Attribution is the first signal that checks a declared scope against the
outside world.

The fixture in ``tests/fixtures/rdap-live.ndjson`` is REAL output from
``scripts/rdap-lookup.py`` against ARIN (8.8.8.0/24) and APNIC (1.1.1.0/24).
Registries differ in how they nest the registrant's jCard, so parsing invented
fixtures would prove very little.
"""

import json
import os
from datetime import datetime, timezone

import pytest

from app.db.models import Host
from app.db.models_attribution import (
    AttributionSource,
    HostNetworkAttribution,
    NetworkAttribution,
)
from app.parsers.content_detection import looks_like_rdap
from app.parsers.rdap_parser import RdapParser, _cidr_from_record, _org_from_entities
from app.services.attribution_correlation import (
    attributions_for_host,
    correlate_project_attributions,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rdap-live.ndjson")


def _host(db, project_id, ip):
    h = Host(
        project_id=project_id, ip_address=ip, state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@pytest.fixture
def live_records():
    with open(FIXTURE) as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# Parsing real registry output
# ---------------------------------------------------------------------------

def test_extracts_the_block_and_registrant_from_two_registries(live_records):
    """ARIN and APNIC nest the registrant differently; both must resolve."""
    by_query = {r["query"]: r["rdap"] for r in live_records}

    arin = by_query["8.8.8.8"]
    assert _cidr_from_record(arin) == "8.8.8.0/24"
    assert _org_from_entities(arin.get("entities") or []) == "Google LLC"

    apnic = by_query["1.1.1.1"]
    assert _cidr_from_record(apnic) == "1.1.1.0/24"
    assert "APNIC" in (_org_from_entities(apnic.get("entities") or []) or "")


def test_registrant_wins_over_the_abuse_contact():
    """Registries return several entities per block. Taking the first would
    routinely attribute a netblock to whoever handles its abuse mail."""
    entities = [
        {"roles": ["abuse"], "vcardArray": ["vcard", [["fn", {}, "text", "Abuse Desk"]]]},
        {"roles": ["registrant"], "vcardArray": ["vcard", [["fn", {}, "text", "Acme Corp"]]]},
    ]
    assert _org_from_entities(entities) == "Acme Corp"


def test_derives_a_block_from_a_start_end_range_without_cidr0():
    """Not every registry returns the cidr0 extension."""
    assert _cidr_from_record(
        {"startAddress": "198.51.100.0", "endAddress": "198.51.100.255"}
    ) == "198.51.100.0/24"


def test_detection_does_not_claim_other_json_tools():
    """The upload path tries detectors in order; a greedy one would hijack
    another tool's file."""
    rdap = b'{"query":"8.8.8.8","rdap":{"objectClassName":"ip network","startAddress":"8.8.8.0"}}'
    assert looks_like_rdap(rdap, "out.json") is True

    httpx = b'{"url":"https://x","tech":["nginx"],"status_code":200}'
    dnsx = b'{"host":"example.com","a":["1.2.3.4"]}'
    amass = b'{"name":"a.example.com","addresses":[{"ip":"1.2.3.4"}]}'
    for sample in (httpx, dnsx, amass):
        assert looks_like_rdap(sample, "out.json") is False


# ---------------------------------------------------------------------------
# Ingest + correlation
# ---------------------------------------------------------------------------

def test_ingest_attributes_hosts_to_their_registered_block(
    db_session, test_project, tmp_path
):
    """The end-to-end property: a host inside a registered block learns who
    that block belongs to."""
    inside = _host(db_session, test_project.id, "8.8.8.8")
    elsewhere = _host(db_session, test_project.id, "203.0.113.7")

    parser = RdapParser(db_session)
    parser.parse_file(FIXTURE, "rdap.ndjson", project_id=test_project.id)

    attrs = attributions_for_host(db_session, inside.id)
    assert len(attrs) == 1
    assert attrs[0].cidr == "8.8.8.0/24"
    assert attrs[0].org_name == "Google LLC"
    assert attrs[0].source == AttributionSource.RDAP
    assert attrs[0].looked_up_at is not None, (
        "registration goes stale; without a timestamp an operator can hand a "
        "client evidence that was true months ago"
    )

    assert attributions_for_host(db_session, elsewhere.id) == []


def test_reingest_refreshes_rather_than_duplicating(
    db_session, test_project, tmp_path
):
    """A re-lookup is the newest answer, not an additional one."""
    _host(db_session, test_project.id, "8.8.8.8")

    RdapParser(db_session).parse_file(FIXTURE, "rdap.ndjson", project_id=test_project.id)
    RdapParser(db_session).parse_file(FIXTURE, "rdap.ndjson", project_id=test_project.id)

    blocks = (
        db_session.query(NetworkAttribution)
        .filter(NetworkAttribution.project_id == test_project.id)
        .all()
    )
    assert len({b.cidr for b in blocks}) == len(blocks), "one row per block per source"
    assert len(blocks) == 2

    mappings = db_session.query(HostNetworkAttribution).count()
    assert mappings == 1, "correlation must replace, not accumulate"


def test_attribution_does_not_leak_across_projects(db_session, test_project, tmp_path):
    """Two engagements can involve the same address space."""
    from app.db.models_project import Project

    other = Project(name="other-engagement", slug="other-engagement")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    mine = _host(db_session, test_project.id, "8.8.8.8")
    theirs = _host(db_session, other.id, "8.8.8.8")

    RdapParser(db_session).parse_file(FIXTURE, "rdap.ndjson", project_id=test_project.id)

    assert len(attributions_for_host(db_session, mine.id)) == 1
    assert attributions_for_host(db_session, theirs.id) == [], (
        "another project's host must not inherit this project's attribution"
    )


def test_hosts_discovered_later_still_get_attributed(db_session, test_project):
    """A registration covers addresses found tomorrow just as much as today's,
    so correlation can't be a one-shot at ingest time."""
    RdapParser(db_session).parse_file(FIXTURE, "rdap.ndjson", project_id=test_project.id)

    late = _host(db_session, test_project.id, "8.8.8.44")
    assert attributions_for_host(db_session, late.id) == []

    correlate_project_attributions(db_session, test_project.id)
    assert len(attributions_for_host(db_session, late.id)) == 1


def test_most_specific_block_is_reported_first(db_session, test_project):
    """A /29 registration says more about who runs an address than the /8 it
    sits inside."""
    host = _host(db_session, test_project.id, "8.8.8.8")
    db_session.add_all([
        NetworkAttribution(
            project_id=test_project.id, cidr="8.0.0.0/8", org_name="Wide",
            source=AttributionSource.RDAP,
        ),
        NetworkAttribution(
            project_id=test_project.id, cidr="8.8.8.0/24", org_name="Narrow",
            source=AttributionSource.RDAP,
        ),
    ])
    db_session.commit()
    correlate_project_attributions(db_session, test_project.id)

    attrs = attributions_for_host(db_session, host.id)
    assert [a.org_name for a in attrs] == ["Narrow", "Wide"]


def test_parse_stats_report_what_landed(db_session, test_project):
    """Ingestion quality is surfaced the same way every other parser does it."""
    _host(db_session, test_project.id, "8.8.8.8")
    parser = RdapParser(db_session)
    parser.parse_file(FIXTURE, "rdap.ndjson", project_id=test_project.id)

    stats = parser.last_parse_stats
    assert stats["skipped"] == 0
    assert stats["warnings"] is None
    assert "2 netblocks attributed" in stats["summary"]
    assert "1 host matched" in stats["summary"]


def test_malformed_lines_are_counted_not_fatal(db_session, test_project, tmp_path):
    """A truncated or hand-edited file should ingest what it can and say what
    it couldn't — silently dropping records is the failure mode that makes
    partial data look complete."""
    path = tmp_path / "mixed.ndjson"
    path.write_text(
        '{"query":"8.8.8.8","rdap":{"startAddress":"8.8.8.0","endAddress":"8.8.8.255"}}\n'
        "not json at all\n"
        '{"query":"x","rdap":{"objectClassName":"ip network"}}\n'
    )
    parser = RdapParser(db_session)
    parser.parse_file(str(path), "mixed.ndjson", project_id=test_project.id)

    assert parser.last_parse_stats["skipped"] == 2
    assert parser.last_parse_stats["warnings"]
    assert (
        db_session.query(NetworkAttribution)
        .filter(NetworkAttribution.project_id == test_project.id)
        .count()
        == 1
    )


# ---------------------------------------------------------------------------
# Querying attribution
# ---------------------------------------------------------------------------
# The point of attribution is answering "what did we touch that isn't the
# client's?" — which requires it to be filterable, not just displayable.

from app.services.host_query_dsl import BuildCtx, evaluate, parse_query  # noqa: E402


def _matching(db, project_id, user, q):
    return {
        h.ip_address
        for h in db.query(Host)
        .filter(Host.project_id == project_id)
        .filter(evaluate(parse_query(q), BuildCtx(db, user, project_id)))
        .all()
    }


@pytest.fixture
def attributed_project(db_session, test_project):
    """Two hosts registered to the client, one to somebody else."""
    client_a = _host(db_session, test_project.id, "198.51.100.10")
    client_b = _host(db_session, test_project.id, "198.51.100.11")
    stranger = _host(db_session, test_project.id, "203.0.113.10")

    db_session.add_all([
        NetworkAttribution(
            project_id=test_project.id, cidr="198.51.100.0/24",
            org_name="Acme Corporation", asn=64500, country="US",
            source=AttributionSource.RDAP,
        ),
        NetworkAttribution(
            project_id=test_project.id, cidr="203.0.113.0/24",
            org_name="Unrelated Hosting BV", asn=64501, country="NL",
            cloud_provider="aws", cloud_region="eu-west-1",
            source=AttributionSource.RDAP,
        ),
    ])
    db_session.commit()
    correlate_project_attributions(db_session, test_project.id)
    return {"client": [client_a, client_b], "stranger": stranger}


def test_org_filter_selects_the_clients_estate(
    db_session, test_project, test_user, attributed_project
):
    assert _matching(db_session, test_project.id, test_user, 'org:"Acme"') == {
        "198.51.100.10", "198.51.100.11",
    }


def test_negated_org_is_the_scope_validation_query(
    db_session, test_project, test_user, attributed_project
):
    """The query this whole feature exists to make possible: what did we touch
    that is NOT registered to the client?"""
    assert _matching(db_session, test_project.id, test_user, 'NOT org:"Acme"') == {
        "203.0.113.10",
    }


def test_asn_filter(db_session, test_project, test_user, attributed_project):
    assert _matching(db_session, test_project.id, test_user, "asn:64500") == {
        "198.51.100.10", "198.51.100.11",
    }
    # The AS prefix is optional — operators write it both ways.
    assert _matching(db_session, test_project.id, test_user, "asn:AS64501") == {
        "203.0.113.10",
    }


def test_cloud_filter_is_not_offered_while_nothing_populates_it(
    db_session, test_project, test_user, attributed_project
):
    """`cloud:` is withheld from the query vocabulary until the cloud
    prefix-list importer exists.

    Registered against columns with no writer it didn't fail — it answered
    *wrongly*: `cloud:aws` returned zero hosts and `cloud:none` ("attributed
    but not in a known cloud range") returned every attributed host. On a
    surface framed as scope validation, a filter that quietly returns the
    wrong set is worse than one that isn't there. The predicate itself is kept
    and correct; this guards the vocabulary, not the SQL.
    """
    from app.services.host_query_dsl import DSLError

    with pytest.raises(DSLError):
        _matching(db_session, test_project.id, test_user, "cloud:aws")


def test_attribution_filters_compose_with_the_rest_of_the_dsl(
    db_session, test_project, test_user, attributed_project
):
    assert _matching(
        db_session, test_project.id, test_user, 'NOT org:"Acme" AND asn:64501',
    ) == {"203.0.113.10"}


def test_ndjson_is_an_allowed_upload_extension():
    """scripts/rdap-lookup.py emits .ndjson, and UPLOAD_FORMATS documents it as
    supported — so the upload allowlist MUST accept it. Regression: .ndjson was
    missing (only .jsonl was listed), so RDAP uploads 400'd on the extension
    check before ever reaching the parser, which had always handled the content."""
    from app.services.ingestion_service import ALLOWED_UPLOAD_EXTENSIONS
    assert ".ndjson" in ALLOWED_UPLOAD_EXTENSIONS
    assert ".jsonl" in ALLOWED_UPLOAD_EXTENSIONS
