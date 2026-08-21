"""drop the agent-session capability columns

The capability system was a second authorization model beside the product's own
RBAC, and only assist sessions ever consulted it — plan, execution and recon
keys resolved to LEGACY_WRITE_CAPABILITIES unconditionally, which is the tell
that the model was grandfathered rather than chosen.

An agent key now does what its operator may do, checked per request against the
same project roles a person is checked against (see
`enforce_agent_operator_access`, v2.305.0), so there is no per-session grant to
store.

No drain: read-only assist was the *default* rather than a deliberate choice,
and every assist session in the deployment was already ended, so no live key's
contract changes underneath it. Auditors and viewers still get read-only agents
— because that is what they can do.

But the columns are the *only* record of what a past session was permitted to
do, and dropping them destroys it. What an agent actually did survives in
`agent_api_calls`; what it was *allowed* to do does not, and "was this key
permitted to write at the time?" is a question an audit can legitimately ask
about a session that has already ended. So the grants are copied into
`audit_logs` first — the table that already exists to answer questions like
that — and only then dropped.

Only rows carrying a non-empty value are archived. Plan, execution and recon
sessions resolved to LEGACY_WRITE_CAPABILITIES regardless of what was stored,
so their column values never governed anything; `workflow` rides along in the
archived payload so a reader can tell an inert value from a governing one.

This archival step was added after the migration had already run on the dev
database (v2.311.0). Editing an applied revision is deliberate here rather than
adding a follow-up: a follow-up would run *after* the drop and could only
recreate the columns empty. Dev has no archive rows — its grants are already
gone — and any deployment still on `d3f8b1e64a29` keeps its history.

The downgrade recreates the columns empty. It does not replay the archive: the
audit rows are the record now, and writing them back into columns that no code
reads would only manufacture the illusion that the system still exists.

Revision ID: f1a6c92d4b70
Revises: d3f8b1e64a29
Create Date: 2026-08-20
"""
import json

from alembic import op
import sqlalchemy as sa


revision = "f1a6c92d4b70"
down_revision = "d3f8b1e64a29"
branch_labels = None
depends_on = None


ARCHIVE_ACTION = "agent_session_capability_archived"


def _archive_grants() -> int:
    """Copy every non-empty capability grant into the audit log.

    Returns the number of rows archived so the caller can log it — a silent 0
    and a silent 200 look identical in migration output, and the difference
    matters if someone later asks where the history went.
    """
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("agent_sessions")}
    if not {"capabilities", "capability_constraint"} <= columns:
        return 0

    rows = bind.execute(sa.text(
        """
        SELECT id, workflow, project_id, started_by_id, status,
               started_at, completed_at,
               CAST(capabilities AS TEXT) AS capabilities,
               capability_constraint
        FROM agent_sessions
        WHERE capability_constraint IS NOT NULL
           OR (capabilities IS NOT NULL
               AND CAST(capabilities AS TEXT) NOT IN ('[]', 'null', '""', ''))
        ORDER BY id
        """
    )).mappings().all()

    for row in rows:
        try:
            granted = json.loads(row["capabilities"]) if row["capabilities"] else []
        except (TypeError, ValueError):
            # Keep the raw text rather than dropping the row: an unparseable
            # value is still evidence, and this is the last chance to keep it.
            granted = row["capabilities"]

        details = {
            "note": (
                "Archived by migration f1a6c92d4b70 before the per-session "
                "capability columns were dropped. An agent key's authority is "
                "now its operator's project role, checked per request."
            ),
            "workflow": row["workflow"],
            "capabilities": granted,
            "capability_constraint": row["capability_constraint"],
            "governed_authorization": row["workflow"] == "assist",
            "project_id": row["project_id"],
            "session_status": row["status"],
            "session_started_at": (
                row["started_at"].isoformat() if row["started_at"] else None
            ),
            "session_completed_at": (
                row["completed_at"].isoformat() if row["completed_at"] else None
            ),
        }

        bind.execute(
            sa.text(
                """
                INSERT INTO audit_logs
                    (user_id, action, resource_type, resource_id,
                     details, success)
                VALUES
                    (:user_id, :action, 'agent_session', :resource_id,
                     CAST(:details AS JSON), TRUE)
                """
            ),
            {
                "user_id": row["started_by_id"],
                "action": ARCHIVE_ACTION,
                "resource_id": str(row["id"]),
                "details": json.dumps(details),
            },
        )

    return len(rows)


def upgrade() -> None:
    archived = _archive_grants()
    print(
        f"[f1a6c92d4b70] archived {archived} agent-session capability "
        f"grant(s) to audit_logs (action={ARCHIVE_ACTION!r}) before drop"
    )
    op.drop_column("agent_sessions", "capability_constraint")
    op.drop_column("agent_sessions", "capabilities")


def downgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column(
            "capabilities",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("capability_constraint", sa.String(length=20), nullable=True),
    )
