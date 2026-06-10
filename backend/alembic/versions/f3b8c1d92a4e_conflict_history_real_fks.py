"""conflict_history: de-polymorphize to real host_id/port_id FKs

Revision ID: f3b8c1d92a4e
Revises: a7c3e91b5d28
Create Date: 2026-06-10 00:00:00.000000

``conflict_history`` keyed its rows with a polymorphic ``(object_type,
object_id)`` pair — a string discriminator + a bare integer with NO foreign
key.  That meant no referential integrity (orphan rows survived a host/port
delete) and no clean joins.  Replace it with real ``host_id`` / ``port_id``
FKs (ON DELETE CASCADE), backfilling from the old pair and dropping orphans
that the missing cascade had left behind.

A host field conflict sets host_id (port_id null); a port field conflict
sets port_id (host_id null).  The API response still exposes object_type /
object_id (derived at serialization), so the contract is unchanged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f3b8c1d92a4e'
down_revision: Union[str, None] = 'a7c3e91b5d28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. New nullable columns (no FK yet — must backfill + clean before the FK).
    op.add_column("conflict_history", sa.Column("host_id", sa.Integer(), nullable=True))
    op.add_column("conflict_history", sa.Column("port_id", sa.Integer(), nullable=True))

    # 2. Backfill from the polymorphic pair.
    op.execute("UPDATE conflict_history SET host_id = object_id WHERE object_type = 'host'")
    op.execute("UPDATE conflict_history SET port_id = object_id WHERE object_type = 'port'")

    # 3. Drop orphans the absent cascade had left behind (rows whose host/port
    #    no longer exists, or whose object_type was neither) so the FK is valid.
    op.execute("DELETE FROM conflict_history WHERE host_id IS NOT NULL AND host_id NOT IN (SELECT id FROM hosts_v2)")
    op.execute("DELETE FROM conflict_history WHERE port_id IS NOT NULL AND port_id NOT IN (SELECT id FROM ports_v2)")
    op.execute("DELETE FROM conflict_history WHERE host_id IS NULL AND port_id IS NULL")

    # 4. Real FKs (cascade) + indexes.
    op.create_foreign_key(
        "fk_conflict_history_host_id_hosts_v2", "conflict_history", "hosts_v2",
        ["host_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_conflict_history_port_id_ports_v2", "conflict_history", "ports_v2",
        ["port_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("idx_conflict_history_host", "conflict_history", ["host_id"])
    op.create_index("idx_conflict_history_port", "conflict_history", ["port_id"])

    # 5. Retire the polymorphic pair.
    op.drop_index("idx_conflict_history_object", table_name="conflict_history")
    op.drop_column("conflict_history", "object_type")
    op.drop_column("conflict_history", "object_id")


def downgrade() -> None:
    op.add_column("conflict_history", sa.Column("object_type", sa.String(), nullable=True))
    op.add_column("conflict_history", sa.Column("object_id", sa.Integer(), nullable=True))
    op.execute("UPDATE conflict_history SET object_type = 'host', object_id = host_id WHERE host_id IS NOT NULL")
    op.execute("UPDATE conflict_history SET object_type = 'port', object_id = port_id WHERE port_id IS NOT NULL")
    op.execute("DELETE FROM conflict_history WHERE object_type IS NULL")
    op.alter_column("conflict_history", "object_type", nullable=False)
    op.alter_column("conflict_history", "object_id", nullable=False)
    op.create_index("idx_conflict_history_object", "conflict_history", ["object_type", "object_id"])

    op.drop_index("idx_conflict_history_port", table_name="conflict_history")
    op.drop_index("idx_conflict_history_host", table_name="conflict_history")
    op.drop_constraint("fk_conflict_history_port_id_ports_v2", "conflict_history", type_="foreignkey")
    op.drop_constraint("fk_conflict_history_host_id_hosts_v2", "conflict_history", type_="foreignkey")
    op.drop_column("conflict_history", "port_id")
    op.drop_column("conflict_history", "host_id")
