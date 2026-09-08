#!/usr/bin/env python3
"""Add a named-asset scenario to an EXISTING project so the Names inventory,
domain scope, the third coverage state, named web interfaces, per-vhost
findings, named plan entries and tested bindings can be evaluated end to end.

Run inside the backend container (scripts/ is bind-mounted at /app/scripts):

    docker compose exec backend python scripts/seed_named_assets.py
    docker compose exec backend python scripts/seed_named_assets.py --reset

Additive to `seed_demo_data.py` (defaults to the same project name).  Goes
through the real write paths — dns_name_service, HostDeduplicationService,
upsert_vulnerability, FindingService, TestPlanService, the execution
endpoint's TESTED helper — so what lands is exactly what ingest would land.

Scenario (every fixture exercises one reviewed behaviour):
  * Imported FQDN list, NOT resolved → unresolved names, no hosts invented.
    Includes `*.dev.example-corp.com` AND `dev.example-corp.com` — two assets.
  * Domain scope: `demo.local` (+subdomains), `portal.example-corp.com`
    (exact), `*.apps.example-corp.com`.  Import ≠ scope: only some imported
    names are in scope.
  * A load balancer 203.0.113.20 (OUTSIDE every subnet) carrying four vhosts,
    three in scope → the host is "reachable via in-scope name": not out of
    scope, not subnet-scoped.  Display name is its PTR; vhosts never displace it.
  * `portal.example-corp.com` moved from 203.0.113.10 (old scan) → .20:
    .10 is a PREVIOUS address and stays out of scope (historical answers
    confer nothing).  `shop.example-corp.com` shares the address but is not
    in scope (name scope never approves the address's other names).
  * Two resolvers answering the same A record (two rows); CNAME chain; a
    cert whose SANs carry a wildcard; HTTP contact evidence.
  * Web interfaces bound to each vhost; nikto-style findings on two vhosts
    with the SAME plugin/port → two scanner rows; promoting both yields one
    umbrella finding with BOTH endpoints.
  * A plan with three entries on the LB host (portal / shop / bare address)
    plus a named internal entry; executed result WITH observed_ip → TESTED,
    skipped result → no evidence, executed WITHOUT observed_ip → no evidence.
  * Internal names bound to existing demo hosts by PTR / scanner / forward
    evidence, and one operator-corrected display name (top rank).

Deterministic; `--reset` removes only what this script created.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import models  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.db.models import (  # noqa: E402
    DNS_OBS_CERT, DNS_OBS_DISCOVERED, DNS_OBS_HTTP, DNS_OBS_SCANNER,
)
from app.db.models_agent import (  # noqa: E402
    Agent, ExecutionSession, TestExecutionResult, TestPlan,
)
from app.db.models_auth import User, UserRole  # noqa: E402
from app.db.models_project import Project  # noqa: E402
from app.db.models_vulnerability import VulnerabilitySeverity, VulnerabilitySource  # noqa: E402
from app.parsers.parser_utils import upsert_vulnerability  # noqa: E402
from app.services import dns_name_service as names  # noqa: E402
from app.services.finding_service import FindingService  # noqa: E402
from app.services.host_deduplication_service import HostDeduplicationService  # noqa: E402
from app.services.test_plan_service import TestPlanService  # noqa: E402
from app.services.tested_binding_service import sync_tested_binding  # noqa: E402

NOW = datetime.now(timezone.utc)
TAG = "[named-assets seed]"           # marks rows this script owns (for --reset)
LB_IP, OLD_LB_IP = "203.0.113.20", "203.0.113.10"
LB_PTR = "lb-edge-01.example-corp.net"
VHOSTS = [
    # (fqdn, title, in scope?)
    ("portal.example-corp.com", "Example Corp — Customer Portal", True),
    ("api.apps.example-corp.com", "Example Corp API v2", True),
    ("admin.apps.example-corp.com", "Admin Console — sign in", True),
    ("shop.example-corp.com", "Example Corp Store", False),
]
IMPORTED = [
    "vpn.example-corp.com", "sso.example-corp.com", "legacy.example-corp.com",
    "*.dev.example-corp.com", "dev.example-corp.com", "intranet.demo.local",
    "https://Portal.Example-Corp.com/login",   # tolerated: URL → portal.example-corp.com
    "203.0.113.20",                            # rejected: an address is a host
]


def _scan(db, project, filename, tool, age_days):
    s = models.Scan(project_id=project.id, filename=f"{TAG} {filename}", tool_name=tool, scan_type=tool)
    s.created_at = NOW - timedelta(days=age_days)
    s.start_time = s.created_at
    db.add(s)
    db.flush()
    return s


# ---------------------------------------------------------------------------
# Ownership: a manifest of exactly what one run created
# ---------------------------------------------------------------------------
# Project membership or a matching fixture IP is NOT ownership — an operator
# may have imported the same name, declared the same domain or scanned the
# same address.  So the seed snapshots every table it touches before and
# after, records the ids that appeared (plus the display names it changed on
# pre-existing hosts), and --reset deletes only those.  No manifest, no reset.
from app.db.models_findings import Finding, FindingHost  # noqa: E402
from app.db.models_vulnerability import Vulnerability  # noqa: E402

_OWNED_TABLES = {
    # attribute -> (model, project filter column or None for join-through)
    "dns_names": models.DNSName,
    "dns_records": models.DNSRecord,
    "scope_domains": models.ScopeDomain,
    "hosts": models.Host,
    "scans": models.Scan,
    "web_interfaces": models.WebInterface,
    "vulnerabilities": Vulnerability,
    "findings": Finding,
    "finding_hosts": FindingHost,
    "test_plans": TestPlan,
    "agents": Agent,
    "execution_sessions": ExecutionSession,
}


def _ids(db, project, key):
    """Current row ids of one owned table, scoped to the project."""
    m = _OWNED_TABLES[key]
    if key == "scope_domains":
        q = db.query(m.id).filter(m.scope_id.in_(db.query(models.Scope.id).filter(models.Scope.project_id == project.id)))
    elif key == "vulnerabilities":
        q = db.query(m.id).join(models.Host, models.Host.id == m.host_id).filter(models.Host.project_id == project.id)
    elif key == "finding_hosts":
        q = db.query(m.id).join(Finding, Finding.id == m.finding_id).filter(Finding.project_id == project.id)
    elif key == "execution_sessions":
        q = db.query(m.id).join(TestPlan, TestPlan.id == m.test_plan_id).filter(TestPlan.project_id == project.id)
    else:
        q = db.query(m.id).filter(m.project_id == project.id)
    return {r[0] for r in q.all()}


def _snapshot(db, project):
    return {key: _ids(db, project, key) for key in _OWNED_TABLES}


def _manifest_path(project, manifest_dir):
    import os
    return os.path.join(manifest_dir, f"seed_named_assets.project-{project.id}.json")


def _write_manifest(path, before, after, hostname_before):
    import json, os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    created = {key: sorted(after[key] - before[key]) for key in _OWNED_TABLES}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "tag": TAG, "written_at": NOW.isoformat(), "created": created,
            # host_id -> [hostname, hostname_source] as they were before we
            # applied PTR / operator display names to pre-existing hosts.
            "hostname_before": {str(k): v for k, v in hostname_before.items()},
        }, fh, indent=1)
    return created


def reset(db, project, manifest_dir):
    """Remove ONLY the rows a previous run created (per its manifest) and
    restore the display names it changed.  Refuses without a manifest, and
    refuses to delete a seeded name that something outside the manifest has
    since come to reference (a raw delete would SET NULL two endpoint rows
    onto one key — the API returns 409 for the same reason)."""
    import json, os
    path = _manifest_path(project, manifest_dir)
    if not os.path.exists(path):
        raise SystemExit(f"refusing to reset: no manifest at {path} — ownership of existing rows cannot be established")
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    created = {k: set(v) for k, v in manifest["created"].items()}

    from app.db.models_agent import TestPlanEntry
    foreign_refs = (
        db.query(models.DNSName.fqdn)
        .filter(models.DNSName.id.in_(created["dns_names"] or {-1}))
        .filter(
            db.query(FindingHost.id).filter(
                FindingHost.name_id == models.DNSName.id, ~FindingHost.id.in_(created["finding_hosts"] or {-1}),
            ).exists()
            | db.query(TestPlanEntry.id).filter(
                TestPlanEntry.name_id == models.DNSName.id,
                ~TestPlanEntry.test_plan_id.in_(created["test_plans"] or {-1}),
            ).exists()
        )
        .all()
    )
    if foreign_refs:
        raise SystemExit(
            "refusing to reset: seeded names are now referenced by rows this script did not create: "
            f"{[r[0] for r in foreign_refs]} — detach them first"
        )

    def _delete(key):
        ids = created.get(key) or set()
        if not ids:
            return 0
        m = _OWNED_TABLES[key]
        rows = db.query(m).filter(m.id.in_(ids)).all()
        for r in rows:
            db.delete(r)
        db.flush()
        return len(rows)

    removed = {}
    # Dependency order: plans (cascade entries/results/sessions) and agents,
    # then findings / endpoints / scanner rows / web interfaces we created,
    # then hosts we created (their remaining children cascade), scans, then
    # observations and names, then scope domains.
    for key in ("test_plans", "agents", "execution_sessions", "finding_hosts", "findings",
                "vulnerabilities", "web_interfaces", "hosts", "scans", "dns_records", "dns_names", "scope_domains"):
        removed[key] = _delete(key)
    for host_id, (hostname, source) in manifest.get("hostname_before", {}).items():
        h = db.get(models.Host, int(host_id))
        if h is not None:
            h.hostname, h.hostname_source = hostname, source
    db.commit()
    os.remove(path)
    print("  reset: removed " + ", ".join(f"{v} {k}" for k, v in removed.items() if v)
          + f"; restored {len(manifest.get('hostname_before', {}))} display name(s)")


def seed(db, project, owner, manifest_dir):
    before = _snapshot(db, project)
    hostname_before = {}
    _seed_body(db, project, owner, hostname_before)
    after = _snapshot(db, project)
    created = _write_manifest(_manifest_path(project, manifest_dir), before, after, hostname_before)
    print("  manifest: " + ", ".join(f"{len(v)} {k}" for k, v in created.items() if v))


def _seed_body(db, project, owner, hostname_before):
    scope = db.query(models.Scope).filter(models.Scope.project_id == project.id).first()
    if scope is None:
        scope = models.Scope(project_id=project.id, name="Demo scope")
        db.add(scope)
        db.flush()
    dedup = HostDeduplicationService(db)
    cache = names.ObservationCache()

    # 1. Domain scope — a separate, explicit declaration.
    added, updated, invalid = names.upsert_scope_domains(db, scope, [
        ("demo.local", True, "Internal AD namespace"),
        ("portal.example-corp.com", False, "Customer portal (exact)"),
        ("*.apps.example-corp.com", False, "Application vhosts"),
    ], created_by_id=owner.id)
    print(f"  scope domains: +{added} ~{updated} invalid={invalid}")

    # 2. Imported FQDN list — nothing resolved, nothing invented.
    stats = names.import_names(db, project_id=project.id, raw_names=IMPORTED, created_by_id=owner.id)
    print(f"  import: created={stats['names_created']} existing={stats['names_existing']} "
          f"wildcards={stats['wildcards']} invalid={stats['invalid']}")

    # 3. The load balancer: PTR display name, four vhosts, resolver-attributed A
    #    records, a CNAME chain, HTTP + CERT evidence, and a rotated address.
    old_dns = _scan(db, project, "dnsx-old.jsonl", "dnsx", 20)
    new_dns = _scan(db, project, "dnsx-fresh.jsonl", "dnsx", 1)
    httpx = _scan(db, project, "httpx.jsonl", "httpx", 1)
    nikto = _scan(db, project, "nikto.json", "nikto", 1)

    # Pre-existing hosts on the fixture addresses are REUSED, never owned —
    # but their display names may change below, so snapshot them for reset.
    for h in db.query(models.Host).filter(
        models.Host.project_id == project.id, models.Host.ip_address.in_([LB_IP, OLD_LB_IP]),
    ).all():
        hostname_before[h.id] = [h.hostname, h.hostname_source]
    lb = dedup.find_or_create_host(LB_IP, new_dns.id, {
        "hostname": LB_PTR, "hostname_source": "ptr", "hostnames": [(LB_PTR, "PTR")], "state": "up",
    }, project_id=project.id)
    old_lb = dedup.find_or_create_host(OLD_LB_IP, old_dns.id, {
        "hostname": "lb-edge-00.example-corp.net", "hostname_source": "ptr",
        "hostnames": [("lb-edge-00.example-corp.net", "PTR")], "state": "up",
    }, project_id=project.id)
    old_lb.last_seen = NOW - timedelta(days=20)
    for port, svc in ((443, "https"), (80, "http")):
        dedup.find_or_create_port(lb.id, new_dns.id, {"port_number": port, "protocol": "tcp", "state": "open", "service_name": svc})
    db.flush()

    # portal used to live on .10 (old scan, one resolver) …
    names.record_observation(db, project_id=project.id, name="portal.example-corp.com", record_type="A",
                             value=OLD_LB_IP, scan_id=old_dns.id, resolver_name="10.10.0.53:53", ttl=300,
                             observed_at=NOW - timedelta(days=20), cache=cache)
    # … and now resolves to .20, answered by two resolvers (two rows).
    for fqdn, _title, _scoped in VHOSTS:
        for resolver in ("10.10.0.53:53", "1.1.1.1:53"):
            names.record_observation(db, project_id=project.id, name=fqdn, record_type="A", value=LB_IP,
                                     scan_id=new_dns.id, resolver_name=resolver, ttl=60,
                                     observed_at=NOW - timedelta(hours=6), cache=cache)
    names.record_observation(db, project_id=project.id, name="www.example-corp.com", record_type="CNAME",
                             value="portal.example-corp.com", scan_id=new_dns.id, resolver_name="1.1.1.1:53",
                             ttl=3600, observed_at=NOW - timedelta(hours=6), cache=cache)
    names.record_observation(db, project_id=project.id, name="www.example-corp.com", record_type="A",
                             value=LB_IP, scan_id=new_dns.id, resolver_name="1.1.1.1:53", ttl=60,
                             observed_at=NOW - timedelta(hours=6), cache=cache)

    # httpx: one interface per vhost + HTTP/CERT evidence.  The cert's SANs
    # carry a wildcard — a PATTERN asset, never in scope by itself.
    port_row = db.query(models.Port).filter_by(host_id=lb.id, port_number=443).first()
    for fqdn, title, _scoped in VHOSTS:
        url = f"https://{fqdn}/"
        name_id = names.bind_url_name(db, project_id=project.id, url=url, ip_address=LB_IP,
                                      scan_id=httpx.id, cache=cache)
        for san in ("portal.example-corp.com", "*.apps.example-corp.com", "shop.example-corp.com"):
            names.record_observation(db, project_id=project.id, name=san, record_type=DNS_OBS_CERT,
                                     value=LB_IP, scan_id=httpx.id, cache=cache)
        db.add(models.WebInterface(
            scan_id=httpx.id, host_id=lb.id, port_id=port_row.id if port_row else None,
            project_id=project.id, source="httpx", url=url, protocol="https", port=443,
            ip_address=LB_IP, name_id=name_id, status_code=200, title=title,
            server_header="nginx/1.24.0", technologies=["Nginx 1.24.0", "React"],
            tls_info={"subject_cn": "portal.example-corp.com",
                      "subject_an": ["portal.example-corp.com", "*.apps.example-corp.com", "shop.example-corp.com"],
                      "issuer_dn": "CN=Example Corp Issuing CA", "not_after": (NOW + timedelta(days=90)).isoformat()},
        ))
    db.flush()

    # nikto: the same check on two vhosts, same port → TWO scanner rows; plus a
    # host-level (unnamed) finding that must stay separate.
    for fqdn in ("portal.example-corp.com", "shop.example-corp.com"):
        name_id = names.bind_hostname(db, project_id=project.id, hostname=fqdn, ip_address=LB_IP,
                                      scan_id=nikto.id, cache=cache)
        upsert_vulnerability(
            db=db, host_id=lb.id, scan_id=nikto.id, source=VulnerabilitySource.NIKTO,
            title="The anti-clickjacking X-Frame-Options header is not present",
            severity=VulnerabilitySeverity.LOW, plugin_id="999957",
            port_id=port_row.id if port_row else None,
            description=f"Observed on https://{fqdn}/ — the response lacks X-Frame-Options.",
            name_id=name_id,
        )
    upsert_vulnerability(
        db=db, host_id=lb.id, scan_id=nikto.id, source=VulnerabilitySource.NIKTO,
        title="Server leaks version via the Server header", severity=VulnerabilitySeverity.INFO,
        plugin_id="999996", port_id=port_row.id if port_row else None,
        description="nginx/1.24.0 — host-level, no vhost.",
    )
    db.flush()

    # Promote both vhost findings → ONE umbrella finding, BOTH endpoints kept.
    fsvc = FindingService(db)
    vhost_vulns = (
        db.query(Vulnerability)
        .filter(Vulnerability.host_id == lb.id, Vulnerability.plugin_id == "999957")
        .order_by(Vulnerability.id).all()
    )
    finding = None
    for v in vhost_vulns:
        finding = fsvc.promote_vulnerability(vuln=v, project_id=project.id, actor_id=owner.id)
    db.flush()
    db.refresh(finding)
    print(f"  finding #{finding.id} '{finding.title[:40]}…' endpoints="
          f"{sorted(fh.name.fqdn for fh in finding.hosts if fh.name)}")

    # 4. Internal names on existing demo hosts: PTR (display), scanner-reported
    #    vhost, forward-resolved name (weakest), one operator correction.
    internal = (
        db.query(models.Host)
        .filter(models.Host.project_id == project.id, models.Host.ip_address.like("10.10.%"))
        .order_by(models.Host.id).limit(6).all()
    )
    internal_scan = _scan(db, project, "nmap-internal.xml", "nmap", 3)
    labels = ["dc01", "fs01", "wiki", "print01", "git", "jump"]
    named_internal = None
    for h, label in zip(internal, labels):
        hostname_before[h.id] = [h.hostname, h.hostname_source]   # restored on --reset
        ptr = f"{label}.demo.local"
        dedup.find_or_create_host(h.ip_address, internal_scan.id, {
            "hostname": ptr, "hostname_source": "ptr", "hostname_kind": "PTR",
            "hostnames": [(ptr, "PTR")], "state": "up",
        }, project_id=project.id)
        if label == "wiki":
            # a vhost forward-resolved onto the same box — evidence, not display
            names.record_observation(db, project_id=project.id, name="kb.demo.local", record_type="A",
                                     value=h.ip_address, scan_id=internal_scan.id, cache=cache)
            names.record_observation(db, project_id=project.id, name="kb.demo.local", record_type=DNS_OBS_HTTP,
                                     value=h.ip_address, scan_id=internal_scan.id, cache=cache)
            names.apply_hostname_candidate(h, "kb.demo.local", "forward")   # must NOT win over PTR
            named_internal = h
        if label == "jump":
            names.apply_hostname_candidate(h, "bastion-jump.demo.local", "operator")  # operator wins, locks
            db.flush()
    # A name a scanner reported for an address (nessus host-fqdn shape).
    if len(internal) > 3:
        names.record_observation(db, project_id=project.id, name="PRINT01", record_type=DNS_OBS_SCANNER,
                                 value=internal[3].ip_address, scan_id=internal_scan.id, cache=cache)
    # amass/subfinder-style bare discovery (no address).
    names.record_observation(db, project_id=project.id, name="staging.apps.example-corp.com",
                             record_type=DNS_OBS_DISCOVERED, value="subfinder", scan_id=new_dns.id, cache=cache)
    db.flush()

    # 5. Plan: three entries on the LB (portal / shop / bare) + a named internal
    #    entry; execution results exercising the TESTED rule.
    agent = Agent(name=f"{TAG} planner", project_id=project.id, owner_id=owner.id,
                  description="seeded", is_active=True)
    db.add(agent)
    db.flush()
    plan = TestPlan(project_id=project.id, agent_id=agent.id, created_by_user_id=owner.id, version=1,
                    title=f"{TAG} Named endpoint evaluation", status="approved",
                    description="Seeded plan: named targets on a shared address.")
    db.add(plan)
    db.flush()
    psvc = TestPlanService(db)
    base = {"host_id": lb.id, "priority": "high", "test_phase": "enumeration", "rationale": "Seeded: vhost-specific web test."}
    entries = psvc.add_entries(plan, [
        {**base, "target_fqdn": "portal.example-corp.com", "proposed_tests": [
            {"tool": "curl", "description": "Confirm missing X-Frame-Options on the portal vhost",
             "command": "curl -skI https://{fqdn}/ --resolve {fqdn}:443:{ip} | grep -i x-frame", "expected_result": "No header → finding"},
        ]},
        {**base, "target_fqdn": "shop.example-corp.com", "priority": "medium", "proposed_tests": [
            {"tool": "curl", "description": "Same check on the store vhost",
             "command": "curl -skI https://{fqdn}/ --resolve {fqdn}:443:{ip} | grep -i x-frame"},
        ]},
        {**base, "priority": "low", "rationale": "Seeded: bare-address service check.", "proposed_tests": [
            {"tool": "nmap", "description": "TLS ciphers on the shared address", "command": "nmap --script ssl-enum-ciphers -p 443 {ip}"},
        ]},
    ] + ([{
        "host_id": named_internal.id, "priority": "medium", "test_phase": "enumeration",
        "rationale": "Seeded: internal vhost.", "target_fqdn": "kb.demo.local",
        "proposed_tests": [{"tool": "nikto", "description": "Web checks on the wiki vhost", "command": "nikto -h https://{fqdn}/"}],
    }] if named_internal else []), "user", owner.id)
    db.flush()
    session = ExecutionSession(test_plan_id=plan.id, agent_id=agent.id, started_by_id=owner.id, status="active")
    db.add(session)
    db.flush()
    by_target = {(e.target_name.fqdn if e.target_name else None): e for e in entries if e.host_id == lb.id}
    results = [
        (by_target["portal.example-corp.com"], "executed", LB_IP,
         "HTTP/2 200 … (no X-Frame-Options header)", True, "low"),
        (by_target["shop.example-corp.com"], "skipped", None, None, False, None),
        (by_target[None], "executed", None, "443/tcp open  https  TLSv1.2 …", False, None),
    ]
    for entry, status, observed_ip, output, is_finding, sev in results:
        r = TestExecutionResult(
            execution_session_id=session.id, entry_id=entry.id, test_index=0, status=status,
            command_run=TestPlanService.resolve_command_placeholders(
                entry.proposed_tests[0]["command"], LB_IP, entry.target_name.fqdn if entry.target_name else None),
            raw_output=output, findings_summary=("Missing X-Frame-Options" if is_finding else None),
            severity=sev, is_finding=is_finding, observed_ip=observed_ip,
            executed_at=NOW - timedelta(hours=2) if status == "executed" else None,
        )
        db.add(r)
        db.flush()
        db.refresh(entry)
        sync_tested_binding(db, entry, r)   # executed + observed_ip → TESTED; otherwise nothing
    db.commit()
    print(f"  plan #{plan.id}: {len(entries)} entries (LB host appears {sum(1 for e in entries if e.host_id == lb.id)}×), "
          f"session #{session.id} with {len(results)} results")


def summarize(db, project):
    from app.services.scope_coverage import out_of_scope_hosts
    from sqlalchemy import func
    n = db.query(func.count(models.DNSName.id)).filter(models.DNSName.project_id == project.id).scalar()
    in_scope = db.query(func.count(models.DNSName.id)).filter(
        models.DNSName.project_id == project.id, names.name_in_scope_condition(project.id)).scalar()
    kinds = dict(db.query(models.DNSRecord.record_type, func.count(models.DNSRecord.id))
                 .filter(models.DNSRecord.project_id == project.id).group_by(models.DNSRecord.record_type).all())
    reachable = db.query(func.count(models.Host.id)).filter(
        models.Host.project_id == project.id,
        names.host_reachable_via_in_scope_name_condition(project.id)).scalar()
    _, oos = out_of_scope_hosts(db, project.id)
    print(f"  names={n} in_scope={in_scope} observations={kinds}")
    print(f"  hosts reachable via in-scope name={reachable}  out-of-scope hosts={oos} "
          f"(expect {OLD_LB_IP} among them, {LB_IP} not)")


def main():
    ap = argparse.ArgumentParser(description="Seed a named-asset scenario into an existing project.")
    ap.add_argument("--project", default="Demo — Insights Eval")
    ap.add_argument("--reset", action="store_true",
                    help="Remove ONLY the rows a previous run created (per its manifest), then re-seed.")
    ap.add_argument("--reset-only", action="store_true", help="Remove the previous run's rows and stop.")
    ap.add_argument("--manifest-dir", default=None,
                    help="Where the ownership manifest lives (default: the app's UPLOAD_DIR).")
    args = ap.parse_args()
    import os
    from app.core.config import settings
    manifest_dir = args.manifest_dir or settings.UPLOAD_DIR
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.name == args.project).first()
        if project is None:
            print(f"Project '{args.project}' not found — run seed_demo_data.py first.")
            return 1
        owner = (db.query(User).filter(User.role == UserRole.ADMIN).first() or db.query(User).first())
        if args.reset or args.reset_only:
            reset(db, project, manifest_dir)
            if args.reset_only:
                return 0
        elif os.path.exists(_manifest_path(project, manifest_dir)):
            print("This project already carries a seeded scenario. Re-run with --reset to replace it.")
            return 1
        print(f"Seeding named assets into '{project.name}' (id={project.id})…")
        seed(db, project, owner, manifest_dir)
        summarize(db, project)
        print("Done. Open Inventory → Names, the Scopes page (Domains in scope), the LB host "
              f"{LB_IP}, Findings, and the seeded test plan.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
