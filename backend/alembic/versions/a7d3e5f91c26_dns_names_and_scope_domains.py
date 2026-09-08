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
1. ``dns_names`` — the named asset, unique per (project, normalised fqdn).
2. ``scope_domains`` — domain scope alongside subnet scope (exact vs
   include-subdomains, deliberately separate).
3. ``dns_records`` gains ``name_id`` (FK dns_names, CASCADE) and
   ``observed_at``; its role broadens from "DNS answer" to "one immutable
   observation about a name" (see the model docstring).  A unique index on
   (name_id, record_type, value, resolver_name, scan_id) NULLS NOT DISTINCT
   makes re-ingesting the same answer in the same scan a no-op.
4. ``hosts_v2.hostname_source`` — provenance rank of the display name so the
   centralised precedence rule can decide replacements.

Backfill
--------
* Duplicate dns_records rows (same project, domain, type, value, resolver,
  scan) are collapsed to the lowest id BEFORE the unique index is created —
  the CSV parser never deduped, so real deployments have them.
* ``observed_at`` := ``created_at`` for existing rows.
* A dns_names row is created for every distinct (project_id, domain) whose
  domain normalises to a valid name; dns_records.name_id is set accordingly.
  Rows whose domain is an IP or garbage keep name_id NULL (legacy, unbound).
  The normaliser here is a deliberately self-contained copy of the service's
  rules (lowercase, strip trailing dot, punycode) so this migration never
  depends on app code that may later change.
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
    if len(fqdn) > 253:
        return None
    return fqdn, kind


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

    # 5. Collapse pre-existing duplicate observations (lowest id survives).
    if is_pg:
        bind.execute(sa.text(
            "DELETE FROM dns_records d USING dns_records k "
            "WHERE d.id > k.id "
            "  AND d.project_id IS NOT DISTINCT FROM k.project_id "
            "  AND d.domain = k.domain AND d.record_type = k.record_type AND d.value = k.value "
            "  AND d.resolver_name IS NOT DISTINCT FROM k.resolver_name "
            "  AND d.scan_id IS NOT DISTINCT FROM k.scan_id"
        ))
    else:
        bind.execute(sa.text(
            "DELETE FROM dns_records WHERE id NOT IN ("
            "  SELECT MIN(id) FROM dns_records "
            "  GROUP BY project_id, domain, record_type, value, resolver_name, scan_id)"
        ))

    # 6. Backfill dns_names from distinct (project_id, domain) and link rows.
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
        result = bind.execute(
            dns_names.insert().values(
                project_id=entry["project_id"], fqdn=entry["fqdn"], kind=entry["kind"],
                first_seen=entry["first_seen"], last_seen=entry["last_seen"],
            ).returning(dns_names.c.id) if is_pg else
            dns_names.insert().values(
                project_id=entry["project_id"], fqdn=entry["fqdn"], kind=entry["kind"],
                first_seen=entry["first_seen"], last_seen=entry["last_seen"],
            )
        )
        name_id = result.scalar() if is_pg else result.inserted_primary_key[0]
        for domain in entry["domains"]:
            bind.execute(
                sa.text(
                    "UPDATE dns_records SET name_id = :name_id "
                    "WHERE project_id = :project_id AND domain = :domain"
                ),
                {"name_id": name_id, "project_id": entry["project_id"], "domain": domain},
            )

    # 7. The observation-identity index.  NULLS NOT DISTINCT so NULL scan_id
    #    (imports) and NULL resolver_name (CSV/amass) collide as intended.
    if is_pg:
        op.execute(
            "CREATE UNIQUE INDEX uq_dns_record_observation ON dns_records "
            "(name_id, record_type, value, resolver_name, scan_id) NULLS NOT DISTINCT"
        )
    else:
        op.create_index(
            "uq_dns_record_observation", "dns_records",
            ["name_id", "record_type", "value", "resolver_name", "scan_id"], unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_dns_record_observation", table_name="dns_records")
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
