"""The Quarto report templates (v2.380.0).

Templates live in the repository's root ``report-templates/`` folder — one
folder per template — mounted read-only at ``settings.REPORT_TEMPLATES_DIR``.
A folder is a template when it holds ``template.json``:

    {
      "title": "Penetration test report",
      "description": "…",
      "entry": "report.qmd",
      "formats": ["html", "docx", "qmd"],
      "images": {"fields": "all", "trailing": true},
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

``images`` says which evidence images the template prints — the written
fields whose placed images it prints, and whether it prints the rest in a
trailing block (``_image_declaration``).  It is what the report page quotes;
what a given report prints is measured from a fill (``printed_parts``), and
``image_declaration_problem`` says when the two disagree.

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
from app.services.report_text import REPORT_TEXT_FIELDS

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
    # Which evidence images the template prints (review 2026-10-01 S2), from
    # template.json → "images": the written fields whose placed images it
    # prints, and whether it prints the rest in a trailing evidence block.
    # What the report page tells an author; what a given report prints is
    # measured from a fill (``printed_parts``).
    image_fields: tuple = REPORT_TEXT_FIELDS
    image_trailing: bool = True

    @property
    def images(self) -> dict:
        return {"fields": list(self.image_fields), "trailing": self.image_trailing}

    def as_dict(self) -> dict:
        return {
            "name": self.name, "title": self.title, "description": self.description,
            "formats": list(self.formats), "assets": [dict(a) for a in self.assets],
            "scope_inline_max": self.scope_inline_max,
            "scope_domains_inline_max": self.scope_domains_inline_max,
            "evidence_records": self.evidence_records,
            "images": self.images,
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
    image_fields, image_trailing = _image_declaration(folder.name, data.get("images"))
    return ReportTemplate(
        name=folder.name, path=folder, title=str(data.get("title") or folder.name),
        description=str(data.get("description") or ""), entry=entry, formats=formats,
        postprocess=post, assets=assets,
        evidence_records=data.get("evidence_records") is True,
        image_fields=image_fields, image_trailing=image_trailing, **cutoffs,
    )


def _image_declaration(name: str, declared) -> tuple:
    """``template.json`` → ``"images": {"fields": …, "trailing": …}`` as
    ``(fields, trailing)``.

    ``fields``: ``"all"``, or a list of the written fields whose placed images
    the template prints (``[]``: none).  ``trailing``: whether it prints the
    images no field places (a loop over ``f.evidence`` with ``image(e)``).
    Left out entirely, a template is taken to print every image — placed ones
    in every field and the rest in a trailing block — which is what a
    template written before the key existed was assumed to do."""
    if declared is None:
        return tuple(REPORT_TEXT_FIELDS), True
    problem = (
        f"{name}: template.json: images must be {{\"fields\": \"all\" or a list of "
        f"{', '.join(REPORT_TEXT_FIELDS)}, \"trailing\": true or false}}"
    )
    if not isinstance(declared, dict) or set(declared) - {"fields", "trailing"}:
        raise TemplateError(problem + ".")
    if "fields" not in declared or "trailing" not in declared:
        raise TemplateError(problem + " — both keys, so nothing is assumed.")
    fields, trailing = declared["fields"], declared["trailing"]
    if fields == "all":
        fields = list(REPORT_TEXT_FIELDS)
    if (
        not isinstance(fields, list) or not isinstance(trailing, bool)
        or any(not isinstance(f, str) or f not in REPORT_TEXT_FIELDS for f in fields)
        or len(set(fields)) != len(fields)
    ):
        raise TemplateError(problem + ".")
    # In the one order of the fields, whatever order they were written in.
    return tuple(f for f in REPORT_TEXT_FIELDS if f in fields), trailing


def printed_parts(template: ReportTemplate, dataset: dict) -> dict:
    """What ``template`` prints of ``dataset``'s findings
    (``quarto_render.printed_parts``, with the template's uploaded files in
    place).  Raises ``quarto_render.RenderError`` when it cannot be filled."""
    return quarto_render.printed_parts(template.path, template.entry, dataset, asset_files(template))


def image_probe_dataset(dataset: dict, index: int = 0) -> dict:
    """A copy of ``dataset`` (a template's ``sample-data.json``) in which
    finding ``index`` has one image placed in EVERY written field — captioned
    ``in <field>`` — and one placed nowhere (``unplaced``), and no other
    finding has any: what ``image_declaration_problem`` and the shipped
    templates' render test check a declaration with.  Attachment ids 1…6;
    files ``evidence/<id>.png``."""
    import copy

    probe = copy.deepcopy(dataset)
    for finding in probe.get("findings") or []:
        finding["images"], finding["placed"], finding["evidence"] = [], {}, []
    finding = probe["findings"][index]
    for att_id, name in enumerate(REPORT_TEXT_FIELDS, start=1):
        entry = {"attachment_id": att_id, "file": f"evidence/{att_id}.png", "caption": f"in {name}"}
        finding[name] = f"Text for {name}.\n\n![](evidence:{att_id})\n"
        finding["placed"][name] = {str(att_id): dict(entry)}
        finding["images"].append({**entry, "placed_in": [name]})
    last = len(REPORT_TEXT_FIELDS) + 1
    loose = {"attachment_id": last, "file": f"evidence/{last}.png", "caption": "unplaced", "placed_in": []}
    finding["images"].append(loose)
    finding["evidence"] = [dict(loose)]
    return probe


def image_declaration_problem(template: ReportTemplate, dataset: dict) -> Optional[str]:
    """None when what ``template.json`` declares under ``images`` is what the
    template's ``.qmd`` does with ``dataset``; else the sentence saying how
    they differ.

    For a dataset in which a finding the template shows in detail has an
    image placed in EVERY written field and one placed nowhere (the shipped
    templates are tested with exactly that): the fields the template printed
    with images must be the declared ones, and it printed the unplaced image
    exactly when ``trailing`` is declared."""
    parts = printed_parts(template, dataset)
    printed = {
        name for fields in parts["fields"].values() for name, images in fields.items() if images
    }
    trailing = bool(parts["figures"])
    differences = []
    declared = set(template.image_fields)
    if printed != declared:
        extra, absent = sorted(printed - declared), sorted(declared - printed)
        if extra:
            differences.append(f"it prints the images placed in {', '.join(extra)}, which are not declared")
        if absent:
            differences.append(f"it declares {', '.join(absent)} but prints no image placed there")
    if trailing != template.image_trailing:
        differences.append(
            "it prints a trailing evidence block but declares \"trailing\": false" if trailing
            else "it declares \"trailing\": true but prints no trailing evidence block"
        )
    if not differences:
        return None
    return f"{template.name}: template.json: images does not match {template.entry}: {'; '.join(differences)}."


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
