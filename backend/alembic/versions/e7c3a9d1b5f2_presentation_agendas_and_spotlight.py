"""Presentation agendas and the spotlight

Presentation mode, stages B and C (2.531.0): agendas — an ordered selection of
a report's findings and sections, saved as immutable versions — and the
spotlight (``models_presentations``).

**Upgrade**: CREATES ``presentation_agendas`` (one per name the team saves for a
report) and ``presentation_agenda_versions`` (its items, one row per save;
unique per agenda and version); ADDS to ``presentations`` the agenda state
(``agenda_id``, ``agenda_version``, ``agenda_mode``, ``agenda_item_index`` —
all set or all NULL, ``ck_presentations_agenda``) and the spotlight
(``spotlight_seq`` default 0, ``spotlight_revision``, ``spotlight_x``,
``spotlight_y``, ``spotlight_at``). No existing row changes; nothing refused.

**Downgrade**: drops the columns and the two tables (saved agendas are lost).

Revision ID: e7c3a9d1b5f2
Revises: d2d7730acb94
"""
import sqlalchemy as sa
from alembic import op

revision = "e7c3a9d1b5f2"
down_revision = "d2d7730acb94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presentation_agendas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_presentation_agendas_report_id", "presentation_agendas", ["report_id"])
    op.create_index("ix_presentation_agendas_project_id", "presentation_agendas", ["project_id"])

    op.create_table(
        "presentation_agenda_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agenda_id", sa.Integer(),
                  sa.ForeignKey("presentation_agendas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agenda_id", "version", name="uq_presentation_agenda_versions_version"),
    )

    op.add_column("presentations", sa.Column(
        "agenda_id", sa.Integer(), sa.ForeignKey("presentation_agendas.id", name="fk_presentations_agenda_id"),
        nullable=True))
    op.add_column("presentations", sa.Column("agenda_version", sa.Integer(), nullable=True))
    op.add_column("presentations", sa.Column("agenda_mode", sa.String(10), nullable=True))
    op.add_column("presentations", sa.Column("agenda_item_index", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_presentations_agenda", "presentations",
        "(agenda_id IS NULL AND agenda_version IS NULL AND agenda_mode IS NULL AND agenda_item_index IS NULL)"
        " OR (agenda_id IS NOT NULL AND agenda_version IS NOT NULL AND agenda_mode IN ('agenda', 'explore')"
        " AND agenda_item_index >= 0)",
    )
    op.create_index("ix_presentations_agenda_id", "presentations", ["agenda_id"])

    op.add_column("presentations", sa.Column(
        "spotlight_seq", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("presentations", sa.Column("spotlight_revision", sa.Integer(), nullable=True))
    op.add_column("presentations", sa.Column("spotlight_x", sa.Integer(), nullable=True))
    op.add_column("presentations", sa.Column("spotlight_y", sa.Integer(), nullable=True))
    op.add_column("presentations", sa.Column("spotlight_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ("spotlight_at", "spotlight_y", "spotlight_x", "spotlight_revision", "spotlight_seq"):
        op.drop_column("presentations", column)
    op.drop_index("ix_presentations_agenda_id", table_name="presentations")
    op.drop_constraint("ck_presentations_agenda", "presentations", type_="check")
    op.drop_constraint("fk_presentations_agenda_id", "presentations", type_="foreignkey")
    for column in ("agenda_item_index", "agenda_mode", "agenda_version", "agenda_id"):
        op.drop_column("presentations", column)
    op.drop_table("presentation_agenda_versions")
    op.drop_index("ix_presentation_agendas_project_id", table_name="presentation_agendas")
    op.drop_index("ix_presentation_agendas_report_id", table_name="presentation_agendas")
    op.drop_table("presentation_agendas")
