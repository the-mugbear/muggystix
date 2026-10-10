"""a project has exactly one scope, and it has no name

Owner decision of 2026-10-10 ("A project is meant to have only one scope";
a second "shouldn't be possible"; "Drop the scope name").

The application has only ever been able to make ONE scope per project since
v2.9.4 (``default_scope.get_or_create_default_scope`` is the one place a scope
is constructed, behind "return the existing one").  Nothing in the schema said
so: a seed, or data older than that, could hold a second, whose subnets and
domains were then on no page.  This revision makes the schema say it.

**Upgrade.**

1. A scope with no project stops the upgrade (nothing can say whose it is).
2. For a project with more than one scope the LOWEST id is kept — the one
   every page and write already used — and the others' rows are MOVED into
   it: ``subnets``, ``scope_domains``, and the ``scope_id`` of
   ``annotations``.  Nothing is merged by guess: when a CIDR or a
   domain is held by two scopes of one project the upgrade STOPS and names
   them (remove one of the two on the running version, then upgrade).  The
   emptied scopes are deleted.
3. ``scopes.project_id`` becomes NOT NULL and UNIQUE (``uq_scopes_project``).
4. ``scopes.name`` is dropped.  It held ``__default__`` or a seed's label; no
   person could set it.
5. ``agent_api_calls.scope_id`` is dropped.  It recorded which scope an
   agent's ``/agent/scopes/{scope_id}/…`` call named; no agent route names a
   scope any more and nothing ever read the column (a call's project and
   path say the same).

A project with one scope — every project the application itself made — loses
only the unused name and that unread column.

**Downgrade.**  The constraint and the NOT NULL go, ``name`` comes back as
``__default__`` on every row, and ``agent_api_calls.scope_id`` comes back
empty.  A scope that was moved into another is NOT
split again, and an earlier name is not restored: which scope a row came from
is not recorded, and recording it only to undo this would be keeping the model
the product dropped.  Restore from a backup to go back with the data.

Revision ID: e1b4c7d0a2f5
Revises: d9a3e6b0f4c2
"""
import sqlalchemy as sa
from alembic import op

revision = "e1b4c7d0a2f5"
down_revision = "d9a3e6b0f4c2"
branch_labels = None
depends_on = None


def _refuse(message: str) -> None:
    raise RuntimeError(f"one scope per project (e1b4c7d0a2f5): {message}")


def upgrade() -> None:
    conn = op.get_bind()

    orphans = [r[0] for r in conn.execute(sa.text("SELECT id FROM scopes WHERE project_id IS NULL ORDER BY id"))]
    if orphans:
        _refuse(
            f"scope(s) {orphans} belong to no project. Delete them, or set their project_id, "
            "on the running version and upgrade again. Nothing was changed."
        )

    # A CIDR or a domain in two scopes of one project: not merged by guess.
    clashes = conn.execute(sa.text("""
        SELECT s.project_id, 'subnet ' || sn.cidr AS what
          FROM subnets sn JOIN scopes s ON s.id = sn.scope_id
         GROUP BY s.project_id, sn.cidr HAVING count(DISTINCT sn.scope_id) > 1
        UNION ALL
        SELECT s.project_id, 'domain ' || d.domain
          FROM scope_domains d JOIN scopes s ON s.id = d.scope_id
         GROUP BY s.project_id, d.domain HAVING count(DISTINCT d.scope_id) > 1
         ORDER BY 1, 2
    """)).fetchall()
    if clashes:
        listed = "; ".join(f"project {pid}: {what}" for pid, what in clashes[:20])
        _refuse(
            f"{len(clashes)} entr{'y is' if len(clashes) == 1 else 'ies are'} held by two scopes of one "
            f"project ({listed}{' …' if len(clashes) > 20 else ''}). Remove one of each pair on the "
            "running version and upgrade again. Nothing was changed."
        )

    # (scope to remove, scope kept) for every scope that is not its project's first.
    conn.execute(sa.text("""
        CREATE TEMPORARY TABLE _scope_merge ON COMMIT DROP AS
        SELECT s.id AS gone, k.kept
          FROM scopes s
          JOIN (SELECT project_id, min(id) AS kept FROM scopes GROUP BY project_id) k
            ON k.project_id = s.project_id
         WHERE s.id <> k.kept
    """))
    for table in ("subnets", "scope_domains", "annotations"):
        conn.execute(sa.text(
            f"UPDATE {table} t SET scope_id = m.kept FROM _scope_merge m WHERE t.scope_id = m.gone"
        ))
    # Dropped before the scopes go, so the delete has no call rows to null.
    op.drop_column("agent_api_calls", "scope_id")
    conn.execute(sa.text("DELETE FROM scopes s USING _scope_merge m WHERE s.id = m.gone"))

    op.alter_column("scopes", "project_id", existing_type=sa.Integer(), nullable=False)
    op.create_unique_constraint("uq_scopes_project", "scopes", ["project_id"])
    op.drop_column("scopes", "name")


def downgrade() -> None:
    op.add_column("scopes", sa.Column("name", sa.String(), nullable=True))
    op.execute("UPDATE scopes SET name = '__default__'")
    op.alter_column("scopes", "name", existing_type=sa.String(), nullable=False)
    op.drop_constraint("uq_scopes_project", "scopes", type_="unique")
    op.alter_column("scopes", "project_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "agent_api_calls",
        sa.Column("scope_id", sa.Integer(), sa.ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True),
    )
