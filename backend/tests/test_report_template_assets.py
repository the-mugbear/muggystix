"""A report template's own images (logo, cover art) — template.json → assets.

The manifest names what the template expects; the Reports page lists each one
as installed or missing before a report is generated; a REQUIRED one that is
missing blocks preview, issue and render with a message naming where the file
goes; ``asset()`` gives the template the path only when the file is there.
Declarations are paths inside the template folder, never anything else.
"""
from __future__ import annotations

import json
import os

import pytest

from app.core.config import settings
from app.services import quarto_render
from app.services import report_template_service as templates
from app.services.quarto_render import RenderError, TemplateAssetError, template_assets

PNG = b"\x89PNG\r\n\x1a\n"  # enough for a presence check


def _template(folder, assets, qmd="---\ntitle: x\n---\n"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx", "pdf"],
        "assets": assets,
    }))
    (folder / "report.qmd").write_text(qmd)
    return folder


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "report-templates"
    root.mkdir()
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(root))
    return root


# --- the manifest ---------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "../logo.png", "img/../../logo.png", "/etc/logo.png", "img//logo.png", "./logo.png",
    "img\\logo.png", "img/logo.png)", "img/lo go.png", "_output/logo.png", "img/{{x}}.png",
])
def test_an_asset_path_outside_the_folder_or_unprintable_is_refused(tmp_path, path):
    folder = _template(tmp_path / "t", [{"id": "logo", "path": path}])
    with pytest.raises(TemplateAssetError):
        template_assets(folder)


def test_only_image_extensions_and_unique_ids_are_accepted(tmp_path):
    with pytest.raises(TemplateAssetError, match="not an image"):
        template_assets(_template(tmp_path / "a", [{"id": "logo", "path": "img/logo.qmd"}]))
    with pytest.raises(TemplateAssetError, match="twice"):
        template_assets(_template(tmp_path / "b", [
            {"id": "logo", "path": "img/a.png"}, {"id": "logo", "path": "img/b.svg"},
        ]))
    with pytest.raises(TemplateAssetError):
        template_assets(_template(tmp_path / "c", [{"id": "Logo!", "path": "img/a.png"}]))


def test_a_template_with_a_bad_declaration_is_not_offered(root):
    _template(root / "good", [{"id": "logo", "path": "img/logo.png"}])
    _template(root / "bad", [{"id": "logo", "path": "../../secret.png"}])
    assert [t.name for t in templates.list_templates()] == ["good"]
    with pytest.raises(templates.TemplateError, match="template.json"):
        templates.get_template("bad")


def test_present_means_a_regular_file_the_render_would_copy(tmp_path):
    folder = _template(tmp_path / "t", [
        {"id": "logo", "path": "img/logo.png", "label": "Company logo", "required": True},
        {"id": "cover", "path": "img/cover.jpg"},
        {"id": "linked", "path": "img/linked.png"},
        {"id": "dirlinked", "path": "art/x.png"},
    ])
    (folder / "img").mkdir()
    (folder / "img" / "logo.png").write_bytes(PNG)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    os.symlink(outside, folder / "img" / "linked.png")
    (tmp_path / "artdir").mkdir()
    (tmp_path / "artdir" / "x.png").write_bytes(PNG)
    os.symlink(tmp_path / "artdir", folder / "art")

    by_id = {a["id"]: a for a in template_assets(folder)}
    assert by_id["logo"]["present"] is True and by_id["logo"]["required"] is True
    assert by_id["logo"]["label"] == "Company logo"
    assert by_id["cover"]["present"] is False and by_id["cover"]["required"] is False
    # _copy_template skips symlinks: a linked file would be missing at render time.
    assert by_id["linked"]["present"] is False
    assert by_id["dirlinked"]["present"] is False
    assert by_id["cover"]["formats"] == ["html", "docx", "pdf"]


# --- the helper ------------------------------------------------------------------

def test_asset_returns_the_path_only_when_the_file_is_installed(tmp_path):
    qmd = '---\ntitle: x\n---\n<% if asset("logo") %>LOGO=<< asset("logo") >><% endif %>|'
    folder = _template(tmp_path / "t", [{"id": "logo", "path": "img/logo.png"}], qmd=qmd)
    assert "LOGO" not in quarto_render.render_source(folder, "report.qmd", {})
    (folder / "img").mkdir()
    (folder / "img" / "logo.png").write_bytes(PNG)
    # Printed as the path itself — not Markdown-escaped like data.
    assert "LOGO=img/logo.png|" in quarto_render.render_source(folder, "report.qmd", {})


def test_asset_refuses_an_undeclared_id(tmp_path):
    folder = _template(tmp_path / "t", [], qmd='---\ntitle: x\n---\n<< asset("logo") >>')
    with pytest.raises(RenderError, match="not declared"):
        quarto_render.render_source(folder, "report.qmd", {})


def test_the_renderer_refuses_a_missing_required_image_before_running_quarto(tmp_path):
    folder = _template(tmp_path / "t", [{"id": "logo", "path": "img/logo.png", "required": True}])
    with pytest.raises(RenderError, match="report-templates/t/img/logo.png"):
        quarto_render.render(folder, "report.qmd", {}, ["html"], tmp_path / "out", quarto="/nonexistent")


# --- the API ---------------------------------------------------------------------

def _base(project):
    return f"/api/v1/projects/{project.id}/client-reports"


def test_the_template_list_says_which_images_are_installed(client, root, test_project):
    folder = _template(root / "pentest", [
        {"id": "logo", "path": "img/logo.png", "label": "Company logo", "description": "Top of page one",
         "note": "Word header: reference.docx", "formats": ["html", "pdf"]},
        {"id": "cover", "path": "img/cover.png", "required": True},
    ])
    (folder / "img").mkdir()
    (folder / "img" / "logo.png").write_bytes(PNG)
    r = client.get(f"{_base(test_project)}/templates")
    assert r.status_code == 200, r.text
    assets = {a["id"]: a for a in r.json()[0]["assets"]}
    assert assets["logo"] == {
        "id": "logo", "path": "img/logo.png", "label": "Company logo", "description": "Top of page one",
        "note": "Word header: reference.docx", "required": False, "formats": ["html", "pdf"], "present": True,
    }
    assert assets["cover"]["present"] is False and assets["cover"]["required"] is True


def test_a_missing_required_image_blocks_preview_and_issue_and_names_the_file(client, root, test_project):
    folder = _template(root / "pentest", [
        {"id": "logo", "path": "img/logo.png", "label": "Company logo", "required": True},
        {"id": "cover", "path": "img/cover.png"},  # optional: never blocks
    ])
    r = client.post(_base(test_project), json={"kind": "full"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]

    for path, body in ((f"{rid}/preview", {"format": "html"}), (f"{rid}/issue", None)):
        r = client.post(f"{_base(test_project)}/{path}", json=body)
        assert r.status_code == 409, r.text
        assert "report-templates/pentest/img/logo.png" in r.json()["detail"]
        assert "cover" not in r.json()["detail"]

    (folder / "img").mkdir()
    (folder / "img" / "logo.png").write_bytes(PNG)
    r = client.post(f"{_base(test_project)}/{rid}/preview", json={"format": "html"})
    assert r.status_code == 202, r.text
