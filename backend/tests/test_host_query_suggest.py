"""``GET /hosts/query/suggest`` — value autocomplete for the Hosts query DSL.

The command bar's facet lists are capped and cascaded by the active filters;
this endpoint asks the database for ONE field's values, project-wide.  The
rules pinned here: every source is project-scoped (several predicates lean on
the outer host query for that, which a suggestion does not have), a suggested
port/service is one the plain insertion would match (open ports), and every
field's ``value_source`` is either enumerable or deliberately not.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.host_query_dsl import _FIELD_SPECS
from app.services.host_query_suggest import supported_sources


def _seed(db_session, project_id, *, ip_prefix="10.0.0", cve="CVE-2021-44228"):
    scan = models.Scan(project_id=project_id, filename=f"openvas-{ip_prefix}.xml",
                       scan_type="openvas", tool_name="openvas")
    db_session.add(scan)
    db_session.flush()
    hosts = []
    for i, os_name in enumerate(["Windows Server 2019", "Linux", "Linux"], start=1):
        h = models.Host(project_id=project_id, ip_address=f"{ip_prefix}.{i}",
                        hostname=f"h{i}.corp.example", os_name=os_name, state="up")
        db_session.add(h)
        db_session.flush()
        hosts.append(h)
    # 445 open on two hosts; 4444 only closed (a plain port:4444 matches nothing).
    for h, port, svc, state in [
        (hosts[0], 445, "microsoft-ds", "open"),
        (hosts[1], 445, "microsoft-ds", "open"),
        (hosts[2], 4444, "krb524", "closed"),
        (hosts[2], 22, "ssh", "open"),
    ]:
        db_session.add(models.Port(host_id=h.id, port_number=port, protocol="tcp",
                                   state=state, service_name=svc,
                                   service_product="OpenSSH" if svc == "ssh" else None,
                                   service_version="8.9p1" if svc == "ssh" else None))
    db_session.add(models.WebInterface(
        project_id=project_id, host_id=hosts[0].id, scan_id=scan.id, port=443,
        url=f"https://{ip_prefix}.1/", source="httpx", server_header="nginx/1.18.0",
        title="Welcome", technologies=["Nginx", "PHP"],
    ))
    db_session.add(Vulnerability(
        host_id=hosts[0].id, scan_id=scan.id, cve_id=cve, plugin_id="155999",
        title="Apache Log4Shell RCE", severity=VulnerabilitySeverity.CRITICAL,
        source=VulnerabilitySource.NESSUS,
    ))
    tag = models.HostTag(project_id=project_id, name="production")
    db_session.add(tag)
    db_session.flush()
    db_session.add(models.HostTagAssignment(host_id=hosts[0].id, tag_id=tag.id))
    db_session.flush()
    return {"scan": scan, "hosts": hosts}


def _other_project(db_session):
    p = Project(name="other", slug="other-project", description="x", is_default=False)
    db_session.add(p)
    db_session.flush()
    return p


def _suggest(client, project_id, field, prefix="", **params):
    resp = client.get(
        f"/api/v1/projects/{project_id}/hosts/query/suggest",
        params={"field": field, "prefix": prefix, **params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _values(body):
    return [v["value"] for v in body["values"]]


def test_port_suggests_open_ports_only_with_host_counts(client, db_session, test_project):
    _seed(db_session, test_project.id)
    body = _suggest(client, test_project.id, "port", "44")
    assert body["supported"] is True
    # 4444 exists but only closed: port:4444 would match nothing, so it isn't offered.
    assert _values(body) == ["445"]
    assert body["values"][0]["count"] == 2


def test_port_with_a_name_suggests_nothing(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _values(_suggest(client, test_project.id, "port", "ssh")) == []


def test_suggestions_never_cross_projects(client, db_session, test_project):
    _seed(db_session, test_project.id)
    other = _other_project(db_session)
    _seed(db_session, other.id, ip_prefix="192.168.9", cve="CVE-2099-0001")

    assert _values(_suggest(client, test_project.id, "cve", "CVE-20")) == ["CVE-2021-44228"]
    assert _values(_suggest(client, test_project.id, "ip", "192.")) == []
    tech = _suggest(client, test_project.id, "tech", "ng")
    assert tech["values"] == [{"value": "Nginx", "label": None, "count": 1}]
    scans = _values(_suggest(client, test_project.id, "scan", "openvas"))
    assert len(scans) == 1


def test_substring_match_on_text_fields(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _values(_suggest(client, test_project.id, "os", "server")) == ["Windows Server 2019"]
    assert _values(_suggest(client, test_project.id, "vuln", "log4")) == ["Apache Log4Shell RCE"]
    assert _values(_suggest(client, test_project.id, "header", "nginx")) == ["nginx/1.18.0"]
    assert _values(_suggest(client, test_project.id, "version", "openssh")) == ["OpenSSH 8.9p1"]
    assert _values(_suggest(client, test_project.id, "tag", "prod")) == ["production"]


def test_alias_resolves_to_its_field(client, db_session, test_project):
    _seed(db_session, test_project.id)
    body = _suggest(client, test_project.id, "svc", "micro")
    assert body["field"] == "service"
    assert _values(body) == ["microsoft-ds"]


def test_scan_is_found_by_filename_and_offers_the_id(client, db_session, test_project):
    seeded = _seed(db_session, test_project.id)
    body = _suggest(client, test_project.id, "scan", "openvas")
    assert body["values"] == [{
        "value": str(seeded["scan"].id),
        "label": "openvas-10.0.0.xml (openvas)",
        "count": None,
    }]


def test_enum_field_carries_descriptions(client, db_session, test_project):
    body = _suggest(client, test_project.id, "has", "smb")
    assert _values(body) == ["smb_unsigned"]
    assert body["values"][0]["label"]


def test_assigned_offers_fixed_words_and_project_members_only(client, db_session, test_project):
    # Explicit ids: test_user takes id=1 without advancing the sequence.
    member = User(id=431, username="ana.analyst", email="ana@example.com", full_name="Ana Analyst",
                  hashed_password="x", role=UserRole.MEMBER, is_active=True,
                  created_at=datetime.now(timezone.utc))
    outsider = User(id=432, username="ana.outsider", email="out@example.com", full_name="Ana Outsider",
                    hashed_password="x", role=UserRole.MEMBER, is_active=True,
                    created_at=datetime.now(timezone.utc))
    db_session.add_all([member, outsider])
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role="analyst"))
    db_session.flush()

    assert _values(_suggest(client, test_project.id, "assigned", "ana")) == ["ana.analyst"]
    # Full names match too — what a teammate is known by.
    assert _values(_suggest(client, test_project.id, "assignee", "Analyst")) == ["ana.analyst"]
    # The fixed words come first, matched by prefix.
    assert _values(_suggest(client, test_project.id, "assigned", "no"))[0] == "none"
    assert _values(_suggest(client, test_project.id, "assigned", "m"))[0] == "me"


def test_unknown_and_free_fields_are_unsupported(client, db_session, test_project):
    assert _suggest(client, test_project.id, "nosuchfield", "x")["supported"] is False
    assert _suggest(client, test_project.id, "note", "x")["supported"] is False
    assert _suggest(client, test_project.id, "firstseen", "")["supported"] is False


def test_limit_is_honoured(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert len(_suggest(client, test_project.id, "ip", "10.", limit=2)["values"]) == 2


def test_every_value_source_is_enumerable_or_deliberately_not():
    """A new field whose value_source this module can't enumerate would get no
    suggestions without anyone noticing — name it here or add a source."""
    not_enumerated = {"enum", "window", "free"}
    for spec in _FIELD_SPECS:
        assert spec.value_source in set(supported_sources()) | not_enumerated, spec.name
        if spec.value_source == "enum":
            assert spec.enum_values, spec.name
    # "free" is for text there is nothing to enumerate from — keep it that way.
    assert {s.name for s in _FIELD_SPECS if s.value_source == "free"} == {"note"}
