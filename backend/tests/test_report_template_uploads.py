"""Uploading a report template's own images from the Reports page (v2.431.0).

Before this, a logo or cover image was installed only by copying it into the
read-only ``report-templates/<name>/`` folder on the server.  Now a global
administrator uploads it; it is stored under ``UPLOAD_DIR/template_assets``
and placed over the template's own in the render's private copy.  What is
pinned here:

* validation reads what the bytes ARE — PNG / JPEG signature and pixel size
  from the header, a real .docx without macros or escaping paths — against the
  template's declared guidance (size, minimum pixels; aspect is a warning);
* only a global administrator may change instance-wide branding, and every
  change is audited;
* an upload makes the asset present (a required one stops blocking), wins
  over a server-installed file in the render, and changes the template
  fingerprint — so an issued report refuses to re-render with other branding
  — while no upload leaves the fingerprint exactly as before.
"""
from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.main import app
from app.api.v1.endpoints.auth import get_current_user
from app.services import quarto_render
from app.services import report_template_service as templates
from app.services import template_asset_store as store


# --- real file bytes --------------------------------------------------------------

def png(width: int, height: int) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def docx(extra: dict = None, drop: tuple = (), content_type: str = "wordprocessingml.document.main") -> bytes:
    members = {
        "[Content_Types].xml": f'<Types><Override ContentType="application/vnd.openxmlformats-officedocument.{content_type}+xml"/></Types>',
        "word/document.xml": "<w:document/>",
        "word/styles.xml": "<w:styles/>",
        **(extra or {}),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in members.items():
            if name not in drop:
                zf.writestr(name, body)
    return buf.getvalue()


LOGO = {"id": "logo", "path": "img/logo.png", "label": "Company logo", "max_bytes": 2097152,
        "min_width": 400, "min_height": 100, "aspect": "3.4:1"}
COVER = {"id": "cover", "path": "img/cover.jpg", "label": "Title page image", "required": True,
         "min_width": 1200, "min_height": 800, "aspect": "3:2"}
STYLES = {"id": "reference-docx", "path": "branding/reference.docx", "replaces": "reference.docx",
          "label": "Word styles (reference.docx)"}
ICON = {"id": "icon", "path": "img/icon.svg", "label": "Icon"}


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    folder = root / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx"],
        "assets": [LOGO, COVER, STYLES, ICON],
    }))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    (folder / "reference.docx").write_bytes(b"shipped")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    return folder


def _asset(asset_id):
    return next(a for a in templates.get_template("pentest").assets if a["id"] == asset_id)


def _base(project):
    return f"/api/v1/projects/{project.id}/client-reports/templates/pentest/assets"


def _put(client, project, asset_id, data, filename="upload.bin"):
    return client.put(f"{_base(project)}/{asset_id}", files={"file": (filename, data, "application/octet-stream")})


# --- guidance declared by the template -------------------------------------------

def test_the_template_list_carries_each_assets_guidance(root):
    logo, cover, styles, icon = (_asset(i) for i in ("logo", "cover", "reference-docx", "icon"))
    assert (logo["kind"], logo["uploadable"], logo["max_bytes"], logo["min_width"], logo["aspect"]) == (
        "png", True, 2097152, 400, "3.4:1")
    assert (cover["kind"], cover["max_bytes"]) == ("jpeg", 5 * 1024 * 1024)  # the default for an image
    assert (styles["kind"], styles["uploadable"], styles["max_bytes"]) == ("docx", True, 10 * 1024 * 1024)
    # An SVG would carry script into the HTML report: server-installed only.
    assert (icon["kind"], icon["uploadable"]) == ("svg", False)
    assert all(a["source"] is None and a["upload"] is None for a in (logo, cover, styles, icon))


