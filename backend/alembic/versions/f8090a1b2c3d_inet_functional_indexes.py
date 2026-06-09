"""functional inet indexes on hosts_v2.ip_address

ip_address is stored as text with a plain btree, which cannot serve the
subnet-containment filter (``ip_address::inet <<= :cidr``, a GiST operator)
nor the inet-ordered sort (``ORDER BY ip_address::inet``).  Every CIDR filter
and IP-sorted page therefore seq-scans all project hosts.  This adds:

  * a GiST functional index (inet_ops) for the ``<<=`` containment filter, and
  * a btree functional index for the inet sort.

Postgres-only (the ``::inet`` cast is PG); guarded so a SQLite/other-dialect
run is a no-op.  Built without CONCURRENTLY (Alembic runs in a txn) — the
hosts table is bounded by dedup, so the brief lock is acceptable.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f8090a1b2c3d"
down_revision: Union[str, None] = "e7f8090a1b2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_host_ip_inet_gist "
        "ON hosts_v2 USING gist ((ip_address::inet) inet_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_host_ip_inet "
        "ON hosts_v2 ((ip_address::inet))"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_host_ip_inet")
    op.execute("DROP INDEX IF EXISTS idx_host_ip_inet_gist")
