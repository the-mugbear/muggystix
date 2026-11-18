"""The project's changelog, for the inventory: hosts, scans and imports, scope.

Behaviour, in the style of ``test_project_changelog.py``: an action through
its real route leaves ONE line — the action, what it was done to, the short
detail, how many — and an agent's line is an agent's.  A bulk action is one
line with its count; an action that changed nothing leaves none; nothing
anyone wrote (a description, a caption) is copied into a line.
"""
from __future__ import annotations

from app.core.config import settings
from app.db import models
from app.db.models_project import ProjectEvent

NMAP_XML = b"""<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -sS 10.61.9.1" start="1700000000" version="7.94">
<host><status state="up"/><address addr="10.61.9.1" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="22"><state state="open"/><service name="ssh"/></port></ports>
</host>
<runstats><finished time="1700000100"/><hosts up="1" down="0" total="1"/></runstats>
</nmaprun>
"""
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _lines(db, project, action=None):
    db.expire_all()
    q = db.query(ProjectEvent).filter(ProjectEvent.project_id == project.id)
    if action:
        q = q.filter(ProjectEvent.action == action)
    return q.order_by(ProjectEvent.id).all()


def _one(db, project, action):
    (line,) = _lines(db, project, action)
    return line


def _hosts(db, project, *ips):
    hosts = [models.Host(project_id=project.id, ip_address=ip, state="up") for ip in ips]
    db.add_all(hosts)
    db.commit()
    return hosts


def _upload(client, project, data=NMAP_XML, name="sweep.xml", **form):
    answer = client.post(
        f"/api/v1/projects/{project.id}/upload/",
        files={"file": (name, data, "text/xml")},
        data={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()},
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["job_id"]


def _agent_key(client, project):
    started = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "imports"})
    assert started.status_code == 201, started.text
    return {"X-API-Key": started.json()["api_key"]}


# --- hosts -------------------------------------------------------------------

def test_each_bulk_host_action_is_one_line_with_its_count(client, db_session, test_project, test_user):
    hosts = _hosts(db_session, test_project, "10.61.0.1", "10.61.0.2", "10.61.0.3")
    ids = [h.id for h in hosts]
    bulk = f"/api/v1/projects/{test_project.id}/hosts/bulk"

    assert client.post(f"{bulk}/assign", json={"host_ids": ids, "assignee_user_id": test_user.id}).status_code == 200
    assert client.post(f"{bulk}/unassign", json={"host_ids": ids[:2]}).json()["affected"] == 2
    # Nobody is assigned on those two any more: nothing changes, nothing is recorded.
    assert client.post(f"{bulk}/unassign", json={"host_ids": ids[:2]}).json()["affected"] == 0
    assert client.post(f"{bulk}/tags", json={"host_ids": ids, "names": ["dmz"]}).status_code == 200
    assert client.post(f"{bulk}/follow", json={"host_ids": ids, "status": "reviewed"}).status_code == 200

    lines = _lines(db_session, test_project)
    assert [(line.action, line.count, line.summary) for line in lines] == [
        ("host_assigned", 3, f"3 hosts to {test_user.username}"),
        ("host_unassigned", 2, "2 hosts"),
        ("host_tags_changed", 3, "3 hosts, added: dmz"),
        ("host_review_changed", 3, "3 hosts → reviewed"),      # one line, none per host
    ]
    assert {(line.subject_type, line.subject_id, line.by_agent) for line in lines} == {("host", None, False)}

    # The tag is already on every host: nothing changed, nothing recorded.
    assert client.post(f"{bulk}/tags", json={"host_ids": ids, "names": ["dmz"]}).json()["affected"] == 0
    assert len(_lines(db_session, test_project)) == 4

    removed = client.post(f"{bulk}/tags", json={"host_ids": ids[:1], "names": [], "tag_ids": [
        db_session.query(models.HostTag.id).filter_by(project_id=test_project.id).scalar(),
    ], "action": "remove"})
    assert removed.json()["affected"] == 1
    assert _lines(db_session, test_project)[-1].summary == "1 host, removed: dmz"


