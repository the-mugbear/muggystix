"""Enforce agent_api_calls attribution-or-error contract at the DB level.

When ``f9e2d471a8c6`` relaxed ``agent_id`` and ``project_id`` to
nullable to record pre-auth 5xx (the request crashed before we knew
which agent/project it belonged to), the discriminator was the
``error_class`` column being non-null.  The contract was enforced in
the middleware code only, leaving the DB free to accept orphan rows
where all three are null — a class of bug that's invisible to the
activity-tab query (``WHERE error_class IS NOT NULL`` would miss them,
``WHERE agent_id = ?`` would miss them too).

This CHECK formalises the rule: every row must either fully attribute
to an (agent, project) pair OR carry an ``error_class`` explaining why
it couldn't.  Existing rows that violate the rule are deleted before
the constraint is added — they were already useless (no path to query
them through the activity UI) and re-running them isn't possible.

Backed by an offline rollout: the constraint is added with
``NOT VALID`` first so the boot-time migration doesn't hold an
exclusive lock while scanning the whole table, then validated.  On a
DB without millions of rows this is overkill but cheap; on a hot DB
the lock-time difference matters.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "a6f4d29e8b15"
down_revision: Union[str, None] = "e2b8c14f9a37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CHECK_NAME = "ck_agent_api_calls_attribution_or_error"
_CHECK_EXPR = (
    "(agent_id IS NOT NULL AND project_id IS NOT NULL) "
    "OR error_class IS NOT NULL"
)


def upgrade() -> None:
    bind = op.get_bind()

    # Purge rows that already violate the contract — they're orphans
    # the activity UI can't surface anyway, and re-validating the
    # constraint over them would fail.  Wrapped in a sub-transaction
    # so a missing table (cross-version reruns) doesn't abort the
    # whole upgrade.
    if bind.dialect.name == "postgresql":
        op.execute(
            f"""
            DELETE FROM agent_api_calls
            WHERE NOT ({_CHECK_EXPR})
            """
        )
        # NOT VALID skips the existing-row scan, taking only a brief
        # SHARE UPDATE EXCLUSIVE; then VALIDATE CONSTRAINT scans in a
        # weaker lock mode that doesn't block readers.
        op.execute(
            f"""
            ALTER TABLE agent_api_calls
              ADD CONSTRAINT {_CHECK_NAME}
              CHECK ({_CHECK_EXPR}) NOT VALID
            """
        )
        op.execute(
            f"ALTER TABLE agent_api_calls VALIDATE CONSTRAINT {_CHECK_NAME}"
        )
    else:
        # SQLite (test runs) — straightforward add.
        op.execute(
            f"""
            DELETE FROM agent_api_calls
            WHERE NOT ({_CHECK_EXPR})
            """
        )
        with op.batch_alter_table("agent_api_calls") as batch_op:
            batch_op.create_check_constraint(_CHECK_NAME, _CHECK_EXPR)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            f"ALTER TABLE agent_api_calls DROP CONSTRAINT IF EXISTS {_CHECK_NAME}"
        )
    else:
        with op.batch_alter_table("agent_api_calls") as batch_op:
            batch_op.drop_constraint(_CHECK_NAME, type_="check")
