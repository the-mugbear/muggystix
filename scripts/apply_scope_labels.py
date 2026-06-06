#!/usr/bin/env python3
"""One-off: bulk-assign subnet labels from a CSV.

CSV shape: one column holds a scope entry (CIDR or IP), another holds a label
name. One row per (subnet, label) pair — a subnet may appear on several rows to
get several labels.

It matches each CSV CIDR to the subnets already in the target PROJECT's scope
(normalising both sides with ``ipaddress`` so ``10.0.0.0/24`` matches regardless
of whitespace/host-bit formatting), find-or-creates each label (unique per
project), and creates the subnet↔label assignment if it doesn't already exist.
Everything is idempotent, so re-running is safe.

Runs INSIDE the backend container (it uses the app's models + DB session):

    # put your CSV somewhere mounted into the container, e.g. scripts/
    docker compose exec backend python /app/scripts/apply_scope_labels.py \
        --project-id 1 --csv /app/scripts/labels.csv            # dry run (default)

    docker compose exec backend python /app/scripts/apply_scope_labels.py \
        --project-id 1 --csv /app/scripts/labels.csv --apply     # actually write

Defaults: column 0 = CIDR, column 1 = label, comma-delimited, header auto-detected.
Dry run prints exactly what WOULD happen (labels to create, assignments to add,
and any unmatched/oversized rows) and writes nothing until you pass --apply.
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import os
import sys
from collections import defaultdict

# This script lives in /app/scripts; the backend package is at /app. Running it
# by path puts /app/scripts on sys.path (not /app), so add the app root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the app package so every SQLAlchemy model + relationship is registered
# before we query (Subnet → Scope → Project etc. must all be mapped).
import app.main  # noqa: F401,E402  (import side effect: registers models)
from app.db.session import SessionLocal
from app.db import models
from app.db.models_project import Project

LABEL_NAME_MAX = 60  # SubnetLabel.name is String(60)


def normalize(value: str) -> str | None:
    """Return a canonical CIDR string, or None if unparseable.

    Accepts CIDRs (10.0.0.0/24), bare IPs (10.0.0.5 → /32 or /128), with
    surrounding whitespace and non-zero host bits (strict=False)."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        return str(ipaddress.ip_network(v, strict=False))
    except ValueError:
        pass
    try:
        ip = ipaddress.ip_address(v)
        return str(ipaddress.ip_network(f"{ip}/{ip.max_prefixlen}", strict=False))
    except ValueError:
        return None


def looks_like_header(row: list[str], cidr_col: int) -> bool:
    return cidr_col >= len(row) or normalize(row[cidr_col]) is None


def main() -> int:
    ap = argparse.ArgumentParser(description="Bulk-assign subnet labels from a CSV (one-off).")
    ap.add_argument("--project-id", type=int, required=True)
    ap.add_argument("--csv", required=True, help="Path to the CSV (inside the container).")
    ap.add_argument("--cidr-col", type=int, default=0)
    ap.add_argument("--label-col", type=int, default=1)
    ap.add_argument("--delimiter", default=",")
    ap.add_argument("--color", default=None, help="Palette key for any labels created (e.g. blue).")
    ap.add_argument("--apply", action="store_true", help="Write changes. Omit for a dry run.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        project = db.get(Project, args.project_id)
        if project is None:
            print(f"ERROR: project {args.project_id} not found", file=sys.stderr)
            return 2

        # Index the project's subnets by normalized CIDR → [subnet_id, ...]
        # (a CIDR can recur across scopes within one project).
        subnet_rows = (
            db.query(models.Subnet.id, models.Subnet.cidr)
            .join(models.Scope, models.Scope.id == models.Subnet.scope_id)
            .filter(models.Scope.project_id == args.project_id)
            .all()
        )
        by_cidr: dict[str, list[int]] = defaultdict(list)
        for sid, cidr in subnet_rows:
            key = normalize(cidr)
            if key:
                by_cidr[key].append(sid)
        print(f"project {args.project_id}: {len(subnet_rows)} subnets in scope "
              f"({len(by_cidr)} distinct CIDRs)")

        # Existing labels (name → id) and existing assignments set.
        labels = {
            lbl.name: lbl
            for lbl in db.query(models.SubnetLabel).filter(
                models.SubnetLabel.project_id == args.project_id
            ).all()
        }
        existing_assignments = {
            (a.subnet_id, a.label_id)
            for a in db.query(models.SubnetLabelAssignment)
            .join(models.SubnetLabel, models.SubnetLabel.id == models.SubnetLabelAssignment.label_id)
            .filter(models.SubnetLabel.project_id == args.project_id)
            .all()
        }

        labels_to_create: set[str] = set()
        assignments_to_add: set[tuple[str, int]] = set()  # (label_name, subnet_id)
        unmatched: list[str] = []
        oversized: set[str] = set()
        malformed: list[str] = []
        rows = 0

        with open(args.csv, newline="") as fh:
            reader = csv.reader(fh, delimiter=args.delimiter)
            first = True
            for raw in reader:
                if not raw:
                    continue
                if first:
                    first = False
                    if looks_like_header(raw, args.cidr_col):
                        print(f"skipping header row: {raw}")
                        continue
                rows += 1
                if args.cidr_col >= len(raw) or args.label_col >= len(raw):
                    malformed.append(",".join(raw))
                    continue
                key = normalize(raw[args.cidr_col])
                name = raw[args.label_col].strip()
                if key is None:
                    malformed.append(",".join(raw))
                    continue
                if not name:
                    malformed.append(",".join(raw))
                    continue
                if len(name) > LABEL_NAME_MAX:
                    oversized.add(name)
                    continue
                sids = by_cidr.get(key)
                if not sids:
                    unmatched.append(raw[args.cidr_col].strip())
                    continue
                if name not in labels:
                    labels_to_create.add(name)
                for sid in sids:
                    assignments_to_add.add((name, sid))

        # Resolve which assignments are genuinely new (label may not exist yet,
        # so compare by name; existing-by-id checked after labels are created).
        print("\n--- plan ---")
        print(f"  rows read:               {rows}")
        print(f"  labels to create:        {len(labels_to_create)}")
        print(f"  candidate assignments:   {len(assignments_to_add)}")
        print(f"  unmatched CIDRs:         {len(unmatched)}")
        print(f"  malformed rows:          {len(malformed)}")
        print(f"  oversized label names:   {len(oversized)} (>{LABEL_NAME_MAX} chars, skipped)")
        for sample, items in (("unmatched", unmatched), ("malformed", malformed), ("oversized", sorted(oversized))):
            if items:
                print(f"    e.g. {sample}: {items[:5]}{' …' if len(items) > 5 else ''}")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return 0

        # Create missing labels.
        for name in labels_to_create:
            lbl = models.SubnetLabel(project_id=args.project_id, name=name, color=args.color)
            db.add(lbl)
            labels[name] = lbl
        db.flush()  # assigns label ids

        added = 0
        for name, sid in assignments_to_add:
            label_id = labels[name].id
            if (sid, label_id) in existing_assignments:
                continue
            db.add(models.SubnetLabelAssignment(subnet_id=sid, label_id=label_id))
            existing_assignments.add((sid, label_id))
            added += 1

        db.commit()
        print(f"\nAPPLIED — created {len(labels_to_create)} labels, {added} new assignments.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
