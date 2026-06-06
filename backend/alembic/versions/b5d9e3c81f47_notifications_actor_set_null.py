"""notifications.actor_id -> ON DELETE SET NULL (v2.86.1)

Field-reported: deleting a user account 500s on a FK violation —
``notifications_actor_id_fkey`` had no ON DELETE behaviour, so any user
who had ever been the "actor" on a notification couldn't be deleted.
Recipient direction (``user_id``) already CASCADEs; the actor direction
should null out so the notification body (audit trail) survives.

Other ``users.id`` FKs across the schema have the same hazard
(``audit_logs.user_id``, ``projects.created_by_id``,
``scans.uploaded_by_id``, agent test-plan approval columns, etc.) and
will block deletion of different user-shaped data — those are covered
by the separate broader-audit task; this migration is the targeted
unblock for the immediate field report.

Revision ID: b5d9e3c81f47
Revises: a4b2f8e1c9d3
Create Date: 2026-06-03
"""
from alembic import op


revision = "b5d9e3c81f47"
down_revision = "a4b2f8e1c9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres can't ALTER an FK in place — drop and recreate with the
    # new behaviour.  The column itself stays untouched, only the
    # referential action changes.
    op.drop_constraint(
        "notifications_actor_id_fkey", "notifications", type_="foreignkey",
    )
    op.create_foreign_key(
        "notifications_actor_id_fkey",
        source_table="notifications",
        referent_table="users",
        local_cols=["actor_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "notifications_actor_id_fkey", "notifications", type_="foreignkey",
    )
    op.create_foreign_key(
        "notifications_actor_id_fkey",
        source_table="notifications",
        referent_table="users",
        local_cols=["actor_id"],
        remote_cols=["id"],
    )