@pytest.mark.parametrize("bad", [
    {"max_bytes": 0}, {"max_bytes": "2MB"}, {"min_width": -1}, {"min_height": True}, {"aspect": "wide"}, {"aspect": "3:0"},
])
def test_bad_guidance_is_a_template_error(tmp_path, bad):
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "template.json").write_text(json.dumps({"assets": [{**LOGO, **bad}]}))
    with pytest.raises(quarto_render.TemplateAssetError):
        quarto_render.template_assets(folder)


# --- validation: what the bytes are -------------------------------------------------

def test_a_png_of_the_right_size_is_accepted_and_measured(root):
    facts, warnings = store.validate(_asset("logo"), png(680, 200))
    assert (facts["kind"], facts["width"], facts["height"]) == ("png", 680, 200)
    assert warnings == []  # 3.4:1


def test_an_image_of_another_shape_is_accepted_with_a_warning(root):
    _facts, warnings = store.validate(_asset("logo"), png(400, 400))
    assert len(warnings) == 1 and "3.4:1" in warnings[0] and "400 × 400" in warnings[0]


@pytest.mark.parametrize("data, match", [
    (jpeg(800, 200), "must be a PNG image \\(it is a JPEG image\\)"),
    (png(680, 200)[:-12], "not a complete PNG"),           # truncated: no IEND
    (b"<svg onload=alert(1)>", "not a complete PNG"),
    (b"", "empty"),
    (png(300, 100), "at least 400 × 100 px"),
    (png(680, 200) + b"\0" * 2097152, "at most 2.0 MB"),
])
def test_an_image_that_is_not_what_the_asset_needs_is_refused(root, data, match):
    with pytest.raises(store.AssetUploadError, match=match):
        store.validate(_asset("logo"), data)


def test_a_jpeg_cover_is_measured_from_its_frame_header(root):
    facts, warnings = store.validate(_asset("cover"), jpeg(1500, 1000))
    assert (facts["width"], facts["height"], warnings) == (1500, 1000, [])
    with pytest.raises(store.AssetUploadError, match="JPEG"):
        store.validate(_asset("cover"), png(1500, 1000))


def test_a_giant_pixel_count_is_refused_whatever_the_file_size(root):
    # A tiny file can claim billions of pixels (a decompression bomb for
    # whatever decodes it later).
    with pytest.raises(store.AssetUploadError, match="50 megapixels"):
        store.validate(_asset("cover"), jpeg(60000, 60000))


def test_a_word_styles_file_must_be_a_plain_docx(root):
    styles = _asset("reference-docx")
    facts, _ = store.validate(styles, docx())
    assert facts["kind"] == "docx"
    for data, match in (
        (b"not a zip", "not a valid archive"),
        (docx(drop=("word/styles.xml",)), "word/styles.xml is missing"),
        (docx(extra={"word/vbaProject.bin": "x"}), "macros"),
        (docx(extra={"../evil.txt": "x"}), "outside itself"),
        (docx(content_type="wordprocessingml.template.main"), "template \\(.dotx\\)"),
    ):
        with pytest.raises(store.AssetUploadError, match=match):
            store.validate(styles, data)


def test_an_svg_asset_cannot_be_uploaded(root):
    with pytest.raises(store.AssetUploadError, match="cannot be uploaded"):
        store.validate(_asset("icon"), b"<svg/>")


# --- the endpoints ----------------------------------------------------------------

def test_a_global_admin_uploads_and_the_asset_becomes_present(client, db_session, root, test_project):
    r = _put(client, test_project, "cover", jpeg(1500, 1000), "Our cover.jpg")
    assert r.status_code == 200, r.text
    body = r.json()
    cover = next(a for a in body["template"]["assets"] if a["id"] == "cover")
    assert cover["present"] is True and cover["installed"] is False and cover["source"] == "uploaded"
    assert cover["upload"]["width"] == 1500 and cover["upload"]["original_filename"] == "Our cover.jpg"
    assert cover["upload"]["uploaded_by"] == "test-admin"
    assert body["warnings"] == []
    # Stored outside the read-only template folder.
    assert not (root / "img" / "cover.jpg").exists()
    assert (store.root() / "pentest" / "cover.jpg").read_bytes() == jpeg(1500, 1000)
    audit = db_session.query(AuditLog).filter(AuditLog.action == "report_template_asset_uploaded").one()
    assert audit.details["asset"] == "cover" and audit.details["template"] == "pentest"


