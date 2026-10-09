#!/usr/bin/env python3
"""Give a project findings owned by SEVERAL people (local development only).

Run inside the backend container (scripts/ is bind-mounted at /app/scripts):

    docker compose exec backend python scripts/seed_finding_owners.py --project 3
    docker compose exec backend python scripts/seed_finding_owners.py --project 3 --dry-run

Why: on a real deployment several people promote findings, and whoever
promotes a finding owns it.  The demo seed gives every owned finding to ONE
account, so nothing here could show the Findings page's Owner filter listing
teammates (reported from the remote deployment, 2026-10-09).

What it does, in one transaction:

  * makes three member accounts if they do not exist — ``owner-ana``
    (Ana Ruiz), ``owner-ben`` (Ben Okafor) and ``owner-cy`` (no full name, so
    a row and the filter fall back to the username) — and makes each a project
    ANALYST;
  * hands them the project's UNOWNED findings in turn (ana, ben, cy, ana, …),
    in id order, leaving every third one unowned so that "Unowned" still
    lists something.  A finding that already has an owner is never touched.

The accounts are placeholders that own things: each gets a random password
that is not printed or stored anywhere, so nobody signs in as them (reset one
from System settings if a second sign-in is wanted).  Idempotent: a re-run
reuses the accounts, and hands out nothing more once they own a finding in
the project (so it never eats into what it left unowned).
"""
from __future__ import annotations

import argparse
import secrets
import sys

sys.path.insert(0, "/app")

from app.core.security import get_password_hash  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.db.models_auth import User, UserRole  # noqa: E402
from app.db.models_findings import Finding  # noqa: E402
from app.db.models_project import Project, ProjectMembership, ProjectRole  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

OWNERS = [
    ("owner-ana", "Ana Ruiz"),
    ("owner-ben", "Ben Okafor"),
    ("owner-cy", None),
]


def _ensure_owner(db, project: Project, username: str, full_name: str | None) -> tuple[User, bool]:
    user = db.query(User).filter(User.username == username).first()
    created = user is None
    if user is None:
        user = User(
            username=username, full_name=full_name, email=f"{username}@seed.invalid",
            hashed_password=get_password_hash(secrets.token_urlsafe(24)), role=UserRole.MEMBER.value,
            is_active=True, is_verified=True, must_change_password=True,
        )
        db.add(user)
        db.flush()
    membership = db.query(ProjectMembership).filter_by(project_id=project.id, user_id=user.id).first()
    if membership is None:
        db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=ProjectRole.ANALYST.value))
        db.flush()
    return user, created


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=int, required=True, help="the project's id")
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == args.project).first()
        if project is None:
            print(f"No project with id {args.project}.")
            return 1

        owners = []
        for username, full_name in OWNERS:
            user, created = _ensure_owner(db, project, username, full_name)
            owners.append(user)
            print(f"  {'created' if created else 'reused '} {username} (user {user.id}) — analyst on '{project.name}'")

        already = (
            db.query(Finding)
            .filter(Finding.project_id == project.id, Finding.owner_id.in_([u.id for u in owners]))
            .count()
        )
        if already:
            # A second run must not keep eating into what was left unowned.
            print(f"  These accounts already own {already} finding(s) here: nothing more is handed out.")
            db.rollback() if args.dry_run else db.commit()
            return 0

        unowned = (
            db.query(Finding)
            .filter(Finding.project_id == project.id, Finding.owner_id.is_(None))
            .order_by(Finding.id)
            .all()
        )
        given = {user.username: 0 for user in owners}
        left = 0
        turn = 0
        for index, finding in enumerate(unowned):
            if index % 3 == 2:          # every third stays unowned
                left += 1
                continue
            owner = owners[turn % len(owners)]
            turn += 1
            finding.owner_id = owner.id
            given[owner.username] += 1

        summary = ", ".join(f"{name} {count}" for name, count in given.items())
        print(f"  {len(unowned)} unowned finding(s): {summary}; {left} left unowned.")

        if args.dry_run:
            db.rollback()
            print("Dry run: nothing was changed.")
        else:
            db.commit()
            print("Done.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
