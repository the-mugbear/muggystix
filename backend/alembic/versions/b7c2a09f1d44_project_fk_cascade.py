"""cascade FKs that reference projects.id

Revision ID: b7c2a09f1d44
Revises: a39f25b76e10
Create Date: 2026-05-18 01:30:00.000000

Audit:  several tables FK to ``projects.id`` with ``ON DELETE NO ACTION``,
which silently blocks the project-delete endpoint added in v2.40.0.
The frontend's new Delete-project button calls ``DELETE /projects/{id}``
which does ``db.delete(project)``; the unscoped FKs cause an
``IntegrityError`` before the user sees anything change.  This was
caught when an operator tried to remove a no-longer-needed bootstrap
project and the delete failed silently in the UI.

This migration switches every project-referencing FK from
``NO ACTION`` to ``CASCADE`` so deleting a project removes all its
scans, hosts, scopes, ingestion jobs, parse errors, etc.  Two
exceptions:

  * ``agent_feedback`` keeps its existing ``ON DELETE SET NULL`` —
    feedback is cross-project telemetry that outlives the project
    being critiqued.
  * Tables already on ``CASCADE`` (agents, agent_api_calls,
    host_filter_views, integration_credentials, notifications,
    project_memberships, recon_sessions, test_plans, web_interfaces)
    are untouched.

Tables converted (all had ``NO ACTION``):
  * dns_records
  * hosts_v2
  * ingestion_jobs
  * out_of_scope_hosts
  * parse_errors
  * scans
  * scopes  (cascades through to subnets via the existing scope FK)

Down-revision restores ``NO ACTION`` for symmetry, though anyone
applying this migration should not rely on rollback semantics for
cascade-FK changes.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7c2a09f1d44'
down_revision: Union[str, None] = 'a39f25b76e10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table_name, fk_constraint_name) pairs to drop + recreate with
# ON DELETE CASCADE. Constraint names follow the SQLAlchemy default
# of ``{table}_{column}_fkey``.
CASCADE_TABLES = [
    ('dns_records', 'dns_records_project_id_fkey'),
    ('hosts_v2', 'hosts_v2_project_id_fkey'),
    ('ingestion_jobs', 'ingestion_jobs_project_id_fkey'),
    ('out_of_scope_hosts', 'out_of_scope_hosts_project_id_fkey'),
    ('parse_errors', 'parse_errors_project_id_fkey'),
    ('scans', 'scans_project_id_fkey'),
    ('scopes', 'scopes_project_id_fkey'),
]


def upgrade() -> None:
    for table, fk_name in CASCADE_TABLES:
        op.drop_constraint(fk_name, table, type_='foreignkey')
        op.create_foreign_key(
            fk_name,
            table,
            'projects',
            ['project_id'],
            ['id'],
            ondelete='CASCADE',
        )


def downgrade() -> None:
    for table, fk_name in CASCADE_TABLES:
        op.drop_constraint(fk_name, table, type_='foreignkey')
        op.create_foreign_key(
            fk_name,
            table,
            'projects',
            ['project_id'],
            ['id'],
            # PostgreSQL default; matches the pre-migration behaviour.
            ondelete='NO ACTION',
        )
