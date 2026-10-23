"""every host, scan, import job, parse error and DNS record belongs to a project

Review 2026-10-10, R7.  ``project_id`` was nullable on five tables, a relic of
the schema before projects.  Nothing the application writes leaves it empty,
but the schema did not say so: ``startup.ensure_default_project`` ran in every
process at every boot and moved any row with no project into the LOWEST-id
project (another client's, on a shared installation) and wrote a global role
into a project membership; and ``uq_project_ip (project_id, ip_address)``
allowed any number of hosts with one address and no project.  The repair is
removed with this revision; the schema now says it.

**Upgrade.**  A row with no project in any of the five tables STOPS the
upgrade, naming the table and the row ids — nothing is moved, guessed or
deleted (nothing can say whose the row is).  Delete those rows, or set their
``project_id``, on the running version and upgrade again.  Otherwise
``project_id`` becomes NOT NULL on ``hosts_v2``, ``scans``,
``ingestion_jobs``, ``parse_errors`` and ``dns_records``.  No data changes.

To see such rows beforehand::

    select 'hosts_v2', id from hosts_v2 where project_id is null union all
    select 'scans', id from scans where project_id is null union all
    select 'ingestion_jobs', id from ingestion_jobs where project_id is null union all
    select 'parse_errors', id from parse_errors where project_id is null union all
    select 'dns_records', id from dns_records where project_id is null;

**Downgrade.**  The five columns are nullable again.  No data changes.

Revision ID: e3b7a1c9d5f0
Revises: e7b3d9f1a5c2
"""
import sqlalchemy as sa
from alembic import op

revision = "e3b7a1c9d5f0"
down_revision = "e7b3d9f1a5c2"
branch_labels = None
depends_on = None

TABLES = ("hosts_v2", "scans", "ingestion_jobs", "parse_errors", "dns_records")

#: How many ids of one table the refusal names before it says "…".
_NAMED = 20


def upgrade() -> None:
    conn = op.get_bind()
    found = []
    for table in TABLES:
        ids = [r[0] for r in conn.execute(sa.text(
            f"SELECT id FROM {table} WHERE project_id IS NULL ORDER BY id"
        ))]
        if ids:
            more = f" … ({len(ids)} rows)" if len(ids) > _NAMED else ""
            found.append(f"{table} ids {ids[:_NAMED]}{more}")
    if found:
        raise RuntimeError(
            "project_id NOT NULL (e3b7a1c9d5f0): rows that belong to no project: "
            + "; ".join(found)
            + ". Delete them, or set their project_id, on the running version and "
            "upgrade again. Nothing was changed."
        )
    for table in TABLES:
        op.alter_column(table, "project_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    for table in TABLES:
        op.alter_column(table, "project_id", existing_type=sa.Integer(), nullable=True)
