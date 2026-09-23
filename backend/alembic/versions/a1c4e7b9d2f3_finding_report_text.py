"""findings report text + note_attachments.include_in_report (v2.379.0)

The client report (Quarto, findings-first) needs what a finding says to the
client: description, impact, recommendation, references, steps to reproduce
and CVSS.  Typed columns, authored content.  New findings are seeded at
promotion (finding_service.seed_report_text_from_vuln / the source note); this
migration seeds the EXISTING ones the same way so a project's first report is
not empty — scanner findings from their originating vulnerability row, note
findings from their source note.  Only NULL fields are written.

``note_attachments.include_in_report``: images are opt-in for the report.

Reversible: the downgrade drops the columns (the seeded text goes with them).

Revision ID: a1c4e7b9d2f3
Revises: f7b2d4e6a8c1
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op

from app.services.cvss_service import normalize_cvss
from app.services.report_text import clip as _clip, references_markdown


revision = "a1c4e7b9d2f3"
down_revision = "f7b2d4e6a8c1"
branch_labels = None
depends_on = None

_BATCH = 2000


def upgrade():
    op.add_column("findings", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("impact", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("recommendation", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("references", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("steps_to_reproduce", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("cvss_vector", sa.String(200), nullable=True))
    op.add_column("findings", sa.Column("cvss_score", sa.Float(), nullable=True))
    op.add_column(
        "note_attachments",
        sa.Column("include_in_report", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    conn = op.get_bind()

    # Scanner findings ← their originating vulnerability row.
    last_id = 0
    while True:
        rows = conn.execute(
            sa.text(
                'SELECT f.id, v.description, v.solution, v."references", v.cvss_vector, v.cvss_score '
                "FROM findings f JOIN vulnerabilities v ON v.id = f.vuln_id "
                "WHERE f.id > :last ORDER BY f.id LIMIT :n"
            ),
            {"last": last_id, "n": _BATCH},
        ).fetchall()
        if not rows:
            break
        params = []
        for fid, description, solution, refs, vector, score in rows:
            vec, sc = normalize_cvss(vector, score, strict=False)
            params.append({
                "id": fid,
                "d": _clip(description), "r": _clip(solution),
                "refs": _clip(references_markdown(refs)), "v": vec, "s": sc,
            })
        conn.execute(
            sa.text(
                "UPDATE findings SET "
                "description = COALESCE(description, :d), "
                "recommendation = COALESCE(recommendation, :r), "
                '"references" = COALESCE("references", :refs), '
                "cvss_vector = COALESCE(cvss_vector, :v), "
                "cvss_score = COALESCE(cvss_score, :s) "
                "WHERE id = :id"
            ),
            params,
        )
        last_id = rows[-1][0]

    # Note findings ← the body of the note they were promoted from.
    last_id = 0
    while True:
        rows = conn.execute(
            sa.text(
                "SELECT f.id, a.body FROM findings f "
                "JOIN annotations a ON a.id = f.evidence_annotation_id "
                "WHERE f.source = 'note' AND f.description IS NULL AND f.id > :last "
                "ORDER BY f.id LIMIT :n"
            ),
            {"last": last_id, "n": _BATCH},
        ).fetchall()
        if not rows:
            break
        params = [{"id": fid, "d": _clip(body)} for fid, body in rows if _clip(body)]
        if params:
            conn.execute(
                sa.text("UPDATE findings SET description = :d WHERE id = :id AND description IS NULL"),
                params,
            )
        last_id = rows[-1][0]


def downgrade():
    op.drop_column("note_attachments", "include_in_report")
    op.drop_column("findings", "cvss_score")
    op.drop_column("findings", "cvss_vector")
    op.drop_column("findings", "steps_to_reproduce")
    op.drop_column("findings", "references")
    op.drop_column("findings", "recommendation")
    op.drop_column("findings", "impact")
    op.drop_column("findings", "description")
