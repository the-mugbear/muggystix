"""users.id FK ondelete sweep (v2.86.2)

Companion to ``b5d9e3c81f47`` (notifications.actor_id).  That migration
only patched the single column the field report named — every other
``users.id`` FK without an ON DELETE behaviour would still 500 the next
delete on different rows.  This revision sweeps the remaining 19
constraints across 14 tables and assigns each one of two policies:

  SET NULL  — audit / "by_id" columns whose row is shared content
              (notes, tags, scans, audit logs, plans).  Deleting the
              user nulls the link; the row survives as "by deleted
              user".

  CASCADE   — owner-shaped rows that exist BECAUSE of the user
              (sessions, personal API keys, host follows).  Deleting
              the user removes them.

One column is also flipped from ``NOT NULL`` to nullable:
``host_notes.user_id`` (so SET NULL is legal at the DB level — pre-fix
the column was NOT NULL which would forbid the action).

The User-side ORM cascade flags on ``audit_logs`` and ``host_notes``
were also stripped (in models_auth.py) — those were ``delete-orphan``
and would otherwise wipe the rows in Python before the DB-level FK
ever fired, which is the opposite of the policy this migration encodes.

Revision ID: c6e0fa492b15
Revises: b5d9e3c81f47
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa


revision = "c6e0fa492b15"
down_revision = "b5d9e3c81f47"
branch_labels = None
depends_on = None


# (constraint_name, table, column)
SET_NULL_FKS = [
    ("scans_uploaded_by_id_fkey",                   "scans",                    "uploaded_by_id"),
    ("scopes_uploaded_by_id_fkey",                  "scopes",                   "uploaded_by_id"),
    ("ingestion_jobs_submitted_by_id_fkey",         "ingestion_jobs",           "submitted_by_id"),
    ("host_follows_assigned_by_id_fkey",            "host_follows",             "assigned_by_id"),
    ("host_tags_created_by_id_fkey",                "host_tags",                "created_by_id"),
    ("host_tag_assignments_created_by_id_fkey",     "host_tag_assignments",     "created_by_id"),
    ("subnet_labels_created_by_id_fkey",            "subnet_labels",            "created_by_id"),
    ("subnet_label_assignments_created_by_id_fkey", "subnet_label_assignments", "created_by_id"),
    ("users_created_by_id_fkey",                    "users",                    "created_by_id"),
    ("audit_logs_user_id_fkey",                     "audit_logs",               "user_id"),
    ("security_policies_updated_by_id_fkey",        "security_policies",        "updated_by_id"),
    ("projects_created_by_id_fkey",                 "projects",                 "created_by_id"),
    ("webhook_configs_created_by_id_fkey",          "webhook_configs",          "created_by_id"),
    ("test_plans_approved_by_id_fkey",              "test_plans",               "approved_by_id"),
    ("test_plans_rejected_by_id_fkey",              "test_plans",               "rejected_by_id"),
    ("test_plan_entries_assigned_to_id_fkey",       "test_plan_entries",        "assigned_to_id"),
]

CASCADE_FKS = [
    ("host_follows_user_id_fkey",  "host_follows",  "user_id"),
    ("user_sessions_user_id_fkey", "user_sessions", "user_id"),
    ("api_keys_user_id_fkey",      "api_keys",      "user_id"),
]


def _swap(constraint_name, table, column, *, ondelete):
    """Drop and recreate an FK with the requested ondelete.

    Postgres doesn't allow ALTER CONSTRAINT to change referential
    actions in place — drop+create is the only way.  Column itself
    is untouched.
    """
    op.drop_constraint(constraint_name, table, type_="foreignkey")
    op.create_foreign_key(
        constraint_name,
        source_table=table,
        referent_table="users",
        local_cols=[column],
        remote_cols=["id"],
        ondelete=ondelete,
    )


def upgrade():
    # host_notes.user_id must become nullable before its FK can SET NULL.
    # Existing rows have non-null user_id (the author who wrote the note)
    # and stay populated — this just permits future SET NULL writes.
    op.alter_column(
        "host_notes",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    _swap("host_notes_user_id_fkey", "host_notes", "user_id", ondelete="SET NULL")

    for c, t, col in SET_NULL_FKS:
        _swap(c, t, col, ondelete="SET NULL")
    for c, t, col in CASCADE_FKS:
        _swap(c, t, col, ondelete="CASCADE")


def downgrade():
    for c, t, col in CASCADE_FKS:
        _swap(c, t, col, ondelete=None)
    for c, t, col in SET_NULL_FKS:
        _swap(c, t, col, ondelete=None)
    _swap("host_notes_user_id_fkey", "host_notes", "user_id", ondelete=None)
    op.alter_column(
        "host_notes",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
