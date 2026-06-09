"""annotations.host_id -> ON DELETE CASCADE (+ index)

The five generalized annotation target FKs (port/scan/scope/plan/project)
are ON DELETE CASCADE, but host_id (the original, from the host_notes era)
had no ondelete — so deleting a host with host-scoped notes would be blocked
(or orphan them) instead of cascading like every other target.  Brings it in
line and adds the missing index on host_id.

The old constraint name is uncertain (the table was renamed host_notes ->
annotations, which doesn't rename constraints), so we resolve it dynamically
by its referenced table.  Postgres-only.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "090a1b2c3d4e"
down_revision: Union[str, None] = "f8090a1b2c3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _swap_host_fk(ondelete: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE cname text;
        BEGIN
            SELECT conname INTO cname FROM pg_constraint
             WHERE conrelid = 'annotations'::regclass AND contype = 'f'
               AND confrelid = 'hosts_v2'::regclass;
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE annotations DROP CONSTRAINT %I', cname);
            END IF;
            ALTER TABLE annotations
              ADD CONSTRAINT annotations_host_id_fkey
              FOREIGN KEY (host_id) REFERENCES hosts_v2(id) {ondelete};
        END $$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _swap_host_fk("ON DELETE CASCADE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_annotations_host_id ON annotations (host_id)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_annotations_host_id")
    _swap_host_fk("")  # back to NO ACTION
