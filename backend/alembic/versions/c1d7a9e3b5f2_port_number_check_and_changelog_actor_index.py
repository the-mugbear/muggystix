"""ports_v2 states what a port number is; the changelog's per-person index

Review 2026-11-20 (T2, C25).

**``ck_ports_v2_port_number``** — ``port_number BETWEEN 1 AND 65535``.  Every
writer already asks ``host_deduplication_service.valid_port_number``; the
constraint is the same rule where no writer can forget it.  It is added
``NOT VALID``, so it holds for every row written from now on and **refuses
nothing at upgrade**: a database that already holds a row outside the range
keeps it (the constraint then stays unvalidated and the upgrade says how many
such rows there are); one that holds none has the constraint validated in the
same step.

**``ix_project_events_project_actor_created``** — ``(project_id,
actor_user_id, created_at, id)``: the changelog filtered to one person reads
its page from it, and "who has a line in this project" is one probe per
person instead of a DISTINCT over every line.

**Downgrade.**  Drops both; no data changes.

Revision ID: c1d7a9e3b5f2
Revises: f6b9d4e2a8c1
Create Date: 2026-11-21
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "c1d7a9e3b5f2"
down_revision = "f6b9d4e2a8c1"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_CHECK = "ck_ports_v2_port_number"
_INDEX = "ix_project_events_project_actor_created"


def upgrade() -> None:
    conn = op.get_bind()
    op.execute(
        f"ALTER TABLE ports_v2 ADD CONSTRAINT {_CHECK} "
        "CHECK (port_number BETWEEN 1 AND 65535) NOT VALID"
    )
    outside = conn.execute(sa.text(
        "SELECT count(*) FROM ports_v2 WHERE port_number NOT BETWEEN 1 AND 65535"
    )).scalar() or 0
    if outside:
        logger.warning(
            "%s: %d existing port row(s) hold a number outside 1-65535; they are kept, and the "
            "constraint applies to rows written from now on (not validated).", _CHECK, outside,
        )
    else:
        op.execute(f"ALTER TABLE ports_v2 VALIDATE CONSTRAINT {_CHECK}")
    op.create_index(_INDEX, "project_events", ["project_id", "actor_user_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="project_events")
    op.drop_constraint(_CHECK, "ports_v2", type_="check")
