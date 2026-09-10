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
        ("*.Example.com", ("*.example.com", "wildcard")),
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
        assert names == {"unresolved.example.com": "fqdn", "portal.example.com": "fqdn", "*.example.com": "wildcard"}
        kinds = {(r.name.fqdn, r.record_type) for r in db_session.query(models.DNSRecord).all()}
        assert ("unresolved.example.com", DNS_OBS_DISCOVERED) in kinds
        assert ("portal.example.com", DNS_OBS_HTTP) in kinds
        assert ("portal.example.com", DNS_OBS_CERT) in kinds
        assert ("*.example.com", DNS_OBS_CERT) in kinds
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

    def test_covered_names_total_is_deduplicated_across_nested_entries(self, db_session, test_project):
        """A wildcard and one of its exact descendants both count the same
        name per row; the project total counts it once."""
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(
            db_session, scope, [("*.example.com", False, None), ("portal.example.com", False, None)],
        )
        svc.get_or_create_name(db_session, test_project.id, "portal.example.com")
        svc.get_or_create_name(db_session, test_project.id, "outside.test")
        db_session.flush()
        domains = db_session.query(models.ScopeDomain).filter_by(scope_id=scope.id).all()
        per_row = svc.scope_domain_name_counts(db_session, test_project.id, domains)
        assert sum(per_row.values()) == 2  # counted under both entries
        assert svc.scope_domains_covered_names_total(db_session, test_project.id) == 1

    def test_wildcard_name_is_never_in_scope_by_itself(self, db_session, test_project):
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(db_session, scope, [("*.example.com", False, None)])
        svc.get_or_create_name(db_session, test_project.id, "*.example.com", kind="wildcard")
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
        listing = client.get(f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/domains").json()
        assert listing["items"] == [] and listing["total"] == 0


# ---------------------------------------------------------------------------
# Review fixes (v2.323.0)
# ---------------------------------------------------------------------------
class TestReviewFixes:
    def test_wildcard_and_base_domain_are_distinct_assets_either_order(self, db_session, test_project):
        for order in (["*.example.com", "example.com"], ["example.com", "*.example.com"]):
            svc.import_names(db_session, project_id=test_project.id, raw_names=order, created_by_id=None)
        rows = {n.fqdn: n.kind for n in db_session.query(models.DNSName).all()}
        assert rows == {"example.com": "fqdn", "*.example.com": "wildcard"}
        # Certificate wildcard evidence lands on the pattern, never the base.
        scan = _scan(db_session, test_project, tool="httpx")
        svc.record_observation(db_session, project_id=test_project.id, name="*.example.com",
                               record_type=DNS_OBS_CERT, value="203.0.113.20", scan_id=scan.id)
        base = db_session.query(models.DNSName).filter_by(fqdn="example.com").one()
        assert db_session.query(models.DNSRecord).filter_by(name_id=base.id).count() == 1  # its IMPORT only

    def test_normalisation_collisions_are_one_observation(self, db_session, test_project):
        scan = _scan(db_session, test_project)
        a = svc.record_observation(db_session, project_id=test_project.id, name="A.Example.com",
                                   record_type="A", value="10.0.0.1", scan_id=scan.id)
        b = svc.record_observation(db_session, project_id=test_project.id, name="a.example.com.",
                                   record_type="A", value="10.0.0.1", scan_id=scan.id)
        assert a is not None and b is None
        assert db_session.query(models.DNSName).count() == 1

    def test_deleting_both_scans_in_either_order_succeeds(self, db_session, test_project):
        """Two scans hold the same answer; deleting both must not trip the
        observation identity on the orphaned (scan_id NULL) rows."""
        for first, second in ((0, 1), (1, 0)):
            db_session.query(models.DNSRecord).delete()
            db_session.query(models.DNSName).delete()
            db_session.query(models.Scan).delete()
            scans = [_scan(db_session, test_project) for _ in range(2)]
            for s in scans:
                svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                                       record_type="A", value="203.0.113.20", scan_id=s.id, resolver_name="1.1.1.1:53")
            db_session.flush()
            db_session.delete(scans[first])
            db_session.flush()
            db_session.delete(scans[second])
            db_session.flush()  # would raise IntegrityError under the old single index
            orphans = db_session.query(models.DNSRecord).filter(models.DNSRecord.scan_id.is_(None)).count()
            assert orphans == 2

    def test_historical_answer_does_not_confer_coverage(self, db_session, test_project):
        """Name moved .10 -> .20: only .20 is reachable; .10 is out of scope
        again, and the by-host view files .10 under 'previous'."""
        from app.services.scope_coverage import out_of_scope_hosts
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(db_session, scope, [("portal.example.com", False, None)])
        t0 = datetime.now(timezone.utc) - timedelta(days=7)
        old = _scan(db_session, test_project, when=t0)
        new = _scan(db_session, test_project)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                               record_type="A", value="203.0.113.10", scan_id=old.id, observed_at=t0)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                               record_type="A", value="203.0.113.20", scan_id=new.id,
                               observed_at=datetime.now(timezone.utc))
        h_old = models.Host(ip_address="203.0.113.10", project_id=test_project.id, state="up")
        h_new = models.Host(ip_address="203.0.113.20", project_id=test_project.id, state="up")
        db_session.add_all([h_old, h_new])
        db_session.flush()
        hosts, total = out_of_scope_hosts(db_session, test_project.id)
        assert total == 1 and hosts[0].ip_address == "203.0.113.10"
        assert svc.names_per_address(db_session, test_project.id, ["203.0.113.10", "203.0.113.20"]) == {"203.0.113.20": 1}

    def test_by_host_view_splits_current_previous_other(self, client, db_session, test_project):
        t0 = datetime.now(timezone.utc) - timedelta(days=7)
        old = _scan(db_session, test_project, when=t0)
        new = _scan(db_session, test_project)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                               record_type="A", value="203.0.113.10", scan_id=old.id, observed_at=t0)
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                               record_type="A", value="203.0.113.20", scan_id=new.id,
                               observed_at=datetime.now(timezone.utc))
        svc.record_observation(db_session, project_id=test_project.id, name="cert-only.example.com",
                               record_type=DNS_OBS_CERT, value="203.0.113.10", scan_id=new.id)
        h_old = models.Host(ip_address="203.0.113.10", project_id=test_project.id, state="up")
        db_session.add(h_old)
        db_session.commit()
        body = client.get(f"/api/v1/projects/{test_project.id}/names/by-host/{h_old.id}").json()
        assert body["current"] == []
        assert [b["fqdn"] for b in body["previous"]] == ["portal.example.com"]
        assert [b["fqdn"] for b in body["other"]] == ["cert-only.example.com"]
        assert body["in_scope_via_names"] is False

    def test_reimport_query_budget(self, db_session, test_project, test_engine):
        """A no-change re-import must not issue a statement per name."""
        from sqlalchemy import event
        names = [f"host{i}.example.com" for i in range(100)]
        svc.import_names(db_session, project_id=test_project.id, raw_names=names, created_by_id=None)
        db_session.flush()
        count = {"n": 0}

        def _before(*_a, **_k):
            count["n"] += 1

        event.listen(test_engine, "before_cursor_execute", _before)
        try:
            stats = svc.import_names(db_session, project_id=test_project.id, raw_names=names, created_by_id=None)
        finally:
            event.remove(test_engine, "before_cursor_execute", _before)
        assert stats["names_created"] == 0 and stats["observations_recorded"] == 0
        assert count["n"] <= 6, f"re-import of 100 names issued {count['n']} statements"

    def test_scope_domain_listing_is_paged_and_counts_set_based(self, client, db_session, test_project, test_engine):
        from sqlalchemy import event
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(
            db_session, scope, [(f"d{i}.example.com", i % 2 == 0, None) for i in range(30)],
        )
        for i in range(30):
            svc.get_or_create_name(db_session, test_project.id, f"d{i}.example.com")
            svc.get_or_create_name(db_session, test_project.id, f"x.d{i}.example.com")
        db_session.commit()
        count = {"n": 0}

        def _before(*_a, **_k):
            count["n"] += 1

        event.listen(test_engine, "before_cursor_execute", _before)
        try:
            body = client.get(f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/domains?limit=10").json()
        finally:
            event.remove(test_engine, "before_cursor_execute", _before)
        assert body["total"] == 30 and len(body["items"]) == 10 and body["has_more"] is True
        # Even-numbered entries include subdomains (exact + x.<domain> = 2);
        # odd ones are exact-only (1).  Collation decides which land on page 1.
        for d in body["items"]:
            idx = int(d["domain"].split(".")[0][1:])
            assert d["name_count"] == (2 if idx % 2 == 0 else 1), d
        assert count["n"] <= 12, f"paged scope-domain listing issued {count['n']} statements"


# ---------------------------------------------------------------------------
# Phases two and three (v2.323.0)
# ---------------------------------------------------------------------------
class TestNamedEndpoints:
    def test_web_interfaces_bind_to_the_url_name(self, db_session, test_project, tmp_path):
        from app.parsers.httpx_parser import HttpxParser
        rows = [
            {"url": "https://portal.example.com", "host": "portal.example.com", "host_ip": "203.0.113.20",
             "port": 443, "scheme": "https", "status_code": 200},
            {"url": "https://203.0.113.20:8443", "host": "203.0.113.20", "host_ip": "203.0.113.20",
             "port": 8443, "scheme": "https", "status_code": 200},
        ]
        f = tmp_path / "httpx.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        HttpxParser(db_session).parse_file(str(f), "httpx.jsonl", project_id=test_project.id)
        wis = {w.url: w for w in db_session.query(models.WebInterface).all()}
        assert wis["https://portal.example.com"].name.fqdn == "portal.example.com"
        assert wis["https://203.0.113.20:8443"].name_id is None

    def test_nikto_findings_anchor_to_the_name_and_promotion_inherits_it(self, db_session, test_project, tmp_path):
        from app.parsers.nikto_parser import NiktoParser
        from app.services.finding_service import FindingService
        from app.db.models_vulnerability import Vulnerability
        # nikto JSON: one record per finding, each carrying its own target.
        payload = {"vulnerabilities": [
            {"id": "999990", "msg": "Test header missing", "url": "/", "OSVDB": "0",
             "ip": "203.0.113.20", "hostname": "portal.example.com", "port": "443"},
        ]}
        f = tmp_path / "nikto.json"
        f.write_text(json.dumps(payload))
        NiktoParser(db_session).parse_file(str(f), "nikto.json", project_id=test_project.id)
        vuln = db_session.query(Vulnerability).one()
        assert vuln.name.fqdn == "portal.example.com"
        finding = FindingService(db_session).promote_vulnerability(
            vuln=vuln, project_id=test_project.id, actor_id=None,
        )
        db_session.flush()
        assert [fh.name.fqdn for fh in finding.hosts] == ["portal.example.com"]

    def test_plan_entry_target_fqdn_must_be_bound_to_the_host(self, db_session, test_project, test_plan):
        from app.services.test_plan_service import TestPlanService
        scan = _scan(db_session, test_project)
        host = models.Host(ip_address="203.0.113.20", project_id=test_project.id, state="up")
        db_session.add(host)
        db_session.flush()
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                               record_type="A", value="203.0.113.20", scan_id=scan.id)
        svc.get_or_create_name(db_session, test_project.id, "elsewhere.example.com")
        plan_svc = TestPlanService(db_session)
        base = {"host_id": host.id, "priority": "high", "test_phase": "enumeration",
                "proposed_tests": [{"tool": "curl", "description": "x", "command": "curl https://{fqdn}/"}],
                "rationale": "r"}
        with pytest.raises(ValueError, match="never been observed"):
            plan_svc.add_entries(test_plan, [{**base, "target_fqdn": "elsewhere.example.com"}], "user", 1)
        with pytest.raises(ValueError, match="not a name"):
            plan_svc.add_entries(test_plan, [{**base, "target_fqdn": "unknown.example.org"}], "user", 1)
        created = plan_svc.add_entries(test_plan, [{**base, "target_fqdn": "Portal.Example.com"}], "user", 1)
        assert created[0].target_name.fqdn == "portal.example.com"

    def _named_entry(self, db_session, test_project, test_plan):
        from app.db.models_agent import TestPlanEntry
        scan = _scan(db_session, test_project)
        host = models.Host(ip_address="203.0.113.20", project_id=test_project.id, state="up")
        db_session.add(host)
        db_session.flush()
        svc.record_observation(db_session, project_id=test_project.id, name="portal.example.com",
                               record_type="A", value="203.0.113.20", scan_id=scan.id)
        name = db_session.query(models.DNSName).one()
        entry = TestPlanEntry(test_plan_id=test_plan.id, host_id=host.id, name_id=name.id, priority="high",
                              test_phase="enumeration", proposed_tests=[], rationale="r")
        db_session.add(entry)
        db_session.flush()
        db_session.refresh(entry)
        return host, name, entry

    def _result(self, db_session, entry, status, observed_ip, test_index=0):
        from app.db.models_agent import ExecutionSession, TestExecutionResult
        session = db_session.query(ExecutionSession).filter_by(test_plan_id=entry.test_plan_id).first()
        if session is None:
            session = ExecutionSession(test_plan_id=entry.test_plan_id, status="active")
            db_session.add(session)
            db_session.flush()
        row = TestExecutionResult(
            execution_session_id=session.id, entry_id=entry.id, test_index=test_index, status=status,
            observed_ip=observed_ip, executed_at=datetime.now(timezone.utc) if status == "executed" else None,
        )
        db_session.add(row)
        db_session.flush()
        return row

    def test_observed_ip_is_validated(self):
        from app.api.v1.endpoints.agent_execution import _validate_observed_ip
        from fastapi import HTTPException
        assert _validate_observed_ip(" 203.0.113.21 ") == "203.0.113.21"
        assert _validate_observed_ip(None) is None
        with pytest.raises(HTTPException):
            _validate_observed_ip("portal.example.com")

    def test_tested_binding_only_for_executed_results_with_an_observed_address(
        self, db_session, test_project, test_plan,
    ):
        """Review C3: a skipped result, or one with no observed address, must
        not manufacture TESTED evidence — and the inventory IP is never
        assumed.  A correction replaces the result's observation."""
        from app.db.models import DNS_OBS_TESTED
        from app.api.v1.endpoints.agent_execution import _record_tested_binding
        host, name, entry = self._named_entry(db_session, test_project, test_plan)
        tested = lambda: db_session.query(models.DNSRecord).filter_by(record_type=DNS_OBS_TESTED).all()  # noqa: E731

        skipped = self._result(db_session, entry, "skipped", None)
        _record_tested_binding(db_session, entry, skipped)
        assert tested() == []

        no_ip = self._result(db_session, entry, "executed", None, test_index=1)
        _record_tested_binding(db_session, entry, no_ip)
        assert tested() == []  # unknown stays unknown

        no_ip.observed_ip = "203.0.113.21"
        db_session.flush()
        _record_tested_binding(db_session, entry, no_ip)
        rows = tested()
        assert [(r.name_id, r.value, r.exec_result_id) for r in rows] == [(name.id, "203.0.113.21", no_ip.id)]

        # Re-recording the same result replaces, never piles up …
        no_ip.observed_ip = "203.0.113.22"
        db_session.flush()
        _record_tested_binding(db_session, entry, no_ip)
        assert [r.value for r in tested()] == ["203.0.113.22"]
        # … and downgrading it to skipped withdraws the evidence.
        no_ip.status = "skipped"
        db_session.flush()
        _record_tested_binding(db_session, entry, no_ip)
        assert tested() == []

    def test_two_named_targets_on_one_host_are_two_entries(self, db_session, test_project, test_plan):
        """Review C2: the shared-address use case — two vhosts, two entries."""
        from app.services.test_plan_service import TestPlanService
        scan = _scan(db_session, test_project)
        host = models.Host(ip_address="203.0.113.20", project_id=test_project.id, state="up")
        db_session.add(host)
        db_session.flush()
        for fq in ("a.example.com", "b.example.com"):
            svc.record_observation(db_session, project_id=test_project.id, name=fq, record_type="A",
                                   value="203.0.113.20", scan_id=scan.id)
        base = {"host_id": host.id, "priority": "high", "test_phase": "enumeration",
                "proposed_tests": [{"tool": "curl", "description": "x", "command": "curl https://{fqdn}/"}],
                "rationale": "r"}
        created = TestPlanService(db_session).add_entries(
            test_plan,
            [{**base, "target_fqdn": "a.example.com"}, {**base, "target_fqdn": "b.example.com"}, dict(base)],
            "user", 1,
        )
        targets = sorted(((e.target_name.fqdn if e.target_name else "") for e in created))
        assert targets == ["", "a.example.com", "b.example.com"]
        # A true duplicate (same host, same name) is still skipped.
        again = TestPlanService(db_session).add_entries(test_plan, [{**base, "target_fqdn": "a.example.com"}], "user", 1)
        assert again == []

    def test_vulnerabilities_on_two_vhosts_stay_distinct_and_promotion_keeps_both(
        self, db_session, test_project, tmp_path,
    ):
        """Review C1: nikto findings for a. and b.example.com on one IP/port are
        two scanner rows; promoting the issue keeps both affected endpoints."""
        from app.parsers.nikto_parser import NiktoParser
        from app.services.finding_service import FindingService
        from app.db.models_vulnerability import Vulnerability
        payload = {"vulnerabilities": [
            {"id": "999990", "msg": "Test header missing", "url": "/", "ip": "203.0.113.20",
             "hostname": h, "port": "443"} for h in ("a.example.com", "b.example.com")
        ]}
        f = tmp_path / "nikto.json"
        f.write_text(json.dumps(payload))
        NiktoParser(db_session).parse_file(str(f), "nikto.json", project_id=test_project.id)
        vulns = db_session.query(Vulnerability).order_by(Vulnerability.id).all()
        assert [v.name.fqdn for v in vulns] == ["a.example.com", "b.example.com"]
        fsvc = FindingService(db_session)
        f1 = fsvc.promote_vulnerability(vuln=vulns[0], project_id=test_project.id, actor_id=None)
        f2 = fsvc.promote_vulnerability(vuln=vulns[1], project_id=test_project.id, actor_id=None)
        db_session.flush()
        assert f1.id == f2.id  # same issue → one umbrella finding …
        db_session.refresh(f1)
        assert sorted(fh.name.fqdn for fh in f1.hosts) == ["a.example.com", "b.example.com"]  # … both endpoints

    def test_bundle_snapshot_carries_target_and_resolved_commands(self, db_session, test_project, test_plan, test_user):
        """Review C4: the offline executor must see the approved vhost, not
        just the host's display name, and no literal placeholders."""
        import io, zipfile
        from app.services.bundle_service import build_export_bundle
        from app.db.models_agent import TestPlanEntry
        scan = _scan(db_session, test_project)
        host = models.Host(ip_address="203.0.113.20", project_id=test_project.id, state="up",
                           hostname="lb.example.com")
        db_session.add(host)
        db_session.flush()
        svc.record_observation(db_session, project_id=test_project.id, name="a.example.com", record_type="A",
                               value="203.0.113.20", scan_id=scan.id)
        name = db_session.query(models.DNSName).one()
        db_session.add(TestPlanEntry(
            test_plan_id=test_plan.id, host_id=host.id, name_id=name.id, priority="high", test_phase="enumeration",
            proposed_tests=[{"tool": "curl", "description": "x", "command": "curl -k https://{fqdn}/ --resolve {fqdn}:443:{ip}"}],
            rationale="r",
        ))
        db_session.commit()
        bundle = build_export_bundle(db=db_session, request=None, plan=test_plan, started_by_id=test_user.id, agent_id=None)
        plan_json = json.loads(zipfile.ZipFile(io.BytesIO(bundle["zip_bytes"])).read("plan.json"))
        entry = plan_json["entries"][0]
        assert entry["target_fqdn"] == "a.example.com" and entry["host_hostname"] == "lb.example.com"
        assert entry["proposed_tests"][0]["command"] == "curl -k https://a.example.com/ --resolve a.example.com:443:203.0.113.20"

    def test_first_time_import_query_budget(self, db_session, test_project, test_engine):
        """Review refactor 1: a fresh import of 100 names is bulk work, not
        four statements per name."""
        from sqlalchemy import event
        names = [f"new{i}.example.com" for i in range(100)]
        count = {"n": 0}

        def _before(*_a, **_k):
            count["n"] += 1

        event.listen(test_engine, "before_cursor_execute", _before)
        try:
            stats = svc.import_names(db_session, project_id=test_project.id, raw_names=names, created_by_id=None)
        finally:
            event.remove(test_engine, "before_cursor_execute", _before)
        assert stats["names_created"] == 100 and stats["observations_recorded"] == 100
        assert count["n"] <= 8, f"first-time import of 100 names issued {count['n']} statements"
        assert db_session.query(models.DNSRecord).filter_by(record_type=DNS_OBS_IMPORT).count() == 100

    def test_agent_host_detail_lists_names(self, db_session, test_project):
        from app.api.v1.endpoints.agent_schemas import HostDetail
        assert "names" in HostDetail.model_fields


