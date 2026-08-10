"""web_interfaces: promote certificate Organization out of the tls_info blob

The subject Organization is attribution a public CA validated before issuing —
stronger evidence of who runs a host than a self-declared registry record, and
already present in every web scan's tls_info. It was parsed for the
self-signed comparison and then discarded.

Nullable with no backfill: existing rows keep their blob, and the value is
derived on the next scan. Backfilling would mean re-parsing every historical
blob for a field that refreshes naturally.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d3e05f7a21"
down_revision: Union[str, None] = "a2f7b91c4d06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("web_interfaces", sa.Column("cert_subject_org", sa.String(length=255), nullable=True))
    op.add_column("web_interfaces", sa.Column("cert_issuer_org", sa.String(length=255), nullable=True))
    op.create_index("ix_web_interfaces_cert_subject_org", "web_interfaces", ["cert_subject_org"])


def downgrade() -> None:
    op.drop_index("ix_web_interfaces_cert_subject_org", table_name="web_interfaces")
    op.drop_column("web_interfaces", "cert_issuer_org")
    op.drop_column("web_interfaces", "cert_subject_org")
