"""backfill note_type='finding' annotations into the Finding spine (foundation 7a)

Mirrors the promote_annotation logic for any annotation a human already
classified as a finding (``note_type='finding'``) before the spine existed:
one note-sourced Finding per root thread, with its origin host attached.
Idempotent — skips any thread root that already has a note-sourced finding
(e.g. promoted via the API after deploy).

Additive: creates rows only.  Likely a no-op on installs that never used
the note_type='finding' kind.  Rehearse on a staging copy before a
populated production DB (it touches live annotation rows).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f809"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Create one Finding per root finding-note (host-scoped) not already
    # promoted.  severity defaults to 'medium', status 'confirmed' (a human
    # classified it a finding).  Title = first non-empty body line.
    bind.execute(sa.text(
        """
        INSERT INTO findings
          (project_id, title, severity, status, source, owner_id,
           evidence_annotation_id, created_by_id, created_at)
        SELECT h.project_id,
               left(coalesce(nullif(trim(split_part(a.body, E'\\n', 1)), ''),
                             'Promoted finding'), 500),
               'medium', 'confirmed', 'note', a.assignee_id,
               coalesce(a.thread_root_id, a.id), a.user_id, a.created_at
        FROM annotations a
        JOIN hosts_v2 h ON h.id = a.host_id
        WHERE a.note_type = 'finding'
          AND a.parent_id IS NULL
          AND a.host_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM findings f
            WHERE f.source = 'note'
              AND f.evidence_annotation_id = coalesce(a.thread_root_id, a.id)
          )
        """
    ))
    # Attach the origin host to each just-created note-sourced finding that
    # has no hosts yet.
    bind.execute(sa.text(
        """
        INSERT INTO finding_hosts (finding_id, host_id, host_status, created_at)
        SELECT f.id, a.host_id, 'open', now()
        FROM findings f
        JOIN annotations a ON a.id = f.evidence_annotation_id
        WHERE f.source = 'note'
          AND a.host_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM finding_hosts fh WHERE fh.finding_id = f.id
          )
        """
    ))


def downgrade() -> None:
    # Remove only the note-sourced findings that point at an annotation
    # thread root (the ones this backfill could have created).  finding_hosts
    # rows cascade with the finding.
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM findings WHERE source = 'note' "
        "AND evidence_annotation_id IS NOT NULL"
    ))
