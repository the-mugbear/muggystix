#!/usr/bin/env python3
"""Seed a SMALL project where every host is placed on purpose, so a feature or
a change can be evaluated by opening one named host rather than hunting for a
case in random data.

Run inside the backend container (scripts/ is bind-mounted at /app/scripts):

    docker compose exec backend python scripts/seed_eval_scenarios.py
    docker compose exec backend python scripts/seed_eval_scenarios.py --wipe    # rebuild (and reprint the guide)

Different job from its neighbours:
  * seed_demo_data.py     — hundreds of RANDOM hosts, for the insights pages.
  * seed_named_assets.py  — a named-asset scenario added to an existing project.
  * this                  — ~25 hand-placed hosts, one scenario each, plus a
                            printed guide: "open X, expect Y".

Every host's hostname starts with its scenario id (s01-…), so it is findable
with the Hosts search, and the guide printed at the end says what to look at.
It creates its own project and touches nothing else; --wipe deletes only a
project of the same name.

Where a real write path is cheap it is used (the Nessus import, the DNS name
service, subnet correlation), so those fixtures are what ingest would produce.
Screenshots are referenced, not written: opening one reports "not available on
the server", which is itself the state to check.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import textwrap
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from app.db.session import SessionLocal  # noqa: E402
from app.db import models  # noqa: E402
from app.db import model_registry  # noqa: E402,F401  (registers every model module)
from app.db.models import FollowStatus, NoteStatus, OperationsCursor  # noqa: E402
from app.db.models_agent import AgentSession, ExecutionSession, TestPlan, TestPlanEntry  # noqa: E402
from app.db.models_auth import User, UserRole  # noqa: E402
from app.db.models_confidence import ConflictHistory, HostConfidence, NetexecResult  # noqa: E402
from app.db.models_findings import Finding, FindingHost, FindingHostStatus  # noqa: E402
from app.db.models_project import Project  # noqa: E402
from app.db.models_vulnerability import (  # noqa: E402
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)
from app.services import dns_name_service as names  # noqa: E402
from app.services.nessus_integration_service import NessusIntegrationService  # noqa: E402
from app.services.subnet_correlation import SubnetCorrelationService  # noqa: E402

NOW = datetime.now(timezone.utc)
DEFAULT_NAME = "Eval — Scenarios"


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


# ---------------------------------------------------------------------------
# Small builders
# ---------------------------------------------------------------------------

class Ctx:
    def __init__(self, db, project, owner):
        self.db, self.project, self.owner = db, project, owner
        # Plain values: the real Nessus import (s03) commits and expires this
        # session's objects, after which `project.id` would be a detached read.
        self.pid, self.pname = project.id, project.name
        self.guide: list[tuple[str, str, list[str]]] = []
        self.hosts: dict[str, models.Host] = {}
        self.host_count = 0

    def note(self, sid: str, title: str, *lines: str):
        self.guide.append((sid, title, list(lines)))


def scan(c: Ctx, filename: str, tool: str, *, imported: datetime,
         start: datetime | None = None, end: datetime | None = None, time_source: str | None = None):
    s = models.Scan(project_id=c.project.id, filename=filename, scan_type=tool, tool_name=tool,
                    start_time=start.replace(tzinfo=None) if start else None,
                    end_time=end.replace(tzinfo=None) if end else None,
                    time_source=time_source)
    s.created_at = imported
    c.db.add(s)
    c.db.flush()
    return s


def host(c: Ctx, key: str, ip: str, hostname: str | None, sc, *, os_name=None, os_family=None,
         first_seen: datetime | None = None, last_seen: datetime | None = None, **kw):
    first_seen = first_seen or ago(days=20)
    h = models.Host(project_id=c.project.id, ip_address=ip, hostname=hostname, state="up",
                    os_name=os_name, os_family=os_family,
                    first_seen=first_seen, last_seen=last_seen or ago(days=1), **kw)
    c.db.add(h)
    c.db.flush()
    c.db.add(models.HostScanHistory(host_id=h.id, scan_id=sc.id, discovered_at=first_seen))
    c.hosts[key] = h
    return h


def port(c: Ctx, h, sc, number: int, service: str, *, protocol="tcp", state="open",
         product=None, version=None, first_seen: datetime | None = None, extrainfo=None):
    first_seen = first_seen or h.first_seen
    p = models.Port(host_id=h.id, port_number=number, protocol=protocol, state=state,
                    service_name=service, service_product=product, service_version=version,
                    service_extrainfo=extrainfo, is_active=True,
                    first_seen=first_seen, last_seen=h.last_seen)
    c.db.add(p)
    c.db.flush()
    # The real ingest path writes a PortScanHistory row per (port, scan); the
    # per-scan port counts are read from it.
    c.db.add(models.PortScanHistory(port_id=p.id, scan_id=sc.id, state_at_scan=state,
                                    discovered_at=first_seen))
    return p


def vuln(c: Ctx, h, sc, title: str, severity, *, source=VulnerabilitySource.NESSUS, port_obj=None,
         cve=None, exploitable=False, plugin_id=None, created: datetime | None = None,
         description=None, solution=None):
    v = Vulnerability(
        title=title, severity=severity, source=source, host_id=h.id, scan_id=sc.id,
        port_id=port_obj.id if port_obj else None, cve_id=cve, exploitable=exploitable,
        plugin_id=plugin_id,
        description=description or f"{title}: scanner write-up. " * 6,
        solution=solution or "Apply the vendor's fix and re-scan to confirm.",
    )
    # ALWAYS dated. Left to the column default an observation is recorded "now",
    # which puts every fixture inside s07's since-last-visit window (the first
    # build of this script reported 12 new criticals instead of 1).
    v.created_at = (created or h.first_seen).replace(tzinfo=None)
    c.db.add(v)
    c.db.flush()
    return v


def annotation(c: Ctx, h, body: str, *, created: datetime, status=NoteStatus.OPEN, pinned=False,
               parent=None, note_type="observation", assignee=None, due=None):
    # Exactly ONE target per note (ck_annotations_exactly_one_target): the host.
    a = models.Annotation(host_id=h.id, user_id=c.owner.id, body=body,
                          status=status, pinned=pinned, note_type=note_type,
                          parent_id=parent.id if parent else None,
                          thread_root_id=(parent.thread_root_id or parent.id) if parent else None,
                          assignee_id=assignee.id if assignee else None, due_at=due)
    a.created_at = created
    c.db.add(a)
    c.db.flush()
    return a


def follow(c: Ctx, h, status: FollowStatus, *, updated: datetime, conclusion=None, summary=None,
           reviewed_at: datetime | None = None, assigned=False):
    f = models.HostFollow(host_id=h.id, user_id=c.owner.id, status=status,
                          review_conclusion=conclusion, review_summary=summary,
                          reviewed_at=reviewed_at,
                          assigned_at=updated if assigned else None,
                          assigned_by_id=c.owner.id if assigned else None)
    f.updated_at = updated
    c.db.add(f)
    c.db.flush()
    return f


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

AGENT_NOTE = textwrap.dedent("""\
    🤖 **Agent-generated** — eval-agent

    ## AI Assessment — exposed management plane

    **Risk Level:** High
    **Confidence:** Medium
    **Services:** SSH 22; HTTPS 8443

    ### Observations
    - The admin console on 8443 answers 401 with a default realm string.
    - SSH banner is two minor versions behind the vendor's current release.
    - No lockout was observed across five failed authentications.

    ### Recommended Actions
    1. Confirm whether the console is reachable from the user VLAN.
    2. Review the account-lockout policy with the platform owner.
    3. Re-scan after the vendor patch window.
