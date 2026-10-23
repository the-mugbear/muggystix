"""the database states the role and status vocabularies

Review 2026-10-23 (auth nits).  Three columns that decide who may do what, or
whether a project is in selection, were free text: ``users.role`` (the
global role, binary since v2.46.0 — ``admin`` / ``member``),
``project_memberships.role`` (``admin`` / ``analyst`` / ``auditor`` /
``viewer``) and ``projects.status`` (``active`` / ``completed`` /
``archived``; ``in_progress`` was merged into ``active`` by
``d9f1b3c5e7a2``).  ``core.security.check_permissions`` reads an unknown role
as level 0, so a stray value silently meant "no rights" on one axis and could
not be told from a typo on the other.  Each now carries a CHECK.

**Upgrade.**  A row holding any other value STOPS the upgrade, naming the
table and the count — nothing is changed (correct the rows on the running
version, then upgrade).  To see them beforehand, the line for this revision in
``scripts/upgrade-refusals.sql``.  Otherwise the three constraints are added.

**Downgrade.**  Drops the three constraints; no data changes.

Revision ID: 47e5e79b90a9
Revises: c8e2a4f6b1d9
"""
import sqlalchemy as sa
from alembic import op

revision = "47e5e79b90a9"
down_revision = "c8e2a4f6b1d9"
branch_labels = None
depends_on = None

#: (constraint, table, column, accepted values) — the models declare the same.
CHECKS = (
    ("ck_users_role", "users", "role", ("admin", "member")),
    ("ck_project_memberships_role", "project_memberships", "role", ("admin", "analyst", "auditor", "viewer")),
    ("ck_projects_status", "projects", "status", ("active", "completed", "archived")),
)


def _condition(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    conn = op.get_bind()
    refused = []
    for _name, table, column, values in CHECKS:
        count = conn.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE NOT ({_condition(column, values)})"
        )).scalar()
        if count:
            refused.append(f"{table}.{column}: {count} row(s) outside {list(values)}")
    if refused:
        raise RuntimeError(
            "Role and status vocabularies (47e5e79b90a9): " + "; ".join(refused)
            + ". Correct those rows on the running version and upgrade again. Nothing was changed."
        )
    for name, table, column, values in CHECKS:
        op.create_check_constraint(name, table, _condition(column, values))


def downgrade() -> None:
    for name, table, _column, _values in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
