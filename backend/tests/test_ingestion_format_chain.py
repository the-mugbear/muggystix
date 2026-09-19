"""The format chain on an ingestion job (v2.351.0; staged-import phase A).

Pins:

* the format registry names every ``file_type`` the dispatcher can emit,
  and every registry key resolves to an importable parser;
* an operator override is the WHOLE attempt list — exactly the chosen
  parser, no fallback — and an unknown override yields no attempt;
* the worker records what it detected first and what actually parsed the
  file, and with an override records both the override and what it would
  have detected;
* a wrong override fails with a message that says so, not with another
  parser quietly taking over;
* the job schema and the Ingestion Results item carry the chain with labels.
"""
import re
from pathlib import Path
from types import SimpleNamespace

from app.db import models
from app.services import ingestion_service as ingestion_module
from app.services.format_registry import FORMATS, format_label, resolve_parser
from app.services.ingestion_service import IngestionService, ParseFailure

SERVICE_SRC = Path(ingestion_module.__file__).read_text()

NMAP_XML = b"""<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -sS 10.9.9.1" start="1700000000" version="7.94">
<host><status state="up"/><address addr="10.9.9.1" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="22"><state state="open"/><service name="ssh"/></port></ports>
</host>
<runstats><finished time="1700000100"/><hosts up="1" down="0" total="1"/></runstats>
</nmaprun>
"""


def test_registry_covers_every_file_type_the_dispatcher_emits():
    emitted = set(re.findall(r'attempts\.append\(\("([a-z_]+)"', SERVICE_SRC))
    assert emitted, "could not find any attempts.append((...)) in the dispatcher"
    missing = sorted(emitted - set(FORMATS))
    assert not missing, f"dispatcher emits file_types the registry does not name: {missing}"
    for key in FORMATS:
        assert resolve_parser(key) is not None, f"registry key {key} does not resolve to a parser"
    assert format_label("nmap_xml") == "Nmap XML"
    assert format_label("not_a_key") == "not_a_key"
    assert format_label(None) is None


def test_override_is_the_whole_attempt_list():
    svc = IngestionService()
    job = SimpleNamespace(original_filename="anything.txt", options={}, format_override="naabu_output")
    attempts = svc._build_parsing_attempts(job, b"10.0.0.1:22\n")
    assert [a[0] for a in attempts] == ["naabu_output"]
    # The same bytes with no override route by content/filename as before.
    plain = SimpleNamespace(original_filename="naabu.txt", options={}, format_override=None)
    assert "naabu_output" in [a[0] for a in svc._build_parsing_attempts(plain, b"10.0.0.1:22\n")]
    # Unknown override: nothing to try (the worker then fails visibly).
    bad = SimpleNamespace(original_filename="anything.txt", options={}, format_override="not_a_format")
    assert svc._build_parsing_attempts(bad, b"x") == []


def _job(db, project_id, path: Path, name: str, **fields) -> models.IngestionJob:
    job = models.IngestionJob(
        project_id=project_id, filename=name, original_filename=name,
        storage_path=str(path), status="processing", options={"project_id": project_id}, **fields,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_worker_records_detected_and_final_format(db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_bytes(NMAP_XML)
    job = _job(db_session, test_project.id, f, "scan.xml")
    result = IngestionService()._process_job(db_session, job)
    assert result["final_file_type"] == "nmap_xml"
    db_session.refresh(job)
    assert job.detected_file_type == "nmap_xml"
    assert job.format_override is None


def test_override_is_recorded_beside_what_was_detected(db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_bytes(NMAP_XML)
    # Masscan's parser reads nmap-shaped XML too, so this override parses.
    job = _job(db_session, test_project.id, f, "scan.xml", format_override="masscan_xml")
    result = IngestionService()._process_job(db_session, job)
    assert result["final_file_type"] == "masscan_xml"
    db_session.refresh(job)
    assert job.format_override == "masscan_xml"
    assert job.detected_file_type == "nmap_xml"  # what it would have tried without the override


def test_wrong_override_fails_visibly_instead_of_falling_back(db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_bytes(NMAP_XML)
    job = _job(db_session, test_project.id, f, "scan.xml", format_override="dnsx_json")
    try:
        IngestionService()._process_job(db_session, job)
    except ParseFailure as exc:
        assert "dnsx_json" in (exc.user_message or "") and "Review the format" in (exc.user_message or "")
    else:
        raise AssertionError("a wrong override must fail, not fall back to another parser")
    # No scan was produced by a fallback parser.
    assert db_session.query(models.Scan).filter(models.Scan.project_id == test_project.id).count() == 0


def test_results_item_and_job_schema_carry_the_chain_with_labels(client, db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_bytes(NMAP_XML)
    job = _job(db_session, test_project.id, f, "scan.xml")
    IngestionService()._process_job(db_session, job)
    job.status = "completed"
    job.final_file_type = "nmap_xml"
    job.source_tool = "nmap 7.94"
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results")
    if r.status_code == 404:
        r = client.get(f"/api/v1/projects/{test_project.id}/ingestion-results")
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["id"] == job.id)
    assert item["detected_file_type"] == "nmap_xml"
    assert item["detected_format_label"] == "Nmap XML"
    assert item["final_format_label"] == "Nmap XML"
    assert item["format_override"] is None
    assert item["source_tool"] == "nmap 7.94"

    j = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{job.id}")
    assert j.status_code == 200, j.text
    assert j.json()["final_file_type"] == "nmap_xml"
