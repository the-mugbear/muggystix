"""client reports: a draft's details follow the project unless it overrides them

Owner, 2026-10-14: "Every detail follows the project unless the report
overrides it."  Until now a new draft COPIED the project's report defaults
(client, classification, engagement type, team, distribution, system
description, the three target lists) into ``reports.settings``; only the
author and the disclaimer followed.  From this revision a draft's
``settings`` holds ONLY the details it overrides; a key absent follows the
project's ``report_profiles`` row (author and disclaimer: then the
installation's ``report_defaults``; the team: then the project's analysts and
admins).  A list key present — even ``[]`` — is an override.

**Upgrade.**  Data only, DRAFT reports only (``status = 'draft'``); issued
and superseded reports are untouched (they print their frozen dataset).  For
each draft, each detail key is REMOVED from ``settings`` when:

* it is a text that is null or blank (an empty text is not an override: it
  follows the project now — for the author and disclaimer that was already
  so; for the other texts a draft whose field was left EMPTY while the
  project has a value now prints the project's value);
* it is a text equal (after trimming) to what the draft would follow — the
  project's value; for the author and disclaimer, the project's else the
  installation's;
* it is the distribution list and equals the project's (none = ``[]``);
* it is the team and equals the project's saved team, or — the project
  saving none — the project's active analysts and admins as the page builds
  them (admins first as "Engagement lead", then analysts as "Tester", by name;
  each entry ``user_id`` / ``name`` (full name, else username) / ``role`` /
  ``email``).  An entry differing in any of the four keeps the whole list as
  an override.

Every other key stays: keeping is safe — it only means "override", and the
draft prints exactly what it printed before.  Removing a key whose value
equals the followed one leaves what the draft prints unchanged at the moment
of the upgrade.  No row is touched outside ``reports.settings``.

**Downgrade.**  Approximate.  Each draft gets back a value for every key it
does not override, copied from the project as it stands: the profile's texts,
its distribution (or ``[]``), its team (or the analysts and admins); the
author and disclaimer are written null (the old meaning of "inherited").
What each draft prints at that moment is unchanged, but the drafts no longer
follow later changes to the project's defaults; a text dropped above because
it was blank comes back as the project's value, not blank.

One transaction; a failure leaves the database at ``e8c4a6f0b2d7``.

Revision ID: f3a9c1e7d5b2
Revises: e8c4a6f0b2d7
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "f3a9c1e7d5b2"
down_revision = "e8c4a6f0b2d7"
branch_labels = None
depends_on = None

TEXT_KEYS = (
    "client_name", "classification", "engagement_type", "system_description",
    "applications", "thick_clients", "other_targets", "author_name", "disclaimer",
)
LIST_KEYS = ("testers", "distribution")
INSTALLATION_KEYS = ("author_name", "disclaimer")
# The project roles that make someone part of the assessment team by default,
# and the role line they start with — frozen here as of this revision
# (``client_report_service.TEAM_ROLES``).
TEAM_ROLES = {"admin": "Engagement lead", "analyst": "Tester"}


def _clean(value):
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _loads(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _testers(entries):
    """A team as comparable rows: (user_id, name, role, email)."""
    return [
        (e.get("user_id"), _clean(e.get("name")), _clean(e.get("role")), _clean(e.get("email")))
        for e in (entries or []) if isinstance(e, dict)
    ]


def _recipients(entries):
    return [(_clean(e.get("name")), _clean(e.get("email"))) for e in (entries or []) if isinstance(e, dict)]


def _team(conn, project_id):
    """The project's active analysts and admins, as the page lists them."""
    rows = conn.execute(sa.text(
        "SELECT u.id, u.full_name, u.username, u.email, m.role FROM users u "
        "JOIN project_memberships m ON m.user_id = u.id "
        "WHERE m.project_id = :p AND u.is_active IS TRUE"
    ), {"p": project_id}).all()
    team = []
    for uid, full_name, username, email, role in rows:
        if role not in TEAM_ROLES:
            continue
        team.append((0 if role == "admin" else 1, {
            "user_id": uid, "name": full_name or username, "role": TEAM_ROLES[role], "email": email or None,
        }))
    team.sort(key=lambda t: (t[0], (t[1]["name"] or "").lower()))
    return [entry for _, entry in team]


