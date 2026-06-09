"""drop the dead risk-scoring tables (contract)

The risk-scoring subsystem was never populated (HostRiskAssessment had 0
rows; the page was feature-flagged off) and its CVE data was hardcoded.
Its models/services/endpoint/DSL field have been removed; this drops the
now-orphaned tables.  CASCADE handles the inter-table FKs (host_vulnerabilities
/ security_findings / risk_recommendations referenced host_risk_assessments)
without ordering; IF EXISTS tolerates an install that never had them.

Downgrade is a no-op — this removes a feature, not a schema tweak.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d6e7f8090a1b"
down_revision: Union[str, None] = "c5d6e7f8090a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = [
    "host_vulnerabilities",
    "security_findings",
    "risk_recommendations",
    "host_risk_assessments",
    "vulnerability_database",
]


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    # The dead risk-scoring subsystem is not recreated.
    pass
