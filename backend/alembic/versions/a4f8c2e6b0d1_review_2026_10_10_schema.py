"""a CIDR is in a scope once; four foreign keys get their index; the agent call log outlives its agent; a TOTP code works once

The schema half of the review of 2026-10-10 (H21, H22, H23, H26), in ONE
revision and one transaction.  Each step is its own ``_up_*`` / ``_down_*``
function, listed in ``_STEPS``: a further step is a function and a line there.

**Upgrade.**

1. ``subnets`` gains ``uq_subnets_scope_cidr UNIQUE (scope_id, cidr)``.  Both
   add routes read the scope's CIDRs and then inserted with nothing in
   between, so a double-submitted form, or a person and an agent uploading one
   file, stored the CIDR twice; every host then mapped to both rows and the
   segment counts doubled.

   Rows that already share a ``(scope_id, cidr)`` are MERGED into the lowest
   id, and only where that loses nothing:

   * the host mappings (``host_subnet_mappings``) and label assignments
     (``subnet_label_assignments``) of the others are carried to the kept row,
     without making a pair twice;
   * a non-empty ``description`` / ``site`` (with its ``site_id``) fills an
     empty one on the kept row;
   * the other rows are deleted.  What goes with them: their own
     ``created_at`` (the kept row's stays).

   Nothing is merged by guess.  When two rows of one ``(scope_id, cidr)``
   carry DIFFERENT non-empty descriptions, or different sites, the upgrade
   STOPS, names them and changes nothing: delete or edit one of each pair on
   the running version (Scope page), then upgrade.  To see them beforehand::

       select scope_id, cidr, array_agg(id order by id) as subnet_ids,
              count(distinct nullif(btrim(description), '')) as descriptions,
              count(distinct nullif(btrim(site), '')) as sites,
              count(distinct site_id) as site_ids
         from subnets group by scope_id, cidr having count(*) > 1;

   (any row = a duplicate that will be merged; ``descriptions``, ``sites``
   or ``site_ids`` above 1 = one the upgrade refuses.)

   The constraint is on the STORED TEXT.  The application has always stored
   ``str(ipaddress.ip_network(value, strict=False))`` (the add and edit
   routes, the scope-file parser), so one network has one spelling; a row
   written by hand or by a script as ``10.0.0.1/24`` is a different text from
   ``10.0.0.0/24`` and is NOT merged or refused here.  To look for such rows
   (no row on a database only the application wrote)::

       select id, scope_id, cidr from subnets
        where cidr <> network(cidr::inet)::text;

   ``ix_subnets_cidr`` is dropped: no statement looks a subnet up by its CIDR
   without its scope, and the new constraint's index — which is also the
   index ``subnets.scope_id`` never had — serves both.
2. Four foreign keys get the index their ``ON DELETE SET NULL`` needs.
   Without it, deleting one parent row scans the whole child table:
   ``hosts_v2.last_updated_scan_id`` and ``ports_v2.last_updated_scan_id``
   (a scan delete), ``finding_hosts.port_id`` (once per deleted PORT),
   ``agent_api_calls.api_key_id`` (once per deleted key).  Plain btree
   indexes, built inside the transaction.
3. ``agent_api_calls.agent_id`` becomes ``ON DELETE SET NULL`` (it was
   CASCADE), and ``ck_agent_api_calls_attribution_or_error`` is relaxed from
   "(agent AND project) or an error class" to "project or an error class".
   Owner, 2026-10-11: "Keep the logs with user nulled".  Deleting an account
   deletes its agents (``agents.owner_id`` CASCADE), and that used to delete
   every call those agents had made — the one trace of a deleted account that
   was not kept.  No row changes in this step.
4. ``users.totp_last_step`` (bigint, NULL): the time step of the last
   authenticator code the account had accepted, so a code is refused the
   second time.  NULL until the account next signs in with a code.

**Downgrade.**

4. ``users.totp_last_step`` is dropped (a code can be replayed within its
   window again, as before).
3. The old check comes back, and it cannot hold a call whose agent is gone:
   those rows (``agent_id IS NULL`` with no ``error_class``) are DELETED
   first — which is what the old CASCADE would have done to them when their
   agent was deleted.  Then ``agent_id`` is CASCADE again.
2. The four indexes are dropped.
1. ``uq_subnets_scope_cidr`` is dropped and ``ix_subnets_cidr`` rebuilt.
   Subnets merged by the upgrade are NOT split again: which rows were
   duplicates is not recorded.

Restore from a backup to go back with the merged subnet rows or the deleted
call rows.

Revision ID: a4f8c2e6b0d1
Revises: c2a7e5f1d9b3
"""
import sqlalchemy as sa
from alembic import op

