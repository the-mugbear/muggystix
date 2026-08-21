"""drop the agent-session capability columns

The capability system was a second authorization model beside the product's own
RBAC, and only assist sessions ever consulted it — plan, execution and recon
keys resolved to LEGACY_WRITE_CAPABILITIES unconditionally, which is the tell
that the model was grandfathered rather than chosen.

An agent key now does what its operator may do, checked per request against the
same project roles a person is checked against (see
`enforce_agent_operator_access`, v2.305.0), so there is no per-session grant to
store.

No data migration and no drain: read-only assist was the *default* rather than
a deliberate choice, and every assist session in the deployment was already
ended, so no live key's contract changes underneath it. Auditors and viewers
still get read-only agents — because that is what they can do.

The downgrade recreates the columns empty. It cannot restore grants, which is
honest: the information they carried has no source to be reconstructed from,
and any session they described is long finished.

Revision ID: f1a6c92d4b70
Revises: d3f8b1e64a29
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a6c92d4b70"
down_revision = "d3f8b1e64a29"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
