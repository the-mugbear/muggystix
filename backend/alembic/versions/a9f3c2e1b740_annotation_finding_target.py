"""annotation finding target — comment/evidence threads on findings

Adds ``annotations.finding_id`` (FK → findings, CASCADE) so a Finding can host
its own note thread + screenshot attachments — the notes→findings→reports flow:
host notes capture issues, findings are reviewed/refined with evidence, reports
render that evidence.  Works regardless of how the finding was created (note
promote / vuln promote / manual).

Recreates the "exactly one target" CHECK to include ``finding_id``.  Existing
rows (one of the six prior targets set, finding_id null) still satisfy it, so no
data migration is needed.

The CHECK stays out of the model ``__table_args__`` — ``num_nonnulls`` is
Postgres-only and the test suite's SQLite ``create_all`` would choke on it; the
write paths validate exactly-one in application code too.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9f3c2e1b740"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("annotations", sa.Column("finding_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_annotations_finding_id", "annotations", "findings",
        ["finding_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_annotations_finding_id", "annotations", ["finding_id"])
    op.drop_constraint("ck_annotations_exactly_one_target", "annotations", type_="check")
    op.create_check_constraint(
        "ck_annotations_exactly_one_target",
        "annotations",
        "num_nonnulls(host_id, port_id, scan_id, scope_id, plan_id, project_id, finding_id) = 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_annotations_exactly_one_target", "annotations", type_="check")
    # Finding-targeted notes have no valid home under the 6-target CHECK; drop
    # them before restoring it (downgrade is destructive by nature).
    op.execute("DELETE FROM annotations WHERE finding_id IS NOT NULL")
    op.create_check_constraint(
        "ck_annotations_exactly_one_target",
        "annotations",
        "num_nonnulls(host_id, port_id, scan_id, scope_id, plan_id, project_id) = 1",
    )
    op.drop_index("ix_annotations_finding_id", table_name="annotations")
    op.drop_constraint("fk_annotations_finding_id", "annotations", type_="foreignkey")
    op.drop_column("annotations", "finding_id")
