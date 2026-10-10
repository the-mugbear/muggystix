"""agent feedback carries no label for the kind of work it is about

``agent_feedback.source`` is dropped, with its index
``idx_agent_feedback_source`` (owner decision of 2026-10-10).

The column named the kind of session a feedback row came from — ``assist``,
``reconnaissance``, ``testing`` and the plan-era names.  Sessions have no kinds
any more, nothing read the label to decide anything, and it was the one field
that could get feedback refused (an unknown value was a 400).  **The stored
labels are discarded.**

The downgrade re-adds the column as the baseline created it — ``VARCHAR(40) NOT
NULL`` — and its index.  The labels cannot be recovered, so every existing row
reads ``assist``: the column is added with that server default to fill the
rows, then the default is dropped (the baseline column had none).

Revision ID: a6d3b1e8c5f7
Revises: f5c2a0d7b4e6
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "a6d3b1e8c5f7"
down_revision = "f5c2a0d7b4e6"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("idx_agent_feedback_source", table_name="agent_feedback")
    op.drop_column("agent_feedback", "source")


def downgrade():
    op.add_column(
        "agent_feedback",
        sa.Column("source", sa.String(length=40), nullable=False, server_default="assist"),
    )
    op.alter_column("agent_feedback", "source", server_default=None)
    op.create_index("idx_agent_feedback_source", "agent_feedback", ["source"], unique=False)
