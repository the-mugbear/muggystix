"""ingestion_jobs: the format chain (v2.351.0, staged-import phase A)

Four typed columns recording how a file was read: ``detected_file_type`` (the
dispatcher's first candidate for the file), ``format_override`` (what the
operator told it to use instead, when they did), ``final_file_type`` (the
parser that actually produced the scan), and ``source_tool`` (the tool the
operator named, when a format does not reveal it — a hostname list can come
from Amass or Subfinder).

Typed columns rather than keys in ``options`` because Ingestion Results
filters on them ("everything parsed as X") and the retry / re-process flows of
later phases read them back (CLAUDE.md column-vs-blob policy).  All nullable;
rows from before this migration stay NULL rather than being guessed.

Revision ID: c3f8a1d2b7e4
Revises: a7b3c9d2e4f1
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa


revision = "c3f8a1d2b7e4"
down_revision = "a7b3c9d2e4f1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ingestion_jobs", sa.Column("detected_file_type", sa.String(length=64), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("format_override", sa.String(length=64), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("final_file_type", sa.String(length=64), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("source_tool", sa.String(length=64), nullable=True))
    op.create_index("idx_ingestion_jobs_final_file_type", "ingestion_jobs", ["final_file_type"])


def downgrade():
    op.drop_index("idx_ingestion_jobs_final_file_type", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "source_tool")
    op.drop_column("ingestion_jobs", "final_file_type")
    op.drop_column("ingestion_jobs", "format_override")
    op.drop_column("ingestion_jobs", "detected_file_type")