def _project(conn, project_id, installation):
    """What a draft of this project follows, per key (the team asked lazily)."""
    row = conn.execute(sa.text(
        "SELECT client_name, classification, engagement_type, system_description, applications, "
        "thick_clients, other_targets, author_name, disclaimer, testers, distribution "
        "FROM report_profiles WHERE project_id = :p"
    ), {"p": project_id}).mappings().first()
    row = dict(row) if row is not None else {}
    follows = {key: _clean(row.get(key)) for key in TEXT_KEYS}
    for key in INSTALLATION_KEYS:
        follows[key] = follows[key] or installation.get(key)
    follows["distribution"] = [e for e in (_loads(row.get("distribution")) or []) if isinstance(e, dict)]
    follows["testers"] = [e for e in (_loads(row.get("testers")) or []) if isinstance(e, dict)]
    return follows


def _drafts(conn):
    return conn.execute(sa.text(
        "SELECT id, project_id, settings FROM reports WHERE status = 'draft' ORDER BY id"
    )).all()


def _write(conn, report_id, settings):
    conn.execute(
        sa.text("UPDATE reports SET settings = CAST(:s AS json) WHERE id = :id"),
        {"s": json.dumps(settings), "id": report_id},
    )


def upgrade() -> None:
    conn = op.get_bind()
    installation = {
        key: _clean(text) for key, text in conn.execute(sa.text("SELECT key, text FROM report_defaults")).all()
    }
    projects, teams = {}, {}
    for report_id, project_id, raw in _drafts(conn):
        settings = _loads(raw)
        if not isinstance(settings, dict):
            continue
        if project_id not in projects:
            projects[project_id] = _project(conn, project_id, installation)
        follows = projects[project_id]
        kept = dict(settings)
        for key in TEXT_KEYS:
            if key in kept and (_clean(kept[key]) is None or _clean(kept[key]) == follows[key]):
                del kept[key]
        if "distribution" in kept:
            value = kept["distribution"]
            if value is None or (isinstance(value, list) and _recipients(value) == _recipients(follows["distribution"])):
                del kept["distribution"]
        if "testers" in kept:
            value = kept["testers"]
            if value is None:
                del kept["testers"]
            elif isinstance(value, list):
                followed_team = follows["testers"]
                if not followed_team:
                    if project_id not in teams:
                        teams[project_id] = _team(conn, project_id)
                    followed_team = teams[project_id]
                if _testers(value) == _testers(followed_team):
                    del kept["testers"]
        if kept != settings:
            _write(conn, report_id, kept)


def downgrade() -> None:
    conn = op.get_bind()
    projects, teams = {}, {}
    for report_id, project_id, raw in _drafts(conn):
        settings = _loads(raw)
        settings = dict(settings) if isinstance(settings, dict) else {}
        if project_id not in projects:
            # The old meaning of an empty author / disclaimer was "inherited":
            # they are not copied, so no installation value is read.
            projects[project_id] = _project(conn, project_id, {})
        follows = projects[project_id]
        restored = dict(settings)
        for key in TEXT_KEYS:
            if key not in restored:
                restored[key] = None if key in INSTALLATION_KEYS else follows[key]
        if "distribution" not in restored:
            restored["distribution"] = follows["distribution"]
        if "testers" not in restored:
            team = follows["testers"]
            if not team:
                if project_id not in teams:
                    teams[project_id] = _team(conn, project_id)
                team = teams[project_id]
            restored["testers"] = team
        if restored != settings or raw is None:
            _write(conn, report_id, restored)
