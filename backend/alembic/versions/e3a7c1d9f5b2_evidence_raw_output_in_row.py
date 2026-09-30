"""Evidence raw output lives in the row, not in a file (v2.439.0)

``evidence_records.raw_output_path`` pointed at ``uploads/evidence/<project>/``.
Nothing removed those files when a host or project was deleted (the rows
cascade, the files stayed: raw client tool output outliving its project),
and a failed commit after the write left an orphan.  At ≤5 MB per record the
output belongs in a ``Text`` column, which the cascade deletes with the row
(Postgres keeps large values out of line; the model defers the column so a
list never loads it).

Upgrade reads each file into ``raw_output`` (a missing or unreadable file
leaves it NULL and the size with it), then drops ``raw_output_path``.  The
old files are left on disk, never deleted by a migration; once upgraded,
``uploads/evidence/`` can be removed by hand.  Downgrade writes each row's
output back to a file and restores the path.

Revision ID: e3a7c1d9f5b2
Revises: b6d4f1e8c203
Create Date: 2026-09-30
"""
import os
import uuid

import sqlalchemy as sa
from alembic import op


revision = "e3a7c1d9f5b2"
down_revision = "b6d4f1e8c203"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("evidence_records", sa.Column("raw_output", sa.Text(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, raw_output_path FROM evidence_records WHERE raw_output_path IS NOT NULL"
    )).fetchall()
    for rid, path in rows:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            conn.execute(sa.text(
                "UPDATE evidence_records SET raw_output_bytes = NULL, raw_output_preview = NULL WHERE id = :id"
            ), {"id": rid})
            continue
        # PostgreSQL text cannot hold NUL (the ingestion rule, v2.420.0).
        text = data.decode("utf-8", errors="replace").replace("\x00", "")
        conn.execute(sa.text(
            "UPDATE evidence_records SET raw_output = :raw WHERE id = :id"
        ), {"raw": text, "id": rid})
    op.drop_column("evidence_records", "raw_output_path")


def downgrade():
    op.add_column("evidence_records", sa.Column("raw_output_path", sa.String(500), nullable=True))
    conn = op.get_bind()
    upload_dir = os.getenv("UPLOAD_DIR", os.path.join(os.getcwd(), "uploads"))
    rows = conn.execute(sa.text(
        "SELECT id, project_id, raw_output FROM evidence_records WHERE raw_output IS NOT NULL"
    )).fetchall()
    for rid, project_id, raw in rows:
        directory = os.path.join(upload_dir, "evidence", str(project_id))
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{uuid.uuid4().hex}.txt")
        with open(path, "wb") as fh:
            fh.write(raw.encode("utf-8"))
        conn.execute(sa.text(
            "UPDATE evidence_records SET raw_output_path = :p WHERE id = :id"
        ), {"p": path, "id": rid})
    op.drop_column("evidence_records", "raw_output")