def test_a_refused_upload_says_why_and_stores_nothing(client, root, test_project):
    r = _put(client, test_project, "logo", jpeg(800, 200))
    assert r.status_code == 422 and "PNG" in r.json()["detail"]
    assert store.uploads("pentest") == {}


def test_an_upload_of_another_shape_is_stored_with_a_warning(client, root, test_project):
    r = _put(client, test_project, "logo", png(500, 500))
    assert r.status_code == 200 and "3.4:1" in r.json()["warnings"][0]


def test_only_a_global_admin_may_change_template_files(client, db_session, root, test_project):
    project_admin = User(
        id=501, username="padmin", email="padmin@example.com", full_name="P Admin", hashed_password="x",
        role=UserRole.MEMBER, is_active=True, is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(project_admin)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=501, role=ProjectRole.ADMIN.value))
    db_session.commit()
    _put(client, test_project, "cover", jpeg(1500, 1000))
    app.dependency_overrides[get_current_user] = lambda: project_admin
    assert _put(client, test_project, "cover", jpeg(1500, 1000)).status_code == 403
    assert client.delete(f"{_base(test_project)}/cover").status_code == 403
    # Anyone who may read the reports may see the preview.
    r = client.get(f"{_base(test_project)}/cover/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_unknown_assets_and_templates_are_not_found(client, root, test_project):
    assert _put(client, test_project, "nope", png(680, 200)).status_code == 404
    r = client.put(f"/api/v1/projects/{test_project.id}/client-reports/templates/other/assets/logo",
                   files={"file": ("a.png", png(680, 200), "image/png")})
    assert r.status_code == 422


def test_removing_an_upload_falls_back_to_the_installed_file(client, db_session, root, test_project):
    (root / "img").mkdir()
    (root / "img" / "logo.png").write_bytes(png(680, 200))
    assert _put(client, test_project, "logo", png(1360, 400)).status_code == 200
    assert _asset("logo")["source"] == "uploaded"
    r = client.delete(f"{_base(test_project)}/logo")
    assert r.status_code == 200
    logo = next(a for a in r.json()["template"]["assets"] if a["id"] == "logo")
    assert (logo["source"], logo["present"], logo["upload"]) == ("installed", True, None)
    assert client.delete(f"{_base(test_project)}/logo").status_code == 404
    assert db_session.query(AuditLog).filter(AuditLog.action == "report_template_asset_removed").count() == 1


def test_the_preview_serves_the_image_the_render_would_use(client, root, test_project):
    assert client.get(f"{_base(test_project)}/logo/preview").status_code == 404  # nothing yet
    (root / "img").mkdir()
    (root / "img" / "logo.png").write_bytes(png(680, 200))
    r = client.get(f"{_base(test_project)}/logo/preview")
    assert r.status_code == 200 and r.content == png(680, 200)
    assert r.headers["x-content-type-options"] == "nosniff"
    _put(client, test_project, "logo", png(1360, 400))
    assert client.get(f"{_base(test_project)}/logo/preview").content == png(1360, 400)  # the upload wins
    # Never an SVG, never the Word file.
    (root / "img" / "icon.svg").write_bytes(b"<svg/>")
    assert client.get(f"{_base(test_project)}/icon/preview").status_code == 404
    assert client.get(f"{_base(test_project)}/reference-docx/preview").status_code == 404


def test_an_uploaded_required_image_unblocks_preview(client, root, test_project):
    base = f"/api/v1/projects/{test_project.id}/client-reports"
    rid = client.post(base, json={"kind": "full"}).json()["id"]
    r = client.post(f"{base}/{rid}/preview", json={"format": "html"})
    assert r.status_code == 409 and "Template files" in r.json()["detail"]
    assert _put(client, test_project, "cover", jpeg(1500, 1000)).status_code == 200
    assert client.post(f"{base}/{rid}/preview", json={"format": "html"}).status_code == 202


# --- the render and the fingerprint -------------------------------------------------

def test_the_render_places_uploads_over_the_templates_own(client, root, test_project, tmp_path):
    (root / "img").mkdir()
    (root / "img" / "logo.png").write_bytes(b"installed")
    _put(client, test_project, "logo", png(680, 200))
    _put(client, test_project, "reference-docx", docx())
    template = templates.get_template("pentest")
    work = tmp_path / "work"
    quarto_render._copy_template(template.path, work)
    quarto_render._place_overrides(template.path, work, templates.asset_files(template))
    quarto_render._apply_replacements(template.path, work, templates.asset_files(template))
    assert (work / "img" / "logo.png").read_bytes() == png(680, 200)
    assert (work / "reference.docx").read_bytes() == docx()  # the uploaded Word styles replace the shipped
    assert (root / "img" / "logo.png").read_bytes() == b"installed"  # the template folder is untouched
    env = quarto_render.jinja_environment(template.path, overrides=templates.asset_files(template))
    assert str(env.globals["asset"]("cover")) == ""  # not uploaded, not installed
    assert str(env.globals["asset"]("logo")) == "img/logo.png"


def test_an_upload_changes_the_fingerprint_and_no_upload_leaves_it_as_before(client, root, test_project):
    before = templates.fingerprint(templates.get_template("pentest"))
    _put(client, test_project, "logo", png(680, 200))
    with_logo = templates.fingerprint(templates.get_template("pentest"))
    assert with_logo != before
    _put(client, test_project, "logo", png(1360, 400))
    assert templates.fingerprint(templates.get_template("pentest")) not in (before, with_logo)
    client.delete(f"{_base(test_project)}/logo")
    # Back to exactly the folder-only value that earlier issued reports hold.
    assert templates.fingerprint(templates.get_template("pentest")) == before


def test_a_half_written_upload_is_never_used(root):
    folder = store.root() / "pentest"
    folder.mkdir(parents=True)
    (folder / "logo.png").write_bytes(png(680, 200))  # no metadata: not complete
    assert store.uploads("pentest") == {}
    assert _asset("logo")["present"] is False


# --- two workers, one asset (review 2026-10-02, H3) -------------------------------
#
# Several API worker processes share the uploads volume.  `save` wrote
# `<file>.tmp` and `<meta>.tmp` under fixed names: two uploads of one asset
# shared them, so one rename took the file away from the other
# (FileNotFoundError), or one upload's metadata was published beside the
# other's bytes.  Threads stand in for the processes: the lock is `flock` on a
# descriptor each call opens for itself, which excludes threads the same way.

def _published(asset_id="logo"):
    """(sha256 of the published bytes, sha256 the published metadata states),
    or None when nothing is published."""
    import hashlib
    meta = store.uploads("pentest").get(asset_id)
    if meta is None:
        return None
    return hashlib.sha256(meta["file"].read_bytes()).hexdigest(), meta["sha256"]


def _leftovers():
    return sorted(p.name for p in (store.root() / "pentest").iterdir() if p.name.endswith(".tmp"))


def test_two_uploads_of_one_asset_at_once_both_succeed_and_agree(root, monkeypatch):
    import threading
    from pathlib import Path

    # Both uploads have written their temporary file before either publishes:
    # each thread waits once, at the first point where that is true — after
    # the temporary write in the old code, before taking the lock in the new.
    both_written = threading.Barrier(2)
    waited = threading.local()

    def wait_once():
        if not getattr(waited, "done", False):
            waited.done = True
            both_written.wait(timeout=10)

    write_bytes = Path.write_bytes

    def write_then_wait(path, data):
        result = write_bytes(path, data)
        if path.name.endswith(".tmp"):
            wait_once()
        return result

    monkeypatch.setattr(Path, "write_bytes", write_then_wait)
    real_lock = getattr(store, "_publication_lock", None)
    if real_lock is not None:
        def lock_after_both_wrote(*args, **kwargs):
            wait_once()
            return real_lock(*args, **kwargs)
        monkeypatch.setattr(store, "_publication_lock", lock_after_both_wrote)

    one, two = png(680, 200), png(700, 210)
    outcomes = []

    def upload(data):
        try:
            meta, _ = store.save("pentest", _asset("logo"), data, uploaded_by="admin")
            outcomes.append(meta["sha256"])
        except Exception as exc:  # the defect was a FileNotFoundError here
            outcomes.append(exc)

    threads = [threading.Thread(target=upload, args=(data,)) for data in (one, two)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not any(t.is_alive() for t in threads)
    assert not [o for o in outcomes if isinstance(o, Exception)], outcomes
    assert len(outcomes) == 2

    file_sha, meta_sha = _published()
    assert file_sha == meta_sha, "the metadata describes the bytes that are published"
    assert file_sha in outcomes
    assert _leftovers() == []
    # The lock file is not an upload, and does not change what is listed.
    assert set(store.uploads("pentest")) == {"logo"}
    assert set(store.overrides("pentest")) == {"logo"}


def test_an_upload_racing_a_removal_ends_whole_or_gone(root):
    import threading

    one, two = png(680, 200), png(700, 210)
    for _ in range(25):
        store.save("pentest", _asset("logo"), one, uploaded_by="admin")
        start = threading.Barrier(2)
        errors = []

        def run(action):
            try:
                start.wait(timeout=10)
                action()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(
                lambda: store.save("pentest", _asset("logo"), two, uploaded_by="admin"),)),
            threading.Thread(target=run, args=(lambda: store.remove("pentest", "logo"),)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        assert not errors, errors
        published = _published()
        if published is not None:  # the upload came second
            assert published[0] == published[1]
        else:  # the removal came second: neither the file nor its metadata
            folder = store.root() / "pentest"
            assert not (folder / "logo.json").exists() and not (folder / "logo.png").exists()
        assert _leftovers() == []


def test_a_failed_publication_leaves_no_temporary_file_and_the_previous_upload(root, monkeypatch):
    import os

    store.save("pentest", _asset("logo"), png(680, 200), uploaded_by="admin")
    before = _published()

    def refuse(src, dst):
        raise OSError("disk says no")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", refuse)
        with pytest.raises(OSError):
            store.save("pentest", _asset("logo"), png(700, 210), uploaded_by="admin")
    assert _leftovers() == []
    assert _published() == before


# --- a real render (report-worker image: Quarto installed, templates mounted) ------

def _shipped_root():
    from pathlib import Path
    here = Path(__file__).resolve()
    return next((p for p in (Path("/app/report-templates"), here.parents[2] / "report-templates")
                 if (p / "pentest" / "template.json").is_file()), None)


@pytest.mark.skipif(
    __import__("shutil").which("quarto") is None or _shipped_root() is None,
    reason="Quarto and the shipped templates are in the report-worker image",
)
def test_an_uploaded_logo_is_in_the_rendered_report(tmp_path):
    import base64

    shipped = _shipped_root() / "pentest"
    logo = tmp_path / "logo.png"
    logo.write_bytes(png(680, 200))
    sample = json.loads((shipped / "sample-data.json").read_text())
    files = quarto_render.render(
        shipped, "report.qmd", sample, ["html"], tmp_path / "out", asset_files={"logo": logo},
    )
    html = files["html"].read_text(encoding="utf-8")
    assert base64.b64encode(png(680, 200)).decode() in html
    assert 'class="bs-logo"' in html
