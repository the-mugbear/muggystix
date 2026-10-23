"""A host's review marks outlive the account that made them

Owner, 2026-10-22: when a user account is deleted, their host review marks
(``host_follows`` rows — In review / Reviewed, ``reviewed_at``, the review
note) are KEPT, attributed to no one, the way findings, notes and proposals
already treat a deleted person.  Until now the rows cascaded with the account,
which silently turned reviewed hosts back into unreviewed ("untouched") hosts
and moved the project's reviewed and assessed counts.

**Upgrade**: ``host_follows.user_id`` becomes nullable and its foreign key
``host_follows_user_id_fkey`` is ``ON DELETE SET NULL`` (it was CASCADE).  No
row changes.  ``uq_host_follow_user (host_id, user_id)`` is kept as it is:
Postgres treats NULLs as distinct there, so every former member's row on a
host is kept (two people who reviewed one host and were both deleted leave two
rows), and for a living account it still means one row per (host, person).

**Downgrade**: the old column is NOT NULL, so it cannot hold a mark whose
reviewer is gone — rows with ``user_id IS NULL`` are DELETED (the hosts they
marked read as unreviewed again, as they did before this revision), then the
column is NOT NULL and the key CASCADE again.

Revision ID: e8c4a2f6d1b9
Revises: f2b8d4a6c1e3
"""
from alembic import op

revision = "e8c4a2f6d1b9"
down_revision = "f2b8d4a6c1e3"
branch_labels = None
depends_on = None

_FK = "host_follows_user_id_fkey"


def _swap_fk(ondelete: str) -> None:
    op.drop_constraint(_FK, "host_follows", type_="foreignkey")
    op.create_foreign_key(
        _FK, "host_follows", "users", ["user_id"], ["id"], ondelete=ondelete,
    )


def upgrade() -> None:
    op.alter_column("host_follows", "user_id", nullable=True)
    _swap_fk("SET NULL")


def downgrade() -> None:
    op.execute("DELETE FROM host_follows WHERE user_id IS NULL")
    _swap_fk("CASCADE")
    op.alter_column("host_follows", "user_id", nullable=False)
