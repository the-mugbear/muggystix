"""Comprehensive review 2026-10-16, batch T3, A4 — upload folders no job
names.  (A3, the decision lock order, is in ``test_proposal_decide_locking.py``.)

An upload is written to its own folder before its job row exists: a process
that died in between, or a request cancelled while the file arrived, left a
folder nothing would ever remove; and a re-process whose copy or insert
failed left its copy.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import models
from app.services import staged_import_service as staged
from app.services.ingestion_service import ingestion_service


def _folder(root: Path, name: str, *, hours_ago: float) -> Path:
    folder = root / name
    folder.mkdir()
    (folder / "scan.xml").write_text("<nmaprun/>")
    then = time.time() - hours_ago * 3600
    for path in (folder / "scan.xml", folder):
        os.utime(path, (then, then))
    return folder


def test_the_sweep_removes_only_old_folders_no_job_names(db_session, test_project, tmp_path):
    orphan = _folder(tmp_path, "a" * 32, hours_ago=3)
    fresh = _folder(tmp_path, "b" * 32, hours_ago=0.1)           # an upload still arriving / registering
    named = _folder(tmp_path, "c" * 32, hours_ago=3)
    other = _folder(tmp_path, "not-a-job-folder", hours_ago=3)   # never the sweep's to touch
    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="scan.xml", original_filename="scan.xml",
        storage_path=str(named / "scan.xml"), status="completed", retry_count=0, options={},
    ))
    db_session.commit()

    assert staged.sweep_orphan_upload_dirs(db_session, root=tmp_path) == 1
    assert not orphan.exists()
    assert fresh.exists() and named.exists() and other.exists()
    # A second pass has nothing left to do.
    assert staged.sweep_orphan_upload_dirs(db_session, root=tmp_path) == 0


def test_a_folder_named_like_another_jobs_prefix_is_not_taken_for_it(db_session, test_project, tmp_path):
    """`<root>/<token>/` is matched with its separator: a job in `aaa…a1/`
    does not keep `aaa…a/` (nor the reverse)."""
    lone = _folder(tmp_path, "d" * 32, hours_ago=3)
    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="x", original_filename="x",
        storage_path=str(tmp_path / ("d" * 31 + "e") / "x"), status="completed", retry_count=0, options={},
    ))
    db_session.commit()
    assert staged.sweep_orphan_upload_dirs(db_session, root=tmp_path) == 1
    assert not lone.exists()


def test_a_failed_reprocess_leaves_no_copy(db_session, test_project, tmp_path, monkeypatch):
    src_dir = tmp_path / ("f" * 32)
    src_dir.mkdir()
    (src_dir / "scan.xml").write_text("<nmaprun/>")
    job = models.IngestionJob(
        project_id=test_project.id, filename="scan.xml", original_filename="scan.xml",
        storage_path=str(src_dir / "scan.xml"), status="completed", retry_count=0, options={},
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(ingestion_service, "_storage_root", tmp_path)

    def broken_copy(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(staged.shutil, "copy2", broken_copy)
    with pytest.raises(OSError):
        staged.reprocess_job(db_session, job, submitted_by_id=None, format_override=None, source_tool=None)
    # Only the original's folder is left.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f" * 32]
