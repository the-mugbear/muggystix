#!/usr/bin/env python3
"""Seed a realistic synthetic project so /insights (and the reshaped Operations)
can be evaluated with real data.

Run inside the backend container (the app package + DB are there):

    docker compose exec backend python scripts/seed_demo_data.py
    docker compose exec backend python scripts/seed_demo_data.py --hosts 600 --wipe

What it populates (the signals the insight services actually read):
  * Sites with criticality tiers (1=most critical) + subnets mapped to them
    — drives the per-site exposure scaling and the "By site" breakdowns.
  * Hosts spread across subnets with a mix of EOL OS (Win Server 2008/Win 7/
    CentOS 6/…) and current OS, SMB-signing disabled on some, and risky open
    services (telnet/ftp/rdp/smb) — the Hygiene lenses (EOL OS, risky services).
  * Vulnerabilities by severity — the Project-state "Vulnerabilities" tile.
  * **Findings (the spine)** with FindingHost links — exposure counts spine
    Findings (status open/confirmed/retest), NOT raw vulns, so these are what
    make AttentionCard / SubnetInsights exposure non-zero. Some unowned.
  * Scans dated fresh / stale / very-stale + spread last_seen — Neglect/staleness.
  * Host follows (assigned / in-review / reviewed) + authored notes for the
    owner user — the personal My-work queue + Recent-notes card.
  * Runs SubnetCorrelationService so host_subnet_mapping is populated.

Not seeded (would need WebInterface/netexec shapes): TLS-cert + weak-auth
hygiene lenses — they'll read zero, which is honest.

Deterministic (fixed RNG seed) so re-runs with --wipe reproduce the same data.
"""
from __future__ import annotations

import argparse
import ipaddress
import random
import sys
from datetime import datetime, timedelta, timezone

# Allow `python scripts/seed_demo_data.py` from /app (app is a package there).
sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import models  # noqa: E402
# Register every model module so SQLAlchemy can resolve cross-module
# relationships (Annotation → TestPlan, etc.) before mapper configuration.
from app.db import (  # noqa: E402,F401
    models_agent, models_auth, models_confidence, models_findings,
    models_integrations, models_llm, models_project, models_vulnerability,
)
from app.db.models_auth import User, UserRole  # noqa: E402
from app.db.models_project import Project  # noqa: E402
from app.db.models_vulnerability import (  # noqa: E402
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)
from app.db.models_findings import Finding, FindingHost  # noqa: E402
from app.db.models import FollowStatus  # noqa: E402
from app.services.subnet_correlation import SubnetCorrelationService  # noqa: E402

RNG = random.Random(1337)
NOW = datetime.now(timezone.utc)

# (os_name, os_family, vendor) — EOL ones first so the Hygiene EOL lens lights up.
OS_POOL = [
    ("Windows Server 2008 R2", "Windows", "Microsoft"),   # EOL
    ("Windows 7 Professional", "Windows", "Microsoft"),   # EOL
    ("Windows XP", "Windows", "Microsoft"),               # EOL
    ("CentOS 6.10", "Linux", "CentOS"),                   # EOL
    ("Windows Server 2012 R2", "Windows", "Microsoft"),   # EOL
    ("Windows Server 2022", "Windows", "Microsoft"),      # current
    ("Windows 11 Pro", "Windows", "Microsoft"),           # current
    ("Ubuntu 22.04.3 LTS", "Linux", "Canonical"),         # current
    ("Debian 12", "Linux", "Debian"),                     # current
    (None, None, None),                                   # unknown OS
]
# (port, service, risky?) — risky ones drive the "risky services" hygiene lens.
SERVICE_POOL = [
    (23, "telnet", True), (21, "ftp", True), (3389, "ms-wbt-server", True),
    (445, "microsoft-ds", True), (139, "netbios-ssn", True),
    (22, "ssh", False), (80, "http", False), (443, "https", False),
    (3306, "mysql", False), (8080, "http-proxy", False), (53, "domain", False),
]
SEVERITIES = [
    (VulnerabilitySeverity.CRITICAL, "critical", 0.06),
    (VulnerabilitySeverity.HIGH, "high", 0.14),
    (VulnerabilitySeverity.MEDIUM, "medium", 0.30),
    (VulnerabilitySeverity.LOW, "low", 0.30),
    (VulnerabilitySeverity.INFO, "info", 0.20),
]
SITES = [
    ("DMZ / Internet-facing", 1, ["10.10.0.0/24", "10.10.1.0/24"]),
    ("Corporate", 2, ["10.10.2.0/24", "10.10.3.0/24", "10.10.4.0/24"]),
    ("Lab / Test", 4, ["10.10.5.0/24", "10.10.6.0/24", "10.10.7.0/24"]),
]


