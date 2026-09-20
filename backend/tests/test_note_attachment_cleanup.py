"""R6 — attachment files must not orphan on delete / failed commit.

Covers the reconciler (delete dirs with no surviving NoteAttachment row, but
never reap an in-flight upload via the grace window) and the per-note purge.
"""
import os
import time

from app.core.config import settings
from app.services import note_attachment_service as svc


def _root(tmp_path):
    return tmp_path / "note_attachments"


def test_reconcile_removes_aged_orphans_but_spares_in_flight(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    root = _root(tmp_path)

    # Aged orphan (no NoteAttachment row, older than the grace window) — reaped.
    aged = root / "900001"
    aged.mkdir(parents=True)
    (aged / "shot.png").write_bytes(b"\x89PNG")
    old = time.time() - svc._RECONCILE_GRACE_SECONDS - 60
    os.utime(aged, (old, old))

    # Fresh dir (within grace) — could be an upload mid-commit, so spared.
    fresh = root / "900002"
    fresh.mkdir(parents=True)

    removed = svc.reconcile_orphan_attachments(db_session)

    assert removed == 1
    assert not aged.exists()
    assert fresh.exists()


def test_purge_note_files_removes_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    d = _root(tmp_path) / "12345"
    d.mkdir(parents=True)
    (d / "a.png").write_bytes(b"\x89PNG")

    svc.purge_note_files(12345)

    assert not d.exists()


def test_purge_note_files_path_confined(tmp_path, monkeypatch):
    # A tampered note_id can't escape the attachments root.
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    assert svc._safe_note_dir(123) is not None


def test_an_unreadable_stored_file_names_its_cause(tmp_path, monkeypatch):
    """v2.374.4 — prod, after an uploads tree was copied in from the previous
    deployment: the copied 0700 dirs kept the copier's ownership, the app
    (uid 999) could not enter them, ``Path.exists()`` RAISED PermissionError and
    every note image answered a bare 500.  Missing is 404; unreadable is a 500
    that says what is wrong and how to fix it."""
    import pytest
    from pathlib import Path
    from fastapi import HTTPException
    from app.services.note_attachment_service import (
        UNREADABLE_UPLOADS_FIX, require_readable_file,
    )

    present = tmp_path / "img.png"
    present.write_bytes(b"x")
    require_readable_file(present, "Attachment")          # readable: no error

    with pytest.raises(HTTPException) as missing:
        require_readable_file(tmp_path / "gone.png", "Attachment")
    assert missing.value.status_code == 404

    def denied(self, *a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "exists", denied)
    with pytest.raises(HTTPException) as unreadable:
        require_readable_file(present, "Attachment")
    assert unreadable.value.status_code == 500
    assert UNREADABLE_UPLOADS_FIX in unreadable.value.detail
