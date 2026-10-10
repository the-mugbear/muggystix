"""a project is archived by its status alone; five columns nothing needs are dropped

Owner, 2026-10-10 ("proceed with all remaining queue items"): the columns the
review found duplicated, unread or left over from removed features go in ONE
revision.

**Upgrade.**

1. ``projects.is_archived`` is dropped; ``projects.status = 'archived'`` is
   the one fact that a project is archived.  Every writer set the two
   together, so on a database the application made they agree.  Where they do
   NOT (a row from before creation set both, or edited by hand), the flag
   wins, because the flag is what the application ENFORCED (the 410 on an
   archived project's routes, the project list) while the status was only
   displayed:

   * flag set, status not ``archived``  -> status becomes ``archived``
     (the project stays closed, as it was);
   * flag not set, status ``archived``  -> status becomes ``completed``
     (the project stays open, as it was; ``completed`` is the nearest state
     that is not archived, and Oversight counts both as complete).

   To see such rows before upgrading:
   ``select id, name, status, is_archived from projects
   where coalesce(is_archived, false) <> (status = 'archived')``.
2. ``agent_sessions.legacy_assist_session_id`` and its unique index
   ``uq_agent_session_legacy_assist`` are dropped.  The column remembered the
   id of the ``assist_sessions`` row a session had before ``b8e2a5c7d1f3``,
   only so an old ``/assist-sessions/{id}`` link could find its session.
   Those routes and the page are gone; nothing wrote the column.
3. ``tool_registry.phases`` is dropped: the recon phase(s) a catalogue tool
   belonged to.  Recon runs went in v2.433.0; no page showed the value.
4. ``report_jobs.truncated`` is dropped: unread and unwritten since the capped
   host-report formats were retired.
5. ``users.is_verified`` is dropped: written at account creation, never read.

``agent_sessions.workflow`` is NOT touched: the column and every stored value
stay (no answer carries it any more).  ``host_follows.status`` is not touched
either: the retired ``watching`` default was the MODEL's, the column never had
a database default, and rows that hold ``WATCHING`` keep it.

**Downgrade.**  Every column comes back with the definition it had:

* ``projects.is_archived`` is filled from the status (``status =
  'archived'``).  A status this upgrade changed in step 1 is NOT changed back
  — which rows disagreed is not recorded.
* ``agent_sessions.legacy_assist_session_id`` (with its index),
  ``tool_registry.phases`` and ``users.is_verified`` come back EMPTY (NULL on
  every row): the old ids, the phase lists and the flags are not recoverable.
  An old ``/assist-sessions/{id}`` link then finds a session only by the
  session's own id.
* ``report_jobs.truncated`` comes back ``false`` on every row, which is what
  every row held.

Restore from a backup to go back with the dropped values.

Revision ID: c2a7e5f1d9b3
Revises: e1b4c7d0a2f5
"""
import sqlalchemy as sa
from alembic import op

revision = "c2a7e5f1d9b3"
down_revision = "e1b4c7d0a2f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. The flag is what was enforced: carry it into the status, then drop it.
    op.execute(
        "UPDATE projects SET status = 'archived' "
        "WHERE is_archived IS TRUE AND status <> 'archived'"
    )
    op.execute(
        "UPDATE projects SET status = 'completed' "
        "WHERE is_archived IS NOT TRUE AND status = 'archived'"
    )
    op.drop_column("projects", "is_archived")

    # 2. The last trace of the second session id.
    op.drop_index("uq_agent_session_legacy_assist", table_name="agent_sessions")
    op.drop_column("agent_sessions", "legacy_assist_session_id")

    # 3-5. Unread columns.
    op.drop_column("tool_registry", "phases")
    op.drop_column("report_jobs", "truncated")
    op.drop_column("users", "is_verified")


def downgrade() -> None:
    op.add_column("users", sa.Column("is_verified", sa.Boolean(), nullable=True))
    op.add_column(
        "report_jobs",
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("tool_registry", sa.Column("phases", sa.JSON(), nullable=True))

    op.add_column(
        "agent_sessions",
        sa.Column("legacy_assist_session_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "uq_agent_session_legacy_assist", "agent_sessions",
        ["legacy_assist_session_id"], unique=True,
    )

    op.add_column("projects", sa.Column("is_archived", sa.Boolean(), nullable=True))
    op.execute("UPDATE projects SET is_archived = (status = 'archived')")
