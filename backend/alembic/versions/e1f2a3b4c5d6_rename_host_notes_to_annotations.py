"""rename host_notes -> annotations (foundation 1b)

Part of the finding/annotation consolidation.  ``HostNote`` became
``Annotation`` because the model will target more than hosts (scope, scan,
plan, project, port) in the next phase, so the tables follow the model.

Pure table renames — primary keys, foreign keys, and other constraints
ride along automatically (Postgres references them by object id, not by the
table's old name).  Index names keep their old ``host_note*`` spelling; that
is cosmetic only and not worth a churny per-index rename.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c2d4e6f8a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("host_notes", "annotations")
    op.rename_table("host_note_status_history", "annotation_status_history")


def downgrade() -> None:
    op.rename_table("annotation_status_history", "host_note_status_history")
    op.rename_table("annotations", "host_notes")
