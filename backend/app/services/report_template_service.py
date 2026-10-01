"""The Quarto report templates (v2.380.0).

Templates live in the repository's root ``report-templates/`` folder — one
folder per template — mounted read-only at ``settings.REPORT_TEMPLATES_DIR``.
A folder is a template when it holds ``template.json``:

    {
      "title": "Penetration test report",
      "description": "…",
      "entry": "report.qmd",
      "formats": ["html", "docx", "qmd"],
      "postprocess": {"docx": "scripts/fix-docx-report.py"},
      "assets": [{"id": "logo", "path": "img/logo.png", "label": "Company logo",
                  "description": "…where it appears…", "required": false,
                  "formats": ["html"], "note": "…"}]
    }

``assets`` are the template's own images (logos, cover art — never evidence).
Each is reported with ``present`` so the Reports page can say which are
installed before a report is generated; a REQUIRED one that is missing blocks
preview, issue and render.  Validation lives in ``quarto_render`` (the renderer
stays standalone for template authors).

Templates come from the repository, never from users: nothing here accepts an
uploaded template, and a name is only ever resolved to a folder directly under
the templates root.  A template's declared IMAGES may be uploaded (v2.431.0,
``template_asset_store``): they are stored outside the folder and placed over
the template's own in the render's private copy.

``fingerprint`` is a SHA-256 over every file in the folder (paths and bytes,
rendered output excluded), recorded on each issued report so its history says
exactly which template produced it — the folder is mounted, so it can change
between two reports without a rebuild.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from app.services import quarto_render, report_scope
from app.services import template_asset_store as asset_store

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# A template that still lists "pdf" (removed v2.407.0) simply loses it.
FORMATS = ("html", "docx", "qmd")
# Rendered output and editor state inside a template folder are not part of
# the template.
_SKIP_DIRS = {"_output", ".quarto", "__pycache__", ".git"}
_SKIP_SUFFIXES = ("_files",)


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class ReportTemplate:
    name: str
    path: Path
    title: str
    description: str
    entry: str
    formats: tuple
    postprocess: Dict[str, str] = field(default_factory=dict)
    assets: tuple = ()
    # v2.441.0 — over these, the report summarises the scope and names a
    # separate scope file instead of listing it (report_scope).
    # None: the template does not print the scope at all.
    scope_inline_max: Optional[int] = report_scope.DEFAULT_INLINE_MAX
    scope_domains_inline_max: Optional[int] = report_scope.DEFAULT_INLINE_MAX
    # Whether the template prints how each finding was confirmed (its
    # evidence records).  Opt-in: only a literal ``true`` in template.json.
    evidence_records: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name, "title": self.title, "description": self.description,
            "formats": list(self.formats), "assets": [dict(a) for a in self.assets],
            "scope_inline_max": self.scope_inline_max,
            "scope_domains_inline_max": self.scope_domains_inline_max,
            "evidence_records": self.evidence_records,
        }

    def missing_required_assets(self) -> List[dict]:
        return [a for a in self.assets if a["required"] and not a["present"]]


def templates_root() -> Path:
    return Path(settings.REPORT_TEMPLATES_DIR)


def _load(folder: Path) -> ReportTemplate:
    manifest = folder / "template.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TemplateError(f"{folder.name}: template.json is missing or unreadable ({exc}).")
    entry = str(data.get("entry") or "report.qmd")
    if "/" in entry or "\\" in entry or not entry.endswith(".qmd") or not (folder / entry).is_file():
        raise TemplateError(f"{folder.name}: entry '{entry}' is not a .qmd file in the template folder.")
    formats = tuple(f for f in (data.get("formats") or FORMATS) if f in FORMATS)
    if not formats:
        raise TemplateError(f"{folder.name}: no supported formats (html, docx, qmd).")
    post = {}
    for fmt, script in (data.get("postprocess") or {}).items():
        target = (folder / str(script)).resolve()
        if fmt in FORMATS and target.is_file() and target.is_relative_to(folder.resolve()):
            post[fmt] = str(script)
    uploaded = asset_store.uploads(folder.name)
    try:
        declared = quarto_render.template_assets(
            folder, data, overrides={k: m["file"] for k, m in uploaded.items()},
        )
    except quarto_render.TemplateAssetError as exc:
        raise TemplateError(f"{folder.name}: template.json: {exc}")
    # v2.431.0 — where each present file comes from: an upload (it wins) or
    # the server's template folder.
    assets = tuple(
        {
            **a,
            "source": "uploaded" if a["id"] in uploaded else ("installed" if a["installed"] else None),
            "upload": asset_store.public_info(uploaded[a["id"]]) if a["id"] in uploaded else None,
        }
        for a in declared
    )
    cutoffs = {}
    for key in ("scope_inline_max", "scope_domains_inline_max"):
        value = data.get(key, report_scope.DEFAULT_INLINE_MAX)
        # null: this template does not print the scope, so it never names a
        # scope file (and issuing never asks for one).  bool is an int in
        # Python; `true` is not a count.
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100000):
            raise TemplateError(
                f"{folder.name}: template.json: {key} must be a whole number from 0 to 100000, "
                "or null for a template that does not print the scope."
            )
        cutoffs[key] = value
    return ReportTemplate(
        name=folder.name, path=folder, title=str(data.get("title") or folder.name),
        description=str(data.get("description") or ""), entry=entry, formats=formats,
        postprocess=post, assets=assets,
        evidence_records=data.get("evidence_records") is True, **cutoffs,
    )


def list_templates() -> List[ReportTemplate]:
    root = templates_root()
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and _NAME.match(p.name)):
        if (folder / "template.json").is_file():
            try:
                out.append(_load(folder))
            except TemplateError:
                continue
    return out


def template_problems() -> List[Dict[str, str]]:
    """The folders under the templates root that are NOT offered, and why
    (v2.409.0).  ``list_templates`` skips them so one broken folder never
    hides the others, but skipping silently left a template author with a
    template that simply did not appear.  Folders starting with ``.`` or
    ``_`` are the author's own business and are not reported."""
    root = templates_root()
    if not root.is_dir():
        return []
    out: List[Dict[str, str]] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if folder.name.startswith((".", "_")):
            continue
        if not _NAME.match(folder.name):
            out.append({"name": folder.name, "error": (
                "The folder name must be lower-case letters, digits, '-' or '_' "
                "(at most 64 characters, starting with a letter or digit)."
            )})
        elif not (folder / "template.json").is_file():
            out.append({"name": folder.name, "error": "The folder has no template.json."})
        else:
            try:
                _load(folder)
            except TemplateError as exc:
                out.append({"name": folder.name, "error": str(exc)})
    return out


