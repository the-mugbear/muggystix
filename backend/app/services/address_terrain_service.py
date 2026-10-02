"""The engagement by address block — the address terrain (v2.426.0; Posture's
"Where the team has been" since v2.451.0, on Operations before).

Every host of the project, grouped into its /24 (IPv6: its /64), and within a
block counted by how far the team has taken it:

* ``tested``    — a planned test was executed against it (``has:tested``);
* ``planned``   — in a test plan, not yet tested (``has:planned``);
* ``worked``    — someone has it: a review or assignment, a note, or a
  finding — but no plan entry;
* ``untouched`` — none of those (the "Worth a look" definition of untouched).

The four stages are exclusive and add up to ``hosts``.  Beside them, the
exposure the team has not reached yet: ``critical`` hosts carry a critical
scanner observation (``has:critical``), ``critical_untouched`` are the ones
among them nobody has touched.  Counts only — no score, no age (a project is
one assessment window).

One statement: one row per host with four flags, grouped here in Python so the
address arithmetic stays exact for IPv4 and IPv6 alike.
"""
from __future__ import annotations

import ipaddress
from typing import Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import Annotation, HostFollow
from app.db.models_host_tests import HostTest, ACTIVE_TEST_STATUSES, TESTED_OUTCOMES
from app.db.models_proposals import EvidenceRecord
from app.db.models_findings import FindingHost
from app.db.models_project import Project
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity

#: Enough blocks for any engagement BlueStick has seen (production: ~300);
#: beyond it the largest blocks are kept and the response says so.
MAX_BLOCKS = 4096

STAGES = ("tested", "planned", "worked", "untouched")


class TerrainBlock(BaseModel):
    cidr: str
    hosts: int = 0
    tested: int = 0
    planned: int = 0
    worked: int = 0
    untouched: int = 0
    critical: int = 0
    critical_untouched: int = 0


class AddressTerrainResponse(BaseModel):
    blocks: List[TerrainBlock]
    total_hosts: int = 0
    #: Hosts whose address is not an IP (should not exist; counted, not drawn).
    unplaced_hosts: int = 0
    #: More blocks than MAX_BLOCKS: the smallest were left out.
    truncated: bool = False


def _block_of(ip: str) -> Optional[str]:
    """The /24 (IPv6: /64) holding ``ip``, or None when it is not an address.
    IPv4 by string arithmetic — ``ipaddress`` objects for every host were
    most of this endpoint's time at 70k hosts."""
    parts = ip.split(".")
    if len(parts) == 4:
        if all(p.isdigit() and len(p) <= 3 and int(p) <= 255 for p in parts):
            return f"{int(parts[0])}.{int(parts[1])}.{int(parts[2])}.0/24"
        return None
    try:
        addr = ipaddress.IPv6Address(ip.strip())
    except ValueError:
        return None
    return str(ipaddress.IPv6Network(f"{addr}/64", strict=False))


def _sort_key(cidr: str):
    if ":" not in cidr:
        a, b, c, _ = cidr.split("/")[0].split(".")
        return (4, (int(a) << 24) | (int(b) << 16) | (int(c) << 8))
    return (6, int(ipaddress.IPv6Network(cidr).network_address))


def compute_address_terrain(db: Session, project: Project) -> AddressTerrainResponse:
    host = models.Host
    in_project = db.query(host.id).filter(host.project_id == project.id)
    tested = (
        db.query(EvidenceRecord.host_id.label("hid"))
        .filter(
            EvidenceRecord.outcome.in_(TESTED_OUTCOMES),
            EvidenceRecord.host_id.in_(in_project),
        )
        .distinct().subquery("tested")
    )
    planned = (
        db.query(HostTest.host_id.label("hid"))
        .filter(HostTest.host_id.in_(in_project), HostTest.status.in_(ACTIVE_TEST_STATUSES))
        .distinct().subquery("planned")
    )
    worked = (
        db.query(HostFollow.host_id.label("hid")).filter(HostFollow.host_id.in_(in_project))
        .union(
            db.query(Annotation.host_id).filter(Annotation.host_id.in_(in_project)),
            db.query(FindingHost.host_id).filter(FindingHost.host_id.in_(in_project)),
            db.query(HostTest.host_id).filter(HostTest.host_id.in_(in_project), HostTest.status != "dismissed"),
            db.query(EvidenceRecord.host_id).filter(EvidenceRecord.host_id.in_(in_project)),
        )
        .subquery("worked")
    )
    critical = (
        db.query(Vulnerability.host_id.label("hid"))
        .filter(
            Vulnerability.severity == VulnerabilitySeverity.CRITICAL,
            Vulnerability.host_id.in_(in_project),
        )
        .distinct().subquery("critical")
    )
    flag = lambda sub: sub.c.hid.isnot(None)  # noqa: E731
    rows = (
        db.query(
            host.ip_address,
            flag(tested), flag(planned), flag(worked), flag(critical),
        )
        .outerjoin(tested, tested.c.hid == host.id)
        .outerjoin(planned, planned.c.hid == host.id)
        .outerjoin(worked, worked.c.hid == host.id)
        .outerjoin(critical, critical.c.hid == host.id)
        .filter(host.project_id == project.id)
        .execution_options(yield_per=5000)
    )

    # Per block: [hosts, tested, planned, worked, untouched, critical, critical_untouched].
    counts: Dict[str, List[int]] = {}
    block_of: Dict[str, Optional[str]] = {}  # per /24 prefix string, parsed once
    total = unplaced = 0
    for ip, is_tested, is_planned, is_worked, is_critical in rows:
        total += 1
        ip = ip or ""
        key = ip.rpartition(".")[0] if ip.count(".") == 3 else ip
        cidr = block_of.get(key, False) if key != ip else False
        if cidr is False:
            cidr = _block_of(ip)
            if key != ip and cidr is not None:
                block_of[key] = cidr
        if cidr is None:
            unplaced += 1
            continue
        c = counts.get(cidr)
        if c is None:
            c = counts[cidr] = [0, 0, 0, 0, 0, 0, 0]
        c[0] += 1
        stage = 1 if is_tested else 2 if is_planned else 3 if is_worked else 4
        c[stage] += 1
        if is_critical:
            c[5] += 1
            if stage == 4:
                c[6] += 1

    blocks = {
        cidr: TerrainBlock(cidr=cidr, hosts=c[0], tested=c[1], planned=c[2], worked=c[3],
                           untouched=c[4], critical=c[5], critical_untouched=c[6])
        for cidr, c in counts.items()
    }
    kept = list(blocks.values())
    truncated = len(kept) > MAX_BLOCKS
    if truncated:
        kept = sorted(kept, key=lambda b: -b.hosts)[:MAX_BLOCKS]
    kept.sort(key=lambda b: _sort_key(b.cidr))
    return AddressTerrainResponse(
        blocks=kept, total_hosts=total, unplaced_hosts=unplaced, truncated=truncated,
    )


__all__ = ["compute_address_terrain", "AddressTerrainResponse", "TerrainBlock", "STAGES", "MAX_BLOCKS"]
