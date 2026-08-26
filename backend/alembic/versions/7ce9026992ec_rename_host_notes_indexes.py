"""rename stale host_notes index names to annotations

The ``host_notes`` table was renamed to ``annotations`` (and
``host_note_status_history`` to ``annotation_status_history``), but the indexes
kept their original ``ix_host_notes_*`` / ``ix_host_note_status_history_*``
names. The models produce the new table-based names, so autogenerate saw the
old DB names as "removed" and the model names as "added" — pure rename drift.

This renames the physical indexes to match, which is an instant metadata-only
DDL (no rebuild). Every statement is ``IF EXISTS`` so it is a no-op on a schema
built from the current models via ``create_all`` (the test backends), where the
new names already exist and the old ones never did.

Revision ID: 7ce9026992ec
Revises: b6d1f83a25c9
Create Date: 2026-08-26
"""
from alembic import op


revision = "7ce9026992ec"
down_revision = "b6d1f83a25c9"
branch_labels = None
depends_on = None


# (old_name, new_name) — the redundant PK-id indexes are included so the
# annotations tables carry no lingering host_notes-era names.
_RENAMES = [
    ("ix_host_notes_id", "ix_annotations_id"),
    ("ix_host_notes_assignee_id", "ix_annotations_assignee_id"),
    ("ix_host_notes_thread_root_id", "ix_annotations_thread_root_id"),
    ("ix_host_note_status_history_id", "ix_annotation_status_history_id"),
    ("ix_host_note_status_history_note_id", "ix_annotation_status_history_note_id"),
]


def _rename(old: str, new: str) -> None:
    op.execute(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}")


def upgrade() -> None:
    for old, new in _RENAMES:
        _rename(old, new)


def downgrade() -> None:
    for old, new in _RENAMES:
        _rename(new, old)
