"""scripts/merge_duplicate_scanner_findings.py — the way past the refusal of
revision b2e5a8c1d4f6 (one scanner finding per issue).

An instance upgrading across v2.448.0 with two scanner findings for one issue
could not start: the revision refuses, the upgrade rolls back, and the only
instruction was to merge them by hand on the previous build (2026-10-02: a
70k-host instance, nine findings in two groups).  The script merges a group
into one finding without losing endpoints, comments, evidence or report text.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import models
from app.db.models import Annotation
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import EvidenceRecord


def _script():
    for base in (Path("/app/scripts"), Path("/repo/scripts"), Path(__file__).resolve().parents[2] / "scripts"):
        path = base / "merge_duplicate_scanner_findings.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("merge_duplicate_scanner_findings", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.skip("scripts/ is not mounted in this environment")


def test_the_scripts_predicate_is_the_migrations():
    merge = _script()
    for base in (Path("/app/alembic/versions"), Path(__file__).resolve().parents[1] / "alembic" / "versions"):
        source = base / "b2e5a8c1d4f6_findings_one_scanner_finding_per_issue.py"
        if source.is_file():
            declared = re.search(r'^_PREDICATE = "(.+)"$', source.read_text(), re.M).group(1)
            assert merge.PREDICATE == declared
            return
    pytest.skip("the revision file is not present")


def test_apply_moves_everything_onto_the_kept_finding(db_session, test_project, test_user, tmp_path):
    merge = _script()
    pid = test_project.id
    db_session.execute(text("DROP INDEX IF EXISTS uq_finding_scanner_issue"))
    scan = models.Scan(project_id=pid, filename="n.nessus", tool_name="nessus")
    a, b, c = (models.Host(project_id=pid, ip_address=f"10.81.0.{i}", state="up") for i in (1, 2, 3))
    db_session.add_all([scan, a, b, c])
    db_session.flush()

    def finding(status, on, **fields):
        row = Finding(project_id=pid, title="TLS certificate expired", severity="medium", status=status,
                      source="scanner", dedup_key="check:tls_cert_expired", **fields)
        db_session.add(row)
        db_session.flush()
        endpoints = {}
        for host in on:
            fh = FindingHost(finding_id=row.id, host_id=host.id, host_status="open")
            db_session.add(fh)
            db_session.flush()
            endpoints[host.id] = fh.id
        return row.id, endpoints

    first, first_eps = finding("open", [a, b], impact="Impact written on the first")
    kept, kept_eps = finding("confirmed", [b, c], description="Written on the confirmed one")
    third, third_eps = finding("open", [a], description="Must not replace the kept text")
    lone = Finding(project_id=pid, title="other", severity="low", status="open", source="scanner",
                   dedup_key="check:smb_signing_not_required")
    comment = Annotation(finding_id=third, user_id=test_user.id, body="seen on the third")
    on_repeat = EvidenceRecord(project_id=pid, host_id=a.id, tool="openssl", outcome="finding", summary="s",
                               finding_id=third, finding_host_id=third_eps[a.id])
    on_twin = EvidenceRecord(project_id=pid, host_id=b.id, tool="openssl", outcome="finding", summary="s",
                             finding_id=first, finding_host_id=first_eps[b.id])
    db_session.add_all([lone, comment, on_repeat, on_twin])
    db_session.flush()
    lone_id, comment_id, repeat_id, twin_id = lone.id, comment.id, on_repeat.id, on_twin.id

    lines = []
    result = merge.run(db_session.connection(), apply=True, backup_dir=str(tmp_path), out=lines.append)
    db_session.expire_all()

    assert result["groups"] == 1 and result["left"] == 0 and result["findings_deleted"] == 2
    left = db_session.query(Finding).filter(Finding.project_id == pid).order_by(Finding.id).all()
    assert [f.id for f in left] == sorted([kept, lone_id]), "the confirmed finding is kept; the other issue is untouched"
    merged = db_session.get(Finding, kept)
    # Its own text stays; only the empty field is filled from the others.
    assert merged.description == "Written on the confirmed one"
    assert merged.impact == "Impact written on the first"
    endpoints = db_session.query(FindingHost).filter(FindingHost.finding_id == kept).all()
    assert sorted(e.host_id for e in endpoints) == sorted([a.id, b.id, c.id]), "one endpoint per host, none lost"
    by_host = {e.host_id: e.id for e in endpoints}
    assert by_host[b.id] == kept_eps[b.id], "an endpoint the kept finding already had keeps its row"
    # Comments and evidence follow, and evidence names an endpoint that still exists.
    assert db_session.get(Annotation, comment_id).finding_id == kept
    for evidence_id, host in ((repeat_id, a), (twin_id, b)):
        record = db_session.get(EvidenceRecord, evidence_id)
        assert record.finding_id == kept and record.finding_host_id == by_host[host.id]

    saved = json.loads(Path(result["backup"]).read_text())
    assert sorted(f["id"] for f in saved["findings"]) == sorted([first, kept, third])
    assert {f["description"] for f in saved["findings"]} >= {"Must not replace the kept text"}
    # What the refused revision does next now succeeds.
    db_session.execute(text(
        f"CREATE UNIQUE INDEX uq_finding_scanner_issue ON findings (project_id, dedup_key) WHERE {merge.PREDICATE}"))


def test_the_operator_can_name_the_finding_to_keep(db_session, test_project, tmp_path):
    merge = _script()
    pid = test_project.id
    db_session.execute(text("DROP INDEX IF EXISTS uq_finding_scanner_issue"))
    ids = []
    for status in ("confirmed", "open"):
        row = Finding(project_id=pid, title="t", severity="low", status=status, source="scanner", dedup_key="k:1")
        db_session.add(row)
        db_session.flush()
        ids.append(row.id)
    confirmed, still_open = ids
    conn = db_session.connection()
    with pytest.raises(SystemExit):
        merge.run(conn, keep_ids=[999_999], apply=False, backup_dir=str(tmp_path), out=lambda _l: None)
    with pytest.raises(SystemExit):
        merge.run(conn, keep_ids=ids, apply=False, backup_dir=str(tmp_path), out=lambda _l: None)
    # Named: the open one is kept although the default would keep the confirmed one.
    merge.run(conn, keep_ids=[still_open], apply=True, backup_dir=str(tmp_path), out=lambda _l: None)
    db_session.expire_all()
    assert [f.id for f in db_session.query(Finding).filter(Finding.dedup_key == "k:1")] == [still_open]
