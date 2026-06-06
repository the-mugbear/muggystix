"""CR5-C2 — uploaded scan files are owner-only on disk.

Uploaded scans hold sensitive target/host detail.  The storage root may be a
shared/world-traversable bind mount (unprivileged deploys can't chown it), so
confidentiality has to come from the files: each per-job dir is 0700 and the
scan file 0600, owned by the app user.  Other local accounts can't read them.
"""
from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import pytest
from fastapi import UploadFile


@pytest.mark.asyncio
async def test_job_dir_is_0700_and_file_is_0600(db_session, test_project, monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "INGESTION_STORAGE_DIR", str(tmp_path))
    from app.services.ingestion_service import IngestionService

    svc = IngestionService()
    upload = UploadFile(
        filename="scan.xml",
        file=io.BytesIO(b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"></nmaprun>'),
    )
    job = await svc.create_job(
        db=db_session, upload=upload, submitted_by_id=None,
        options={"project_id": test_project.id},
    )

    dest = Path(job.storage_path)
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600, "scan file must be owner-only"
    assert stat.S_IMODE(os.stat(dest.parent).st_mode) == 0o700, "job dir must be owner-only"
