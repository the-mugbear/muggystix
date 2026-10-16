"""Review Critical-2 — a failure between writing the upload and committing the
job row must NOT orphan the file on disk (it could be up to MAX_FILE_SIZE, and
there'd be no DB row to find it by). create_job now removes the whole per-job
dir on any failure."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import UploadFile

_VALID_XML = b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"></nmaprun>'


@pytest.mark.asyncio
async def test_validation_failure_removes_job_dir(db_session, test_project, monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "INGESTION_STORAGE_DIR", str(tmp_path))
    from app.services.ingestion_service import IngestionService

    svc = IngestionService()
    # A PNG masquerading as .xml fails the magic-byte check.
    upload = UploadFile(filename="scan.xml", file=io.BytesIO(b"\x89PNG\r\n\x1a\n not xml"))
    with pytest.raises(ValueError):
        await svc.create_job(db=db_session, upload=upload, submitted_by_id=None,
                             options={"project_id": test_project.id})
    # No per-job directory (and therefore no orphaned file) left behind.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_commit_failure_removes_job_dir(db_session, test_project, monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "INGESTION_STORAGE_DIR", str(tmp_path))
    from app.services.ingestion_service import IngestionService

    def boom():
        raise RuntimeError("simulated DB commit failure")
    monkeypatch.setattr(db_session, "commit", boom)

    svc = IngestionService()
    upload = UploadFile(filename="scan.xml", file=io.BytesIO(_VALID_XML))
    with pytest.raises(RuntimeError, match="commit failure"):
        await svc.create_job(db=db_session, upload=upload, submitted_by_id=None,
                             options={"project_id": test_project.id})
    # The written file + its dir are cleaned up — no untracked upload.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_registration_runs_off_the_event_loop(db_session, test_project, monkeypatch, tmp_path):
    """External review 2026-10-16, A2: the duplicate lookup (an advisory-lock
    wait), the insert and the commit ran on the event loop, so a slow
    registration stalled every other request on the API process.  They run
    in the thread pool now — where there is no running loop."""
    import asyncio

    from app.core.config import settings
    monkeypatch.setattr(settings, "INGESTION_STORAGE_DIR", str(tmp_path))
    from app.services.ingestion_service import IngestionService

    seen = {}
    real_find = IngestionService._find_duplicate
    real_commit = db_session.commit

    def on_loop() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def find(db, project_id, content_sha256, exclude_job_id=None):
        seen["find_on_loop"] = on_loop()
        return real_find(db, project_id, content_sha256, exclude_job_id)

    def commit():
        seen.setdefault("commit_on_loop", on_loop())
        return real_commit()

    monkeypatch.setattr(IngestionService, "_find_duplicate", staticmethod(find))
    monkeypatch.setattr(db_session, "commit", commit)
    svc = IngestionService()
    job = await svc.create_job(db=db_session, upload=UploadFile(filename="scan.xml", file=io.BytesIO(_VALID_XML)),
                               submitted_by_id=None, options={"project_id": test_project.id})
    assert job.id and job.status == "queued"
    assert seen == {"find_on_loop": False, "commit_on_loop": False}