# ---------------------------------------------------------------------------
# Review round 3 (v2.325.0)
# ---------------------------------------------------------------------------
class TestReviewRound3:
    def _lb_with_two_vhosts(self, db_session, test_project):
        scan = _scan(db_session, test_project)
        host = models.Host(ip_address="203.0.113.20", project_id=test_project.id, state="up")
        db_session.add(host)
        db_session.flush()
        for fq in ("a.example.com", "b.example.com"):
            svc.record_observation(db_session, project_id=test_project.id, name=fq, record_type="A",
                                   value="203.0.113.20", scan_id=scan.id)
        by_fqdn = {n.fqdn: n for n in db_session.query(models.DNSName).all()}
        return host, by_fqdn

    def test_deleting_a_referenced_name_is_refused(self, client, db_session, test_project, test_plan):
        """C1: a named entry + an unnamed entry on one host; deleting the name
        would collapse both onto the NULL key.  Refuse with 409 instead."""
        from app.services.test_plan_service import TestPlanService
        host, names_by = self._lb_with_two_vhosts(db_session, test_project)
        base = {"host_id": host.id, "priority": "high", "test_phase": "enumeration",
                "proposed_tests": [{"tool": "curl", "description": "x", "command": "curl {fqdn}"}], "rationale": "r"}
        TestPlanService(db_session).add_entries(
            test_plan, [{**base, "target_fqdn": "a.example.com"}, dict(base)], "user", 1,
        )
        db_session.commit()
        a = names_by["a.example.com"]
        r = client.delete(f"/api/v1/projects/{test_project.id}/names/{a.id}")
        assert r.status_code == 409, r.text
        assert "1 plan entry" in r.json()["detail"]
        assert db_session.query(models.DNSName).filter_by(id=a.id).count() == 1
        # An unreferenced name still deletes.
        b = names_by["b.example.com"]
        assert client.delete(f"/api/v1/projects/{test_project.id}/names/{b.id}").status_code == 204

    def test_detach_one_endpoint_keeps_sibling_and_undo_restores_name_and_status(
        self, client, db_session, test_project,
    ):
        """C2: FindingHost rows are the affected-endpoint records; detach and
        restore address the row, not the host."""
        from app.services.finding_service import FindingService
        from app.db.models_findings import FindingHost
        host, names_by = self._lb_with_two_vhosts(db_session, test_project)
        fsvc = FindingService(db_session)
        finding = fsvc.create_finding(project_id=test_project.id, title="XFO missing", severity="low", actor_id=None)
        for fq in ("a.example.com", "b.example.com"):
            fsvc.restore_endpoint(finding=finding, host_id=host.id, name_id=names_by[fq].id, host_status="open")
        db_session.commit()
        body = client.get(f"/api/v1/projects/{test_project.id}/findings/{finding.id}").json()
        rows = {h["fqdn"]: h for h in body["hosts"]}
        assert set(rows) == {"a.example.com", "b.example.com"} and all("id" in h for h in rows.values())

        # Mark 'a' remediated, then detach it — 'b' must survive.
        a_row = db_session.query(FindingHost).filter_by(id=rows["a.example.com"]["id"]).one()
        a_row.host_status = "remediated"
        db_session.commit()
        r = client.delete(f"/api/v1/projects/{test_project.id}/findings/{finding.id}/endpoints/{a_row.id}")
        assert r.status_code == 200, r.text
        assert [h["fqdn"] for h in r.json()["hosts"]] == ["b.example.com"]

        # Undo restores the SAME endpoint with its status, not a bare host.
        r = client.post(
            f"/api/v1/projects/{test_project.id}/findings/{finding.id}/hosts",
            json={"host_ids": [], "endpoints": [
                {"host_id": host.id, "name_id": names_by["a.example.com"].id, "host_status": "remediated"},
            ]},
        )
        assert r.status_code == 200, r.text
        restored = {h["fqdn"]: h for h in r.json()["hosts"]}
        assert set(restored) == {"a.example.com", "b.example.com"}
        assert restored["a.example.com"]["host_status"] == "remediated"
        # The legacy "detach every endpoint on this host" route still does exactly that.
        r = client.delete(f"/api/v1/projects/{test_project.id}/findings/{finding.id}/hosts/{host.id}")
        assert r.json()["hosts"] == []

    def test_offline_import_syncs_tested_evidence(self, db_session, test_project, test_plan):
        """C3: results arriving through the bundle importer produce, correct
        and withdraw TESTED evidence exactly like the online endpoint."""
        from app.db.models import DNS_OBS_TESTED
        from app.db.models_agent import ExecutionSession, TestPlanEntry
        from app.services.bundle_import_service import _ingest_results
        host, names_by = self._lb_with_two_vhosts(db_session, test_project)
        entry = TestPlanEntry(test_plan_id=test_plan.id, host_id=host.id, name_id=names_by["a.example.com"].id,
                              priority="high", test_phase="enumeration",
                              proposed_tests=[{"tool": "curl", "description": "x", "command": "curl {fqdn}"}],
                              rationale="r")
        db_session.add(entry)
        session = ExecutionSession(test_plan_id=test_plan.id, status="active", mode="offline_bundle")
        db_session.add(session)
        db_session.flush()
        db_session.refresh(entry)
        tested = lambda: [(r.value, r.exec_result_id) for r in  # noqa: E731
                          db_session.query(models.DNSRecord).filter_by(record_type=DNS_OBS_TESTED).all()]

        def ingest(item):
            errors: list = []
            n, _ = _ingest_results(db_session, session=session, entry_map={entry.id: entry}, items=[item], errors=errors)
            assert n == 1, errors
            db_session.flush()

        ingest({"entry_id": entry.id, "test_index": 0, "status": "skipped"})
        assert tested() == []
        ingest({"entry_id": entry.id, "test_index": 0, "status": "executed"})   # no observed_ip → nothing
        assert tested() == []
        ingest({"entry_id": entry.id, "test_index": 0, "status": "executed", "observed_ip": "203.0.113.21"})
        rows = tested()
        assert len(rows) == 1 and rows[0][0] == "203.0.113.21"
        ingest({"entry_id": entry.id, "test_index": 0, "status": "executed", "observed_ip": "203.0.113.22"})
        assert [v for v, _ in tested()] == ["203.0.113.22"]      # correction replaces
        ingest({"entry_id": entry.id, "test_index": 0, "status": "skipped"})
        assert tested() == []                                     # withdrawal


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


