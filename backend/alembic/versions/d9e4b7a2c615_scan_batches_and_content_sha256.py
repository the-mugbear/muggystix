"""scan_batches; batch_id + content_sha256 on scans and ingestion_jobs

Two problems with large agent sweeps (v2.335.0):

1. Hundreds of chunk files (nmap_0001 … nmap_0312) flattened /scans into an
   unnavigable list. ``scan_batches`` groups the files of one sweep: agents
   name it with a ``batch`` label per recon upload (unique per recon session),
   the Scans page creates one per multi-file upload. Each file stays its own
   scan; ``batch_id`` is copied from the job when it completes.

2. Re-uploading an identical file created a second scan every time — every
   scan counter moved while no data was added. ``content_sha256`` (computed
   while the upload is written) lets the upload refuse a file that is already
   a scan, or still queued/processing, in the project.

No backfill: uploaded files are deleted after a successful parse, so there is
nothing to hash for existing scans. Duplicate detection covers uploads from
this revision on.

Revision ID: d9e4b7a2c615
Revises: c3e9a7d15b62
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d9e4b7a2c615"
down_revision: Union[str, None] = "c3e9a7d15b62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "recon_session_id", sa.Integer(),
            sa.ForeignKey("recon_sessions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.create_index("ix_scan_batches_id", "scan_batches", ["id"])
    op.create_index("ix_scan_batches_project_id", "scan_batches", ["project_id"])
    op.create_index(
        "uq_scan_batches_session_label", "scan_batches", ["recon_session_id", "label"],
        unique=True, postgresql_where=sa.text("recon_session_id IS NOT NULL"),
    )

    for table in ("scans", "ingestion_jobs"):
        op.add_column(
            table,
            sa.Column(
                "batch_id", sa.Integer(),
                sa.ForeignKey("scan_batches.id", ondelete="SET NULL"), nullable=True,
            ),
        )
        op.add_column(table, sa.Column("content_sha256", sa.String(64), nullable=True))
        op.create_index(f"ix_{table}_batch_id", table, ["batch_id"])
        op.create_index(f"ix_{table}_content_sha256", table, ["content_sha256"])


def downgrade() -> None:
    for table in ("ingestion_jobs", "scans"):
        op.drop_index(f"ix_{table}_content_sha256", table_name=table)
        op.drop_index(f"ix_{table}_batch_id", table_name=table)
        op.drop_column(table, "content_sha256")
        op.drop_column(table, "batch_id")
    op.drop_index("uq_scan_batches_session_label", table_name="scan_batches")
    op.drop_index("ix_scan_batches_project_id", table_name="scan_batches")
    op.drop_index("ix_scan_batches_id", table_name="scan_batches")
    op.drop_table("scan_batches")
