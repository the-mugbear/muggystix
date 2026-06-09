"""generalize annotation targets (foundation 2)

An Annotation can now hang off any one of host / port / scan / scope / plan
/ project, not just a host.  Adds the five new nullable target FKs, makes
``host_id`` nullable, and enforces "exactly one target" with a Postgres
``num_nonnulls`` CHECK.  Existing rows (all host-scoped) satisfy the CHECK
(only host_id set → num_nonnulls = 1), so no data migration is needed.

The CHECK is intentionally added here rather than in the model's
__table_args__: ``num_nonnulls`` is Postgres-only, and the test suite's
SQLite ``create_all`` would choke on it.  The write paths validate
exactly-one in application code too.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TARGETS = [
    ("port_id", "ports_v2"),
    ("scan_id", "scans"),
    ("scope_id", "scopes"),
    ("plan_id", "test_plans"),
    ("project_id", "projects"),
]


def upgrade() -> None:
    op.alter_column("annotations", "host_id", existing_type=sa.Integer(), nullable=True)
    for col, target in _TARGETS:
        op.add_column("annotations", sa.Column(col, sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_annotations_{col}", "annotations", target, [col], ["id"], ondelete="CASCADE",
        )
        op.create_index(f"ix_annotations_{col}", "annotations", [col])
    op.create_check_constraint(
        "ck_annotations_exactly_one_target",
        "annotations",
        "num_nonnulls(host_id, port_id, scan_id, scope_id, plan_id, project_id) = 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_annotations_exactly_one_target", "annotations", type_="check")
    for col, _ in _TARGETS:
        op.drop_index(f"ix_annotations_{col}", table_name="annotations")
        op.drop_constraint(f"fk_annotations_{col}", "annotations", type_="foreignkey")
        op.drop_column("annotations", col)
    # Best-effort: re-applying host_id NOT NULL FAILS if any non-host
    # annotation (scope/scan/plan/project-targeted) exists by now.  Delete or
    # re-home those first if you must downgrade a DB that used the feature.
    op.alter_column("annotations", "host_id", existing_type=sa.Integer(), nullable=False)