def get_template(name: Optional[str]) -> ReportTemplate:
    """The named template, or ``TemplateError`` — never a path outside the root."""
    name = (name or "").strip()
    if not _NAME.match(name):
        raise TemplateError(f"'{name}' is not a template name.")
    folder = templates_root() / name
    if not folder.is_dir() or not (folder / "template.json").is_file():
        raise TemplateError(f"There is no report template called '{name}'.")
    return _load(folder)


def default_template_name() -> Optional[str]:
    names = [t.name for t in list_templates()]
    if settings.REPORT_DEFAULT_TEMPLATE in names:
        return settings.REPORT_DEFAULT_TEMPLATE
    return names[0] if names else None


def template_files(template: ReportTemplate) -> List[Path]:
    """Every file that belongs to the template (sorted, relative paths)."""
    files = []
    for path in sorted(template.path.rglob("*")):
        rel = path.relative_to(template.path)
        if any(part in _SKIP_DIRS or part.endswith(_SKIP_SUFFIXES) for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            files.append(rel)
    return files


def asset_files(template: ReportTemplate) -> Dict[str, Path]:
    """The uploaded images the renderer uses for this template (v2.431.0)."""
    return asset_store.overrides(template.name)


def fingerprint(template: ReportTemplate) -> str:
    """The template's files — and, since v2.431.0, its uploaded images, so an
    issued report whose logo was replaced since refuses to re-render (a
    revision), exactly as when a file in the folder changed.  With no upload
    the value is what it always was, so earlier issued reports still match."""
    digest = hashlib.sha256()
    for rel in template_files(template):
        digest.update(rel.as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256((template.path / rel).read_bytes()).digest())
    for asset_id, path in sorted(asset_files(template).items()):
        digest.update(f"upload:{asset_id}".encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
