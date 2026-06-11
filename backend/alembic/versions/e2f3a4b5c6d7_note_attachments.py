"""Note image attachments

Revision ID: e2f3a4b5c6d7
Revises: d1a2b3c4e5f6
Create Date: 2026-06-11 19:40:00.000000

Adds ``note_attachments`` — image/screenshot files attached to notes
(annotations) so findings can carry visual evidence.  Bytes live on disk; this
table holds metadata + the relative storage path.  Additive only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1a2b3c4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'note_attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('annotation_id', sa.Integer(), sa.ForeignKey('annotations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('storage_path', sa.String(), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_note_attachments_annotation_id', 'note_attachments', ['annotation_id'])
    op.create_index('ix_note_attachments_project_id', 'note_attachments', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_note_attachments_project_id', table_name='note_attachments')
    op.drop_index('ix_note_attachments_annotation_id', table_name='note_attachments')
    op.drop_table('note_attachments')
