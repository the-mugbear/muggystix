"""One scanner finding per issue, enforced by the database (review 2026-10-01 R8).

``FindingService.promote_vulnerability`` and ``dismiss_vulnerability_on_host``
looked a finding up by ``(project_id, dedup_key)`` and inserted when there was
none.  Two concurrent promotions of one issue both found none and inserted two
findings; every later lookup then picked one of them arbitrarily, and the
client report listed the issue twice.

This adds a partial unique index over scanner findings that carry a real issue
key (``row:`` keys identify one scanner row, not an issue, and stay out).

EXISTING DUPLICATES ARE NOT MERGED.  Two findings for one issue each have their
own report text, status, history, comments, endpoints and proposals, and
nothing here can choose between two people's writing — so the upgrade stops
and names them.  Resolve each pair on the version you are upgrading FROM (keep
one finding, add the other's hosts to it, delete the other), then upgrade.  To
look before upgrading::

    SELECT project_id, dedup_key, array_agg(id ORDER BY id)
    FROM findings
    WHERE source = 'scanner' AND dedup_key IS NOT NULL
      AND substr(dedup_key, 1, 4) <> 'row:'
    GROUP BY project_id, dedup_key HAVING count(*) > 1;
"""
import sqlalchemy as sa
from alembic import op

revision = "b2e5a8c1d4f6"
down_revision = "a1d4f7b9c2e3"
branch_labels = None
depends_on = None

_PREDICATE = "source = 'scanner' AND dedup_key IS NOT NULL AND substr(dedup_key, 1, 4) <> 'row:'"
_MAX_LISTED = 50


def upgrade():
    rows = op.get_bind().execute(sa.text(
        "SELECT project_id, dedup_key, array_agg(id ORDER BY id) AS ids "
        f"FROM findings WHERE {_PREDICATE} "
        "GROUP BY project_id, dedup_key HAVING count(*) > 1 "
        "ORDER BY project_id, dedup_key"
    )).fetchall()
    if rows:
        listed = "\n".join(
            f"  project {r.project_id}: findings {', '.join('#%d' % i for i in r.ids)} — issue {r.dedup_key[:120]}"
            for r in rows[:_MAX_LISTED]
        )
        more = f"\n  … and {len(rows) - _MAX_LISTED} more" if len(rows) > _MAX_LISTED else ""
        raise RuntimeError(
            f"{len(rows)} issue(s) have more than one scanner finding, so the one-finding-per-issue "
            "index cannot be created. Nothing was changed. Merge each group into one finding, then "
            "start this build again — either with the script, which works while this build is "
            "failing to start (dry run first; it keeps endpoints, comments, evidence and report "
            "text, and writes every finding to a file before it changes anything):\n"
            "    docker compose exec backend python scripts/merge_duplicate_scanner_findings.py\n"
            "or by hand on the version you are upgrading from: keep one finding of each group "
            "(add the other's hosts to it) and delete the rest. The groups:\n" + listed + more
        )
    op.create_index(
        "uq_finding_scanner_issue", "findings", ["project_id", "dedup_key"], unique=True,
        postgresql_where=sa.text(_PREDICATE),
    )


def downgrade():
    op.drop_index("uq_finding_scanner_issue", table_name="findings")
