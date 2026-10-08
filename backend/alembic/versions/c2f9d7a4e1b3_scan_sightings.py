"""scan sightings: one row per (thing, scan that reported it)

``scripts_v2``, ``host_scripts_v2``, ``host_attributes`` and
``vulnerabilities`` hold ONE row per thing, overwritten by every scan that
reports it, and name only the scan that first recorded it.  Deleting that scan
therefore had to guess which other scan had also seen the row (the newest scan
with history on its port or host), and a delete path that forgot to guess took
rows a later scan had reported.

* Four tables record every scan that reported a row: ``script_sightings``,
  ``host_script_sightings``, ``host_attribute_sightings``,
  ``vulnerability_sightings`` — ``(thing, scan)`` primary key, both foreign
  keys ``ON DELETE CASCADE``, ``seen_at``, an index on ``scan_id`` (built
  inside the upgrade transaction).
* ``scripts_v2.scan_id``, ``host_scripts_v2.scan_id`` and
  ``host_attributes.scan_id`` become nullable with ``ON DELETE SET NULL``: a
  delete that skips the shared step loses the pointer, never the row.
* Back-fill (``BACKFILL``, set-based): one sighting per existing row for the
  scan it names; for vulnerabilities also ``last_seen_scan_id``; for the other
  three also the scan the old guess would have moved the row to, so existing
  rows survive exactly the deletes they survived before.  One row per script /
  host script / attribute / vulnerability, plus one for each that a second
  scan is known to have seen.

The downgrade deletes the rows whose ``scan_id`` is NULL (in the old schema
they would have been deleted with their scan), restores NOT NULL + CASCADE and
drops the four tables.  Which scans re-observed a row is lost with them.

Revision ID: c2f9d7a4e1b3
Revises: b1e8c6f3d0a2
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "c2f9d7a4e1b3"
down_revision = "b1e8c6f3d0a2"
branch_labels = None
depends_on = None


# (sighting table, its column for the thing, the thing's table)
SIGHTING_TABLES = (
    ("script_sightings", "script_id", "scripts_v2"),
    ("host_script_sightings", "host_script_id", "host_scripts_v2"),
    ("host_attribute_sightings", "host_attribute_id", "host_attributes"),
    ("vulnerability_sightings", "vulnerability_id", "vulnerabilities"),
)

# The three "first recorded by" pointers that stop cascading.
FIRST_POINTERS = (
    ("scripts_v2_scan_id_fkey", "scripts_v2"),
    ("host_scripts_v2_scan_id_fkey", "host_scripts_v2"),
    ("host_attributes_scan_id_fkey", "host_attributes"),
)


def _also_seen_by(sightings, thing_col, things, history, parent, touched_again, seen_at):
    """The old guess, as a sighting: the newest OTHER scan with history on the
    row's port / host, when that scan is later than the row's own (a higher
    id) or the row was touched again after it was written."""
    return f"""
        INSERT INTO {sightings} ({thing_col}, scan_id, seen_at)
        SELECT t.id, guess.scan_id, COALESCE({seen_at}, now())
          FROM {things} AS t
         CROSS JOIN LATERAL (
                SELECT max(h.scan_id) AS scan_id
                  FROM {history} AS h
                 WHERE h.{parent} = t.{parent}
                   AND h.scan_id <> t.scan_id
                   AND (h.scan_id > t.scan_id OR {touched_again})
               ) AS guess
         WHERE t.scan_id IS NOT NULL
           AND guess.scan_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """


# ``vulnerabilities`` and ``host_attributes`` store their times without a zone,
# as UTC.
BACKFILL = (
    """
    INSERT INTO script_sightings (script_id, scan_id, seen_at)
    SELECT id, scan_id, COALESCE(first_seen, now()) FROM scripts_v2 WHERE scan_id IS NOT NULL
    ON CONFLICT DO NOTHING
    """,
    _also_seen_by(
        "script_sightings", "script_id", "scripts_v2", "port_scan_history", "port_id",
        "t.last_seen > t.first_seen", "t.last_seen",
    ),
    """
    INSERT INTO host_script_sightings (host_script_id, scan_id, seen_at)
    SELECT id, scan_id, COALESCE(first_seen, now()) FROM host_scripts_v2 WHERE scan_id IS NOT NULL
    ON CONFLICT DO NOTHING
    """,
    _also_seen_by(
        "host_script_sightings", "host_script_id", "host_scripts_v2", "host_scan_history", "host_id",
        "t.last_seen > t.first_seen", "t.last_seen",
    ),
    """
    INSERT INTO host_attribute_sightings (host_attribute_id, scan_id, seen_at)
    SELECT id, scan_id, COALESCE(first_seen AT TIME ZONE 'UTC', now())
      FROM host_attributes WHERE scan_id IS NOT NULL
    ON CONFLICT DO NOTHING
    """,
    # first_seen / last_seen are two clock reads at insert, a few microseconds
    # apart: "touched again" needs a real gap.
    _also_seen_by(
        "host_attribute_sightings", "host_attribute_id", "host_attributes", "host_scan_history", "host_id",
        "t.last_seen > t.first_seen + interval '1 second'", "t.last_seen AT TIME ZONE 'UTC'",
    ),
    """
    INSERT INTO vulnerability_sightings (vulnerability_id, scan_id, seen_at)
    SELECT id, scan_id, COALESCE(first_seen AT TIME ZONE 'UTC', now())
      FROM vulnerabilities WHERE scan_id IS NOT NULL
    ON CONFLICT DO NOTHING
    """,
    """
    INSERT INTO vulnerability_sightings (vulnerability_id, scan_id, seen_at)
    SELECT id, last_seen_scan_id, COALESCE(last_seen AT TIME ZONE 'UTC', now())
      FROM vulnerabilities
     WHERE last_seen_scan_id IS NOT NULL
       AND last_seen_scan_id IS DISTINCT FROM scan_id
    ON CONFLICT DO NOTHING
    """,
)


def upgrade() -> None:
    for sightings, thing_col, things in SIGHTING_TABLES:
        op.create_table(
            sightings,
            sa.Column(
                thing_col, sa.Integer(),
                sa.ForeignKey(f"{things}.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "scan_id", sa.Integer(),
                sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "seen_at", sa.DateTime(timezone=True),
                server_default=sa.text("now()"), nullable=False,
            ),
            sa.PrimaryKeyConstraint(thing_col, "scan_id"),
        )

    for statement in BACKFILL:
        op.execute(statement)

    for sightings, _thing_col, _things in SIGHTING_TABLES:
        op.create_index(f"ix_{sightings}_scan_id", sightings, ["scan_id"], unique=False)

    for fk_name, table in FIRST_POINTERS:
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.alter_column(table, "scan_id", existing_type=sa.Integer(), nullable=True)
        op.create_foreign_key(fk_name, table, "scans", ["scan_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    for fk_name, table in FIRST_POINTERS:
        # In the schema being restored these rows were deleted with their scan.
        op.execute(f"DELETE FROM {table} WHERE scan_id IS NULL")
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.alter_column(table, "scan_id", existing_type=sa.Integer(), nullable=False)
        op.create_foreign_key(fk_name, table, "scans", ["scan_id"], ["id"], ondelete="CASCADE")

    for sightings, _thing_col, _things in reversed(SIGHTING_TABLES):
        op.drop_index(f"ix_{sightings}_scan_id", table_name=sightings)
        op.drop_table(sightings)
