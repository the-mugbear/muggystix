"""global role collapses to admin/member (v2.46.0)

The global ``users.role`` previously carried the four-tier
analyst/auditor/viewer vocabulary, but no endpoint ever gated a
non-admin global tier — every granular check goes through
``require_project_role`` against ``project_memberships.role``.  The
extra global tiers were dead weight, so the global role collapses to
``admin`` / ``member`` and the four-tier vocabulary is now owned
solely by ``project_memberships.role`` (``ProjectRole``).

This migration only remaps DATA — both ``users.role`` and
``project_memberships.role`` are plain ``VARCHAR(20)`` columns (no
Postgres ENUM type), so there is no type to alter.

  * ``users.role``: ``analyst`` / ``auditor`` / ``viewer`` -> ``member``;
    ``admin`` is left unchanged.
  * ``project_memberships.role`` is untouched — it keeps the four
    tiers.

Downgrade is necessarily lossy: the original analyst/auditor/viewer
distinction on the global role is not recoverable (it carried no
behaviour, which is the whole point), so downgrade maps ``member``
back to ``viewer`` — the pre-2.46.0 default.

Revision ID: e2b8c14f9a37
Revises: c4d70a8b6e2f
Create Date: 2026-05-21
"""
from alembic import op


revision = "e2b8c14f9a37"
down_revision = "c4d70a8b6e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Collapse every non-admin global role to "member".
    op.execute(
        "UPDATE users SET role = 'member' "
        "WHERE role IS NULL OR role NOT IN ('admin', 'member')"
    )


def downgrade() -> None:
    # Lossy: "member" was previously most often "viewer" (the old
    # default); restore to that.  The analyst/auditor distinction is
    # gone — it never carried behaviour.
    op.execute(
        "UPDATE users SET role = 'viewer' WHERE role = 'member'"
    )