def test_renaming_and_deleting_a_tag_are_lines_about_the_tag(client, db_session, test_project):
    (host,) = _hosts(db_session, test_project, "10.61.1.1")
    base = f"/api/v1/projects/{test_project.id}/hosts"
    client.post(f"{base}/bulk/tags", json={"host_ids": [host.id], "names": ["prod"]})
    tag_id = db_session.query(models.HostTag.id).filter_by(project_id=test_project.id).scalar()

    assert client.patch(f"{base}/tags/{tag_id}", json={"name": "production"}).status_code == 200
    assert client.patch(f"{base}/tags/{tag_id}", json={"name": "production"}).status_code == 200   # no change
    assert client.delete(f"{base}/tags/{tag_id}").status_code == 204

    lines = [line for line in _lines(db_session, test_project, "host_tags_changed") if line.subject_type == "tag"]
    assert [(line.subject_id, line.subject_label, line.summary) for line in lines] == [
        (tag_id, "production", "renamed"), (tag_id, "production", "tag deleted"),
    ]


def test_the_projects_default_view_set_and_cleared(client, db_session, test_project):
    base = f"/api/v1/projects/{test_project.id}/hosts"
    view = client.post(f"{base}/views", json={"name": "Web servers", "filter_json": {"ports": "443"}})
    assert view.status_code == 201, view.text
    assert _lines(db_session, test_project) == []          # a saved view is the reader's own
    view_id = view.json()["id"]

    assert client.post(f"{base}/views/{view_id}/promote").status_code == 200
    assert client.post(f"{base}/views/{view_id}/promote").status_code == 200      # already the default
    assert client.delete(f"{base}/default-view").status_code == 204
    assert client.delete(f"{base}/default-view").status_code == 204               # nothing to clear

    assert [
        (line.subject_id, line.subject_label, line.summary)
        for line in _lines(db_session, test_project, "default_view_changed")
    ] == [(view_id, "Web servers", "set"), (None, None, "cleared")]