revision = "a4f8c2e6b0d1"
down_revision = "c2a7e5f1d9b3"
branch_labels = None
depends_on = None


def _refuse(message: str) -> None:
    raise RuntimeError(f"review 2026-10-10 schema (a4f8c2e6b0d1): {message}")


# ---------------------------------------------------------------------------
# 1. one row per (scope, CIDR)
# ---------------------------------------------------------------------------

_NON_EMPTY_DESCRIPTION = "nullif(btrim(description), '')"
_NON_EMPTY_SITE = "nullif(btrim(site), '')"


def _up_subnets_unique() -> None:
    conn = op.get_bind()

    # Duplicates that say different things: not merged by guess.
    clashes = conn.execute(sa.text(f"""
        SELECT scope_id, cidr, array_agg(id ORDER BY id) AS ids,
               count(DISTINCT {_NON_EMPTY_DESCRIPTION}) AS descriptions,
               count(DISTINCT {_NON_EMPTY_SITE}) AS sites,
               count(DISTINCT site_id) AS site_ids
          FROM subnets
         GROUP BY scope_id, cidr
        HAVING count(*) > 1
           AND (count(DISTINCT {_NON_EMPTY_DESCRIPTION}) > 1
                OR count(DISTINCT {_NON_EMPTY_SITE}) > 1
                OR count(DISTINCT site_id) > 1)
         ORDER BY scope_id, cidr
    """)).fetchall()
    if clashes:
        def _what(row) -> str:
            differs = []
            if row.descriptions > 1:
                differs.append("descriptions")
            if row.sites > 1 or row.site_ids > 1:
                differs.append("sites")
            return f"scope {row.scope_id}: {row.cidr} (subnet ids {list(row.ids)}; different {' and '.join(differs)})"

        listed = "; ".join(_what(r) for r in clashes[:20])
        _refuse(
            f"{len(clashes)} CIDR(s) are stored more than once in one scope with different "
            f"descriptions or sites ({listed}{' …' if len(clashes) > 20 else ''}). Delete or edit one "
            "of each on the running version (Scope page) so that they agree, and upgrade again. "
            "Nothing was changed."
        )

    # (row to remove, row kept) for every row that is not the first of its
    # (scope, CIDR).  Empty on a database with no duplicate: the statements
    # below then touch nothing.
    conn.execute(sa.text("""
        CREATE TEMPORARY TABLE _subnet_merge ON COMMIT DROP AS
        SELECT s.id AS gone, k.kept
          FROM subnets s
          JOIN (SELECT scope_id, cidr, min(id) AS kept
                  FROM subnets GROUP BY scope_id, cidr HAVING count(*) > 1) k
            ON k.scope_id = s.scope_id AND k.cidr = s.cidr
         WHERE s.id <> k.kept
    """))

    # A value the kept row lacks, taken from a duplicate that has it.  The
    # refusal above guarantees at most one distinct non-empty value per group.
    conn.execute(sa.text("""
        UPDATE subnets k
           SET description = d.description
          FROM (SELECT DISTINCT ON (m.kept) m.kept, s.description
                  FROM _subnet_merge m JOIN subnets s ON s.id = m.gone
                 WHERE nullif(btrim(s.description), '') IS NOT NULL
                 ORDER BY m.kept, s.id) d
         WHERE k.id = d.kept AND nullif(btrim(k.description), '') IS NULL
    """))
    conn.execute(sa.text("""
        UPDATE subnets k
           SET site = coalesce(nullif(btrim(k.site), ''), d.site),
               site_id = coalesce(k.site_id, d.site_id)
          FROM (SELECT m.kept,
                       max(nullif(btrim(s.site), '')) AS site,
                       max(s.site_id) AS site_id
                  FROM _subnet_merge m JOIN subnets s ON s.id = m.gone
                 GROUP BY m.kept) d
         WHERE k.id = d.kept
           AND (nullif(btrim(k.site), '') IS NULL OR k.site_id IS NULL)
    """))

    # Carry the mappings and the labels over.  INSERT … ON CONFLICT rather
    # than re-pointing: three rows of one CIDR may all map one host, and two
    # re-pointed rows would collide with each other.  The originals go with
    # their subnet below (ON DELETE CASCADE).
    conn.execute(sa.text("""
        INSERT INTO host_subnet_mappings (host_id, subnet_id, created_at)
        SELECT h.host_id, m.kept, min(h.created_at)
          FROM host_subnet_mappings h JOIN _subnet_merge m ON m.gone = h.subnet_id
         GROUP BY h.host_id, m.kept
        ON CONFLICT (host_id, subnet_id) DO NOTHING
    """))
    conn.execute(sa.text("""
        INSERT INTO subnet_label_assignments (subnet_id, label_id, created_by_id, created_at)
        SELECT DISTINCT ON (m.kept, a.label_id) m.kept, a.label_id, a.created_by_id, a.created_at
          FROM subnet_label_assignments a JOIN _subnet_merge m ON m.gone = a.subnet_id
         ORDER BY m.kept, a.label_id, a.id
        ON CONFLICT (subnet_id, label_id) DO NOTHING
    """))
    conn.execute(sa.text("DELETE FROM subnets s USING _subnet_merge m WHERE s.id = m.gone"))

    op.create_unique_constraint("uq_subnets_scope_cidr", "subnets", ["scope_id", "cidr"])
    op.execute("DROP INDEX IF EXISTS ix_subnets_cidr")


