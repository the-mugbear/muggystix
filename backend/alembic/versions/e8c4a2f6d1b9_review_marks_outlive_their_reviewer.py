"""A host's review marks outlive the account that made them

Owner, 2026-10-22: when a user account is deleted, their host review marks
(``host_follows`` rows — In review / Reviewed, ``reviewed_at``, the review
note) are KEPT, attributed to no one, the way findings, notes and proposals
already treat a deleted person.  Until now the rows cascaded with the account,
which silently turned reviewed hosts back into unreviewed ("untouched") hosts
and moved the project's reviewed and assessed counts.

**Upgrade**: ``host_follows.user_id`` becomes nullable and its foreign key
``host_follows_user_id_fkey`` is ``ON DELETE SET NULL`` (it was CASCADE).  No
row changes.  ``uq_host_follow_user (host_id, user_id)`` is kept as it is:
Postgres treats NULLs as distinct there, so every former member's row on a
host is kept (two people who reviewed one host and were both deleted leave two
rows), and for a living account it still means one row per (host, person).

**An assignment needs a person.**  A row's ``assigned_at`` / ``assigned_by_id``
mean "this host is assigned to ``user_id``"; a row with no ``user_id`` must
hold no assignment, or ``assigned:any`` lists a host nobody holds and
``assigned:none`` leaves it out of the pick-up queue.  Clearing it in the
delete route was not enough (external review of 2c1007ba, confirmed with two
connections): an assignment committed between that UPDATE and the DELETE
survived the SET NULL.  So the DATABASE keeps the rule for every writer:
``host_follows_assignment_needs_a_person()``, a BEFORE INSERT OR UPDATE row
trigger, clears both assignment columns whenever the row's resulting
``user_id`` is NULL — the foreign key's own SET NULL fires it too — and
``ck_host_follows_assignment_needs_a_person`` states it.

**Downgrade**: the old column is NOT NULL, so it cannot hold a mark whose
reviewer is gone — rows with ``user_id IS NULL`` are DELETED (the hosts they
marked read as unreviewed again, as they did before this revision), then the
trigger and the check go, and the column is NOT NULL and the key CASCADE again.

Revision ID: e8c4a2f6d1b9
Revises: f2b8d4a6c1e3
"""
from alembic import op

revision = "e8c4a2f6d1b9"
down_revision = "f2b8d4a6c1e3"
branch_labels = None
depends_on = None

_FK = "host_follows_user_id_fkey"
_CHECK = "ck_host_follows_assignment_needs_a_person"
_FUNCTION = "host_follows_assignment_needs_a_person"
_TRIGGER = "trg_host_follows_assignment_needs_a_person"


def _swap_fk(ondelete: str) -> None:
    op.drop_constraint(_FK, "host_follows", type_="foreignkey")
    op.create_foreign_key(
        _FK, "host_follows", "users", ["user_id"], ["id"], ondelete=ondelete,
    )


def upgrade() -> None:
    op.alter_column("host_follows", "user_id", nullable=True)
    _swap_fk("SET NULL")
    op.execute(f"""
        CREATE FUNCTION {_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.user_id IS NULL THEN
                NEW.assigned_at := NULL;
                NEW.assigned_by_id := NULL;
            END IF;
            RETURN NEW;
        END
        $$
    """)
    op.execute(f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE INSERT OR UPDATE ON host_follows
        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()
    """)
    op.create_check_constraint(
        _CHECK, "host_follows",
        "user_id IS NOT NULL OR (assigned_at IS NULL AND assigned_by_id IS NULL)",
    )


def downgrade() -> None:
    op.execute("DELETE FROM host_follows WHERE user_id IS NULL")
    op.drop_constraint(_CHECK, "host_follows", type_="check")
    op.execute(f"DROP TRIGGER {_TRIGGER} ON host_follows")
    op.execute(f"DROP FUNCTION {_FUNCTION}()")
    _swap_fk("CASCADE")
    op.alter_column("host_follows", "user_id", nullable=False)