def test_a_notes_image_is_a_line_that_never_carries_the_caption(
    client, db_session, test_project, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    (host,) = _hosts(db_session, test_project, "10.61.2.1")
    hosts = f"/api/v1/projects/{test_project.id}/hosts"
    note_id = client.post(f"{hosts}/{host.id}/notes", json={"body": "login page"}).json()["id"]

    uploaded = client.post(
        f"{hosts}/{host.id}/notes/{note_id}/attachments", files={"file": ("login page.png", PNG, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    att_id = uploaded.json()["id"]
    patch = f"{hosts}/notes/attachments/{att_id}"
    assert client.patch(patch, json={"caption": "The administrator sign-in", "include_in_report": True}).status_code == 200
    assert client.patch(patch, json={"include_in_report": True}).status_code == 200       # no change
    assert client.delete(patch).status_code == 204

    lines = _lines(db_session, test_project, "note_image_changed")
    assert [line.summary for line in lines] == ["added", "caption changed, included in report", "removed"]
    assert {(line.subject_type, line.subject_id, line.subject_label) for line in lines} == {
        ("note", note_id, "login_page.png"),
    }

    # A finding comment's image goes through the same upload.
    findings = f"/api/v1/projects/{test_project.id}/findings"
    fid = client.post(findings, json={"title": "Default login", "severity": "high"}).json()["id"]
    comment_id = client.post(f"{findings}/{fid}/notes", json={"body": "see screenshot"}).json()["id"]
    assert client.post(
        f"{findings}/{fid}/notes/{comment_id}/attachments", files={"file": ("proof.png", PNG, "image/png")},
    ).status_code == 200
    last = _lines(db_session, test_project, "note_image_changed")[-1]
    assert (last.subject_id, last.subject_label, last.summary) == (comment_id, "proof.png", "added")


# --- scans and imports -------------------------------------------------------

def test_an_upload_its_start_and_its_discard_are_lines_about_the_file(client, db_session, test_project, test_user):
    base = f"/api/v1/projects/{test_project.id}/upload"
    job_id = _upload(client, test_project, name="perimeter.xml", stage=True)
    uploaded = _one(db_session, test_project, "scan_uploaded")
    assert (uploaded.subject_type, uploaded.subject_id, uploaded.subject_label, uploaded.summary) == (
        "upload", job_id, "perimeter.xml", "staged for review",
    )
    assert uploaded.actor_user_id == test_user.id and uploaded.by_agent is False

    # The same file again is refused — and nothing is recorded for it.
    again = client.post(f"{base}/", files={"file": ("perimeter.xml", NMAP_XML, "text/xml")}, data={"stage": "true"})
    assert again.status_code == 409
    assert len(_lines(db_session, test_project, "scan_uploaded")) == 1

    assert client.post(f"{base}/jobs/{job_id}/start", json={"format_override": "nmap_xml"}).status_code == 200
    started = _one(db_session, test_project, "scan_import_started")
    assert (started.subject_id, started.subject_label, started.summary) == (job_id, "perimeter.xml", "as Nmap XML")

    assert client.post(f"{base}/jobs/{job_id}/cancel").status_code == 200
    cancelled = _one(db_session, test_project, "import_cancelled")
    assert (cancelled.subject_id, cancelled.subject_label) == (job_id, "perimeter.xml")

    # A cancelled import is a failed one: retried (a second start line), cancelled, dismissed.
    assert client.post(f"{base}/jobs/{job_id}/retry").status_code == 200
    assert [line.summary for line in _lines(db_session, test_project, "scan_import_started")] == ["as Nmap XML", "retry"]
    assert client.post(f"{base}/jobs/{job_id}/cancel").status_code == 200
    assert client.post(f"{base}/jobs/{job_id}/dismiss").status_code == 200
    assert client.post(f"{base}/jobs/{job_id}/dismiss").status_code == 200        # already dismissed
    dismissed = _one(db_session, test_project, "import_dismissed")
    assert (dismissed.subject_id, dismissed.summary) == (job_id, "failed import")

    other = _upload(client, test_project, data=NMAP_XML + b"<!-- other -->", name="inside.xml", stage=True)
    assert client.post(f"{base}/jobs/{other}/discard").status_code == 200
    discarded = _one(db_session, test_project, "scan_upload_discarded")
    assert (discarded.subject_id, discarded.subject_label, discarded.count) == (other, "inside.xml", 1)


def test_discarding_several_staged_uploads_is_one_line(client, db_session, test_project):
    ids = [
        _upload(client, test_project, data=NMAP_XML + f"<!-- {n} -->".encode(), name=f"chunk{n}.xml", stage=True)
        for n in range(3)
    ]
    answer = client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/discard-staged", json={"job_ids": ids},
    )
    assert answer.status_code == 200 and answer.json()["discarded"] == 3
    line = _one(db_session, test_project, "scan_upload_discarded")
    assert (line.count, line.summary, line.subject_id) == (3, "3 staged uploads", None)

    # Nothing left to discard: no line.
    client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/discard-staged", json={"job_ids": ids})
    assert len(_lines(db_session, test_project, "scan_upload_discarded")) == 1


def test_an_agents_upload_and_its_observations_are_the_agents_lines(client, db_session, test_project, test_user):
    key = _agent_key(client, test_project)
    uploaded = client.post(
        "/api/v1/agent/uploads", headers=key, files={"file": ("agent-sweep.xml", NMAP_XML, "application/xml")},
        data={"tool_name": "nmap"},
    )
    assert uploaded.status_code == 201, uploaded.text
    line = _one(db_session, test_project, "scan_uploaded")
    assert (line.subject_id, line.subject_label) == (uploaded.json()["job_id"], "agent-sweep.xml")
    assert line.by_agent is True and line.agent_session_id is not None
    assert line.actor_user_id == test_user.id

    recorded = client.post("/api/v1/agent/observations", headers=key, json={
        "tool": "custom-check",
        "observations": [
            {"host": "10.61.9.1", "title": "Default community string", "port": 161, "protocol": "udp"},
            {"host": "10.61.9.2", "title": "Anonymous bind allowed", "port": 389},
        ],
    })
    assert recorded.status_code == 201, recorded.text
    obs = _one(db_session, test_project, "observations_recorded")
    assert (obs.count, obs.summary, obs.subject_id, obs.by_agent) == (
        2, "2 observations", recorded.json()["job_id"], True,
    )
    # … INSTEAD of an upload line, not beside one.
    assert len(_lines(db_session, test_project, "scan_uploaded")) == 1


def test_a_reprocess_is_a_line_about_the_new_job(client, db_session, test_project):
    job_id = _upload(client, test_project, name="perimeter.xml")
    job = db_session.get(models.IngestionJob, job_id)
    job.status = "completed"
    db_session.commit()
    answer = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/reprocess", json={})
    assert answer.status_code == 200, answer.text
    line = _one(db_session, test_project, "scan_reprocessed")
    assert (line.subject_id, line.subject_label) == (answer.json()["id"], "perimeter.xml")
    assert line.subject_id != job_id
    assert len(_lines(db_session, test_project, "scan_uploaded")) == 1       # the re-process is not an upload


def test_an_agent_keeping_a_cut_short_upload_is_that_line_and_not_a_start(client, db_session, test_project):
    from tests.test_truncated_upload_agent import _failed_cut_upload, _headers, _keep_path

    headers = _headers(db_session, test_project)
    job_id = _failed_cut_upload(client, db_session, headers)
    assert client.post(_keep_path(job_id), json={"operator_agreed": True}, headers=headers).status_code == 200
    line = _one(db_session, test_project, "truncated_upload_kept")
    assert (line.subject_id, line.subject_label, line.by_agent) == (job_id, "sweep.xml", True)
    assert _lines(db_session, test_project, "scan_import_started") == []


def test_deleting_a_scan_and_naming_a_batch(client, db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="old-sweep.xml", tool_name="nmap", scan_type="nmap_xml")
    batch = models.ScanBatch(project_id=test_project.id, label="3 files")
    db_session.add_all([scan, batch])
    db_session.commit()
    scan_id, batch_id = scan.id, batch.id
    scans = f"/api/v1/projects/{test_project.id}/scans"

    assert client.patch(f"{scans}/batches/{batch_id}", json={"label": "Perimeter, week 1"}).status_code == 200
    assert client.patch(f"{scans}/batches/{batch_id}", json={"label": "Perimeter, week 1"}).status_code == 200
    named = _one(db_session, test_project, "scan_batch_named")
    assert (named.subject_type, named.subject_id, named.subject_label) == ("scan_batch", batch_id, "Perimeter, week 1")

    assert client.delete(f"{scans}/{scan_id}").status_code == 200
    deleted = _one(db_session, test_project, "scan_deleted")
    assert (deleted.subject_type, deleted.subject_id, deleted.subject_label, deleted.summary) == (
        "scan", scan_id, "old-sweep.xml", "0 hosts removed, 0 ports removed on hosts that stay",
    )


def test_a_parse_errors_status_and_its_deletion(client, db_session, test_project):
    error = models.ParseError(
        project_id=test_project.id, filename="broken.xml", error_type="parse", error_message="unexpected end",
        status="unresolved",
    )
    db_session.add(error)
    db_session.commit()
    error_id = error.id
    base = f"/api/v1/projects/{test_project.id}/parse-errors/{error_id}"

    assert client.put(f"{base}/status", params={"status": "fixed"}).status_code == 200
    assert client.put(f"{base}/status", params={"status": "fixed"}).status_code == 200       # no change
    assert client.delete(base).status_code == 200
    assert [
        (line.subject_id, line.subject_label, line.summary)
        for line in _lines(db_session, test_project, "parse_error_changed")
    ] == [(error_id, "broken.xml", "unresolved → fixed"), (error_id, "broken.xml", "deleted")]


# --- scope -------------------------------------------------------------------

def test_a_subnets_life_in_the_scope(client, db_session, test_project):
    scopes = f"/api/v1/projects/{test_project.id}/scopes"
    added = client.post(f"{scopes}/subnets", json={"subnets": [
        {"cidr": "10.62.0.0/24", "description": "Head office wired"}, {"cidr": "10.62.1.0/24"},
    ]})
    assert added.status_code == 201, added.text
    subnet_id = added.json()[0]["id"]
    line = _one(db_session, test_project, "scope_subnets_added")
    assert (line.count, line.summary, line.subject_id) == (2, "10.62.0.0/24, 10.62.1.0/24", None)

    # A refused add (the CIDR is already there) records nothing.
    assert client.post(f"{scopes}/subnets", json={"subnets": [{"cidr": "10.62.0.0/24"}]}).status_code == 400
    assert len(_lines(db_session, test_project, "scope_subnets_added")) == 1

    changed = client.patch(
        f"{scopes}/subnets/{subnet_id}", json={"description": "Head office, second floor", "site": "HQ"},
    )
    assert changed.status_code == 200, changed.text
    assert client.patch(f"{scopes}/subnets/{subnet_id}", json={"site": "HQ"}).status_code == 200     # no change
    assert client.patch(f"{scopes}/subnets/{subnet_id}", json={"cidr": "10.62.2.0/24"}).status_code == 200
    edits = _lines(db_session, test_project, "scope_subnet_changed")
    assert [(e.subject_id, e.subject_label, e.summary) for e in edits] == [
        (subnet_id, "10.62.0.0/24", "description, site"), (subnet_id, "10.62.2.0/24", "CIDR"),
    ]
    # The fields' names, never what was written in them.
    assert all("floor" not in (e.summary or "") for e in edits)

    assert client.delete(f"{scopes}/subnets/{subnet_id}").status_code == 200
    removed = _one(db_session, test_project, "scope_subnet_removed")
    assert (removed.subject_id, removed.subject_label) == (subnet_id, "10.62.2.0/24")


def test_a_scope_file_is_one_line(client, db_session, test_project):
    answer = client.post(
        f"/api/v1/projects/{test_project.id}/scopes/upload-subnets",
        files={"file": ("scope.txt", b"10.63.0.0/24\n10.63.1.0/24\n10.63.2.0/24\nshop.example.test\n", "text/plain")},
    )
    assert answer.status_code == 200, answer.text
    line = _one(db_session, test_project, "scope_subnets_added")
    assert (line.count, line.summary) == (4, "10.63.0.0/24, 10.63.1.0/24, 10.63.2.0/24; 1 domain")

    # The same file again adds nothing, and records nothing.
    client.post(
        f"/api/v1/projects/{test_project.id}/scopes/upload-subnets",
        files={"file": ("scope.txt", b"10.63.0.0/24\n10.63.1.0/24\n10.63.2.0/24\nshop.example.test\n", "text/plain")},
    )
    assert len(_lines(db_session, test_project, "scope_subnets_added")) == 1


def test_a_domain_declared_in_scope_and_removed(client, db_session, test_project):
    scopes = f"/api/v1/projects/{test_project.id}/scopes"
    added = client.post(f"{scopes}/domains", json={"domains": [{"domain": "example.test"}]})
    assert added.status_code == 200, added.text
    assert client.post(f"{scopes}/domains", json={"domains": [{"domain": "example.test"}]}).status_code == 200
    line = _one(db_session, test_project, "scope_domains_added")
    assert (line.subject_label, line.summary, line.count) == ("example.test", "1 added", 1)

    domain_id = added.json()["domains"][0]["id"]
    assert client.delete(f"{scopes}/domains/{domain_id}").status_code == 200
    removed = _one(db_session, test_project, "scope_domain_removed")
    assert (removed.subject_id, removed.subject_label) == (domain_id, "example.test")


def test_subnet_labels_and_a_sites_details(client, db_session, test_project):
    scopes = f"/api/v1/projects/{test_project.id}/scopes"
    subnets = client.post(f"{scopes}/subnets", json={"subnets": [
        {"cidr": "10.64.0.0/24", "site": "Plant"}, {"cidr": "10.64.1.0/24"},
    ]}).json()
    subnet_ids = [s["id"] for s in subnets]

    label_id = client.post(f"{scopes}/subnet-labels", json={"name": "OT"}).json()["id"]
    assert client.patch(f"{scopes}/subnet-labels/{label_id}", json={"name": "OT network"}).status_code == 200
    assert client.post(f"{scopes}/subnet-labels/{label_id}/subnets", json={"subnet_ids": subnet_ids}).status_code == 200
    assert client.post(f"{scopes}/subnet-labels/{label_id}/subnets", json={"subnet_ids": subnet_ids}).status_code == 200
    assert client.put(f"{scopes}/subnets/{subnet_ids[0]}/labels", json={"label_ids": []}).status_code == 200
    assert client.put(f"{scopes}/subnets/{subnet_ids[0]}/labels", json={"label_ids": []}).status_code == 200
    assert client.delete(f"{scopes}/subnet-labels/{label_id}").status_code == 204

    assert [
        (line.subject_type, line.subject_label, line.summary, line.count)
        for line in _lines(db_session, test_project, "subnet_label_changed")
    ] == [
        ("subnet_label", "OT", "label created", 1),
        ("subnet_label", "OT network", "label renamed", 1),
        ("subnet_label", "OT network", "applied to 2 subnets", 2),
        ("subnet", "10.64.0.0/24", "1 label detached", 1),
        ("subnet_label", "OT network", "label deleted", 1),
    ]

    site = db_session.query(models.Site).filter_by(project_id=test_project.id, name="Plant").one()
    sites = f"/api/v1/projects/{test_project.id}/sites/{site.id}"
    assert client.patch(sites, json={"criticality_tier": 1, "expected_host_count": 40}).status_code == 200
    assert client.patch(sites, json={"criticality_tier": 1}).status_code == 200            # no change
    changed = _one(db_session, test_project, "site_changed")
    assert (changed.subject_id, changed.subject_label, changed.summary) == (
        site.id, "Plant", "criticality tier, expected host count",
    )


def test_a_names_import_and_a_names_deletion(client, db_session, test_project):
    names = f"/api/v1/projects/{test_project.id}/names"
    imported = client.post(f"{names}/import", json={"names": ["www.example.test", "mail.example.test", "not a name"]})
    assert imported.status_code == 200, imported.text
    assert imported.json()["names_created"] == 2
    line = _one(db_session, test_project, "names_imported")
    assert (line.count, line.summary, line.subject_id) == (2, "2 names added", None)

    # Importing the same list again adds nothing.
    client.post(f"{names}/import", json={"names": ["www.example.test", "mail.example.test"]})
    assert len(_lines(db_session, test_project, "names_imported")) == 1

    name = db_session.query(models.DNSName).filter_by(project_id=test_project.id, fqdn="www.example.test").one()
    name_id = name.id
    assert client.delete(f"{names}/{name_id}").status_code == 204
    deleted = _one(db_session, test_project, "name_deleted")
    assert (deleted.subject_type, deleted.subject_id, deleted.subject_label) == ("name", name_id, "www.example.test")
