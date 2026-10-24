"""Presentation mode and profile pictures

Presentation mode, stage A (2.530.0): a shared, driven view of an issued
report (``models_presentations``), and a profile picture per user.

**Upgrade**: CREATES ``presentations`` (one live per report — a partial
unique index on ``report_id WHERE ended_at IS NULL``), ``presentation_viewers``
(presence and a Display's rendered position) and
``presentation_layout_reports``; ADDS ``users.profile_image_sha256`` and
``users.profile_image_type`` (NULL: no picture) with ``ck_users_profile_image``.
No existing row changes.

**Downgrade**: drops the three tables and the two columns (pictures already
stored stay on disk under ``uploads/profile_images/``, unreferenced).

Revision ID: d2d7730acb94
Revises: d4a8c2e6f1b3
"""
import sqlalchemy as sa
from alembic import op

revision = "d2d7730acb94"
down_revision = "d4a8c2e6f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("profile_image_sha256", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("profile_image_type", sa.String(20), nullable=True))
    op.create_check_constraint(
        "ck_users_profile_image", "users",
        "(profile_image_sha256 IS NULL) = (profile_image_type IS NULL)"
        " AND (profile_image_type IS NULL OR profile_image_type IN ('image/png', 'image/jpeg'))",
    )

    op.create_table(
        "presentations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("driver_client_id", sa.String(64), nullable=True),
        sa.Column("slide_key", sa.String(200), nullable=False),
        sa.Column("host_filter_kind", sa.String(10), nullable=True),
        sa.Column("host_filter_value", sa.String(200), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_move_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ended_reason", sa.String(10), nullable=True),
        sa.CheckConstraint(
            "(host_filter_kind IS NULL AND host_filter_value IS NULL)"
            " OR (host_filter_kind IN ('segment', 'site', 'text') AND host_filter_value IS NOT NULL)",
            name="ck_presentations_host_filter",
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND ended_reason IS NULL)"
            " OR (ended_at IS NOT NULL AND ended_reason IN ('ended', 'idle'))",
            name="ck_presentations_ended",
        ),
    )
    op.create_index(
        "uq_presentations_live_report", "presentations", ["report_id"], unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index("ix_presentations_project_id", "presentations", ["project_id"])

    op.create_table(
        "presentation_viewers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("presentation_id", sa.Integer(),
                  sa.ForeignKey("presentations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rendered_revision", sa.Integer(), nullable=True),
        sa.Column("rendered_slide_key", sa.String(200), nullable=True),
        sa.Column("rendered_host_filter_kind", sa.String(10), nullable=True),
        sa.Column("rendered_host_filter_value", sa.String(200), nullable=True),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("presentation_id", "client_id", name="uq_presentation_viewers_client"),
        sa.CheckConstraint("kind IN ('display', 'controller', 'browse')", name="ck_presentation_viewers_kind"),
    )
    op.create_index("ix_presentation_viewers_user_id", "presentation_viewers", ["user_id"])

    op.create_table(
        "presentation_layout_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slide_key", sa.String(200), nullable=False),
        sa.Column("overflow_px", sa.Integer(), nullable=False),
        sa.Column("font", sa.String(100), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_presentation_layout_reports_report_id", "presentation_layout_reports", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_presentation_layout_reports_report_id", table_name="presentation_layout_reports")
    op.drop_table("presentation_layout_reports")
    op.drop_index("ix_presentation_viewers_user_id", table_name="presentation_viewers")
    op.drop_table("presentation_viewers")
    op.drop_index("ix_presentations_project_id", table_name="presentations")
    op.drop_index("uq_presentations_live_report", table_name="presentations")
    op.drop_table("presentations")
    op.drop_constraint("ck_users_profile_image", "users", type_="check")
    op.drop_column("users", "profile_image_type")
    op.drop_column("users", "profile_image_sha256")