""")


def s01_notes(c: Ctx, sc):
    h = host(c, "s01", "10.77.1.11", "s01-notes.eval.test", sc, os_name="Ubuntu 22.04", os_family="Linux")
    p = port(c, h, sc, 22, "ssh", product="OpenSSH", version="8.9")
    port(c, h, sc, 8443, "https", product="nginx", version="1.24.0")
    vuln(c, h, sc, "OpenSSH outdated", VulnerabilitySeverity.MEDIUM, port_obj=p, plugin_id="s01-1")

    pinned = annotation(c, h, "PINNED — out-of-hours testing only on this host (change window Tue 22:00).",
                        created=ago(days=30), pinned=True, note_type="action")
    old_active = annotation(c, h, "Old thread: is the 8443 console meant to be reachable from the user VLAN?",
                            created=ago(days=25), note_type="question")
    annotation(c, h, "Reply from TODAY — yes, confirmed with the platform owner.", created=ago(hours=1),
               parent=old_active)
    resolved = annotation(c, h, "Resolved long ago: NTP drift explained the certificate warning.",
                          created=ago(days=22), status=NoteStatus.RESOLVED)
    stale_open = annotation(c, h, "Open and never answered: who owns the backup job on this box?",
                            created=ago(days=20), note_type="question")
    agent = annotation(c, h, AGENT_NOTE, created=ago(days=3))
    annotation(c, h, "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 14, created=ago(days=2))
    newest = annotation(c, h, "Newest short note.", created=ago(hours=5))
    follow(c, h, FollowStatus.IN_REVIEW, updated=ago(hours=5), assigned=True)
    c.note("s01", "Notes: preview rules, deep links, long bodies",
           f"/hosts/{h.id} — 7 root threads. Visible without clicking: the PINNED one (oldest), the old "
           "thread that got a reply today, and the newest; the rest are behind 'Show N earlier threads "
           "· M not resolved'.",
           f"Deep link to a HIDDEN thread:  /hosts/{h.id}#note-{stale_open.id}  → it must be revealed and scrolled to.",
           f"Deep link to a hidden RESOLVED thread:  /hosts/{h.id}#note-{resolved.id}",
           "Collapse the Notes section, reload with a #note- link → the section re-opens.",
           f"Notes {agent.id} (agent markdown) and the Lorem one are clamped to 4 lines with 'Show full note'.",
           f"Attach an image from a note's action row (note {newest.id}); the button disables while uploading.")
    return h


def s02_web_history(c: Ctx, scans):
    aug, sep, untimed, late_old = scans["web_aug"], scans["web_sep"], scans["web_untimed"], scans["web_late_old"]
    h = host(c, "s02", "10.77.1.12", "s02-web.eval.test", sep, os_name="Windows Server 2022", os_family="Windows")
    p = port(c, h, sep, 8443, "https", product="Microsoft IIS httpd", version="10.0")
    p80 = port(c, h, sep, 80, "http", product=None, extrainfo="[401] /console (512B)")

    def iface(sc, url, *, status, title, shot=False, source="eyewitness", written: datetime):
        w = models.WebInterface(host_id=h.id, port_id=p.id if ":8443" in url else p80.id, scan_id=sc.id,
                                project_id=c.project.id, source=source, url=url,
                                protocol="https" if url.startswith("https") else "http",
                                port=8443 if ":8443" in url else 80, ip_address=h.ip_address,
                                status_code=status, title=title, server_header="Microsoft-IIS/10.0",
                                content_length=7168,
                                screenshot_path=f"{sc.id}/s02-{status}.png" if shot else None)
        w.first_seen = written
        w.last_seen = written
        c.db.add(w)

    base = f"https://{h.ip_address}:8443"
    # Re-scanned URL: the OLDER observation has the screenshot, the latest has none.
    iface(aug, f"{base}/", status=401, title="Old admin console", shot=True, written=ago(days=44))
    iface(sep, f"{base}/", status=200, title="Admin Console", shot=False, written=ago(days=10))
    iface(aug, f"{base}/login", status=200, title="Management Login", shot=True, written=ago(days=44))
    iface(sep, f"{base}/login", status=200, title="Management Login", shot=True, written=ago(days=10))
    # Only an import time is known for this one.
    iface(untimed, f"http://{h.ip_address}/console", status=401, title="Console", written=ago(days=6))
    # An OLD scan imported AFTER the newer one: its row was written last.
    iface(late_old, f"{base}/status", status=500, title="July state (old scan, imported late)", written=ago(days=2))
    iface(sep, f"{base}/status", status=200, title="September state", written=ago(days=10))
    c.db.flush()
    c.note("s02", "Web interfaces: history, screenshots, what 'latest' means",
           f"/hosts/{h.id} — strip reads '4 web' and the section 'Web interfaces 4' (7 rows, 4 distinct).",
           "'/': shows 'Admin Console' (September); '1 earlier observation (with a screenshot) · show' opens the "
           "August row with its own status and a dated screenshot link (the file is not seeded: expect "
           "'Screenshot not available on the server').",
           "'/status': shows 'September state' — NOT the July scan that was imported later.",
           "'/console': reads 'imported …' (the tool recorded no scan time); the others read 'observed …'.",
           "Ports: 80/tcp has no product, so Version shows '[401] /console (512B)' alone, not '— (…)'; "
           "the Evidence column links 'web N' to the section.")
    return h


NESSUS_XML = textwrap.dedent("""\
    <?xml version="1.0" ?>
    <NessusClientData_v2>
    <Report name="eval-udp">
    <ReportHost name="10.77.1.13">
      <HostProperties>
        <tag name="host-ip">10.77.1.13</tag>
        <tag name="host-fqdn">s03-nessus-udp.eval.test</tag>
        <tag name="operating-system">Linux Kernel 5.15</tag>
      </HostProperties>
      <ReportItem port="161" svc_name="snmp" protocol="udp" severity="3" pluginID="41028" pluginName="SNMP Agent Default Community Name (public)">
        <description>The community name of the remote SNMP server can be guessed.</description>
        <solution>Disable SNMP or change the default community string.</solution><risk_factor>High</risk_factor>
      </ReportItem>
      <ReportItem port="53" svc_name="dns" protocol="udp" severity="2" pluginID="10539" pluginName="DNS Server Recursive Query Cache Poisoning Weakness">
        <description>The remote name server allows recursive queries.</description><risk_factor>Medium</risk_factor>
      </ReportItem>
      <ReportItem port="53" svc_name="dns" protocol="tcp" severity="2" pluginID="12217" pluginName="DNS Server Cache Snooping Remote Information Disclosure">
        <description>The remote DNS server is vulnerable to cache snooping.</description><risk_factor>Medium</risk_factor>
      </ReportItem>
      <ReportItem port="123" svc_name="ntp" protocol="udp" severity="0" pluginID="10884" pluginName="Network Time Protocol (NTP) Server Detection">
        <description>An NTP server is listening.</description>
      </ReportItem>
      <ReportItem port="22" svc_name="ssh" protocol="tcp" severity="0" pluginID="10267" pluginName="SSH Server Type and Version Information">
        <description>SSH server detected.</description>
      </ReportItem>
    </ReportHost>
    </Report>
    </NessusClientData_v2>
