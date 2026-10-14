"""client reports: a finding's evidence text, the report's own images, author and disclaimer

Owner, 2026-10-13: five requests about what a client report can say.

**Upgrade.**  Catalogue changes only — no row is rewritten, no index built.

* ``findings.evidence_text TEXT NULL`` — the written text of a finding's
  Evidence section (a sixth report text field).  NULL on every existing
  finding: the section prints as before until someone writes it.
* ``note_attachments.annotation_id`` becomes NULLABLE.  A row without a note
  is an image of the project's REPORT text (the system description), kept in
  the table that already holds every image a report prints so the two share
  one id space — and so one reference grammar (``evidence:<id>``), one serve
  route and one copy at issue.  The check ``ck_note_attachment_owner`` says
  such a row names its project.  No existing row changes: each has a note.
* ``report_profiles.author_name VARCHAR(255) NULL`` and
  ``report_profiles.disclaimer TEXT NULL`` — who produced the report, and
  the disclaimer it closes with, per project.  NULL = the installation's.
* ``report_defaults`` (empty) — the installation's own author and disclaimer,
  one row per key, written by a global admin.  No row = nothing is set: a
  report then prints its template's own disclaimer, as before.

**Downgrade.**

* ``findings.evidence_text``, the two profile columns and ``report_defaults``
  are dropped: every written Evidence text, project author / disclaimer and
  installation default is lost.  (An ISSUED report keeps what it printed: it
  is in its frozen dataset.)
* The report's own images — ``note_attachments`` rows with no note — are
  DELETED, since the old schema cannot hold them; their files stay on disk
  under ``uploads/note_attachments/report/`` and nothing serves them.  A
  draft whose system description places one then prints its alt text.  An
  issued report is unaffected (it holds its own copies).

One transaction; a failure leaves the database at ``d7b3e9a1c5f2``.

Revision ID: e8c4a6f0b2d7
Revises: d7b3e9a1c5f2
"""
import sqlalchemy as sa
from alembic import op

revision = "e8c4a6f0b2d7"
down_revision = "d7b3e9a1c5f2"
branch_labels = None
depends_on = None

OWNER_CHECK = "ck_note_attachment_owner"


def upgrade() -> None:
    op.add_column("findings", sa.Column("evidence_text", sa.Text(), nullable=True))
    op.alter_column("note_attachments", "annotation_id", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(
        OWNER_CHECK, "note_attachments", "annotation_id IS NOT NULL OR project_id IS NOT NULL",
    )
    op.add_column("report_profiles", sa.Column("author_name", sa.String(length=255), nullable=True))
    op.add_column("report_profiles", sa.Column("disclaimer", sa.Text(), nullable=True))
    op.create_table(
        "report_defaults",
        sa.Column("key", sa.String(length=40), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("report_defaults")
    op.drop_column("report_profiles", "disclaimer")
    op.drop_column("report_profiles", "author_name")
    op.drop_constraint(OWNER_CHECK, "note_attachments", type_="check")
    op.execute("DELETE FROM note_attachments WHERE annotation_id IS NULL")
    op.alter_column("note_attachments", "annotation_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("findings", "evidence_text")
