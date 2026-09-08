"""Named assets (v2.322.0) — phase-one acceptance criteria as tests.

- Re-importing a name list creates no duplicate name assets.
- Unresolved names remain visible (no placeholder hosts).
- Shared / changing addresses create relationships without merging names.
- Evidence kinds and observation times stay distinguishable.
- Domain coverage works independently of subnet coverage; a host reached via
  an in-scope name is neither out of scope nor subnet-in-scope.
- Existing IP-based ingestion keeps working; distinct names on one address
  no longer generate hostname conflicts.
- The display-name rule: operator > PTR > scanner > forward.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models import DNS_OBS_CERT, DNS_OBS_DISCOVERED, DNS_OBS_HTTP, DNS_OBS_IMPORT, DNS_OBS_SCANNER
from app.db.models_confidence import ConflictHistory
from app.services import dns_name_service as svc
from app.services.host_deduplication_service import HostDeduplicationService


# ---------------------------------------------------------------------------
# Normalisation (pure)
# ---------------------------------------------------------------------------
class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("Portal.Example.COM.", ("portal.example.com", "fqdn")),
        ("  api.example.com ", ("api.example.com", "fqdn")),
        ("https://Portal.example.com/login?x=1", ("portal.example.com", "fqdn")),
        ("portal.example.com:8443", ("portal.example.com", "fqdn")),
        ("*.Example.com", ("example.com", "wildcard")),
        ("_dmarc.example.com", ("_dmarc.example.com", "fqdn")),
        ("db01", ("db01", "fqdn")),
        ("bücher.example", ("xn--bcher-kva.example", "fqdn")),
    ])
    def test_normalises(self, raw, expected):
        assert svc.normalize_fqdn(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "10.0.0.5", "::1", "[2001:db8::1]:443", "a..b", "-bad.example", "foo.*.example"])
    def test_rejects(self, raw):
        with pytest.raises(svc.InvalidName):
            svc.normalize_fqdn(raw)


# ---------------------------------------------------------------------------
# Display-name rule (pure)
# ---------------------------------------------------------------------------
class TestHostnameRule:
    def _host(self, hostname=None, source=None):
        return models.Host(ip_address="10.0.0.1", hostname=hostname, hostname_source=source)

    def test_fills_empty(self):
        h = self._host()
        assert svc.apply_hostname_candidate(h, "web01", "forward")
        assert (h.hostname, h.hostname_source) == ("web01", "forward")

    def test_forward_never_replaces_scanner_or_legacy(self):
        h = self._host("db01", None)  # legacy row: ranked as scanner
        assert not svc.apply_hostname_candidate(h, "vhost.example.com", "forward")
        assert h.hostname == "db01"

    def test_ptr_replaces_scanner_but_not_operator(self):
        h = self._host("scanner-name", "scanner")
        assert svc.apply_hostname_candidate(h, "db01.corp", "ptr")
        assert h.hostname == "db01.corp"
        h.hostname_source = "operator"
        assert not svc.apply_hostname_candidate(h, "something-else", "ptr")
        assert h.hostname == "db01.corp"

    def test_operator_overrides_operator(self):
        h = self._host("first", "operator")
        assert svc.apply_hostname_candidate(h, "second", "operator")
        assert h.hostname == "second"

    def test_same_name_upgrades_provenance_only(self):
        h = self._host("db01", "scanner")
        assert not svc.apply_hostname_candidate(h, "db01", "ptr")
        assert h.hostname_source == "ptr"


# ---------------------------------------------------------------------------
# Import (DB)
# ---------------------------------------------------------------------------
def _scope(db, project):
    s = models.Scope(project_id=project.id, name="default")
    db.add(s)
    db.flush()
    return s


class TestImport:
    def test_reimport_is_idempotent_and_creates_no_hosts(self, db_session, test_project):
        names = ["Portal.Example.com", "api.example.com", "portal.example.com.", "*.dev.example.com", "10.0.0.5", ""]
        first = svc.import_names(db_session, project_id=test_project.id, raw_names=names, created_by_id=None)
        assert first["names_created"] == 3          # portal (twice, collapsed), api, wildcard
        assert first["wildcards"] == 1
        assert first["invalid_count"] == 1          # the IP
        assert first["observations_recorded"] == 3

        second = svc.import_names(db_session, project_id=test_project.id, raw_names=names, created_by_id=None)
        assert second["names_created"] == 0
        assert second["names_existing"] == 3
        assert second["observations_recorded"] == 0  # same raw string → same IMPORT observation

        assert db_session.query(models.DNSName).filter_by(project_id=test_project.id).count() == 3
        assert db_session.query(models.Host).filter_by(project_id=test_project.id).count() == 0
        imports = db_session.query(models.DNSRecord).filter_by(record_type=DNS_OBS_IMPORT).all()
        assert {r.value for r in imports} == {"Portal.Example.com", "api.example.com", "*.dev.example.com"}

    def test_declare_scope_is_separate_and_explicit(self, db_session, test_project):
        scope = _scope(db_session, test_project)
        svc.import_names(db_session, project_id=test_project.id, raw_names=["a.example.com"], created_by_id=None)
        assert db_session.query(models.ScopeDomain).count() == 0

        stats = svc.import_names(
            db_session, project_id=test_project.id, raw_names=["a.example.com", "*.lab.example.com"],
            created_by_id=None, declare_scope=True, scope=scope,
        )
        assert stats["scope_domains_added"] == 2
        rows = {d.domain: d for d in db_session.query(models.ScopeDomain).all()}
        assert rows["a.example.com"].include_subdomains is False
        assert rows["lab.example.com"].include_subdomains is True

    def test_import_endpoint(self, client, test_project):
        r = client.post(
            f"/api/v1/projects/{test_project.id}/names/import",
            json={"names": ["x.example.com", "y.example.com", "bad name!"], "declare_scope": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["names_created"] == 2 and body["invalid_count"] == 1
        listing = client.get(f"/api/v1/projects/{test_project.id}/names/?state=unresolved").json()
        assert listing["total"] == 2
        row = listing["items"][0]
        assert row["current_addresses"] == [] and row["imported"] is True and row["resolved"] is False


# ---------------------------------------------------------------------------
# Relationships, not merges
# ---------------------------------------------------------------------------
def _scan(db, project, tool="dnsx", when=None):
    s = models.Scan(filename=f"{tool}.json", tool_name=tool, scan_type="t", project_id=project.id,
                    created_at=when or datetime.now(timezone.utc))
    db.add(s)
    db.flush()
    return s


class TestRelationships:
    def test_shared_address_keeps_names_distinct(self, db_session, test_project):
        scan = _scan(db_session, test_project)
        for fqdn in ("portal.example.com", "api.example.com"):
            svc.record_observation(db_session, project_id=test_project.id, name=fqdn, record_type="A",
                                   value="203.0.113.20", scan_id=scan.id)
        host = models.Host(ip_address="203.0.113.20", project_id=test_project.id, hostname="portal.example.com")
        db_session.add(host)
        db_session.flush()

        assert db_session.query(models.DNSName).count() == 2
        states = svc.address_state_for_names(
            db_session, test_project.id, [n.id for n in db_session.query(models.DNSName).all()],
        )
        for st in states.values():
            assert list(st.current) == ["203.0.113.20"]
        assert svc.names_per_address(db_session, test_project.id, ["203.0.113.20"]) == {"203.0.113.20": 2}

    def test_changing_address_gives_current_and_previous(self, db_session, test_project):
        t0 = datetime.now(timezone.utc) - timedelta(days=7)
        old = _scan(db_session, test_project, when=t0)
        new = _scan(db_session, test_project)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com", record_type="A",
                               value="203.0.113.10", scan_id=old.id, observed_at=t0)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com", record_type="A",
                               value="203.0.113.20", scan_id=new.id, observed_at=datetime.now(timezone.utc))
        name = db_session.query(models.DNSName).one()
        st = svc.address_state_for_names(db_session, test_project.id, [name.id])[name.id]
        assert list(st.current) == ["203.0.113.20"]
        assert list(st.previous) == ["203.0.113.10"]
        assert st.evidence == {"A": 2}

    def test_evidence_kinds_do_not_become_resolutions(self, db_session, test_project):
        scan = _scan(db_session, test_project, tool="httpx")
        for kind in (DNS_OBS_HTTP, DNS_OBS_CERT, DNS_OBS_SCANNER):
            svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                                   record_type=kind, value="203.0.113.20", scan_id=scan.id)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                               record_type=DNS_OBS_DISCOVERED, value="amass", scan_id=scan.id)
        name = db_session.query(models.DNSName).one()
        st = svc.address_state_for_names(db_session, test_project.id, [name.id])[name.id]
        assert st.current == {} and st.previous == {}
        assert st.evidence == {DNS_OBS_CERT: 1, DNS_OBS_DISCOVERED: 1, DNS_OBS_HTTP: 1, DNS_OBS_SCANNER: 1}

    def test_same_observation_in_same_scan_is_one_row(self, db_session, test_project):
        scan = _scan(db_session, test_project)
        kw = dict(project_id=test_project.id, name="a.example.com", record_type="A", value="10.0.0.1",
                  scan_id=scan.id, resolver_name="1.1.1.1:53")
        assert svc.record_observation(db_session, **kw) is not None
        assert svc.record_observation(db_session, **kw) is None
        # Different resolver, same answer → a second row (the point of resolver_name).
        kw["resolver_name"] = "8.8.8.8:53"
        assert svc.record_observation(db_session, **kw) is not None
        # A later scan re-observing it is a NEW row (history survives).
        kw["scan_id"] = _scan(db_session, test_project).id
        assert svc.record_observation(db_session, **kw) is not None
        assert db_session.query(models.DNSRecord).count() == 3


# ---------------------------------------------------------------------------
# Dedup service: names become observations, not conflicts
# ---------------------------------------------------------------------------
class TestDedupIntegration:
    def test_distinct_names_on_one_address_are_bindings_not_conflicts(self, db_session, test_project):
        scan_a = _scan(db_session, test_project, tool="nmap")
        scan_b = _scan(db_session, test_project, tool="nessus")
        dedup = HostDeduplicationService(db_session)
        host = dedup.find_or_create_host("10.0.0.9", scan_a.id, {"hostname": "web01", "state": "up"},
                                         project_id=test_project.id)
        dedup.find_or_create_host("10.0.0.9", scan_b.id, {"hostname": "shop.example.com", "state": "up"},
                                  project_id=test_project.id)
        db_session.flush()
        assert host.hostname == "web01" and host.hostname_source == "scanner"
        assert db_session.query(ConflictHistory).filter_by(host_id=host.id, field_name="hostname").count() == 0
        bound = {r.name.fqdn for r in db_session.query(models.DNSRecord).filter_by(value="10.0.0.9").all()}
        assert bound == {"web01", "shop.example.com"}

    def test_nmap_hostnames_types_map_to_kinds(self, db_session, test_project):
        scan = _scan(db_session, test_project, tool="nmap")
        dedup = HostDeduplicationService(db_session)
        dedup.find_or_create_host(
            "10.0.0.10", scan.id,
            {"hostname": "target.example.com", "hostname_kind": "user", "hostname_source": "scanner",
             "hostnames": [("target.example.com", "user"), ("host-10.isp.net", "PTR")], "state": "up"},
            project_id=test_project.id,
        )
        kinds = {(r.name.fqdn, r.record_type) for r in db_session.query(models.DNSRecord).all()}
        assert kinds == {("target.example.com", "A"), ("host-10.isp.net", "PTR")}

    def test_names_recorded_flag_suppresses_duplicate_rows(self, db_session, test_project):
        scan = _scan(db_session, test_project)
        dedup = HostDeduplicationService(db_session)
        dedup.find_or_create_host("10.0.0.11", scan.id,
                                  {"hostname": "x.example.com", "names_recorded": True, "state": "unknown"},
                                  project_id=test_project.id)
        assert db_session.query(models.DNSRecord).count() == 0


# ---------------------------------------------------------------------------
# Parsers keep IP-less names
# ---------------------------------------------------------------------------
class TestParsersKeepNames:
    def test_amass_bare_names_survive(self, db_session, test_project, tmp_path):
        from app.parsers.amass_parser import AmassParser
        f = tmp_path / "subfinder.txt"
        f.write_text("portal.example.com\napi.example.com\nresolved.example.com 203.0.113.5\n")
        AmassParser(db_session).parse_file(str(f), "subfinder.txt", project_id=test_project.id)
        names = {n.fqdn for n in db_session.query(models.DNSName).all()}
        assert names == {"portal.example.com", "api.example.com", "resolved.example.com"}
        kinds = {(r.name.fqdn, r.record_type, r.value) for r in db_session.query(models.DNSRecord).all()}
        assert ("portal.example.com", DNS_OBS_DISCOVERED, "subfinder") in kinds
        assert ("resolved.example.com", "A", "203.0.113.5") in kinds
        # Only the resolved one became a host.
        assert [h.ip_address for h in db_session.query(models.Host).all()] == ["203.0.113.5"]

    def test_httpx_ipless_record_keeps_name_and_cert_sans(self, db_session, test_project, tmp_path):
        from app.parsers.httpx_parser import HttpxParser
        rows = [
            {"url": "https://unresolved.example.com", "host": "unresolved.example.com", "status_code": 200},
            {"url": "https://portal.example.com", "host": "portal.example.com", "host_ip": "203.0.113.20",
             "port": 443, "scheme": "https", "status_code": 200,
             "tls": {"subject_cn": "portal.example.com", "subject_an": ["portal.example.com", "*.example.com"]}},
        ]
        f = tmp_path / "httpx.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        HttpxParser(db_session).parse_file(str(f), "httpx.jsonl", project_id=test_project.id)
        names = {n.fqdn: n.kind for n in db_session.query(models.DNSName).all()}
        assert names == {"unresolved.example.com": "fqdn", "portal.example.com": "fqdn", "example.com": "wildcard"}
        kinds = {(r.name.fqdn, r.record_type) for r in db_session.query(models.DNSRecord).all()}
        assert ("unresolved.example.com", DNS_OBS_DISCOVERED) in kinds
        assert ("portal.example.com", DNS_OBS_HTTP) in kinds
        assert ("portal.example.com", DNS_OBS_CERT) in kinds
        assert ("example.com", DNS_OBS_CERT) in kinds
        assert db_session.query(models.Host).count() == 1

    def test_dnsx_rows_bind_to_names_and_keep_resolver(self, db_session, test_project, tmp_path):
        from app.parsers.dnsx_parser import DnsxParser
        rows = [
            {"host": "portal.example.com", "a": ["203.0.113.20"], "resolver": ["1.1.1.1:53"],
             "status_code": "NOERROR", "ttl": 300, "timestamp": "2026-09-01T10:00:00Z"},
            {"host": "portal.example.com", "a": ["203.0.113.20"], "resolver": ["8.8.8.8:53"], "status_code": "NOERROR"},
            {"host": "203.0.113.20", "ptr": ["lb1.example.net"], "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
        ]
        f = tmp_path / "dnsx.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        DnsxParser(db_session).parse_file(str(f), "dnsx.jsonl", project_id=test_project.id)
        recs = db_session.query(models.DNSRecord).all()
        assert len(recs) == 3 and all(r.name_id is not None for r in recs)
        stamped = [r for r in recs if r.resolver_name == "1.1.1.1:53" and r.record_type == "A"][0]
        assert stamped.observed_at.year == 2026 and stamped.observed_at.month == 9
        host = db_session.query(models.Host).filter_by(ip_address="203.0.113.20").one()
        # PTR outranks the forward-resolved vhost name for display.
        assert (host.hostname, host.hostname_source) == ("lb1.example.net", "ptr")


# ---------------------------------------------------------------------------
# Domain scope + the third coverage state
# ---------------------------------------------------------------------------
class TestDomainScope:
    def test_exact_vs_subdomains(self, db_session, test_project):
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(db_session, scope, [("portal.example.com", False, None), ("*.lab.example.com", False, None)])
        for fqdn in ("portal.example.com", "dev.portal.example.com", "a.lab.example.com", "lab.example.com", "other.example.com"):
            svc.get_or_create_name(db_session, test_project.id, fqdn)
        in_scope = {
            n.fqdn for n in db_session.query(models.DNSName).filter(svc.name_in_scope_condition(test_project.id)).all()
        }
        assert in_scope == {"portal.example.com", "a.lab.example.com", "lab.example.com"}

    def test_wildcard_name_is_never_in_scope_by_itself(self, db_session, test_project):
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(db_session, scope, [("*.example.com", False, None)])
        svc.get_or_create_name(db_session, test_project.id, "example.com", kind="wildcard")
        assert db_session.query(models.DNSName).filter(svc.name_in_scope_condition(test_project.id)).count() == 0

    def test_host_reached_via_in_scope_name_is_neither_in_nor_out(self, client, db_session, test_project):
        from app.services.scope_coverage import out_of_scope_hosts
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(db_session, scope, [("portal.example.com", False, None)])
        scan = _scan(db_session, test_project)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com", record_type="A",
                               value="203.0.113.20", scan_id=scan.id)
        reached = models.Host(ip_address="203.0.113.20", project_id=test_project.id, state="up")
        stray = models.Host(ip_address="198.51.100.7", project_id=test_project.id, state="up")
        db_session.add_all([reached, stray])
        db_session.commit()

        hosts, total = out_of_scope_hosts(db_session, test_project.id)
        assert total == 1 and hosts[0].ip_address == "198.51.100.7"
        assert db_session.query(models.HostSubnetMapping).count() == 0  # never subnet-in-scope

        cov = client.get(f"/api/v1/projects/{test_project.id}/scopes/coverage").json()
        assert cov["total_domains"] == 1
        assert cov["name_reachable_hosts"] == 1
        assert cov["out_of_scope_hosts"] == 1
        assert cov["scoped_hosts"] == 0
        assert cov["has_scope_configuration"] is True

        by_host = client.get(f"/api/v1/projects/{test_project.id}/names/by-host/{reached.id}").json()
        assert by_host["in_scope_via_names"] is True
        assert [b["fqdn"] for b in by_host["current"]] == ["portal.example.com"]

    def test_scope_domain_endpoints(self, client, db_session, test_project):
        scope = _scope(db_session, test_project)
        db_session.commit()
        r = client.post(
            f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/domains",
            json={"domains": [{"domain": "Example.com.", "include_subdomains": True}, {"domain": "10.1.1.1"}]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["added"] == 1 and len(body["invalid"]) == 1
        assert body["domains"][0]["domain"] == "example.com"
        # Idempotent re-add.
        r = client.post(f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/domains",
                        json={"domains": [{"domain": "example.com"}]})
        assert r.json()["added"] == 0
        dom_id = body["domains"][0]["id"]
        assert client.delete(f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/domains/{dom_id}").status_code == 200
        assert client.get(f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/domains").json() == []


# ---------------------------------------------------------------------------
# Existing consumers of dns_records keep working
# ---------------------------------------------------------------------------
class TestLegacyConsumers:
    def test_host_dns_records_card_still_matches_by_ip_and_hostname(self, client, db_session, test_project):
        scan = _scan(db_session, test_project)
        svc.record_observation(db_session, project_id=test_project.id, name="web01.example.com", record_type="A",
                               value="10.0.0.1", scan_id=scan.id, resolver_name="1.1.1.1:53")
        host = models.Host(ip_address="10.0.0.1", project_id=test_project.id, hostname="web01.example.com")
        db_session.add(host)
        db_session.commit()
        body = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}/dns-records").json()
        assert body["total"] == 1 and body["items"][0]["resolver_name"] == "1.1.1.1:53"
        scan_body = client.get(f"/api/v1/projects/{test_project.id}/scans/{scan.id}/dns-records").json()
        assert scan_body["total"] == 1