def _wipe(db, name):
    proj = db.query(Project).filter(Project.name == name).first()
    if proj:
        db.delete(proj)  # cascades to scopes/hosts/scans/findings/…
        db.commit()
        print(f"  wiped existing project '{name}'")


def seed(db, name: str, host_count: int, owner: User):
    project = Project(name=name, slug=name.lower().replace(" ", "-").replace("/", "-")[:90],
                      description="Synthetic data for evaluating /insights + Operations.",
                      status="active")
    db.add(project)
    db.flush()

    scope = models.Scope(project_id=project.id, name="Demo scope")
    db.add(scope)
    db.flush()

    # Sites + subnets
    subnet_cidrs: list[str] = []
    for site_name, tier, cidrs in SITES:
        site = models.Site(project_id=project.id, name=site_name, criticality_tier=tier)
        db.add(site)
        db.flush()
        for cidr in cidrs:
            db.add(models.Subnet(scope_id=scope.id, cidr=cidr, site=site_name, site_id=site.id))
            subnet_cidrs.append(cidr)
    db.flush()

    # Scans: fresh, stale, very-stale
    scans = []
    for label, age_days in (("fresh-nmap.xml", 2), ("stale-nessus.nessus", 45), ("old-masscan.json", 120)):
        s = models.Scan(project_id=project.id, filename=label, scan_type="nmap")
        s.created_at = NOW - timedelta(days=age_days)
        db.add(s)
        scans.append(s)
    db.flush()

    nets = [ipaddress.ip_network(c) for c in subnet_cidrs]
    hosts = []
    for i in range(host_count):
        net = nets[i % len(nets)]
        host_idx = 10 + (i // len(nets))
        ip = str(net.network_address + host_idx)
        os_name, os_family, vendor = RNG.choice(OS_POOL)
        age = RNG.choice([1, 3, 8, 20, 40, 75, 120])  # last_seen spread → staleness mix
        h = models.Host(
            project_id=project.id, ip_address=ip,
            hostname=f"host-{i:04d}.demo.local" if RNG.random() < 0.6 else None,
            state="up" if RNG.random() < 0.7 else "unknown",
            os_name=os_name, os_family=os_family, os_vendor=vendor,
            smb_signing=(RNG.choice(["disabled", "enabled", "required"])
                         if os_family == "Windows" else None),
            first_seen=NOW - timedelta(days=age + RNG.randint(0, 30)),
            last_seen=NOW - timedelta(days=age),
        )
        db.add(h)
        hosts.append(h)
    db.flush()

    scan_for = lambda: RNG.choice(scans)
    for h in hosts:
        sc = scan_for()
        db.add(models.HostScanHistory(host_id=h.id, scan_id=sc.id, discovered_at=h.first_seen))
        # Ports
        for port, svc, _risky in RNG.sample(SERVICE_POOL, RNG.randint(2, 5)):
            db.add(models.Port(host_id=h.id, port_number=port, protocol="tcp",
                               state="open", service_name=svc, is_active=True))
        # Vulnerabilities (severity-weighted)
        for _ in range(RNG.randint(0, 6)):
            sev_enum, _sev_str, _w = RNG.choices(
                SEVERITIES, weights=[w for *_x, w in SEVERITIES])[0]
            db.add(Vulnerability(
                title=f"Synthetic {sev_enum.value} issue",
                severity=sev_enum, source=VulnerabilitySource.MANUAL,
                host_id=h.id, scan_id=sc.id,
                cve_id=f"CVE-2023-{RNG.randint(1000, 9999)}" if RNG.random() < 0.5 else None,
                exploitable=RNG.random() < 0.15,
            ))
    db.flush()

    # Findings (the spine) — exposure counts these, not raw vulns.
    finding_sevs = ["critical", "high", "medium", "low"]
    finding_weights = [0.12, 0.25, 0.4, 0.23]
    n_findings = max(8, host_count // 8)
    for _ in range(n_findings):
        sev = RNG.choices(finding_sevs, weights=finding_weights)[0]
        owned = RNG.random() < 0.45
        f = Finding(
            project_id=project.id,
            title=f"{sev.capitalize()} finding — {RNG.choice(['default creds', 'unpatched service', 'exposed admin panel', 'weak TLS', 'SMB signing disabled'])}",
            severity=sev,
            status=RNG.choice(["open", "open", "confirmed"]),  # all active
            source="scanner",
            owner_id=owner.id if owned else None,
        )
        db.add(f)
        db.flush()
        for h in RNG.sample(hosts, RNG.randint(1, 3)):
            db.add(FindingHost(finding_id=f.id, host_id=h.id))
    db.flush()

    # Review state for the owner + authored notes
    review_hosts = RNG.sample(hosts, max(5, host_count // 5))
    for h in review_hosts:
        roll = RNG.random()
        status = (FollowStatus.IN_REVIEW if roll < 0.5
                  else FollowStatus.REVIEWED if roll < 0.8 else FollowStatus.WATCHING)
        assigned = roll < 0.6
        db.add(models.HostFollow(
            host_id=h.id, user_id=owner.id, status=status,
            assigned_at=NOW - timedelta(days=RNG.randint(0, 10)) if assigned else None,
            assigned_by_id=owner.id if assigned else None,
            updated_at=NOW - timedelta(hours=RNG.randint(1, 200)),
        ))
    for h in RNG.sample(hosts, min(30, host_count)):
        note = models.Annotation(
            host_id=h.id, user_id=owner.id,
            body=f"Investigated {h.ip_address}: {RNG.choice(['confirmed exposed service', 'needs follow-up', 'looks like a false positive', 'escalating to finding'])}.",
            note_type=RNG.choice(["observation", "finding", "action", "question"]),
        )
        note.created_at = NOW - timedelta(hours=RNG.randint(1, 240))
        db.add(note)
    db.commit()

    mappings = SubnetCorrelationService(db).correlate_all_hosts_to_subnets(project_id=project.id)
    print(f"  correlated hosts → subnets: {mappings} mappings")
    return project


def main():
    ap = argparse.ArgumentParser(description="Seed a synthetic project for /insights evaluation.")
    ap.add_argument("--name", default="Demo — Insights Eval")
    ap.add_argument("--hosts", type=int, default=400)
    ap.add_argument("--wipe", action="store_true", help="Delete an existing project of the same name first.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        owner = (db.query(User).filter(User.role == UserRole.ADMIN).first()
                 or db.query(User).first())
        if owner is None:
            print("No users exist yet — log in once to create the admin, then re-run.")
            return 1
        if args.wipe:
            _wipe(db, args.name)
        elif db.query(Project).filter(Project.name == args.name).first():
            print(f"Project '{args.name}' already exists. Re-run with --wipe to replace it.")
            return 1
        print(f"Seeding '{args.name}' with {args.hosts} hosts (owner={owner.username})…")
        project = seed(db, args.name, args.hosts, owner)
        print(f"Done. Project id={project.id}. Open /insights and /operations against it.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
