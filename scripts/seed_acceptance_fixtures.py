#!/usr/bin/env python3
"""Prepare a project for an MCP acceptance run (documentation/MCP_ACCEPTANCE_QUESTIONS.md).

Run inside the backend container (scripts/ is bind-mounted at /app/scripts):

    docker compose exec backend python scripts/seed_acceptance_fixtures.py
    docker compose exec backend python scripts/seed_acceptance_fixtures.py --project 3

Adds what the acceptance questions need beyond the demo seeds, through the
REAL routes (the running API on localhost:8000), as real project members:

  * Four accounts — ``acc-lead`` (project admin), ``acc-analyst``,
    ``acc-auditor``, ``acc-viewer`` — and an agent key for each: ``admin``
    (the lead's — remediation writes need a project admin), ``analyst``,
    ``auditor`` and ``viewer``.  No UI entry point lets a viewer START a
    session (assist start needs auditor); a viewer's key exists when an
    operator is demoted after starting one, because the key carries its
    operator's role re-read on every call.  The viewer key is made exactly
    that way.
  * A host note thread with replies, an @mention and an image attachment,
    marked a handoff and pinned.
  * A finding comment thread with a report image, a status change with a
    justification, and one endpoint marked remediated.
  * An ISSUED client report (the report worker renders its files).
  * A NetExec import with lines the parser does not interpret, processed by
    the ingestion worker.
  * Work that waits on the ANALYST, so its session's workbench is not empty
    and a preview can be told from the whole list: 18 findings it owns that
    need it (10 under investigation = "decide", 8 confirmed with no report
    text = "write" — more than the workbench's 15-row preview), a host it has
    in review, a test assigned to it, and a test on the host in review.
  * A host test proposed by ANOTHER person (the lead), for an agent to work.
  * A SECOND project ("Acceptance — other project") holding a host at the
    same address as one of this project's, a scope, a finding, an import job
    and an attachment: the ids a cross-project request must be refused.
    Created as the first active global admin, through the same routes.
  * Remediation records on three findings on hosts — ONLY where the
    installation has remediation tracking on.  The seed never turns it on
    (that is an installation-wide switch a global admin owns, in System
    settings); where it is off it says so, and every remediation route
    answers 404.

The accounts' passwords and TOTP secrets, the four agent keys and the fixture
ids are written to ``uploads/acceptance-fixtures.json`` (mode 0600) and
printed.  Re-running reuses the accounts, mints fresh keys, and skips fixtures
already present (each is marked ``[acceptance seed]``).  Local development
only.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import struct
import sys
import time
import zlib
from datetime import datetime, timezone

sys.path.insert(0, "/app")

import httpx  # noqa: E402
import pyotp  # noqa: E402

from app.core.security import create_access_token, create_session, get_password_hash, verify_token  # noqa: E402
from app.db import model_registry  # noqa: E402,F401
from app.db import models  # noqa: E402
from app.db.models_agent import AgentSession  # noqa: E402
from app.db.models_auth import User, UserRole  # noqa: E402
from app.db.models_findings import Finding, FindingHost, FindingStatus, FindingStatusHistory  # noqa: E402
from app.db.models_project import Project, ProjectMembership, ProjectRole  # noqa: E402
from app.db.models_reports import Report, ReportStatus  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import totp_service  # noqa: E402

MARK = "[acceptance seed]"
DEFAULT_PROJECT = "Demo — Insights Eval"
ACCOUNTS = (
    ("acc-lead", "Acceptance Lead", ProjectRole.ADMIN),
    ("acc-analyst", "Acceptance Analyst", ProjectRole.ANALYST),
    ("acc-auditor", "Acceptance Auditor", ProjectRole.AUDITOR),
    ("acc-viewer", "Acceptance Viewer", ProjectRole.VIEWER),
)
OUT = "/app/uploads/acceptance-fixtures.json"

# A NetExec run whose last lines no pattern reads: a login, a module result
# and two status lines the parser keeps only as text.
NXC_TEXT = """\
SMB         {ip}      445    {name}          [*] Windows Server 2022 Build 20348 x64 (name:{name}) (domain:demo.local) (signing:False) (SMBv1:False)
SMB         {ip}      445    {name}          [+] demo.local\\svc_backup:Autumn2026! (Pwn3d!)
SMB         {ip}      445    {name}          [+] Dumping LSA secrets
SMB         {ip}      445    {name}          [!] Unexpected response while enumerating sessions
SPOOLER     {ip}      445    {name}          Spooler service enabled
SMB         {ip}      445    {name}          [*] Kerberos relay candidate: ldap signing not enforced
"""


def _png(width: int = 64, height: int = 40) -> bytes:
    """A small valid PNG (a two-colour bar) — evidence without a binary fixture."""
    rows = b"".join(
        b"\x00" + b"".join(
            (b"\x1f\x6f\xb4" if x < width * 2 // 3 else b"\xe4\x57\x2c") for x in range(width)
        )
        for _ in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _ensure_account(db, project: Project, username: str, full_name: str, role: ProjectRole, creds: dict) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        password = secrets.token_urlsafe(18)
        secret = pyotp.random_base32()
        user = User(
            username=username, full_name=full_name, email=f"{username}@acceptance.invalid",
            hashed_password=get_password_hash(password), role=UserRole.MEMBER.value,
            is_active=True, is_verified=True, must_change_password=False,
            totp_secret_encrypted=totp_service.encrypt_secret(secret), totp_enabled=True,
            totp_confirmed_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        creds[username] = {"password": password, "totp_secret": secret}
    membership = db.query(ProjectMembership).filter_by(project_id=project.id, user_id=user.id).first()
    if membership is None:
        db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    else:
        membership.role = role.value
    db.commit()
    return user


def _token(db, user: User) -> str:
    """A real login session for ``user`` (what /auth/login records)."""
    token = create_access_token({"sub": str(user.id), "username": user.username})
    create_session(db, user, verify_token(token)["jti"], user_agent="seed_acceptance_fixtures")
    return token


class Api:
    def __init__(self, base: str, project_id: int, token: str):
        self.c = httpx.Client(base_url=f"{base}/api/v1/projects/{project_id}", timeout=120,
                              headers={"Authorization": f"Bearer {token}"})

    def __call__(self, method: str, path: str, **kw):
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None


def _start_key(api: Api, purpose: str) -> str:
    return api("POST", "/assist/start", json={"purpose": purpose})["api_key"]


#: Findings the analyst owns that need them: more than the workbench's 15-row
#: preview, in both kinds ("decide" = under investigation, "write" = confirmed
#: with no report text).
OWNED_DECIDE, OWNED_WRITE = 10, 8
_SEVERITIES = ("critical", "high", "medium", "low")
OTHER_PROJECT = "Acceptance — other project"


def _seed_analyst_work(db, pid: int, users: dict, lead: Api, analyst: Api) -> dict:
    """Work that waits on ``acc-analyst`` — owned findings of both kinds, a
    host in review, an assigned test, a test on the host in review — and a
    test the LEAD proposed, for another session to work."""
    analyst_user = users["acc-analyst"]
    hosts = (
        db.query(models.Host).filter(models.Host.project_id == pid, models.Host.state == "up")
        .order_by(models.Host.ip_address).offset(32).limit(6).all()
    )
    if len(hosts) < 3:
        raise SystemExit("The project needs more hosts — seed it first (scripts/seed_demo_data.py).")

    wanted = (
        [(f"{MARK} owned, under investigation {i + 1:02d}", "open") for i in range(OWNED_DECIDE)]
        + [(f"{MARK} owned, report text missing {i + 1:02d}", "confirmed") for i in range(OWNED_WRITE)]
    )
    have = {
        title for (title,) in db.query(Finding.title).filter(
            Finding.project_id == pid, Finding.owner_id == analyst_user.id, Finding.title.like(f"{MARK}%"))
    }
    created = 0
    for n, (title, status) in enumerate(wanted):
        if title in have:
            continue
        analyst("POST", "/findings", json={
            "title": title, "severity": _SEVERITIES[n % len(_SEVERITIES)], "status": status,
            "owner_id": analyst_user.id, "host_ids": [hosts[n % len(hosts)].id],
        })
        created += 1
    if created:
        print(f"{created} findings owned by acc-analyst ({OWNED_DECIDE} to decide, {OWNED_WRITE} to write)")

    review_host, other_host = hosts[0], hosts[1]
    db.expire_all()
    if not db.query(models.HostFollow).filter_by(host_id=review_host.id, user_id=analyst_user.id).first():
        analyst("POST", f"/hosts/{review_host.id}/follow", json={"status": "in_review"})
        print(f"{review_host.ip_address}: in review by acc-analyst")

    # Fixed request keys: a re-run returns the stored tests.
    def test(key: str, host_id: int, what: str, **extra) -> dict:
        return {
            "request_key": f"acceptance-seed-{key}", "host_id": host_id, "tool": "manual",
            "description": f"{MARK} {what}", "rationale": "Acceptance fixture.",
            "command": "echo check {ip}", "priority": "medium", "label": "ACCEPTANCE seed", **extra,
        }

    stored = lead("POST", "/host-tests", json={"tests": [
        test("assigned", other_host.id, "a test assigned to acc-analyst", assigned_to_id=analyst_user.id),
        test("in-review", review_host.id, "a test on the host acc-analyst has in review"),
        test("other-person", hosts[2].id, "a test the lead proposed, for another session to work"),
    ]})["items"]
    return {
        "owned_findings": len(wanted), "to_decide": OWNED_DECIDE, "to_write": OWNED_WRITE,
        "host_in_review_id": review_host.id,
        "assigned_test_id": stored[0]["id"], "in_review_test_id": stored[1]["id"],
        "other_persons_test_id": stored[2]["id"],
    }


def _nmap_xml(ip: str) -> bytes:
    kind = "ipv6" if ":" in ip else "ipv4"
    return (
        '<?xml version="1.0"?>\n'
        '<nmaprun scanner="nmap" args="nmap -sV" start="1700000000" version="7.94">\n'
        f'<host><status state="up"/><address addr="{ip}" addrtype="{kind}"/>'
        '<ports><port protocol="tcp" portid="22"><state state="open"/><service name="ssh"/></port></ports>'
        '</host>\n<runstats><finished time="1700000100"/></runstats>\n</nmaprun>\n'
    ).encode()


def _seed_other_project(db, base_url: str, shared_ip: str) -> dict:
    """A second project the acceptance keys are NOT bound to, holding the ids
    a cross-project request must be refused: a host at ``shared_ip`` (the same
    address as a host of the evaluation project), a scope, an import job, a
    finding and an attachment.  Creating a project is a global admin's action,
    so this part runs as the first active global admin."""
    admin_user = (
        db.query(User).filter(User.role == UserRole.ADMIN.value, User.is_active.is_(True))
        .order_by(User.id).first()
    )
    if admin_user is None:
        print("No active global admin: the second project was not created (cross-project steps: not exercised).")
        return {}
    token = _token(db, admin_user)
    other = db.query(Project).filter(Project.name == OTHER_PROJECT).first()
    if other is None:
        r = httpx.post(f"{base_url}/api/v1/projects/", timeout=60,
                       headers={"Authorization": f"Bearer {token}"},
                       json={"name": OTHER_PROJECT, "description": f"{MARK} ids for cross-project refusals"})
        if r.status_code >= 400:
            raise SystemExit(f"POST /projects/ -> {r.status_code}: {r.text[:500]}")
        other = db.get(Project, r.json()["id"])
        print(f"project {other.id} created: {OTHER_PROJECT!r}")
    oid = other.id
    api = Api(base_url, oid, token)

    if not db.query(models.Subnet).join(models.Scope, models.Scope.id == models.Subnet.scope_id).filter(
            models.Scope.project_id == oid).first():
        prefix = "128" if ":" in shared_ip else "32"
        api("POST", "/scopes/upload-subnets",
            files={"file": ("acceptance-scope.txt", io.BytesIO(f"{shared_ip}/{prefix}\n".encode()), "text/plain")})
    job = db.query(models.IngestionJob).filter(
        models.IngestionJob.project_id == oid,
        models.IngestionJob.original_filename == "acceptance-other-project.xml").first()
    if job is None:
        up = api("POST", "/upload/", files={
            "file": ("acceptance-other-project.xml", io.BytesIO(_nmap_xml(shared_ip)), "text/xml")})
        for _ in range(60):
            db.expire_all()
            job = db.get(models.IngestionJob, up["job_id"])
            if job is not None and job.status in ("completed", "failed"):
                break
            time.sleep(1)
        print(f"second project import job {up['job_id']}: {job.status if job else 'not found'}")
    db.expire_all()
    host = db.query(models.Host).filter(models.Host.project_id == oid, models.Host.ip_address == shared_ip).first()
    scope = db.query(models.Scope).filter(models.Scope.project_id == oid).order_by(models.Scope.id).first()
    ids = {
        "project_id": oid, "shared_ip": shared_ip,
        "host_id": host.id if host else None, "scope_id": scope.id if scope else None,
        "job_id": job.id if job else None, "scan_id": job.scan_id if job else None,
        "finding_id": None, "attachment_id": None,
    }
    if host is None:
        print("The second project's import produced no host: its finding and attachment were not created.")
        return ids
    finding = db.query(Finding).filter(Finding.project_id == oid, Finding.title.like(f"{MARK}%")).first()
    if finding is None:
        made = api("POST", "/findings", json={
            "title": f"{MARK} a finding in the other project", "severity": "high",
            "status": "confirmed", "host_ids": [host.id]})
        note = api("POST", f"/findings/{made['id']}/notes", json={"body": f"{MARK} evidence in the other project."})
        api("POST", f"/findings/{made['id']}/notes/{note['id']}/attachments",
            files={"file": ("other-project.png", io.BytesIO(_png()), "image/png")})
        db.expire_all()
        finding = db.get(Finding, made["id"])
    ids["finding_id"] = finding.id
    ids["attachment_id"] = (
        db.query(models.NoteAttachment.id)
        .join(models.Annotation, models.Annotation.id == models.NoteAttachment.annotation_id)
        .filter(models.Annotation.finding_id == finding.id).order_by(models.NoteAttachment.id).limit(1).scalar()
    )
    return ids


def _seed_remediation(db, base_url: str, pid: int, lead_user: User, lead: Api) -> dict:
    """Remediation records on three findings on hosts — only where the
    installation has tracking on.  Never switches it on."""
    from datetime import timedelta

    from app.db.models_remediation import FindingHostRemediation

    r = httpx.get(f"{base_url}/api/v1/remediation-policy", timeout=60,
                  headers={"Authorization": f"Bearer {_token(db, lead_user)}"})
    if r.status_code >= 400 or not r.json().get("enabled"):
        return {"tracking_on": False, "finding_host_ids": []}
    rows = (
        db.query(FindingHost.id).join(Finding, Finding.id == FindingHost.finding_id)
        .filter(Finding.project_id == pid, Finding.status == FindingStatus.CONFIRMED,
                FindingHost.host_status == "open")
        .order_by(FindingHost.finding_id, FindingHost.id).limit(3).all()
    )
    ids = [fh_id for (fh_id,) in rows]
    tracked = {
        fh_id for (fh_id,) in db.query(FindingHostRemediation.finding_host_id)
        .filter(FindingHostRemediation.finding_host_id.in_(ids))
    } if ids else set()
    today = datetime.now(timezone.utc).date()
    plans = (
        # Assigned long enough ago to be overdue under any ordinary policy…
        {"contact_email": "ops@acceptance.invalid", "contact_name": "Acceptance Ops",
         "team": "Infrastructure", "notified_on": (today - timedelta(days=120)).isoformat()},
        # …assigned recently…
        {"contact_email": "ops@acceptance.invalid", "contact_name": "Acceptance Ops",
         "team": "Infrastructure", "notified_on": (today - timedelta(days=2)).isoformat()},
        # …and a contact with no date: no clock runs.
        {"contact_email": "apps@acceptance.invalid", "contact_name": "Acceptance Apps"},
    )
    todo = [{"finding_host_id": fh_id, **plan} for fh_id, plan in zip(ids, plans) if fh_id not in tracked]
    if todo:
        lead("POST", "/remediation/apply", json={"rows": todo})
        print(f"remediation records on {len(todo)} findings on hosts")
    return {"tracking_on": True, "finding_host_ids": ids}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=int, help=f"project id (default: the project named {DEFAULT_PROJECT!r})")
    ap.add_argument("--base-url", default="http://localhost:8000")
    args = ap.parse_args()

    db = SessionLocal()
    project = (
        db.get(Project, args.project) if args.project
        else db.query(Project).filter(Project.name == DEFAULT_PROJECT).first()
    )
    if project is None:
        raise SystemExit("Project not found — seed it first (scripts/seed_demo_data.py) or pass --project.")
    pid = project.id

    creds: dict = {}
    if os.path.exists(OUT):
        with open(OUT) as fh:
            creds = json.load(fh).get("accounts", {})
    users = {u: _ensure_account(db, project, u, n, r, creds) for u, n, r in ACCOUNTS}
    lead, analyst, auditor = (Api(args.base_url, pid, _token(db, users[u])) for u in ("acc-lead", "acc-analyst", "acc-auditor"))

    # --- Agent keys -------------------------------------------------------
    # End the sessions an earlier run started, so a re-run leaves exactly
    # four live keys (the project admin's end route, as the UI does it).
    for (sid,) in db.query(AgentSession.id).filter(
            AgentSession.project_id == pid, AgentSession.purpose.like(f"{MARK}%"),
            AgentSession.status != "ended"):
        lead.c.post(f"/agent-sessions/{sid}/end")
    viewer_user = users["acc-viewer"]
    membership = db.query(ProjectMembership).filter_by(project_id=pid, user_id=viewer_user.id).one()
    membership.role = ProjectRole.AUDITOR.value  # start as auditor…
    db.commit()
    viewer_key = _start_key(Api(args.base_url, pid, _token(db, viewer_user)), f"{MARK} viewer (demoted after start)")
    membership.role = ProjectRole.VIEWER.value  # …then demoted: the key now acts as a viewer
    db.commit()
    keys = {
        # A project admin's key: remediation writes need one.
        "admin": _start_key(lead, f"{MARK} admin"),
        "analyst": _start_key(analyst, f"{MARK} analyst"),
        "auditor": _start_key(auditor, f"{MARK} auditor"),
        "viewer": viewer_key,
    }

    # --- Host note thread --------------------------------------------------
    host = (
        db.query(models.Host).filter(models.Host.project_id == pid, models.Host.state == "up")
        .order_by(models.Host.ip_address).offset(30).first()
    )
    existing = db.query(models.Annotation).filter(
        models.Annotation.host_id == host.id, models.Annotation.body.like(f"{MARK}%")).first()
    if existing is None:
        root = analyst("POST", f"/hosts/{host.id}/notes", json={
            "body": f"{MARK} SMB on this host allows a null session — @eval-ana can you confirm from your box?"})
        reply = lead("POST", f"/hosts/{host.id}/notes", json={
            "body": f"{MARK} Confirmed from the jump host; screenshot attached.", "parent_id": root["id"]})
        analyst("POST", f"/hosts/{host.id}/notes", json={
            "body": f"{MARK} Thanks — I'll write it up.", "parent_id": reply["id"]})
        analyst("PATCH", f"/hosts/{host.id}/notes/{root['id']}", json={
            "note_type": "handoff", "pinned": True})
        lead("POST", f"/hosts/{host.id}/notes/{reply['id']}/attachments",
             files={"file": ("null-session.png", io.BytesIO(_png()), "image/png")})
        print(f"host note thread on {host.ip_address} (root note {root['id']})")

    # --- Finding comments, status change with a reason, endpoint state -----
    critical = (
        db.query(Finding).filter(Finding.project_id == pid, Finding.status == FindingStatus.CONFIRMED,
                                 Finding.severity == "critical")
        .order_by(Finding.id).first()
    )
    if not db.query(models.Annotation).join(Finding, Finding.id == models.Annotation.finding_id).filter(
            Finding.project_id == pid, models.Annotation.body.like(f"{MARK}%")).first():
        comment = analyst("POST", f"/findings/{critical.id}/notes", json={
            "body": f"{MARK} Reproduced with the vendor's PoC; evidence below."})
        answer = lead("POST", f"/findings/{critical.id}/notes", json={
            "body": f"{MARK} Agreed — keep it critical for the report.", "parent_id": comment["id"]})
        att = lead("POST", f"/findings/{critical.id}/notes/{answer['id']}/attachments",
                   files={"file": ("poc-output.png", io.BytesIO(_png(96, 48)), "image/png")})
        lead("PATCH", f"/hosts/notes/attachments/{att['id']}", json={"include_in_report": True})
        print(f"finding {critical.id}: comment thread with a report image")

    already = (
        db.query(FindingStatusHistory).join(Finding, Finding.id == FindingStatusHistory.finding_id)
        .filter(Finding.project_id == pid, FindingStatusHistory.summary.like(f"{MARK}%")).first()
    )
    open_finding = None if already else (
        db.query(Finding).filter(Finding.project_id == pid, Finding.status == FindingStatus.OPEN)
        .order_by(Finding.id).first()
    )
    if open_finding is not None:
        lead("POST", f"/findings/{open_finding.id}/status", json={
            "status": "confirmed", "summary": f"{MARK} Verified by hand on two hosts; not a scanner artefact."})
        print(f"finding {open_finding.id}: open → confirmed with a justification")

    multi = (
        db.query(FindingHost).join(Finding, Finding.id == FindingHost.finding_id)
        .filter(Finding.project_id == pid, Finding.status == FindingStatus.CONFIRMED)
        .order_by(FindingHost.finding_id, FindingHost.id).all()
    )
    by_finding: dict = {}
    for fh in multi:
        by_finding.setdefault(fh.finding_id, []).append(fh)
    spread = next((rows for rows in by_finding.values() if len(rows) >= 2), None)
    if spread and all(r.host_status == "open" for r in spread):
        lead("PATCH", f"/findings/{spread[0].finding_id}/endpoints/{spread[0].id}", json={"host_status": "remediated"})
        print(f"finding {spread[0].finding_id}: one endpoint remediated")

    # --- A finished review that still needs evidence -----------------------
    # (Posture's "still needs evidence"; the DSL's conclusion:needs_evidence.)
    concluded = db.query(models.HostFollow).join(models.Host, models.Host.id == models.HostFollow.host_id).filter(
        models.Host.project_id == pid, models.HostFollow.user_id == users["acc-lead"].id).first()
    if concluded is None:
        target = (
            db.query(models.Host).filter(models.Host.project_id == pid, models.Host.state == "up")
            .order_by(models.Host.ip_address).offset(31).first()
        )
        lead("POST", f"/hosts/{target.id}/follow",
             json={"status": "reviewed", "review_conclusion": "needs_evidence"})
        print(f"{target.ip_address}: reviewed, concluded needs_evidence")

    # --- An issued client report -------------------------------------------
    if not db.query(Report).filter(Report.project_id == pid, Report.status == ReportStatus.ISSUED).first():
        draft = lead("POST", "/client-reports", json={"template": "pentest", "title": f"{MARK} Assessment report"})
        lead("PATCH", f"/client-reports/{draft['id']}", json={
            "executive_summary": "The assessment found exploitable weaknesses in SMB and TLS configuration.",
            "settings": {
                "client_name": "Acceptance Client", "classification": "CONFIDENTIAL",
                "engagement_type": "Internal network assessment",
                "system_description": "The corporate, DMZ and lab networks.",
                "testers": [{"name": "Acceptance Lead", "role": "Lead"}],
                "distribution": [{"name": "Client CISO", "email": "ciso@acceptance.invalid"}],
            },
        })
        issued = lead("POST", f"/client-reports/{draft['id']}/issue")
        print(f"client report {issued['id']} issued as number {issued['number']}")

    # --- A NetExec import with uninterpreted lines -------------------------
    job_id = None
    if not db.query(models.IngestionJob).filter(
            models.IngestionJob.project_id == pid,
            models.IngestionJob.original_filename == "acceptance-nxc-smb.txt").first():
        text = NXC_TEXT.format(ip=host.ip_address, name=(host.hostname or "HOST").split(".")[0].upper()[:15])
        up = analyst("POST", "/upload/", files={"file": ("acceptance-nxc-smb.txt", io.BytesIO(text.encode()), "text/plain")})
        job_id = up["job_id"]
        for _ in range(60):
            db.expire_all()
            job = db.get(models.IngestionJob, job_id)
            if job is not None and job.status in ("completed", "failed"):
                break
            time.sleep(1)
        print(f"NetExec import job {job_id}: {job.status if job else 'not found'}")

    fixtures: dict = {}
    fixtures["analyst_work"] = _seed_analyst_work(db, pid, users, lead, analyst)
    fixtures["other_project"] = _seed_other_project(db, args.base_url, host.ip_address)
    fixtures["remediation"] = _seed_remediation(db, args.base_url, pid, users["acc-lead"], lead)

    data = {"project_id": pid, "keys": keys, "accounts": creds, "fixtures": fixtures,
            "note": "Local acceptance fixtures — keys expire with their sessions."}
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    print(json.dumps({"project_id": pid, "keys": keys, "fixtures": fixtures}, indent=2))
    print(f"accounts, keys and fixture ids written to {OUT} (0600)")
    if not fixtures["remediation"]["tracking_on"]:
        print(
            "Remediation tracking is OFF on this installation: every remediation step of the "
            "acceptance suite answers 404 until a global admin turns it on in System settings "
            "(then run this script again to add the remediation records)."
        )


if __name__ == "__main__":
    main()
