"""named-endpoint references + observation-identity reconcile

Revision ID: b2e6f8a1c3d7
Revises: a7d3e5f91c26
Create Date: 2026-09-08

Phases two and three of the named-asset design (phase one: a7d3e5f91c26),
plus the reconcile for a database that ran the PRE-REVIEW version of
a7d3e5f91c26 (the dev DB did): that version created one NULLS NOT DISTINCT
index that broke scan deletion and stored wildcard names without their
``*.`` prefix.  Every reconcile step is idempotent, so a fresh database that
ran the corrected a7d3e5f91c26 passes through unchanged.

Reconcile
---------
* Drop ``uq_dns_record_observation`` if present; dedupe on the normalised
  identity; create the two partial unique indexes if absent.
* ``dns_names.kind = 'wildcard'`` rows whose fqdn lacks ``*.`` get the prefix
  (a concrete row with the same fqdn cannot be un-conflated here — its
  evidence was mixed at write time — so on collision the wildcard row is
  left as it stands and logged for the operator).

Phase two / three
-----------------
* ``web_interfaces.name_id`` — the URL's hostname as a DNSName; backfilled
  from ``url`` with an HTTP observation (name → ip_address, the interface's
  scan) so the names inventory shows the evidence.
* ``vulnerabilities.name_id`` (set by web scanners at ingest) and
  ``finding_hosts.name_id`` (inherited on promotion) — a finding belongs to
  the named endpoint; the host is where it was observed.  Not backfilled.
* ``test_plan_entries.name_id`` — the named target on that host (entry stays
  one-per-host).  ``test_execution_results.observed_ip`` — the address the
  command actually hit: execution EVIDENCE references the binding; the
  finding anchors to the name.

Every new FK is SET NULL: these rows are evidence and outlive the name.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from typing import Optional, Tuple
from urllib.parse import urlsplit

from alembic import op
import sqlalchemy as sa


revision = "b2e6f8a1c3d7"
down_revision = "a7d3e5f91c26"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")


def _normalize(raw: str) -> Optional[Tuple[str, str]]:
    s = (raw or "").strip().lower().rstrip(".")
    if not s:
        return None
    try:
        ipaddress.ip_address(s)
        return None
    except ValueError:
        pass
    kind = "fqdn"
    if s.startswith("*."):
        kind, s = "wildcard", s[2:]
    if "*" in s or not s:
        return None
    labels = []
    for label in s.split("."):
        if not label:
            return None
        if label.isascii():
            if not _LABEL_RE.match(label):
                return None
            labels.append(label)
        else:
            try:
                import idna
                labels.append(idna.encode(label, uts46=True).decode("ascii"))
            except Exception:  # noqa: BLE001
                return None
    fqdn = ".".join(labels)
    if kind == "wildcard":
        fqdn = "*." + fqdn
    if len(fqdn) > 253:
        return None
    return fqdn, kind


def _url_host(url: str) -> Optional[str]:
    try:
        h = urlsplit(url).hostname
    except ValueError:
        return None
    if not h:
        return None
    try:
        ipaddress.ip_address(h.strip("[]"))
        return None
    except ValueError:
        return h


# Identical to a7d3e5f91c26.dedupe_observations / create_observation_indexes.
def _dedupe_observations(bind) -> None:
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(
            "DELETE FROM dns_records d USING dns_records k "
            "WHERE d.id > k.id AND d.name_id IS NOT NULL AND d.scan_id IS NOT NULL "
            "  AND d.name_id = k.name_id AND d.record_type = k.record_type AND d.value = k.value "
            "  AND COALESCE(d.resolver_name,'') = COALESCE(k.resolver_name,'') "
            "  AND d.scan_id = k.scan_id"
        ))
        bind.execute(sa.text(
            "DELETE FROM dns_records d USING dns_records k "
            "WHERE d.id > k.id AND d.name_id IS NOT NULL AND d.scan_id IS NULL AND k.scan_id IS NULL "
            "  AND d.record_type = 'IMPORT' AND k.record_type = 'IMPORT' "
            "  AND d.name_id = k.name_id AND d.value = k.value"
        ))
    else:
        bind.execute(sa.text(
            "DELETE FROM dns_records WHERE name_id IS NOT NULL AND scan_id IS NOT NULL AND id NOT IN ("
            "  SELECT MIN(id) FROM dns_records WHERE name_id IS NOT NULL AND scan_id IS NOT NULL "
            "  GROUP BY name_id, record_type, value, COALESCE(resolver_name,''), scan_id)"
        ))
        bind.execute(sa.text(
            "DELETE FROM dns_records WHERE name_id IS NOT NULL AND scan_id IS NULL AND record_type = 'IMPORT' "
            "AND id NOT IN (SELECT MIN(id) FROM dns_records WHERE name_id IS NOT NULL AND scan_id IS NULL "
            "  AND record_type = 'IMPORT' GROUP BY name_id, record_type, value)"
        ))


def _create_observation_indexes(bind) -> None:
    bind.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dns_record_scan_observation ON dns_records "
        "(name_id, record_type, value, COALESCE(resolver_name,''), scan_id) WHERE scan_id IS NOT NULL"
    ))
    bind.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dns_record_import_observation ON dns_records "
        "(name_id, record_type, value) WHERE scan_id IS NULL AND record_type = 'IMPORT'"
    ))


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ---- Reconcile a pre-review phase-one schema ---------------------------
    bind.execute(sa.text("DROP INDEX IF EXISTS uq_dns_record_observation"))
    _dedupe_observations(bind)
    _create_observation_indexes(bind)
    wild = bind.execute(sa.text(
        "SELECT id, project_id, fqdn FROM dns_names WHERE kind = 'wildcard' AND fqdn NOT LIKE '*.%'"
    )).fetchall()
    for nid, pid, fqdn in wild:
        clash = bind.execute(
            sa.text("SELECT id FROM dns_names WHERE project_id = :pid AND fqdn = :f"),
            {"pid": pid, "f": "*." + fqdn},
        ).first()
        if clash:
            log.warning("dns_names id=%s: wildcard '%s' already exists as id=%s; left as is", nid, fqdn, clash[0])
            continue
        bind.execute(sa.text("UPDATE dns_names SET fqdn = :f WHERE id = :id"), {"f": "*." + fqdn, "id": nid})

    # ---- Phase two / three columns ----------------------------------------
    for table in ("web_interfaces", "vulnerabilities", "finding_hosts", "test_plan_entries"):
        op.add_column(
            table,
            sa.Column("name_id", sa.Integer(), sa.ForeignKey("dns_names.id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index(f"ix_{table}_name_id", table, ["name_id"])
    op.add_column("test_execution_results", sa.Column("observed_ip", sa.String(length=45), nullable=True))

    # ---- Backfill web_interfaces.name_id (+ HTTP observation) from the URL --
    rows = bind.execute(sa.text(
        "SELECT id, project_id, scan_id, url, ip_address, first_seen FROM web_interfaces "
        "WHERE project_id IS NOT NULL AND url IS NOT NULL"
    )).fetchall()
    names = sa.table(
        "dns_names",
        sa.column("id", sa.Integer), sa.column("project_id", sa.Integer), sa.column("fqdn", sa.String),
        sa.column("kind", sa.String), sa.column("first_seen", sa.DateTime(timezone=True)),
        sa.column("last_seen", sa.DateTime(timezone=True)),
    )
    name_ids: dict[tuple[int, str], int] = {
        (pid, fqdn): nid
        for nid, pid, fqdn in bind.execute(sa.text("SELECT id, project_id, fqdn FROM dns_names")).fetchall()
    }
    for wi_id, project_id, scan_id, url, ip, created_at in rows:
        host = _url_host(url)
        norm = _normalize(host) if host else None
        if norm is None:
            continue
        fqdn, kind = norm
        key = (project_id, fqdn)
        nid = name_ids.get(key)
        if nid is None:
            ins = names.insert().values(
                project_id=project_id, fqdn=fqdn, kind=kind, first_seen=created_at, last_seen=created_at,
            )
            nid = bind.execute(ins.returning(names.c.id)).scalar() if is_pg else bind.execute(ins).inserted_primary_key[0]
            name_ids[key] = nid
        bind.execute(sa.text("UPDATE web_interfaces SET name_id = :nid WHERE id = :wid"), {"nid": nid, "wid": wi_id})
        if ip and scan_id is not None:
            exists = bind.execute(sa.text(
                "SELECT 1 FROM dns_records WHERE name_id = :nid AND record_type = 'HTTP' "
                "AND value = :ip AND COALESCE(resolver_name,'') = '' AND scan_id = :sid"
            ), {"nid": nid, "ip": ip, "sid": scan_id}).first()
            if exists is None:
                bind.execute(sa.text(
                    "INSERT INTO dns_records (project_id, scan_id, name_id, domain, record_type, value, "
                    "observed_at, created_at) VALUES (:pid, :sid, :nid, :domain, 'HTTP', :ip, :ts, :ts)"
                ), {"pid": project_id, "sid": scan_id, "nid": nid, "domain": host, "ip": ip, "ts": created_at})


def downgrade() -> None:
    op.drop_column("test_execution_results", "observed_ip")
    for table in ("test_plan_entries", "finding_hosts", "vulnerabilities", "web_interfaces"):
        op.drop_index(f"ix_{table}_name_id", table_name=table)
        op.drop_column(table, "name_id")
    # The observation-identity indexes belong to a7d3e5f91c26's contract and
    # are left in place; the wildcard prefix fix is not reversible by design.