""")


def s03_nessus_udp(c: Ctx):
    """Through the REAL import, so it is what ingest produces."""
    with tempfile.NamedTemporaryFile("w", suffix=".nessus", delete=False) as fh:
        fh.write(NESSUS_XML)
        path = fh.name
    result = NessusIntegrationService(c.db).process_nessus_file(path, project_id=c.pid)
    h = (c.db.query(models.Host)
         .filter(models.Host.project_id == c.pid, models.Host.ip_address == "10.77.1.13").first())
    if not result.get("success") or h is None:
        c.note("s03", "Nessus UDP", f"IMPORT FAILED: {result}")
        return None
    c.host_count += 1
    # The import stamps everything "now". Backdate it, or this host, its scan
    # and its findings all land inside s07's two-hour window.
    then = ago(days=5)
    h.first_seen = then
    h.last_seen = then
    for p in h.ports:
        p.first_seen = then
        p.last_seen = then
    for v in c.db.query(Vulnerability).filter(Vulnerability.host_id == h.id).all():
        v.created_at = then.replace(tzinfo=None)
    for hist in c.db.query(models.HostScanHistory).filter(models.HostScanHistory.host_id == h.id).all():
        hist.discovered_at = then
        sc_row = c.db.query(models.Scan).filter(models.Scan.id == hist.scan_id).first()
        if sc_row is not None:
            sc_row.created_at = then
    c.db.flush()
    endpoints = sorted((p.port_number, p.protocol) for p in h.ports)
    c.note("s03", "Nessus: UDP findings keep their protocol",
           f"/hosts/{h.id} — ports are {endpoints}.",
           "Expect 161/udp, 123/udp and BOTH 53/udp and 53/tcp — and NO 161/tcp or 123/tcp "
           "(before v2.365.0 every UDP finding created a phantom open TCP port).",
           "'DNS … Cache Poisoning' is on 53/udp; 'DNS … Cache Snooping' is on 53/tcp.",
           "2 informational rows are hidden until '… informational hidden · show'.")
    return h


def s04_finding_dispositions(c: Ctx, sc):
    rows = [("s04a", "10.77.1.41", "s04-fp-here.eval.test", FindingHostStatus.FALSE_POSITIVE.value),
            ("s04b", "10.77.2.41", "s04-live.eval.test", FindingHostStatus.OPEN.value),
            ("s04c", "10.77.3.41", "s04-remediated.eval.test", FindingHostStatus.REMEDIATED.value)]
    f = Finding(project_id=c.project.id, title="s04 — Outdated OpenSSH across three sites",
                severity="critical", status="confirmed", source="scanner", owner_id=c.owner.id)
    c.db.add(f)
    c.db.flush()
    for key, ip, name, state in rows:
        h = host(c, key, ip, name, sc, os_name="Debian 12", os_family="Linux")
        p = port(c, h, sc, 22, "ssh", product="OpenSSH", version="7.4")
        v = vuln(c, h, sc, "OpenSSH < 8.0 multiple vulnerabilities", VulnerabilitySeverity.CRITICAL,
                 port_obj=p, cve="CVE-2019-6111", plugin_id="s04-ssh", exploitable=True)
        v.finding_id = f.id
        c.db.add(FindingHost(finding_id=f.id, host_id=h.id, host_status=state))
    c.db.flush()
    c.note("s04", "One finding, three sites, three endpoint states",
           f"/findings/{f.id} — confirmed; endpoints: false positive (East), open (West), remediated (Lab).",
           "Posture → Segments / site attention: only WEST carries this critical. East and Lab read 0 "
           "(before v2.365.0 all three did).",
           f"/hosts/{c.hosts['s04a'].id} — the observation reads 'False positive on this host'; "
           f"/hosts/{c.hosts['s04b'].id} reads 'Promoted → finding'.")
    return f


def s05_blockers(c: Ctx, sc):
    def job(name, status, **kw):
        j = models.IngestionJob(project_id=c.project.id, filename=name, original_filename=name,
                                storage_path=f"/tmp/eval/{name}", status=status,
                                submitted_by_id=c.owner.id, **kw)
        j.created_at = ago(hours=6)
        j.completed_at = ago(hours=6)
        c.db.add(j)

    job("s05-broken.xml", "failed", tool_name="nmap", error_message="XML not well-formed at line 1")
    job("s05-unsupported.json", "failed", error_message="Unsupported file type or format.")
    job("s05-truncated.xml", "completed", tool_name="nmap", partial=True, scan_id=sc.id,
        message="Nmap XML file processed successfully",
        parser_warnings="Incomplete XML — parsing stopped early, so hosts after this point are MISSING, "
                        "not down. Recovered 1 host(s). Re-upload a complete scan file.")
    job("s05-clean.xml", "completed", tool_name="nmap", scan_id=sc.id, message="Processed successfully")
    job("s05-already-dismissed.xml", "failed", error_message="old failure", dismissed_at=ago(days=2))

    plans = {}
    for i, (title, status) in enumerate((("s05 — paused run", "in_progress"),
                                         ("s05 — run whose agent session ended", "in_progress"),
                                         ("s05 — healthy run", "in_progress"),
                                         ("s05 — awaiting approval", "proposed")), start=1):
        p = TestPlan(project_id=c.project.id, version=i, title=title, status=status,
                     generated_by_model="eval-seed")
        c.db.add(p)
        c.db.flush()
        plans[title] = p
    ended = AgentSession(workflow="execution", project_id=c.project.id, status="completed",
                         started_by_id=c.owner.id)
    live = AgentSession(workflow="execution", project_id=c.project.id, status="active",
                        started_by_id=c.owner.id)
    c.db.add_all([ended, live])
    c.db.flush()
    paused = ExecutionSession(test_plan_id=plans["s05 — paused run"].id, status="paused",
                              started_by_id=c.owner.id, started_at=ago(days=1))
    orphan = ExecutionSession(test_plan_id=plans["s05 — run whose agent session ended"].id, status="active",
                              agent_session_id=ended.id, started_by_id=c.owner.id, started_at=ago(hours=9))
    healthy = ExecutionSession(test_plan_id=plans["s05 — healthy run"].id, status="active",
                               agent_session_id=live.id, started_by_id=c.owner.id, started_at=ago(hours=1))
    c.db.add_all([paused, orphan, healthy])
    c.db.flush()
    c.note("s05", "Operations: the Blocked strip, Ingestion Results, approvals",
           "/operations — 'Blocked': '2 imports failed · 1 finished partial', plus 'Run #… is paused' and "
           "'Run #… lost its agent session'. The healthy run and the dismissed failure are NOT listed.",
           "'Inspect import errors' → /parse-errors?status=needs_attention shows exactly 3 rows; the partial "
           "one carries a 'partial' badge and the parser's warning; 'Dismiss the 3 shown' clears the strip.",
           "'Needs your approval' leads the page (1 proposed plan). Reject it and reload: the block drops to "
           "one line below My work.")


def s06_review_queue(c: Ctx, sc):
    for i in range(1, 6):
        h = host(c, f"s06r{i}", f"10.77.2.{60 + i}", f"s06-in-review-{i}.eval.test", sc,
                 os_name="Ubuntu 22.04", os_family="Linux")
        port(c, h, sc, 22, "ssh", product="OpenSSH", version="8.9")
        follow(c, h, FollowStatus.IN_REVIEW, updated=ago(hours=i), assigned=True)

    need = host(c, "s06n", "10.77.2.70", "s06-needs-evidence.eval.test", sc, os_name="Debian 12", os_family="Linux")
    port(c, need, sc, 443, "https", product="nginx", version="1.22")
    follow(c, need, FollowStatus.REVIEWED, updated=ago(days=4), reviewed_at=ago(days=4),
           conclusion="needs_evidence", summary="Could not reach 443 from the jump host; need a scan from inside the VLAN.")

    changed = host(c, "s06c", "10.77.2.71", "s06-changed-since-review.eval.test", sc,
                   os_name="Windows Server 2019", os_family="Windows")
    port(c, changed, sc, 445, "microsoft-ds")
    follow(c, changed, FollowStatus.REVIEWED, updated=ago(days=6), reviewed_at=ago(days=6),
           conclusion="no_action", summary="Nothing notable at the time.")
    port(c, changed, sc, 3389, "ms-wbt-server", first_seen=ago(days=1))   # appeared AFTER the review
    c.note("s06", "My work: queues, and Next following them",
           "/operations → My work → 'In review' shows 3 of 5 with 'Show 2 more'. Open one of the THREE without "
           "expanding: the host page reads '1 of 5 in In review' and Next reaches all five.",
           f"'Needs another look' lists /hosts/{need.id} (concluded 'needs evidence') and "
           f"/hosts/{changed.id} (a port first seen after the review → 'Changed since review · 1 new port').",
           "Complete a review from the inspector: 'Save and next unreviewed' is offered inside a queue.")


def s07_since_last_visit(c: Ctx, sc_new):
    visit = ago(hours=2)
    cur = c.db.query(OperationsCursor).filter(OperationsCursor.user_id == c.owner.id,
                                              OperationsCursor.project_id == c.project.id).first()
    if cur is None:
        cur = OperationsCursor(user_id=c.owner.id, project_id=c.project.id)
        c.db.add(cur)
    cur.last_viewed_at = visit

    new = host(c, "s07n", "10.77.3.81", "s07-new-host.eval.test", sc_new, first_seen=ago(minutes=50),
               last_seen=ago(minutes=50), os_name="Ubuntu 24.04", os_family="Linux")
    port(c, new, sc_new, 22, "ssh")
    known = host(c, "s07c", "10.77.3.82", "s07-known-host-changed.eval.test", sc_new, first_seen=ago(days=12))
    port(c, known, sc_new, 80, "http")
    port(c, known, sc_new, 8080, "http-proxy", first_seen=ago(minutes=45))
    crit = host(c, "s07v", "10.77.3.83", "s07-new-critical.eval.test", sc_new, first_seen=ago(days=12))
    p = port(c, crit, sc_new, 8080, "http", product="Apache Tomcat", version="8.5.19")
    vuln(c, crit, sc_new, "Apache Struts Remote Code Execution", VulnerabilitySeverity.CRITICAL, port_obj=p,
         cve="CVE-2017-5638", exploitable=True, plugin_id="s07-struts", created=ago(minutes=40))
    # Old critical + NEW low on the same host: must not count as a new critical.
    mixed = host(c, "s07m", "10.77.3.84", "s07-old-critical-new-low.eval.test", sc_new, first_seen=ago(days=12))
    vuln(c, mixed, sc_new, "Old critical", VulnerabilitySeverity.CRITICAL, plugin_id="s07-old", created=ago(days=10))
    vuln(c, mixed, sc_new, "New low", VulnerabilitySeverity.LOW, plugin_id="s07-low", created=ago(minutes=30))
    c.note("s07", "Operations: 'Since your last visit' as a change inbox",
           "Your last visit to this project is set to 2 hours ago.",
           "/operations banner: '1 new import', '1 new host', '3 known hosts changed', "
           "'1 new critical observation · 1 host'. Each chip is a link.",
           "'1 new host' opens Hosts with firstseen:\"…\" → exactly s07-new-host.",
           "'… known hosts changed' → s07-known-host-changed, s07-new-critical, s07-old-critical-new-low.",
           "'new critical' → ONLY s07-new-critical (s07-old-critical-new-low has a critical, but an old one).",
           "'Acknowledge updates' clears the banner; it stays cleared after a reload.")


def s08_conflicts(c: Ctx, scans):
    old, new = scans["masscan"], scans["nmap"]
    h = host(c, "s08", "10.77.1.18", "s08-conflicts.eval.test", new, os_name="Windows Server 2022",
             os_family="Windows", os_accuracy="97")
    port(c, h, new, 445, "microsoft-ds")
    c.db.add(HostConfidence(host_id=h.id, field_name="os_name", confidence_score=95, scan_type="nmap",
                            data_source="os_fingerprint", method="nmap -O", scan_id=new.id,
                            additional_factors={"os_accuracy": 97}))
    for field, prev, newv in (("os_name", "Linux 4.x", "Windows Server 2022"),
                              ("hostname", "WIN-7Q2K", "s08-conflicts.eval.test")):
        c.db.add(ConflictHistory(host_id=h.id, field_name=field, previous_value=prev, previous_confidence=60,
                                 previous_scan_id=old.id, previous_method="masscan banner",
                                 new_value=newv, new_confidence=95, new_scan_id=new.id, new_method="nmap -O",
                                 resolved_at=ago(days=3)))
    # An OS whose source gave no accuracy figure: must not render "· 0%".
    h0 = host(c, "s08z", "10.77.1.19", "s08-os-no-accuracy.eval.test", new, os_name="Ubuntu Linux 22.04",
              os_family="Linux", os_accuracy="0")
    port(c, h0, new, 22, "ssh")
    c.note("s08", "Conflicts and OS confidence",
           f"/hosts/{h.id} — '2 conflicts' button → the panel dates the selected value ('recorded …') and each "
           "resolution; weights are shown as weights, never as percentages.",
           f"/hosts/{h0.id} — OS reads 'Ubuntu Linux 22.04' with no '· 0%' and no tentative '~'.")


def s09_names(c: Ctx, sc):
    cache = names.ObservationCache()
    h = host(c, "s09", "10.77.1.20", "s09-names.eval.test", sc, os_name="Debian 12", os_family="Linux")
    port(c, h, sc, 443, "https", product="nginx", version="1.24")
    for fqdn, rtype in (("app.eval.test", "A"), ("api.eval.test", "A"), ("legacy.eval.test", "A")):
        names.record_observation(c.db, project_id=c.project.id, name=fqdn, record_type=rtype,
                                 value=h.ip_address, scan_id=sc.id, resolver_name="10.77.0.53:53", ttl=300,
                                 observed_at=ago(days=1), cache=cache)
    names.record_observation(c.db, project_id=c.project.id, name="s09-names.eval.test", record_type="PTR",
                             value=h.ip_address, scan_id=sc.id, observed_at=ago(days=1), cache=cache)

    # Reachable ONLY via an in-scope name: outside every subnet.
    lb = host(c, "s09lb", "203.0.113.77", "s09-name-only.eval.test", sc, os_name=None)
    port(c, lb, sc, 443, "https")
    for fqdn in ("portal.client-eval.test", "shop.client-eval.test"):
        names.record_observation(c.db, project_id=c.project.id, name=fqdn, record_type="A",
                                 value=lb.ip_address, scan_id=sc.id, resolver_name="1.1.1.1:53", ttl=60,
                                 observed_at=ago(hours=8), cache=cache)
    out = host(c, "s09out", "198.51.100.9", "s09-out-of-scope.eval.test", sc, os_name=None)
    port(c, out, sc, 80, "http")
    c.note("s09", "Names, DNS evidence, and the three scope states",
           f"/hosts/{h.id} — header 'In scope · 10.77.1.0/24 · show entries'. 'Names at this address' ends with "
           "'N DNS records behind these names · show' (no separate DNS section).",
           f"/hosts/{lb.id} — 'Reachable via in-scope name' with 'portal.client-eval.test' shown INLINE and the "
           "sentence limiting what the name authorises always visible.",
           f"/hosts/{out.id} — 'Out of scope', with no scope entries to show.")


def s10_netexec(c: Ctx, scans):
    a, b = scans["nxc_a"], scans["nxc_b"]
    h = host(c, "s10", "10.77.2.30", "s10-netexec.eval.test", b, os_name="Windows Server 2019",
             os_family="Windows", smb_signing="disabled")
    port(c, h, b, 445, "microsoft-ds")
    for sc in (a, b):                                   # the SAME result, two scans → one row
        c.db.add(NetexecResult(scan_id=sc.id, host_id=h.id, protocol="smb", port=445, auth_success=False,
                               hostname="S10", shares=None))
    c.db.add(NetexecResult(scan_id=b.id, host_id=h.id, protocol="smb", port=445, auth_success=True,
                           username="svc_backup", hostname="S10",
                           shares=[{"name": "ADMIN$", "access": "READ"}, {"name": "Backups", "access": "READ,WRITE"}]))
    c.db.flush()
    c.note("s10", "NetExec: repeats collapse, different outcomes do not",
           f"/hosts/{h.id} — 'NetExec enumeration 2': one line 'Auth failed · no shares enumerated · same result "
           "in 2 scans', and a separate authenticated result listing two shares.",
           "Header shows 'SMB Signing disabled' as a genuine alert.")


def s11_tests(c: Ctx, sc):
    h = host(c, "s11", "10.77.2.31", "s11-proposed-tests.eval.test", sc, os_name="Ubuntu 22.04", os_family="Linux")
    port(c, h, sc, 22, "ssh")
    port(c, h, sc, 80, "http")
    # One entry per (plan, host) — uq_plan_host_name — so three entries on one
    # host are three plans, which is also how it looks in real use.
    for version, (status, tests, findings) in enumerate((
        ("completed", [{"tool": "nmap", "command": "nmap -sV -p22 {ip}"}, {"tool": "ssh-audit", "command": "ssh-audit {ip}"}],
         "Weak KEX algorithms offered; no password auth."),
        ("rejected", [{"tool": "hydra", "command": "hydra -L users.txt {ip} ssh"}], None),
        ("proposed", [{"tool": "nikto", "command": "nikto -h {ip}"}], None),
    ), start=90):
        plan = TestPlan(project_id=c.project.id, version=version, title=f"s11 — plan ({status} entry)",
                        status="in_progress", generated_by_model="eval-seed")
        c.db.add(plan)
        c.db.flush()
        c.db.add(TestPlanEntry(test_plan_id=plan.id, host_id=h.id, priority="high", test_phase="enumeration",
                               proposed_tests=tests, rationale="eval fixture", status=status, findings=findings))
    c.db.flush()
    c.note("s11", "Proposed tests: finished entries fold",
           f"/hosts/{h.id} — the completed entry reads '2 tests · summary · show', the rejected one '1 test · show'; "
           "the proposed entry is open.")


def s12_worst_case(c: Ctx, sc):
    long_name = ("s12-" + "very-long-label-" * 11 + "end.eval.test")[:200]
    h = host(c, "s12", "10.77.3.42", long_name, sc, os_name="A" * 120, os_family="Linux")
    for n in range(1, 41):
        port(c, h, sc, 7000 + n, f"svc-{n}", product="Example Product With A Long Name", version=f"{n}.0.0-build.{n * 97}")
    for n in (21, 23):
        port(c, h, sc, n, "ftp" if n == 21 else "telnet", state="closed")
    for n in (135, 139, 445):
        port(c, h, sc, n, "msrpc", state="filtered")
    for i in range(1, 27):
        sev = [VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH, VulnerabilitySeverity.MEDIUM,
               VulnerabilitySeverity.LOW][i % 4]
        vuln(c, h, sc, f"s12 observation {i:02d} — " + "an unusually long scanner title " * 3, sev, plugin_id=f"s12-{i}")
    bare = host(c, "s12b", "10.77.3.43", None, sc)          # nothing known at all
    c.note("s12", "Worst case and empty case (UI style guide)",
           f"/hosts/{h.id} — 200-char hostname, 120-char OS, 40 open ports, '2 closed · 3 filtered · show', "
           "26 observations (25 shown + 'Show all issues'). No horizontal page scroll, in the sheet or standalone.",
           f"/hosts/{bare.id} — no hostname, no OS, no ports: 'No open ports observed.', fallbacks not blanks.")


def s13_worth_a_look(c: Ctx, sc):
    for i, (title, exploitable) in enumerate((("Exploitable critical", True), ("Critical, no exploit", False)), start=1):
        h = host(c, f"s13{i}", f"10.77.3.{90 + i}", f"s13-untouched-{i}.eval.test", sc, os_name="CentOS 6.10", os_family="Linux")
        p = port(c, h, sc, 445 if i == 1 else 3389, "microsoft-ds" if i == 1 else "ms-wbt-server")
        vuln(c, h, sc, f"s13 — {title}", VulnerabilitySeverity.CRITICAL, port_obj=p, exploitable=exploitable,
             cve=f"CVE-2020-{1000 + i}", plugin_id=f"s13-{i}")
    c.note("s13", "Worth a look: untouched hosts, ordered by a stated tier",
           "/operations → 'Worth a look' orders by tier: exploitable criticals first (s13-untouched-1, and "
           "s07-new-critical, which nobody has touched either), then s13-untouched-2 among the plain criticals. "
           "'Review' takes one into your In review list.")


# ---------------------------------------------------------------------------

def seed(db, name: str, owner: User):
    project = Project(name=name, slug=name.lower().replace(" ", "-").replace("—", "-")[:90],
                      description="Hand-placed scenarios for evaluating features. See scripts/seed_eval_scenarios.py.",
                      status="active")
    db.add(project)
    db.flush()
    c = Ctx(db, project, owner)

    scope = models.Scope(project_id=project.id, name="Eval scope")
    db.add(scope)
    db.flush()
    for site_name, tier, cidr in (("East", 1, "10.77.1.0/24"), ("West", 2, "10.77.2.0/24"), ("Lab", 4, "10.77.3.0/24")):
        site = models.Site(project_id=project.id, name=site_name, criticality_tier=tier)
        db.add(site)
        db.flush()
        db.add(models.Subnet(scope_id=scope.id, cidr=cidr, site=site_name, site_id=site.id,
                             description=f"{site_name} segment"))
    db.add(models.ScopeDomain(scope_id=scope.id, domain="portal.client-eval.test", include_subdomains=False,
                              created_by_id=owner.id))
    db.flush()

    scans = {
        "nmap": scan(c, "eval-nmap.xml", "nmap", imported=ago(days=3), start=ago(days=3, hours=1),
                     end=ago(days=3), time_source="tool_run"),
        "masscan": scan(c, "eval-masscan.json", "masscan", imported=ago(days=9)),
        "web_aug": scan(c, "eval-eyewitness-aug.csv", "eyewitness", imported=ago(days=44),
                        start=ago(days=45), end=ago(days=45), time_source="tool_run"),
        "web_sep": scan(c, "eval-eyewitness-sep.csv", "eyewitness", imported=ago(days=10),
                        start=ago(days=11), end=ago(days=11), time_source="tool_run"),
        "web_untimed": scan(c, "eval-httpx-untimed.json", "httpx", imported=ago(days=6)),
        # Scanned in July, uploaded two days ago.
        "web_late_old": scan(c, "eval-eyewitness-JULY-uploaded-late.csv", "eyewitness", imported=ago(days=2),
                             start=ago(days=75), end=ago(days=75), time_source="tool_run"),
        "nxc_a": scan(c, "eval-netexec-a.json", "netexec", imported=ago(days=8)),
        "nxc_b": scan(c, "eval-netexec-b.json", "netexec", imported=ago(days=2)),
        "fresh": scan(c, "eval-fresh-sweep.xml", "nmap", imported=ago(minutes=55), start=ago(hours=1),
                      end=ago(minutes=56), time_source="tool_run"),
    }

    s01_notes(c, scans["nmap"])
    s02_web_history(c, scans)
    s04_finding_dispositions(c, scans["nmap"])
    s05_blockers(c, scans["nmap"])
    s06_review_queue(c, scans["nmap"])
    s07_since_last_visit(c, scans["fresh"])
    s08_conflicts(c, scans)
    s09_names(c, scans["nmap"])
    s10_netexec(c, scans)
    s11_tests(c, scans["nmap"])
    s12_worst_case(c, scans["nmap"])
    s13_worth_a_look(c, scans["nmap"])
    c.host_count = len(c.hosts)
    db.commit()

    # The real Nessus import commits on its own; run it after ours is durable.
    s03_nessus_udp(c)
    db.commit()

    mappings = SubnetCorrelationService(db).correlate_all_hosts_to_subnets(project_id=c.pid)
    db.commit()
    return c, mappings


def print_guide(c: Ctx, mappings: int):
    print()
    print("=" * 78)
    print(f"  {c.pname}   (project id {c.pid}, {c.host_count} hosts, {mappings} subnet mappings)")
    print("  Select it in the project switcher. Every host is searchable by its scenario id (s01, s02, …).")
    print("=" * 78)
    for sid, title, lines in sorted(c.guide):
        print(f"\n[{sid}] {title}")
        for line in lines:
            print(textwrap.fill(line, width=100, initial_indent="   • ", subsequent_indent="     "))
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed a small project of hand-placed evaluation scenarios.")
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--wipe", action="store_true", help="Delete an existing project of the same name first.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        owner = (db.query(User).filter(User.role == UserRole.ADMIN).first() or db.query(User).first())
        if owner is None:
            print("No users exist yet — log in once to create the admin, then re-run.")
            return 1
        existing = db.query(Project).filter(Project.name == args.name).first()
        if existing and not args.wipe:
            print(f"Project '{args.name}' already exists (id {existing.id}). Re-run with --wipe to rebuild it.")
            return 1
        if existing:
            db.delete(existing)
            db.commit()
            print(f"  wiped existing project '{args.name}'")
        print(f"Seeding '{args.name}' (owner={owner.username})…")
        c, mappings = seed(db, args.name, owner)
        print_guide(c, mappings)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
