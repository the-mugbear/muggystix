"""data_repairs: a ledger of one-off data corrections (review 2026-10-01 B10)

Revision ID: d4a7c1e3f6b8
Revises: c3f6b9d2e5a7
Create Date: 2026-10-01

A data repair (``scripts/backfill_misconfigs.py``,
``scripts/repair_netexec_results.py``) corrects rows written by older code.
Nothing recorded that one was owed or had been run; this table does
(``app/db/models_data_repairs.py``).

A database with NO hosts at this revision has nothing either repair could
touch, and everything imported from here on is written by the corrected
parsers — so on such a database both are recorded as ``not_needed`` and a new
installation is never told to run them.  A database with hosts gets an empty
ledger: the operator is reminded (``deploy.sh``, ``status.sh``) until each
repair has been run once.

The names seeded here are frozen copies of
``app.services.data_repair_service.KNOWN_REPAIRS`` as of this revision — a
migration never imports application code.
"""
import sqlalchemy as sa
from alembic import op

revision = "d4a7c1e3f6b8"
down_revision = "c3f6b9d2e5a7"
branch_labels = None
depends_on = None

_REPAIRS_AT_THIS_REVISION = ("misconfig_backfill", "netexec_results_repair")


def upgrade() -> None:
    op.create_table(
        "data_repairs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("applied_by", sa.String(length=100), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("rows_affected", sa.JSON(), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("run_count", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_data_repairs_name"),
    )

    bind = op.get_bind()
    has_hosts = bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM hosts_v2)")).scalar()
    if not has_hosts:
        for name in _REPAIRS_AT_THIS_REVISION:
            bind.execute(
                sa.text(
                    "INSERT INTO data_repairs (name, applied_by, mode) "
                    "VALUES (:name, 'migration', 'not_needed')"
                ),
                {"name": name},
            )


def downgrade() -> None:
    op.drop_table("data_repairs")
