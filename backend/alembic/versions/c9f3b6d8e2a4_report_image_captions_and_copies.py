"""evidence image captions, and an issued report's own copies of its images

Two things for placing images in a finding's report text:

* ``note_attachments.caption`` — what the image shows, written by the person
  who attached it.  It is the figure caption wherever the client report
  prints the image; NULL falls back to the file name, which is what every
  report printed before this.
* ``report_images`` — one row per evidence image an ISSUED report holds a
  copy of (the bytes are under ``uploads/client_reports/<project>/<report>/
  evidence/``).  Issuing writes them; the issue render reads them, so an
  image deleted after the issue no longer fails the render.  ``attachment_id``
  is the id the frozen dataset names and is deliberately not a foreign key:
  it must outlive the attachment.

Nothing is backfilled.  A report issued before this revision has no rows and
keeps rendering from the live attachments, as it always did (its files exist
already unless the render failed).

The downgrade drops the column and the table.  The captions are lost, and so
are the rows; the copied files stay on disk under ``client_reports/`` and are
simply no longer read.

Revision ID: c9f3b6d8e2a4
Revises: b8e2a5c7d1f3
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op

revision = "c9f3b6d8e2a4"
down_revision = "b8e2a5c7d1f3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("note_attachments", sa.Column("caption", sa.Text(), nullable=True))
    op.create_table(
        "report_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("report_id", "attachment_id", name="uq_report_image_attachment"),
    )
    op.create_index("ix_report_images_id", "report_images", ["id"])
    op.create_index("ix_report_images_report_id", "report_images", ["report_id"])


def downgrade():
    op.drop_index("ix_report_images_report_id", table_name="report_images")
    op.drop_index("ix_report_images_id", table_name="report_images")
    op.drop_table("report_images")
    op.drop_column("note_attachments", "caption")
