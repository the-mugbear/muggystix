"""named assets: dns_names + scope_domains, dns_records promoted to observations

Revision ID: a7d3e5f91c26
Revises: d4e9f1c72a6b
Create Date: 2026-09-08

Why
---
Host identity is (project, IP).  A supplied FQDN list therefore had no home:
a name behind NAT / a load balancer resolves to a rotating or shared address,
and the only options were a placeholder host (a duplicate waiting to happen)
or dropping the name (which the amass, dnsx and httpx parsers did).

What
----
1. ``dns_names`` — the named asset, unique per (project, normalised fqdn).  A
   wildcard pattern keeps its ``*.`` prefix in ``fqdn`` so ``*.example.com``
   and ``example.com`` are two assets.
2. ``scope_domains`` — domain scope alongside subnet scope (exact vs
   include-subdomains, deliberately separate).
3. ``dns_records`` gains ``name_id`` (FK dns_names, CASCADE) and
   ``observed_at``; its role broadens from "DNS answer" to "one immutable
   observation about a name" (see the model docstring).
4. ``hosts_v2.hostname_source`` — provenance rank of the display name so the
   centralised precedence rule can decide replacements.

Observation identity — two PARTIAL unique indexes (review v2.323.0)
--------------------------------------------------------------------
* ``uq_dns_record_scan_observation`` on (name_id, record_type, value,
  COALESCE(resolver_name,''), scan_id) WHERE scan_id IS NOT NULL — one row
  per answer per scan; the same answer from two resolvers stays two rows.
* ``uq_dns_record_import_observation`` on (name_id, record_type, value)
  WHERE scan_id IS NULL AND record_type = 'IMPORT' — re-importing a name is
  a no-op.
* Rows orphaned by a scan delete (scan_id SET NULL) carry NO uniqueness, so
  deleting the second of two scans that held the same answer cannot collide
  on the first scan's orphan.  Unbound rows (name_id NULL — the domain wasn't
  a usable name) are likewise unconstrained; distinct unbound evidence is
  never collapsed.

Backfill order matters
----------------------
Names are created and ``name_id`` assigned FIRST, then duplicates are
collapsed on the NORMALISED identity the indexes enforce (``A.Example.com``
and ``a.example.com.`` are one name), then the indexes are created.  The
pre-review version deduped on the raw domain before normalising and would
have failed CREATE INDEX on legacy data that differed only in case or a
trailing dot.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Optional, Tuple

from alembic import op
import sqlalchemy as sa


revision = "a7d3e5f91c26"
down_revision = "d4e9f1c72a6b"
branch_labels = None
depends_on = None


_LABEL_RE = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")


def _normalize(raw: str) -> Optional[Tuple[str, str]]:
    """Self-contained copy of dns_name_service.normalize_fqdn's rules (the
    migration must not depend on app code that may later change)."""
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


# Shared with b2e6f8a1c3d7 (which reconciles a DB that ran the pre-review
# version of this migration).  Keep the two copies identical.
def dedupe_observations(bind) -> None:
    """Collapse duplicate observations on the identity the partial unique
    indexes enforce, keeping the lowest id.  Scan-bound rows: (name_id,
    record_type, value, COALESCE(resolver_name,''), scan_id).  Imports:
    (name_id, 'IMPORT', value) with no scan.  Unbound / orphaned rows are
    left alone."""
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
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


def create_observation_indexes(bind) -> None:
    """The two partial unique indexes; idempotent (IF NOT EXISTS)."""
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

    # 1. dns_names
    op.create_table(
        "dns_names",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fqdn", sa.String(length=253), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="fqdn"),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("project_id", "fqdn", name="uq_dns_name_project_fqdn"),
    )
    op.create_index("ix_dns_names_id", "dns_names", ["id"])
    op.create_index("ix_dns_names_project_id", "dns_names", ["project_id"])
    op.create_index("ix_dns_names_fqdn", "dns_names", ["fqdn"])
    op.create_index("idx_dns_name_project_kind", "dns_names", ["project_id", "kind"])

    # 2. scope_domains
    op.create_table(
        "scope_domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_id", sa.Integer(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("include_subdomains", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("scope_id", "domain", name="uq_scope_domain"),
    )
    op.create_index("ix_scope_domains_id", "scope_domains", ["id"])
    op.create_index("ix_scope_domains_scope_id", "scope_domains", ["scope_id"])
    op.create_index("ix_scope_domains_domain", "scope_domains", ["domain"])

    # 3. hosts_v2.hostname_source
    op.add_column("hosts_v2", sa.Column("hostname_source", sa.String(length=16), nullable=True))

    # 4. dns_records: new columns
    op.add_column(
        "dns_records",
        sa.Column("name_id", sa.Integer(), sa.ForeignKey("dns_names.id", ondelete="CASCADE"), nullable=True),
    )
    op.add_column("dns_records", sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_index("ix_dns_records_name_id", "dns_records", ["name_id"])
    op.create_index("idx_dns_record_project_value", "dns_records", ["project_id", "value"])
    bind.execute(sa.text("UPDATE dns_records SET observed_at = created_at WHERE created_at IS NOT NULL"))

    # 5. Backfill dns_names from distinct (project_id, normalised domain) and
    #    link every row to its name.
    rows = bind.execute(sa.text(
        "SELECT project_id, domain, MIN(created_at) AS first_seen, MAX(created_at) AS last_seen "
        "FROM dns_records WHERE project_id IS NOT NULL GROUP BY project_id, domain"
    )).fetchall()
    names: dict[tuple[int, str], dict] = {}
    for project_id, domain, first_seen, last_seen in rows:
        norm = _normalize(domain)
        if norm is None:
            continue
        fqdn, kind = norm
        entry = names.setdefault(
            (project_id, fqdn),
            {"project_id": project_id, "fqdn": fqdn, "kind": kind, "first_seen": first_seen,
             "last_seen": last_seen, "domains": []},
        )
        entry["domains"].append(domain)
        if first_seen is not None and (entry["first_seen"] is None or first_seen < entry["first_seen"]):
            entry["first_seen"] = first_seen
        if last_seen is not None and (entry["last_seen"] is None or last_seen > entry["last_seen"]):
            entry["last_seen"] = last_seen

    dns_names = sa.table(
        "dns_names",
        sa.column("id", sa.Integer), sa.column("project_id", sa.Integer), sa.column("fqdn", sa.String),
        sa.column("kind", sa.String), sa.column("first_seen", sa.DateTime(timezone=True)),
        sa.column("last_seen", sa.DateTime(timezone=True)),
    )
    for entry in names.values():
        ins = dns_names.insert().values(
            project_id=entry["project_id"], fqdn=entry["fqdn"], kind=entry["kind"],
            first_seen=entry["first_seen"], last_seen=entry["last_seen"],
        )
        if is_pg:
            name_id = bind.execute(ins.returning(dns_names.c.id)).scalar()
        else:
            name_id = bind.execute(ins).inserted_primary_key[0]
        for domain in entry["domains"]:
            bind.execute(
                sa.text(
                    "UPDATE dns_records SET name_id = :name_id "
                    "WHERE project_id = :project_id AND domain = :domain"
                ),
                {"name_id": name_id, "project_id": entry["project_id"], "domain": domain},
            )

    # 6. Collapse duplicates on the NORMALISED identity, then enforce it.
    dedupe_observations(bind)
    create_observation_indexes(bind)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_dns_record_import_observation")
    op.execute("DROP INDEX IF EXISTS uq_dns_record_scan_observation")
    op.drop_index("idx_dns_record_project_value", table_name="dns_records")
    op.drop_index("ix_dns_records_name_id", table_name="dns_records")
    op.drop_column("dns_records", "observed_at")
    op.drop_column("dns_records", "name_id")
    op.drop_column("hosts_v2", "hostname_source")
    op.drop_index("ix_scope_domains_domain", table_name="scope_domains")
    op.drop_index("ix_scope_domains_scope_id", table_name="scope_domains")
    op.drop_index("ix_scope_domains_id", table_name="scope_domains")
    op.drop_table("scope_domains")
    op.drop_index("idx_dns_name_project_kind", table_name="dns_names")
    op.drop_index("ix_dns_names_fqdn", table_name="dns_names")
    op.drop_index("ix_dns_names_project_id", table_name="dns_names")
    op.drop_index("ix_dns_names_id", table_name="dns_names")
    op.drop_table("dns_names")
