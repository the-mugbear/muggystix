#!/usr/bin/env python3
"""Merge scanner findings that share one issue, so the upgrade across v2.448.0 can run.

Revision ``b2e5a8c1d4f6`` (one scanner finding per issue) refuses to run while a
project has two scanner findings with the same ``dedup_key``, and names them:

    project 1: findings #132, #155, #192 — issue check:tls_cert_expired

This script merges each such group into ONE finding.  Dry run unless --apply.

    docker compose exec backend python scripts/merge_duplicate_scanner_findings.py
    docker compose exec backend python scripts/merge_duplicate_scanner_findings.py --keep 155 --apply

It works on the schema the refused upgrade leaves in place (the upgrade rolled
back, so the database is at the revision it started from) and on any later
one: it uses plain SQL and reads the foreign keys from the database's own
catalog, never the application's models.  It does not import ``app.main``, so
it starts no migration.  The backend may be retrying the upgrade in a loop
while it runs: the script holds the migration lock for its whole run, so
those retries wait for it instead of taking schema locks under it.

What a merge does, per group, in one transaction for the whole run:

* **keeps one finding** — by default the one furthest along (confirmed, then
  accepted risk, remediated, retest, open, false positive), then the one with
  report text, then the one with the most endpoints, then the oldest.
  ``--keep ID`` (repeatable) names the finding to keep for the group that
  contains it;
* **moves the others' endpoints** onto it; an endpoint the kept finding
  already has (same host and name) keeps the kept finding's state, and
  whatever pointed at the other row (evidence, proposals) is re-pointed;
* **moves their scanner rows, comments, status history, evidence and
  proposals** onto it — every table with a foreign key to ``findings``;
* **fills the kept finding's EMPTY report-text fields** from the others
  (oldest first); text the kept finding already has is never overwritten;
* **deletes the others**.

Nothing authored is lost silently: before anything changes, every finding of
every group is written, whole, to a JSON file under ``uploads/`` (the path is
printed), and the dry run shows what each finding holds so the operator can
choose with ``--keep``.

Then start the new build again (``./scripts/deploy.sh`` option 1): the upgrade
re-runs from the start and finds no duplicate.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

#: The migration's own predicate (``b2e5a8c1d4f6._PREDICATE``) — a test pins
#: the two to the same text.
PREDICATE = "source = 'scanner' AND dedup_key IS NOT NULL AND substr(dedup_key, 1, 4) <> 'row:'"

_STATUS_RANK = {
    "confirmed": 0, "accepted_risk": 1, "remediated": 2, "retest": 3, "open": 4, "false_positive": 5,
}
#: Report text a finding carries; copied onto the kept finding only where its
#: own field is empty.  Columns absent at this schema revision are skipped.
_TEXT_FIELDS = (
    "description", "impact", "recommendation", "references", "steps_to_reproduce",
    "cvss_vector", "cvss_score",
)


def _columns(conn, table):
    return {
        r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :t"), {"t": table})
    }


def _foreign_keys_to(conn, table):
    """(table, column) of every single-column foreign key that references
    ``table.id`` — read from the catalog, so a table added by a later
    revision is handled without changing this script."""
    return [
        (r[0], r[1]) for r in conn.execute(text("""
            SELECT src.relname, att.attname
              FROM pg_constraint con
              JOIN pg_class src ON src.oid = con.conrelid
              JOIN pg_class dst ON dst.oid = con.confrelid
              JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
             WHERE con.contype = 'f' AND dst.relname = :t AND array_length(con.conkey, 1) = 1
             ORDER BY src.relname, att.attname"""), {"t": table})
    ]


def duplicate_groups(conn, project_id=None):
    """Groups of scanner findings sharing (project, dedup_key), each a list of
    finding ids in id order."""
    where = PREDICATE + (" AND project_id = :project" if project_id is not None else "")
    params = {"project": project_id} if project_id is not None else {}
    return [
        {"project_id": r[0], "dedup_key": r[1], "ids": list(r[2])}
        for r in conn.execute(text(
            "SELECT project_id, dedup_key, array_agg(id ORDER BY id) FROM findings "
            f"WHERE {where} GROUP BY project_id, dedup_key HAVING count(*) > 1 "
            "ORDER BY project_id, dedup_key"), params)
    ]


def describe(conn, finding_ids):
    """What each finding holds — the facts an operator chooses a keeper by."""
    cols = _columns(conn, "findings")
    text_cols = [c for c in _TEXT_FIELDS if c in cols]
    has_text = " OR ".join(f"nullif(trim(f.{c}::text), '') IS NOT NULL" for c in text_cols) or "false"
    owner = "f.owner_id" if "owner_id" in cols else "NULL"
    comments = (
        "(SELECT count(*) FROM annotations a WHERE a.finding_id = f.id)"
        if "finding_id" in _columns(conn, "annotations") else "0"
    )
    rows = conn.execute(text(f"""
        SELECT f.id, f.title, f.severity, f.status, {owner} AS owner_id, ({has_text}) AS has_text,
               (SELECT count(*) FROM finding_hosts h WHERE h.finding_id = f.id) AS endpoints,
               {comments} AS comments
          FROM findings f WHERE f.id = ANY(:ids) ORDER BY f.id"""), {"ids": list(finding_ids)})
    return [dict(r._mapping) for r in rows]


def choose_keeper(facts, keep_ids=()):
    """The finding to keep: one the operator named, else the furthest along."""
    named = [f for f in facts if f["id"] in set(keep_ids)]
    if len(named) > 1:
        raise SystemExit(
            f"--keep names more than one finding of the same group: {[f['id'] for f in named]}")
    if named:
        return named[0]["id"]
    return min(
        facts,
        key=lambda f: (_STATUS_RANK.get(str(f["status"]), 9), not f["has_text"], -int(f["endpoints"]), f["id"]),
    )["id"]


def backup_rows(conn, finding_ids):
    """Every column of the findings, and their endpoints — what the JSON file holds."""
    def rows(sql):
        return [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r._mapping.items()}
            for r in conn.execute(text(sql), {"ids": list(finding_ids)})
        ]
    return {
        "findings": rows("SELECT * FROM findings WHERE id = ANY(:ids) ORDER BY id"),
        "finding_hosts": rows("SELECT * FROM finding_hosts WHERE finding_id = ANY(:ids) ORDER BY id"),
        "finding_vulnerabilities": rows(
            "SELECT * FROM finding_vulnerabilities WHERE finding_id = ANY(:ids) ORDER BY finding_id"),
    }


def merge_group(conn, keeper, others):
    """Fold ``others`` into ``keeper``.  Returns counts of what moved."""
    counts = {"endpoints_moved": 0, "endpoints_already_on_keeper": 0, "scanner_rows_moved": 0,
              "rows_repointed": 0, "text_fields_filled": 0, "findings_deleted": 0}
    host_cols = _columns(conn, "finding_hosts")
    same_name = "k.name_id IS NOT DISTINCT FROM o.name_id" if "name_id" in host_cols else "true"

    # --- endpoints -------------------------------------------------------
    # An endpoint the keeper already has: whatever names the other finding's
    # row is re-pointed at the keeper's, then that row goes.
    twins = conn.execute(text(f"""
        SELECT o.id, k.id FROM finding_hosts o
          JOIN finding_hosts k ON k.finding_id = :keeper AND k.host_id = o.host_id AND {same_name}
         WHERE o.finding_id = ANY(:others)"""), {"keeper": keeper, "others": others}).fetchall()
    endpoint_refs = _foreign_keys_to(conn, "finding_hosts")
    for other_row, keeper_row in twins:
        for table, column in endpoint_refs:
            counts["rows_repointed"] += conn.execute(text(
                f'UPDATE "{table}" SET "{column}" = :to WHERE "{column}" = :frm'),
                {"to": keeper_row, "frm": other_row}).rowcount
        conn.execute(text("DELETE FROM finding_hosts WHERE id = :id"), {"id": other_row})
        counts["endpoints_already_on_keeper"] += 1
    # Two of the OTHERS may carry the same endpoint: keep the lowest row of
    # each (host, name), re-point the rest at it, then move what is left.
    partition = "host_id, name_id" if "name_id" in host_cols else "host_id"
    repeats = conn.execute(text(f"""
        SELECT id, first FROM (
            SELECT id, first_value(id) OVER (PARTITION BY {partition} ORDER BY id) AS first
              FROM finding_hosts WHERE finding_id = ANY(:others)) ranked
         WHERE id <> first"""), {"others": others}).fetchall()
    for other_row, first_row in repeats:
        for table, column in endpoint_refs:
            counts["rows_repointed"] += conn.execute(text(
                f'UPDATE "{table}" SET "{column}" = :to WHERE "{column}" = :frm'),
                {"to": first_row, "frm": other_row}).rowcount
        conn.execute(text("DELETE FROM finding_hosts WHERE id = :id"), {"id": other_row})
    counts["endpoints_moved"] = conn.execute(text(
        "UPDATE finding_hosts SET finding_id = :keeper WHERE finding_id = ANY(:others)"),
        {"keeper": keeper, "others": others}).rowcount

    # --- scanner rows that evidence the finding -----------------------------
    counts["scanner_rows_moved"] = conn.execute(text("""
        INSERT INTO finding_vulnerabilities (finding_id, vuln_id)
        SELECT DISTINCT :keeper, o.vuln_id FROM finding_vulnerabilities o
         WHERE o.finding_id = ANY(:others)
           AND NOT EXISTS (SELECT 1 FROM finding_vulnerabilities k
                            WHERE k.finding_id = :keeper AND k.vuln_id = o.vuln_id)"""),
        {"keeper": keeper, "others": others}).rowcount
    conn.execute(text("DELETE FROM finding_vulnerabilities WHERE finding_id = ANY(:others)"),
                 {"others": others})

    # --- everything else that names a finding ----------------------------------
    for table, column in _foreign_keys_to(conn, "findings"):
        if table in ("finding_hosts", "finding_vulnerabilities"):
            continue
        # A savepoint per table: a uniqueness rule on one of them (a row the
        # keeper already has) leaves that table's rows to go with the finding
        # they belong to, and the merge carries on.
        savepoint = conn.begin_nested()
        try:
            counts["rows_repointed"] += conn.execute(text(
                f'UPDATE "{table}" SET "{column}" = :keeper WHERE "{column}" = ANY(:others)'),
                {"keeper": keeper, "others": others}).rowcount
            savepoint.commit()
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed silently
            savepoint.rollback()
            print(f"    note: {table}.{column} not re-pointed ({type(exc).__name__}); "
                  "those rows go with the deleted findings")

    # --- report text: fill only what the keeper lacks ---------------------------
    cols = _columns(conn, "findings")
    for field in (c for c in _TEXT_FIELDS if c in cols):
        counts["text_fields_filled"] += conn.execute(text(f"""
            UPDATE findings k SET "{field}" = (
                SELECT o."{field}" FROM findings o
                 WHERE o.id = ANY(:others) AND nullif(trim(o."{field}"::text), '') IS NOT NULL
                 ORDER BY o.id LIMIT 1)
             WHERE k.id = :keeper AND nullif(trim(k."{field}"::text), '') IS NULL
               AND EXISTS (SELECT 1 FROM findings o WHERE o.id = ANY(:others)
                            AND nullif(trim(o."{field}"::text), '') IS NOT NULL)"""),
            {"keeper": keeper, "others": others}).rowcount

    counts["findings_deleted"] = conn.execute(
        text("DELETE FROM findings WHERE id = ANY(:others)"), {"others": others}).rowcount
    return counts


def run(conn, *, project_id=None, keep_ids=(), apply=False, backup_dir="/app/uploads", out=print):
    """Describe, and with ``apply`` perform, every merge.  The caller owns the
    transaction: it commits on apply and rolls back on a dry run."""
    groups = duplicate_groups(conn, project_id)
    if not groups:
        out("No issue has more than one scanner finding: nothing to merge.")
        return {"groups": 0, "backup": None}
    plan = []
    for group in groups:
        facts = describe(conn, group["ids"])
        keeper = choose_keeper(facts, keep_ids)
        plan.append((group, facts, keeper))
        out(f"\nproject {group['project_id']} — issue {group['dedup_key'][:120]}")
        for f in facts:
            mark = "KEEP  " if f["id"] == keeper else "merge "
            out(f"  {mark}#{f['id']}  {f['status']:<14} {f['severity']:<8} endpoints={f['endpoints']:<5} "
                f"comments={f['comments']:<3} report_text={'yes' if f['has_text'] else 'no ':<3} "
                f"owner={f['owner_id']}  {str(f['title'])[:70]}")
    unknown = set(keep_ids) - {i for g, _, _ in plan for i in g["ids"]}
    if unknown:
        raise SystemExit(f"--keep names findings that are in no duplicate group: {sorted(unknown)}")

    backup = None
    if apply:
        every = [i for g, _, _ in plan for i in g["ids"]]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        backup = os.path.join(backup_dir, f"merged_scanner_findings_{stamp}.json")
        with open(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
            json.dump(backup_rows(conn, every), fh, indent=1, default=str)
        out(f"\nEvery finding above was written, whole, to {backup}")

    totals = {}
    for group, _facts, keeper in plan:
        others = [i for i in group["ids"] if i != keeper]
        counts = merge_group(conn, keeper, others)
        out(f"  #{keeper} now holds {group['dedup_key'][:80]}: "
            + ", ".join(f"{k}={v}" for k, v in counts.items()))
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
    left = len(duplicate_groups(conn, project_id))
    out(f"\nDuplicate issues left: {left}")
    return {"groups": len(plan), "left": left, "backup": backup, **totals}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=int, default=None, help="only this project id")
    parser.add_argument("--keep", type=int, action="append", default=[],
                        help="the finding to keep for the group that contains it (repeatable)")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()

    from app.db.init import _migration_lock  # the lock every booting process takes before it migrates
    from app.db.session import engine  # the database only — never app.main (no migration starts)

    # Hold the MIGRATION lock for the whole run.  A backend that is failing to
    # start retries the upgrade about once a second, and each attempt takes
    # schema locks on the very tables this script updates: the two deadlocked
    # and PostgreSQL stopped the merge (2026-10-02, the first real run).  With
    # the lock held, every booting process waits — it polls for this lock
    # before it touches the schema — and resumes when the merge is done.
    print("Waiting for the migration lock (a backend retrying the upgrade holds it for about a second at a time)…")
    with _migration_lock(engine), engine.connect() as conn:
        print("Migration lock held: no upgrade runs while the merge does.")
        transaction = conn.begin()
        try:
            result = run(conn, project_id=args.project, keep_ids=args.keep, apply=args.apply)
            if args.apply and result["groups"]:
                transaction.commit()
                print("\nApplied. Start the new build again (./scripts/deploy.sh option 1): the upgrade "
                      "re-runs from the start.")
            else:
                transaction.rollback()
                if result["groups"]:
                    print("\nDry run: nothing was changed. Choose with --keep ID where the default is not "
                          "the finding you want, then add --apply.")
        except BaseException:
            transaction.rollback()
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