def _down_subnets_unique() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_subnets_cidr ON subnets (cidr)")
    op.drop_constraint("uq_subnets_scope_cidr", "subnets", type_="unique")


# ---------------------------------------------------------------------------
# 2. the index an ON DELETE SET NULL needs on the child column
# ---------------------------------------------------------------------------

_FK_INDEXES = (
    ("ix_hosts_v2_last_updated_scan_id", "hosts_v2", "last_updated_scan_id"),
    ("ix_ports_v2_last_updated_scan_id", "ports_v2", "last_updated_scan_id"),
    ("ix_finding_hosts_port_id", "finding_hosts", "port_id"),
    ("ix_agent_api_calls_api_key_id", "agent_api_calls", "api_key_id"),
)


def _up_fk_indexes() -> None:
    for name, table, column in _FK_INDEXES:
        op.create_index(name, table, [column], unique=False)


def _down_fk_indexes() -> None:
    for name, table, _column in reversed(_FK_INDEXES):
        op.drop_index(name, table_name=table)


# ---------------------------------------------------------------------------
# 3. the call log outlives its agent
# ---------------------------------------------------------------------------

_CHECK = "ck_agent_api_calls_attribution_or_error"
_AGENT_FK = "agent_api_calls_agent_id_fkey"


def _drop_agent_fk() -> None:
    """Drop the foreign key on ``agent_api_calls.agent_id`` whatever it is
    called (the baseline did not name it; Postgres did)."""
    conn = op.get_bind()
    names = [r[0] for r in conn.execute(sa.text("""
        SELECT c.conname
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.contype = 'f' AND c.conrelid = 'agent_api_calls'::regclass
           AND array_length(c.conkey, 1) = 1 AND a.attname = 'agent_id'
    """))]
    for name in names:
        op.drop_constraint(name, "agent_api_calls", type_="foreignkey")


def _up_call_log_outlives_agent() -> None:
    _drop_agent_fk()
    op.create_foreign_key(
        _AGENT_FK, "agent_api_calls", "agents", ["agent_id"], ["id"], ondelete="SET NULL",
    )
    op.drop_constraint(_CHECK, "agent_api_calls", type_="check")
    op.create_check_constraint(
        _CHECK, "agent_api_calls", "project_id IS NOT NULL OR error_class IS NOT NULL",
    )


def _down_call_log_outlives_agent() -> None:
    # The old check cannot hold a call whose agent is gone; the old CASCADE
    # would have deleted it with the agent.
    op.execute("DELETE FROM agent_api_calls WHERE agent_id IS NULL AND error_class IS NULL")
    op.drop_constraint(_CHECK, "agent_api_calls", type_="check")
    op.create_check_constraint(
        _CHECK, "agent_api_calls",
        "(agent_id IS NOT NULL AND project_id IS NOT NULL) OR error_class IS NOT NULL",
    )
    _drop_agent_fk()
    op.create_foreign_key(
        _AGENT_FK, "agent_api_calls", "agents", ["agent_id"], ["id"], ondelete="CASCADE",
    )


# ---------------------------------------------------------------------------
# 4. a TOTP code works once
# ---------------------------------------------------------------------------

def _up_totp_last_step() -> None:
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))


def _down_totp_last_step() -> None:
    op.drop_column("users", "totp_last_step")


# (upgrade, downgrade) in upgrade order; the downgrade runs them reversed.
_STEPS = (
    (_up_subnets_unique, _down_subnets_unique),
    (_up_fk_indexes, _down_fk_indexes),
    (_up_call_log_outlives_agent, _down_call_log_outlives_agent),
    (_up_totp_last_step, _down_totp_last_step),
)


def upgrade() -> None:
    for up, _down in _STEPS:
        up()


def downgrade() -> None:
    for _up, down in reversed(_STEPS):
        down()
