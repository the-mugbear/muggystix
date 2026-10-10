"""scanner integrations belong to the installation, not to a user or a project

Owner decision of 2026-10-10 ("Integrations should be application wide").
Ownership comes off ``integration_credentials``:

* ``project_id`` is DROPPED (with its FK and index).  A row that was limited
  to one project becomes the installation's like every other.
* ``user_id`` becomes ``created_by_id`` — NULLABLE, ``ON DELETE SET NULL``:
  who configured it, as provenance.  Deleting that account no longer deletes
  the integration.
* the unique constraint ``uq_integration_user_project_name`` is dropped and
  NOT replaced.  Two people may have configured a scanner under one name;
  **every row is kept** and a global administrator removes the duplicate.
  Nothing is merged by guess.

**Downgrade.**  The old model comes back with every row in it:

* ``project_id`` returns as NULL on every row — which in the old model means
  "every project", what an application-wide row is.  The project a row was
  limited to before the upgrade is not recoverable.
* ``created_by_id`` returns to ``user_id NOT NULL … ON DELETE CASCADE``.  A
  row whose creator is gone is given an owner first: the first active global
  administrator by id, else the first global administrator, else the first
  user.  With rows to re-home and no user at all the downgrade refuses, and
  nothing is changed (the migration is one transaction).
* the unique constraint is re-created as the baseline had it,
  ``(user_id, project_id, name)``.  It cannot be violated by the rows this
  leaves: every ``project_id`` is NULL and a unique constraint treats NULLs as
  distinct, so two rows of one owner under one name are legal — exactly as
  two all-project rows of one user were before the upgrade.  No name is
  therefore changed (``tests/test_migration_integrations_application_wide.py``
  walks it with such rows).

Revision ID: c8f2d5a9e3b1
Revises: a6d3b1e8c5f7
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "c8f2d5a9e3b1"
down_revision = "a6d3b1e8c5f7"
branch_labels = None
depends_on = None

_TABLE = "integration_credentials"


def upgrade():
    op.drop_constraint("uq_integration_user_project_name", _TABLE, type_="unique")

    op.drop_index("ix_integration_credentials_project_id", table_name=_TABLE)
    op.drop_constraint("integration_credentials_project_id_fkey", _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "project_id")

    op.drop_index("ix_integration_credentials_user_id", table_name=_TABLE)
    op.drop_constraint("integration_credentials_user_id_fkey", _TABLE, type_="foreignkey")
    op.alter_column(
        _TABLE, "user_id",
        new_column_name="created_by_id", existing_type=sa.Integer(), nullable=True,
    )
    op.create_foreign_key(
        "integration_credentials_created_by_id_fkey", _TABLE, "users",
        ["created_by_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_integration_credentials_created_by_id", _TABLE, ["created_by_id"], unique=False,
    )


def downgrade():
    bind = op.get_bind()

    # Every row needs an owner again.
    orphans = bind.execute(sa.text(
        "SELECT count(*) FROM integration_credentials WHERE created_by_id IS NULL"
    )).scalar()
    if orphans:
        owner = bind.execute(sa.text(
            "SELECT id FROM users ORDER BY "
            "  (lower(role) = 'admin' AND is_active IS TRUE) DESC, "
            "  (lower(role) = 'admin') DESC, "
            "  id "
            "LIMIT 1"
        )).scalar()
        if owner is None:
            raise RuntimeError(
                f"{orphans} scanner integration(s) have no creator and this "
                "installation has no user to give them to. Create an "
                "administrator (or delete those integrations) and run the "
                "downgrade again."
            )
        bind.execute(
            sa.text(
                "UPDATE integration_credentials SET created_by_id = :owner "
                "WHERE created_by_id IS NULL"
            ),
            {"owner": owner},
        )

    op.drop_index("ix_integration_credentials_created_by_id", table_name=_TABLE)
    op.drop_constraint("integration_credentials_created_by_id_fkey", _TABLE, type_="foreignkey")
    op.alter_column(
        _TABLE, "created_by_id",
        new_column_name="user_id", existing_type=sa.Integer(), nullable=False,
    )
    op.create_foreign_key(
        "integration_credentials_user_id_fkey", _TABLE, "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_integration_credentials_user_id", _TABLE, ["user_id"], unique=False)

    op.add_column(_TABLE, sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "integration_credentials_project_id_fkey", _TABLE, "projects",
        ["project_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_integration_credentials_project_id", _TABLE, ["project_id"], unique=False)

    op.create_unique_constraint(
        "uq_integration_user_project_name", _TABLE, ["user_id", "project_id", "name"],
    )
