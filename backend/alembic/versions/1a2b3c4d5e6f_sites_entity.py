"""sites entity + subnets.site_id (+ backfill)

Graduates the scalar subnets.site string into a project-scoped Site entity
carrying the attention-model metadata (criticality tier 1-4, owner, expected
host count).  The string remains the human-entered NAME; site_id links it.
Backfills a Site per (project, distinct subnets.site) and sets site_id.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, None] = "090a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("criticality_tier", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expected_host_count", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "name", name="uq_site_project_name"),
    )
    op.create_index("ix_sites_project_id", "sites", ["project_id"])

    op.add_column("subnets", sa.Column("site_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "subnets_site_id_fkey", "subnets", "sites", ["site_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_subnets_site_id", "subnets", ["site_id"])

    # Backfill: one Site per (project, distinct non-empty subnets.site), then
    # link. Postgres-only (NOW()/ON CONFLICT); the SQLite test path builds the
    # schema via create_all and has no data to backfill.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        INSERT INTO sites (project_id, name, criticality_tier, created_at)
        SELECT DISTINCT sc.project_id, s.site, 3, NOW()
          FROM subnets s JOIN scopes sc ON s.scope_id = sc.id
         WHERE s.site IS NOT NULL AND s.site <> ''
        ON CONFLICT (project_id, name) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE subnets s SET site_id = si.id
          FROM scopes sc, sites si
         WHERE s.scope_id = sc.id
           AND si.project_id = sc.project_id
           AND si.name = s.site
           AND s.site IS NOT NULL AND s.site <> ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_subnets_site_id", table_name="subnets")
    op.drop_constraint("subnets_site_id_fkey", "subnets", type_="foreignkey")
    op.drop_column("subnets", "site_id")
    op.drop_index("ix_sites_project_id", table_name="sites")
    op.drop_table("sites")
