"""dns_records.scan_id — provenance for DNS rows (RV-1).

Lets a scan report its dns_record_count instead of appearing empty when it
only produced DNS answers.  Nullable + SET NULL on scan delete.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f9a8b7c6d5"
down_revision: Union[str, None] = "d1e8f7a6b5c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dns_records", sa.Column("scan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_dns_records_scan_id_scans",
        "dns_records", "scans",
        ["scan_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_dns_records_scan_id", "dns_records", ["scan_id"])


def downgrade() -> None:
    op.drop_index("ix_dns_records_scan_id", table_name="dns_records")
    op.drop_constraint("fk_dns_records_scan_id_scans", "dns_records", type_="foreignkey")
    op.drop_column("dns_records", "scan_id")
