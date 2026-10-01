#!/usr/bin/env python3
"""Prepare a project for an MCP acceptance run (documentation/MCP_ACCEPTANCE_QUESTIONS.md).

Run inside the backend container (scripts/ is bind-mounted at /app/scripts):

    docker compose exec backend python scripts/seed_acceptance_fixtures.py
    docker compose exec backend python scripts/seed_acceptance_fixtures.py --project 3

Adds what the acceptance questions need beyond the demo seeds, through the
REAL routes (the running API on localhost:8000), as real project members:

  * Four accounts — ``acc-lead`` (project admin), ``acc-analyst``,
    ``acc-auditor``, ``acc-viewer`` — and an agent key for the analyst, the
    auditor and the viewer.  No UI entry point lets a viewer START a session
    (assist start needs auditor); a viewer's key exists when an operator is
    demoted after starting one, because the key carries its operator's role
    re-read on every call.  The viewer key is made exactly that way.
  * A host note thread with replies, an @mention, an assignee, a due date and
    an image attachment.
  * A finding comment thread with a report image, a status change with a
    justification, and one endpoint marked remediated.
  * An ISSUED client report (the report worker renders its files).
  * A NetExec import with lines the parser does not interpret, processed by
    the ingestion worker.

The accounts' passwords and TOTP secrets and the three agent keys are written
to ``uploads/acceptance-fixtures.json`` (mode 0600) and printed.  Re-running
reuses the accounts, mints fresh keys, and skips fixtures already present
(each is marked ``[acceptance seed]``).  Local development only.
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
from datetime import datetime, timedelta, timezone

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
    # three live keys (the project admin's end route, as the UI does it).
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

    data = {"project_id": pid, "keys": keys, "accounts": creds,
            "note": "Local acceptance fixtures — keys expire with their sessions."}
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    print(json.dumps({"project_id": pid, "keys": keys}, indent=2))
    print(f"accounts and keys written to {OUT} (0600)")


if __name__ == "__main__":
    main()
