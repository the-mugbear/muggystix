"""every API key is an agent session's key

Review 2026-10-10, R7.  The only keys the application mints are agent-session
keys (``agent_session_service``: ``agent_id`` and ``agent_session_id`` set);
the operator / service keys ``api_keys.user_id`` and ``api_keys.scopes`` were
made for went in v2.295.0, and nothing has written or read either column
since.  The auth chain still carried branches for a key with no agent or no
session (refused with a 403 "not bound to a session").  This revision makes
the schema say what every key is, and the branches go with it.

**Upgrade.**  A key with no agent or no session STOPS the upgrade, naming the
key ids — nothing is deleted (such a key cannot authenticate on the running
version either; delete it there, then upgrade).  Otherwise ``user_id`` (with
its foreign key) and ``scopes`` are dropped, and ``agent_id`` and
``agent_session_id`` become NOT NULL.

To see such keys beforehand::

    select id, name, agent_id, agent_session_id from api_keys
     where agent_id is null or agent_session_id is null;

**Downgrade.**  ``agent_id`` and ``agent_session_id`` are nullable again, and
``user_id`` (FK to ``users``, CASCADE) and ``scopes`` come back empty.

Revision ID: f4c8b2d0e6a1
Revises: e3b7a1c9d5f0
"""
import sqlalchemy as sa
from alembic import op

revision = "f4c8b2d0e6a1"
down_revision = "e3b7a1c9d5f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    unbound = [r[0] for r in conn.execute(sa.text(
        "SELECT id FROM api_keys WHERE agent_id IS NULL OR agent_session_id IS NULL ORDER BY id"
    ))]
    if unbound:
        listed = f"{unbound[:20]}{' …' if len(unbound) > 20 else ''}"
        raise RuntimeError(
            f"API keys bound to a session (f4c8b2d0e6a1): {len(unbound)} key(s) have no agent "
            f"or no agent session (ids {listed}). They cannot authenticate; delete them on the "
            "running version and upgrade again. Nothing was changed."
        )
    op.drop_constraint("api_keys_user_id_fkey", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "user_id")
    op.drop_column("api_keys", "scopes")
    op.alter_column("api_keys", "agent_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("api_keys", "agent_session_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.alter_column("api_keys", "agent_session_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("api_keys", "agent_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("api_keys", sa.Column("scopes", sa.JSON(), nullable=True))
    op.add_column("api_keys", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "api_keys_user_id_fkey", "api_keys", "users", ["user_id"], ["id"], ondelete="CASCADE",
    )