# ---------------------------------------------------------------------------
# GET /names/export — the filtered list, taken out (v2.330.0)
# ---------------------------------------------------------------------------
class TestNamesExport:
    def _seed(self, db_session, test_project):
        scope = _scope(db_session, test_project)
        svc.upsert_scope_domains(db_session, scope, [("acme.com", True, None)])
        svc.record_observation(db_session, project_id=test_project.id, name="portal.acme.com",
                               record_type="A", value="10.0.0.10")
        svc.get_or_create_name(db_session, test_project.id, "orphan.other.net")
        svc.get_or_create_name(db_session, test_project.id, "*.acme.com", "wildcard")
        db_session.flush()

    def test_txt_is_one_fqdn_per_line_honouring_the_list_filters(self, client, db_session, test_project):
        self._seed(db_session, test_project)
        r = client.get(f"/api/v1/projects/{test_project.id}/names/export?format=txt")
        assert r.status_code == 200, r.text
        assert r.text.splitlines() == ["*.acme.com", "orphan.other.net", "portal.acme.com"]
        assert r.headers["content-disposition"].endswith("names.txt")

        r = client.get(f"/api/v1/projects/{test_project.id}/names/export?format=txt&state=in_scope")
        assert r.text.splitlines() == ["portal.acme.com"]
        r = client.get(f"/api/v1/projects/{test_project.id}/names/export?format=txt&search=other&order=desc")
        assert r.text.splitlines() == ["orphan.other.net"]

    def test_csv_carries_state_and_current_addresses(self, client, db_session, test_project):
        self._seed(db_session, test_project)
        r = client.get(f"/api/v1/projects/{test_project.id}/names/export?format=csv")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        rows = [ln.split(",") for ln in r.text.splitlines()]
        assert rows[0] == ["fqdn", "kind", "in_scope", "current_ips", "last_seen"]
        by_fqdn = {row[0]: row for row in rows[1:]}
        assert by_fqdn["portal.acme.com"][1:4] == ["fqdn", "true", "10.0.0.10"]
        assert by_fqdn["orphan.other.net"][1:4] == ["fqdn", "false", ""]
        assert by_fqdn["*.acme.com"][1:3] == ["wildcard", "false"]

    def test_bad_format_or_state_is_rejected(self, client, db_session, test_project):
        assert client.get(f"/api/v1/projects/{test_project.id}/names/export?format=xlsx").status_code == 422
        assert client.get(f"/api/v1/projects/{test_project.id}/names/export?state=nope").status_code == 400
