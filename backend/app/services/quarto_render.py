"""Render a client report with Quarto (v2.381.0).

Self-contained on purpose — stdlib + Jinja only, no BlueStick imports — so the
report worker uses it AND a template author can run it on their own machine
against sample data (see report-templates/pentest/Makefile):

    python3 backend/app/services/quarto_render.py report-templates/pentest \\
        --data report-templates/pentest/sample-data.json --to html --out /tmp/out

**How data reaches the document.**  A template's ``.qmd`` is a Jinja template
with delimiters that cannot collide with Quarto: ``<% … %>`` statements,
``<< … >>`` values, ``<# … #>`` comments.  It runs in Jinja's sandbox.

* Every ``<< value >>`` is ESCAPED for Markdown (all ASCII punctuation
  backslash-escaped, newlines folded) — a finding title such as
  ``{{< env DATABASE_URL >}}`` or ``[x](javascript:…)`` is printed as text,
  never read as a shortcode, link, raw block or attribute.
* Written Markdown (a finding's description, the executive summary, …) never
  enters the ``.qmd`` at all.  ``<< md(f, "description") >>`` emits an empty
  placeholder div; the Lua filter ``_bluestick/fields.lua`` (added to every
  render, listed AFTER ``quarto`` in the template's ``filters``) reads the text
  from ``data.json`` and parses it as GitHub Markdown with raw HTML and MATH
  off (``$HOME/bin:$PATH`` is text, never a formula — and no HTML render
  loads a math library, ``_FORMAT_ARGS``), then
  drops raw blocks, images, non-web links, headings and attributes.  One
  image form is kept: ``![alt](evidence:57)``, when the dataset's ``placed``
  map lists attachment 57 for that field of that finding (the builder
  decides; the filter never trusts the text) — it prints as a figure.
* A command line or a tool's output is printed with ``<< code(c, "command") >>``:
  the same kind of placeholder, which the filter replaces with a verbatim
  block built from the string — the text is never parsed at all.
* ``<< image(e) >>`` is a placeholder too: it names the file (a strict
  pattern) and the filter builds the figure, taking the caption from
  ``data.json``.  The filter numbers every figure — placed or printed by the
  template — "Figure N: …" in document order.
* ``<< plain(v) >>`` and ``<< asset("logo") >>`` are the only other ways out
  of escaping, and each validates its input against a strict pattern.
  ``asset`` prints a path from the template's OWN manifest
  (``template.json`` → ``assets``), never data.
* Reusable parts are ``<% include %>``s (their output is written straight
  through); a macro's output would be printed via ``<< >>`` and escaped.

**The Quarto run** happens in a fresh temporary directory holding a copy of
the template, the rendered ``.qmd``, ``data.json`` and the evidence images;
the environment is rebuilt from scratch (PATH, HOME/XDG/TMPDIR inside the work
directory, locale) so no secret from the worker's environment reaches it, and
each format has a timeout.  No execution engine runs (``engine: markdown``).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup

# format → (quarto --to, output suffix, media type)
FORMATS: Dict[str, tuple] = {
    "html": ("html", ".html", "text/html"),
    "docx": ("docx", ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    # No PDF (removed v2.407.0): the Word report carries the design (cover
    # page, header/footer, page numbers) and exports to PDF from Word; a
    # Typst PDF had none of it.
    # The Quarto source itself: a zip of exactly what Quarto would render —
    # the filled report.qmd, data.json, the filters, reference.docx, the
    # evidence screenshots and the template's images — so the report can be
    # re-rendered or reworked locally.  No Quarto run.
    "qmd": (None, "-source.zip", "application/zip"),
}

# Arguments every render of a format gets, whatever the template's front
# matter says (review 2026-10-01 S1).  HTML never typesets a formula, so
# Quarto never adds a math library to the page: by default that is MathJax
# from a CDN — a request from the client's browser and a script the report
# did not have before — and MathJax makes ``\href{javascript:…}{x}`` a link.
# Written text is read without math (quarto_fields.lua); this holds for a
# template's own text and for a template that leaves the option out.  The
# shipped templates set it in their front matter too, so the Quarto source
# bundle renders the same way on its own.
_FORMAT_ARGS: Dict[str, tuple] = {"html": ("-M", "html-math-method:plain")}

# Template-author files that are not part of a filled report's source: the
# Jinja partials are already included in report.qmd (and are Jinja, not
# Quarto), and the sample data / Makefile drive the template, not the report.
_BUNDLE_SKIP_TOP = {
    "partials", "branding", "sample-data.json", "sample-evidence", "Makefile", "template.json",
    ".gitignore", ".luarc.json",
}
# The images a template's sample-data.json refers to (``1.png`` …), used by
# the command line when no ``--evidence`` folder is given.
SAMPLE_EVIDENCE_DIR = "sample-evidence"

_BUNDLE_README = """\
# {title} — Quarto source

This folder is the report exactly as BlueStick rendered it.

    quarto render report.qmd --to html
    quarto render report.qmd --to docx && python3 scripts/fix-docx-report.py report.docx

For a PDF, export the Word report to PDF from Word.

Needs Quarto {quarto} or later (and Python 3 for the Word post-processor,
which frames the screenshots).

- report.qmd       the report's structure and every short value.
- data.json        the written text (descriptions, recommendations, the
                   executive summary …).  It is NOT in report.qmd: the filter
                   _bluestick/fields.lua inserts it while rendering, with raw
                   HTML and shortcodes disabled, so text from findings can
                   never run as Quarto source.  Edit the text here.  An image
                   placed in a section is written ![caption](evidence:57) and
                   prints only when that finding's "placed" map lists 57 for
                   that field; captions are each image's "caption".
- evidence/        the screenshots marked "In report".
- reference.docx   the Word styles (fonts, captions, header/footer).
"""


FIELDS_FILTER = Path(__file__).with_name("quarto_fields.lua")

_PUNCT = set(string.punctuation)
# ``\Z``, never ``$``: ``$`` also matches before a trailing newline, so
# "evidence/1.png\n" or a plain() value ending in one passed and was printed
# unescaped (review 2026-10-01 N5).
_KEY = re.compile(r"\A[a-z_]+(\.(\d+|[a-z_]+))*\Z")
_EVIDENCE = re.compile(r"\Aevidence/\d+\.(png|jpg|gif)\Z")
_WIDTH = re.compile(r"\A\d+(\.\d+)?(in|cm|mm|px|%)\Z")
_PLAIN = re.compile(r"\A[0-9A-Za-z .:+_-]*\Z")
_SKIP = {"_output", ".quarto", "__pycache__", ".git"}
# A template's own images (logo, cover art …) declared in template.json.  The
# path charset is narrow on purpose: ``asset()`` prints it unescaped into
# Markdown, so it must never hold a space, bracket, brace or quote.
_ASSET_ID = re.compile(r"\A[a-z][a-z0-9_-]{0,31}\Z")
_ASSET_PATH = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*\Z")
ASSET_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")
# An asset may instead REPLACE one of the template's own files when installed
# (``"replaces": "reference.docx"`` — an operator's Word styles over the
# shipped ones).  Only these kinds of file can be replaced.
REPLACEABLE_EXTENSIONS = ASSET_EXTENSIONS + (".docx",)
# v2.431.0 — the kinds of file an operator may UPLOAD for an asset (the
# Reports page), by the declared path's extension.  SVG, GIF and WebP stay
# server-installed only: an SVG carries script into the HTML report, and the
# Word pipeline expects PNG/JPEG.
ASSET_KINDS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".svg": "svg",
               ".gif": "gif", ".webp": "webp", ".docx": "docx"}
UPLOADABLE_KINDS = ("png", "jpeg", "docx")
DEFAULT_MAX_BYTES = {"png": 5 * 1024 * 1024, "jpeg": 5 * 1024 * 1024, "docx": 10 * 1024 * 1024}
MAX_ASSET_BYTES = 25 * 1024 * 1024
_ASPECT = re.compile(r"\A\d+(\.\d+)?:\d+(\.\d+)?\Z")


class RenderError(RuntimeError):
    """Quarto (or the template) failed; the message is safe to show."""


class TemplateAssetError(ValueError):
    """template.json declares an asset it may not (the message names it)."""


def template_assets(
    template_dir: Path, manifest: Optional[dict] = None, overrides: Optional[Dict[str, Path]] = None,
) -> List[dict]:
    """The images a template expects besides the findings' evidence, from
    ``template.json`` → ``assets``, each with ``installed``: the file is in the
    folder and would be copied into the render (a regular file, no symlink on
    the way — ``_copy_template`` skips symlinks) — and ``present``: installed,
    or an uploaded file in ``overrides`` ({asset id: file}, v2.431.0) that the
    render puts at the asset's path instead.

    Guidance for an upload (v2.431.0), optional per asset: ``max_bytes``,
    ``min_width`` / ``min_height`` (pixels) and ``aspect`` ("3:2"), reported
    with ``kind`` (png, jpeg, docx …) and ``uploadable``.

    An asset with ``replaces`` names another file of the template (e.g.
    ``reference.docx``): when the asset is installed, the render uses it in
    that file's place (``_apply_replacements``); when not, the shipped file.

    Raises ``TemplateAssetError`` for a declaration that is not a plain
    relative image path inside the folder (absolute, ``..``, a skipped folder,
    another extension), a ``replaces`` of a different kind of file, or a
    duplicate id."""
    if manifest is None:
        manifest = json.loads((template_dir / "template.json").read_text(encoding="utf-8"))
    raw = manifest.get("assets") or []
    if not isinstance(raw, list):
        raise TemplateAssetError("`assets` must be a list.")
    out: List[dict] = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise TemplateAssetError("Each asset must be an object with an id and a path.")
        asset_id = str(entry.get("id") or "")
        path = str(entry.get("path") or "")
        if not _ASSET_ID.match(asset_id):
            raise TemplateAssetError(f"Asset id '{asset_id[:40]}' is not a short lower-case name.")
        if asset_id in seen:
            raise TemplateAssetError(f"Asset id '{asset_id}' is declared twice.")
        seen.add(asset_id)
        parts = _asset_parts(asset_id, path)
        replaces = str(entry.get("replaces") or "")
        if replaces:
            _asset_parts(asset_id, replaces)
            ext = Path(replaces).suffix.lower()
            if ext not in REPLACEABLE_EXTENSIONS or Path(path).suffix.lower() != ext:
                raise TemplateAssetError(
                    f"Asset '{asset_id}': '{path}' cannot replace '{replaces}' "
                    f"(both must be the same kind of file: {', '.join(e[1:] for e in REPLACEABLE_EXTENSIONS)})."
                )
            if replaces == path:
                raise TemplateAssetError(f"Asset '{asset_id}' replaces itself.")
        elif not path.lower().endswith(ASSET_EXTENSIONS):
            raise TemplateAssetError(
                f"Asset '{asset_id}': '{path}' is not an image ({', '.join(e[1:] for e in ASSET_EXTENSIONS)})."
            )
        # Where the file appears: the rendered formats (the source bundle
        # carries every file anyway).
        rendered = [f for f, spec in FORMATS.items() if spec[0] is not None]
        formats = [f for f in (entry.get("formats") or rendered) if f in rendered]
        kind = ASSET_KINDS.get(Path(path).suffix.lower(), "")
        installed = _asset_present(template_dir, parts)
        override = (overrides or {}).get(asset_id)
        out.append({
            "id": asset_id,
            "path": path,
            "label": str(entry.get("label") or asset_id),
            "description": str(entry.get("description") or ""),
            "note": str(entry.get("note") or ""),
            "required": bool(entry.get("required", False)),
            "formats": formats,
            "replaces": replaces or None,
            "present": installed or bool(override is not None and override.is_file()),
            "installed": installed,
            "kind": kind,
            "uploadable": kind in UPLOADABLE_KINDS,
            **_upload_guidance(asset_id, entry, kind),
        })
    return out


def _upload_guidance(asset_id: str, entry: dict, kind: str) -> dict:
    """The optional size / shape guidance a template declares for an asset,
    validated: whole positive numbers, an aspect as "W:H"."""
    def whole(key: str, cap: int) -> Optional[int]:
        value = entry.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= cap:
            raise TemplateAssetError(f"Asset '{asset_id}': {key} must be a whole number from 1 to {cap}.")
        return value

    max_bytes = whole("max_bytes", MAX_ASSET_BYTES) or DEFAULT_MAX_BYTES.get(kind, MAX_ASSET_BYTES)
    aspect = entry.get("aspect")
    if aspect is not None:
        aspect = str(aspect)
        if not _ASPECT.match(aspect) or 0 in [float(x) for x in aspect.split(":")]:
            raise TemplateAssetError(f"Asset '{asset_id}': aspect must be width:height, e.g. \"3:2\".")
    return {
        "max_bytes": max_bytes,
        "min_width": whole("min_width", 20000),
        "min_height": whole("min_height", 20000),
        "aspect": aspect,
    }


def _asset_parts(asset_id: str, path: str) -> List[str]:
    """``path`` split into its parts, when it is a plain relative path inside
    the template folder (no absolute path, ``..``, or skipped folder)."""
    parts = path.split("/")
    if (
        not _ASSET_PATH.match(path)
        or any(p in ("", ".", "..") for p in parts)
        or any(p in _SKIP or p.endswith("_files") for p in parts)
    ):
        raise TemplateAssetError(
            f"Asset '{asset_id}': '{path[:80]}' is not a relative path inside the template folder."
        )
    return parts


def _bundle_files(work: Path) -> List[Path]:
    """The prepared render folder's files that make up the report's source,
    relative to it (see ``_BUNDLE_SKIP_TOP``)."""
    files = []
    for path in sorted(work.rglob("*")):
        rel = path.relative_to(work)
        if rel.parts[0] in _BUNDLE_SKIP_TOP or path.is_symlink() or not path.is_file():
            continue
        files.append(rel)
    return files


def _write_bundle(work: Path, files: List[Path], target: Path, dataset: dict) -> None:
    """Zip ``files`` from ``work`` under one top-level folder, with a README
    saying how to render it."""
    import zipfile

    folder = "report-source"
    title = str(((dataset.get("report") or {}).get("title")) or "Report")
    readme = _BUNDLE_README.format(title=title.replace("\n", " ")[:200], quarto=quarto_version() or "1.10")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{folder}/README.md", readme)
        for rel in files:
            zf.write(work / rel, f"{folder}/{rel.as_posix()}")


def _place_overrides(template_dir: Path, work: Path, overrides: Optional[Dict[str, Path]]) -> None:
    """In the render's copy of the template, put each uploaded file at its
    asset's declared path (over a server-installed one: the upload wins).
    Only declared ids; the template folder itself is never written."""
    if not overrides or not (template_dir / "template.json").is_file():
        return
    for a in template_assets(template_dir):
        source = overrides.get(a["id"])
        if source is not None and source.is_file():
            target = work / a["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def _apply_replacements(template_dir: Path, work: Path, overrides: Optional[Dict[str, Path]] = None) -> None:
    """In the render's copy of the template, put each PRESENT replacing
    asset (installed, or uploaded and already placed by ``_place_overrides``)
    in the place of the file it replaces (an operator's reference.docx over
    the shipped one).  An asset that is not present changes nothing."""
    if not (template_dir / "template.json").is_file():
        return
    for a in template_assets(template_dir, overrides=overrides):
        if a["replaces"] and a["present"]:
            target = work / a["replaces"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(work / a["path"], target)


def _asset_present(template_dir: Path, parts: List[str]) -> bool:
    node = template_dir
    for part in parts:
        node = node / part
        if node.is_symlink():
            return False
    return node.is_file()


def missing_required_assets(template_dir: Path, overrides: Optional[Dict[str, Path]] = None) -> List[dict]:
    """The declared, REQUIRED images neither in the folder nor uploaded."""
    if not (template_dir / "template.json").is_file():
        return []
    try:
        return [a for a in template_assets(template_dir, overrides=overrides) if a["required"] and not a["present"]]
    except (TemplateAssetError, ValueError, OSError) as exc:
        raise RenderError(f"The template '{template_dir.name}' declares unusable assets: {exc}") from exc


def missing_assets_message(template_name: str, missing: List[dict]) -> str:
    where = ", ".join(f"report-templates/{template_name}/{a['path']} ({a['label']})" for a in missing)
    return (
        f"The '{template_name}' template needs image(s) that are not installed: {where}. "
        "A global administrator uploads them under Template files on the Reports page, "
        "or puts them there on the server (the folder is mounted, no rebuild needed)."
    )


def _asset_factory(template_dir: Path, overrides: Optional[Dict[str, Path]] = None):
    assets = (
        {a["id"]: a for a in template_assets(template_dir, overrides=overrides)}
        if (template_dir / "template.json").is_file() else {}
    )

    def asset(asset_id: str) -> Markup:
        """The path of one of the template's declared images when the file is
        there, else '' — so ``<% if asset("logo") %>`` leaves the layout alone
        without one.  An undeclared id fails the render (a typo would
        otherwise drop the logo silently)."""
        found = assets.get(asset_id)
        if found is None:
            raise RenderError(f"asset(): '{asset_id}' is not declared in template.json.")
        return Markup(found["path"] if found["present"] else "")
    return asset


# ---------------------------------------------------------------------------
# Escaping and the template helpers
# ---------------------------------------------------------------------------

def escape_md(value: Any) -> str:
    """Plain text → Markdown that reads as exactly that text, inline."""
    text = str(value)
    text = "".join(" " if ch in "\r\n\t\v\f" else ch for ch in text)
    text = "".join(ch for ch in text if ch >= " " or ch == " ")
    return "".join("\\" + ch if ch in _PUNCT else ch for ch in text)


def _finalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Markup):
        return value
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    return escape_md(value)


TODO_PREFIX = "TODO:"


def todo(text: str, block: bool = False) -> Markup:
    """A highlighted, searchable placeholder for something the report still
    needs (v2.382.0).  Always starts with ``TODO:`` so a reviewer can search
    the document for it; ``_bluestick/fields.lua`` renders it highlighted in
    every format.  ``block`` puts it in its own paragraph."""
    span = f"[{escape_md(f'{TODO_PREFIX} {text}')}]{{.bs-todo}}"
    return Markup(f"\n\n{span}\n\n" if block else span)


def _lookup(dataset: dict, key: str) -> Any:
    node: Any = dataset
    for part in key.split("."):
        if isinstance(node, list) and part.isdigit():
            idx = int(part)
            node = node[idx] if idx < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


#: What ``printed_parts`` hands the helpers: called with ``("md", key,
#: images)``, ``("figure", file)`` or ``("code", key)`` each time a template
#: asks for one.  None for an ordinary fill.
Recorder = Optional[Callable[..., None]]


def _md_factory(dataset: dict, record: Recorder = None):
    def md(obj: Any, field: Optional[str] = None, todo_text: Optional[str] = None, **kwargs) -> Markup:
        """A placeholder the Lua filter fills with the written Markdown at
        ``obj._path + "." + field`` (or the dotted path ``obj``) in data.json.
        With ``todo=`` (or ``todo_text=``), an empty value prints that TODO
        instead.

        An image the author placed in the text (``![…](evidence:57)``, one of
        the finding's own images marked "In report" — the dataset's
        ``placed`` map says which) prints as a numbered figure, ``image_width``
        wide (default 6in).  ``images=False`` is for a template that prints no
        evidence: placed images are left out of that field."""
        todo_text = kwargs.pop("todo", todo_text)
        images = kwargs.pop("images", True)
        image_width = kwargs.pop("image_width", None)
        if kwargs:
            raise RenderError(f"md(): unknown argument(s) {', '.join(kwargs)}.")
        if isinstance(obj, dict):
            base = obj.get("_path")
            if not isinstance(base, str) or not field:
                raise RenderError("md() needs a finding (or other item) and a field name.")
            key = f"{base}.{field}"
        else:
            key = str(obj)
        if not _KEY.match(key):
            raise RenderError(f"md(): '{key}' is not a data path.")
        if image_width is not None and not _WIDTH.match(str(image_width)):
            raise RenderError(f"md(): '{image_width}' is not a width.")
        if record is not None:
            record("md", key, bool(images))
        value = _lookup(dataset, key)
        if not (isinstance(value, str) and value.strip()):
            return todo(todo_text, block=True) if todo_text else Markup("")
        attrs = f'key="{key}"'
        if not images:
            attrs += ' images="none"'
        if image_width is not None:
            attrs += f' image-width="{image_width}"'
        return Markup(f'\n\n::: {{.bs-md {attrs}}}\n:::\n\n')
    return md


def _code_factory(dataset: dict, record: Recorder = None):
    def code(obj: Any, field: Optional[str] = None) -> Markup:
        """A placeholder the Lua filter fills with the value at
        ``obj._path + "." + field`` as a VERBATIM block (review 2026-10-01
        B8) — a command line as it was run, a tool's output.  The text is
        never parsed: the filter builds the code block from the string, after
        Quarto's own filters, so backticks, fences, shortcodes and raw HTML
        in it are characters.  Nothing is printed for an empty value."""
        if isinstance(obj, dict):
            base = obj.get("_path")
            if not isinstance(base, str) or not field:
                raise RenderError("code() needs an item with a data path and a field name.")
            key = f"{base}.{field}"
        else:
            key = str(obj)
        if not _KEY.match(key):
            raise RenderError(f"code(): '{key}' is not a data path.")
        if record is not None:
            record("code", key)
        value = _lookup(dataset, key)
        if not (isinstance(value, str) and value.strip()):
            return Markup("")
        return Markup(f'\n\n::: {{.bs-code key="{key}"}}\n:::\n\n')
    return code


def md(obj: Any, field: Optional[str] = None) -> Markup:
    """The placeholder alone, without the empty check (kept for callers that
    have no dataset)."""
    if isinstance(obj, dict):
        base = obj.get("_path")
        if not isinstance(base, str) or not field:
            raise RenderError("md() needs a finding (or other item) and a field name.")
        key = f"{base}.{field}"
    else:
        key = str(obj)
    if not _KEY.match(key):
        raise RenderError(f"md(): '{key}' is not a data path.")
    return Markup(f'\n\n::: {{.bs-md key="{key}"}}\n:::\n\n')


def image(item: Any, width: str = "6in", number: Optional[int] = None) -> Markup:
    """An evidence image the renderer placed in the work directory, as a
    placeholder the Lua filter turns into a figure: the file from here (it
    matches a strict pattern), the caption from ``data.json`` — like written
    text, a caption never enters the ``.qmd``.

    Every figure is captioned "Figure N: …" by the filter, counted in
    document order across the images an author placed in the text and the
    ones printed here.  ``number`` is accepted and IGNORED: a template written
    when Jinja counted the figures (``image(e, number=counter.figure)``,
    v2.410.0) keeps rendering, with the filter's numbers."""
    if not isinstance(item, dict) or not _EVIDENCE.match(str(item.get("file", ""))):
        raise RenderError("image() takes an item from a finding's `evidence` list.")
    if not _WIDTH.match(width):
        raise RenderError(f"image(): '{width}' is not a width.")
    if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number < 1):
        raise RenderError("image(): number must be a positive whole number.")
    return Markup(f'\n\n::: {{.bs-figure file="{item["file"]}" width="{width}"}}\n:::\n\n')


def plain(value: Any) -> Markup:
    """A value known to be plain (a date, a number, a reference) — for YAML
    and attributes, where backslash escapes do not apply."""
    text = "" if value is None else str(value)
    if not _PLAIN.match(text):
        raise RenderError(f"plain(): '{text[:40]}' contains characters that need escaping.")
    return Markup(text)


def _image_factory(record: Recorder = None):
    if record is None:
        return image

    def recorded(item: Any, width: str = "6in", number: Optional[int] = None) -> Markup:
        out = image(item, width, number)
        record("figure", item["file"])
        return out
    return recorded


def jinja_environment(
    template_dir: Path, dataset: Optional[dict] = None, overrides: Optional[Dict[str, Path]] = None,
    record: Recorder = None,
) -> SandboxedEnvironment:
    """Includes resolve inside the template folder only (FileSystemLoader
    refuses ``..``).  Use includes, not macros, for reusable parts: a macro's
    result is printed through ``<< >>`` and so escaped like data."""
    env = SandboxedEnvironment(
        loader=FileSystemLoader(str(template_dir)),
        block_start_string="<%", block_end_string="%>",
        variable_start_string="<<", variable_end_string=">>",
        comment_start_string="<#", comment_end_string="#>",
        undefined=StrictUndefined, finalize=_finalize, autoescape=False,
        trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
    )
    try:
        asset = _asset_factory(template_dir, overrides)
    except (TemplateAssetError, ValueError, OSError) as exc:
        raise RenderError(f"The template '{template_dir.name}' declares unusable assets: {exc}") from exc
    env.globals.update(
        md=_md_factory(dataset, record) if dataset is not None else md,
        code=_code_factory(dataset or {}, record),
        image=_image_factory(record), plain=plain, todo=todo, asset=asset,
    )
    return env


class _Watched(list):
    """A list that says when a template reads its ITEMS (a loop, an index, a
    filter) — not when it only asks whether there are any, or how many."""

    def __init__(self, items, on_use: Callable[[], None]):
        super().__init__(items)
        self._on_use = on_use

    def __iter__(self):
        self._on_use()
        return super().__iter__()

    def __getitem__(self, index):
        self._on_use()
        return super().__getitem__(index)


_FINDING_KEY = re.compile(r"\Afindings\.(\d+)\.([a-z_]+)\Z")
_CONFIRMATION_KEY = re.compile(r"\Afindings\.(\d+)\.confirmations\.")


def printed_parts(
    template_dir: Path, entry: str, dataset: dict, overrides: Optional[Dict[str, Path]] = None,
) -> Dict[str, Any]:
    """What THIS template prints of THIS dataset's findings — measured, by
    filling the template (Jinja only; no Quarto run) and noting what it asks
    the helpers for (review 2026-10-01 S2):

    * ``fields``: ``{finding index: {field: images}}`` for every ``md(f,
      field)`` the template printed; ``images`` is False for
      ``images=False``.  The filter prints a field's placed images exactly
      when the template printed that field with images.
    * ``figures``: the files the template printed with ``image(e)`` — the
      trailing evidence block.
    * ``confirmations``: the indexes of the findings whose ``confirmations``
      the template read (looped over), or printed a ``code()`` block from.
    * ``findings``: the indexes with any of the above — the findings the
      report shows in detail, not just as a table row.

    A template decides these with its own logic (a brief that prints no
    evidence, a worklist that prints only the recommendation of findings
    still to fix, an addendum that lists a known finding in one line), so
    only a fill can say.  Raises ``RenderError`` when the template cannot be
    filled."""
    fields: Dict[int, Dict[str, bool]] = {}
    figures: set = set()
    confirmations: set = set()

    def record(kind: str, value: str, images: bool = False) -> None:
        if kind == "md":
            m = _FINDING_KEY.match(value)
            if m:
                seen = fields.setdefault(int(m.group(1)), {})
                seen[m.group(2)] = seen.get(m.group(2), False) or images
        elif kind == "figure":
            figures.add(value)
        elif kind == "code":
            m = _CONFIRMATION_KEY.match(value)
            if m:
                confirmations.add(int(m.group(1)))

    probe = dict(dataset)
    findings = []
    for index, finding in enumerate(dataset.get("findings") or []):
        if isinstance(finding, dict) and isinstance(finding.get("confirmations"), list):
            finding = {**finding, "confirmations": _Watched(
                finding["confirmations"], lambda index=index: confirmations.add(index),
            )}
        findings.append(finding)
    probe["findings"] = findings
    env = jinja_environment(template_dir, probe, overrides, record)
    try:
        env.get_template(entry).render(**probe)
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"The template '{template_dir.name}' could not be filled: {exc}") from exc

    file_owner: Dict[str, set] = {}
    for index, finding in enumerate(findings):
        if isinstance(finding, dict):
            for item in finding.get("evidence") or []:
                if isinstance(item, dict):
                    file_owner.setdefault(str(item.get("file")), set()).add(index)
    shown = set(fields) | confirmations
    for file in figures:
        shown |= file_owner.get(file, set())
    return {
        "fields": fields, "figures": sorted(figures),
        "confirmations": sorted(confirmations), "findings": sorted(shown),
    }


def render_source(
    template_dir: Path, entry: str, dataset: dict, overrides: Optional[Dict[str, Path]] = None,
) -> str:
    env = jinja_environment(template_dir, dataset, overrides)
    try:
        # Quarto reads the YAML front matter only at the very top.
        return env.get_template(entry).render(**dataset).lstrip()
    except RenderError:
        raise
    except Exception as exc:  # a template error names the template, not the data
        raise RenderError(f"The template '{template_dir.name}' could not be filled: {exc}") from exc


# ---------------------------------------------------------------------------
# The Quarto run
# ---------------------------------------------------------------------------

def _copy_template(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if any(part in _SKIP or part.endswith("_files") for part in rel.parts):
            continue
        if path.is_symlink():
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def _place_evidence(dataset: dict, work: Path, resolve: Callable[[dict], Optional[Path]]) -> List[str]:
    """Copy each finding's images into ``work/evidence`` and drop the entries
    whose file cannot be found (the render would fail on them).

    A finding's images are its ``images`` list — every image marked for the
    report — of which ``evidence`` is the part no section places (the
    trailing block) and ``placed`` the part a section does
    (``{field: {attachment id: {file, caption}}}``).  A dataset frozen before
    images could be placed has ``evidence`` alone.  A missing file leaves all
    three, so a reference to it in the text prints as its alt text.

    An image the dataset marks ``"printed": false`` (review 2026-10-01 S2 —
    this report's template prints it nowhere: ``client_report_service``
    measures that with ``printed_parts``) is not needed: its file is neither
    looked for nor copied, it is never "missing", and its entries stay as
    they are.  An issued report keeps no copy of such an image.  A dataset
    without the key (frozen before it existed) needs every image, as before."""
    missing = []
    (work / "evidence").mkdir(exist_ok=True)
    for finding in dataset.get("findings") or []:
        present: Dict[str, bool] = {}
        marks: Dict[str, bool] = {}
        for item in finding.get("images") or []:
            if isinstance(item, dict) and isinstance(item.get("printed"), bool):
                file = str(item.get("file", ""))
                # Listed twice with different marks: needed.
                marks[file] = marks.get(file, False) or item["printed"]
        unprinted = {file for file, printed in marks.items() if not printed}

        def have(item: Any) -> bool:
            file = str(item.get("file", "")) if isinstance(item, dict) else ""
            if not _EVIDENCE.match(file):
                return False
            if file in unprinted:
                return True
            if file not in present:
                source = resolve(item)
                present[file] = bool(source is not None and source.is_file())
                if present[file]:
                    shutil.copyfile(source, work / file)
                else:
                    missing.append(str(item.get("caption") or file))
            return present[file]

        for key in ("images", "evidence"):
            if isinstance(finding.get(key), list) or key == "evidence":
                finding[key] = [item for item in finding.get(key) or [] if have(item)]
        placed = finding.get("placed")
        if isinstance(placed, dict):
            finding["placed"] = {
                field: {
                    str(att_id): item for att_id, item in refs.items()
                    if have({"attachment_id": _as_int(att_id), **item} if isinstance(item, dict) else item)
                }
                for field, refs in placed.items() if isinstance(refs, dict)
            }
    return missing


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_env(work: Path) -> Dict[str, str]:
    home = work / ".home"
    tmp = work / ".tmp"
    for d in (home, tmp):
        d.mkdir(exist_ok=True)
    return {
        "PATH": os.pathsep.join(p for p in ("/opt/quarto/bin", "/usr/local/bin", "/usr/bin", "/bin")),
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def quarto_version(quarto: str = "quarto", timeout: float = 60) -> Optional[str]:
    """Quarto's version, or None when it cannot be asked.  Run like a render
    (``_run_group``, defined below): ``quarto`` is a bash launcher that starts
    deno without ``exec``, so ``subprocess.run(timeout=…)`` killed bash and
    left deno running — and then waited on the pipes deno still held."""
    with tempfile.TemporaryDirectory(prefix="bs-qv-") as tmp:
        work = Path(tmp)
        try:
            _code, stdout, _stderr = _run_group(
                [quarto, "--version"], cwd=work, env=_clean_env(work), timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    return stdout.strip()[:40] or None


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


#: After SIGTERM, how long a timed-out render's process group gets before SIGKILL.
KILL_GRACE_SECONDS = 3.0


def _kill_group(proc: "subprocess.Popen") -> None:
    """End every process of ``proc``'s group: TERM, a short wait, then KILL."""
    for sig, wait in ((signal.SIGTERM, KILL_GRACE_SECONDS), (signal.SIGKILL, KILL_GRACE_SECONDS)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return  # the whole group is gone
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            proc.poll()  # reap the leader so the group can empty
            try:
                os.killpg(proc.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def _run_group(cmd: List[str], *, cwd: Path, env: Dict[str, str], timeout: float):
    """Run ``cmd`` in its OWN process group → ``(exit code, stdout, stderr)``.

    On timeout the whole group is killed and reaped before
    ``subprocess.TimeoutExpired`` is raised (review 2026-10-01 R14).
    ``subprocess.run(timeout=…)`` kills only the process it started, and
    ``quarto`` is a bash launcher that runs deno WITHOUT ``exec``: bash died,
    and deno (with pandoc under it) carried on as an orphan — still rendering
    into a work directory that had been deleted, holding its memory and CPU
    while the worker moved on to the next job, one more for every timeout."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            # The pipes close once every holder is dead; bounded all the same.
            proc.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            proc.wait()
        raise
    except BaseException:
        _kill_group(proc)
        proc.wait()
        raise
    return proc.returncode, stdout, stderr


def render(
    template_dir: Path,
    entry: str,
    dataset: dict,
    formats: Iterable[str],
    out_dir: Path,
    *,
    basename: str = "report",
    resolve_evidence: Optional[Callable[[dict], Optional[Path]]] = None,
    postprocess: Optional[Dict[str, str]] = None,
    timeout: int = 300,
    quarto: str = "quarto",
    strict_evidence: bool = False,
    asset_files: Optional[Dict[str, Path]] = None,
) -> Dict[str, Path]:
    """Render ``dataset`` with the template into ``out_dir`` → {format: file}.

    ``asset_files`` ({asset id: file}, v2.431.0): uploaded template images,
    used in place of (or instead of a missing) server-installed file.

    ``strict_evidence`` (an ISSUED report): an evidence image that cannot be
    found fails the render instead of being dropped.  A draft preview drops it
    — the draft is live — but an issued report that silently lost an image
    would no longer be the document that was signed off (review 2026-09-23
    C4)."""
    formats = [f for f in formats if f in FORMATS]
    if not formats:
        raise RenderError("No format to render.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", basename):
        raise RenderError("Unusable output file name.")
    missing_assets = missing_required_assets(template_dir, asset_files)
    if missing_assets:
        raise RenderError(missing_assets_message(template_dir.name, missing_assets))
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(json.dumps(dataset))  # a private copy; evidence is pruned below
    with tempfile.TemporaryDirectory(prefix="bs-report-") as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        _copy_template(template_dir, work)
        _place_overrides(template_dir, work, asset_files)
        _apply_replacements(template_dir, work, asset_files)
        (work / "_bluestick").mkdir(exist_ok=True)
        shutil.copyfile(FIELDS_FILTER, work / "_bluestick" / "fields.lua")
        missing = _place_evidence(dataset, work, resolve_evidence or (lambda _item: None))
        if missing and strict_evidence:
            raise RenderError(
                "Evidence images of this issued report are missing from its storage, or no "
                f"longer the files that were issued: {', '.join(missing[:10])}"
                f"{' …' if len(missing) > 10 else ''}. "
                "Restore them (uploads/client_reports, from a backup), or revise the report."
            )
        (work / "data.json").write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
        source_name = "report.qmd"
        (work / source_name).write_text(render_source(template_dir, entry, dataset, asset_files), encoding="utf-8")
        if entry != source_name and (work / entry).exists():
            (work / entry).unlink()
        # What the source bundle holds — taken now, before any format adds
        # its output (report.html, report_files/, .quarto/ …) to the folder.
        source_files = _bundle_files(work)

        results: Dict[str, Path] = {}
        env = _clean_env(work)
        for fmt in formats:
            to, suffix, _media = FORMATS[fmt]
            produced = work / f"report{suffix}"
            if produced.exists():
                produced.unlink()
            if to is None:
                _write_bundle(work, source_files, produced, dataset)
                target = out_dir / f"{basename}{suffix}"
                shutil.copyfile(produced, target)
                results[fmt] = target
                continue
            try:
                code, stdout, stderr = _run_group(
                    [quarto, "render", source_name, "--to", to, *_FORMAT_ARGS.get(to, ())],
                    cwd=work, env=env, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise RenderError(f"Quarto took longer than {timeout}s to render {fmt}.")
            except OSError as exc:
                raise RenderError(f"Quarto could not be started ({exc}); is it installed?")
            if code != 0 or not produced.is_file():
                raise RenderError(
                    f"Quarto failed to render {fmt} (exit {code}):\n"
                    f"{_tail(stderr or stdout)}"
                )
            script = (postprocess or {}).get(fmt)
            if script:
                # The same treatment as Quarto: its own process group, killed
                # whole on timeout, and a message without the work directory
                # (an uncaught TimeoutExpired printed the full command line,
                # temp paths included, as the job's error).
                try:
                    code, stdout, stderr = _run_group(
                        [sys.executable, str(work / script), str(produced), "-o", str(produced)],
                        cwd=work, env=env, timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    raise RenderError(f"The {fmt} post-processor took longer than {timeout}s.")
                except OSError as exc:
                    raise RenderError(f"The {fmt} post-processor could not be started ({exc.strerror or 'error'}).")
                if code != 0:
                    raise RenderError(f"The {fmt} post-processor failed:\n{_tail(stderr or stdout)}")
            target = out_dir / f"{basename}{suffix}"
            shutil.copyfile(produced, target)
            results[fmt] = target
        return results


# ---------------------------------------------------------------------------
# Command line — for template authors
# ---------------------------------------------------------------------------

def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a BlueStick report template with sample data.")
    parser.add_argument("template", type=Path, help="The template folder (holds template.json).")
    parser.add_argument("--data", type=Path, required=True, help="A dataset JSON file.")
    parser.add_argument("--to", action="append", choices=sorted(FORMATS), help="Format (repeatable; default all).")
    parser.add_argument("--out", type=Path, default=None, help="Output folder (default: <template>/_output).")
    parser.add_argument("--evidence", type=Path, default=None,
                        help="Folder holding the evidence images, named as in the data (12.png …). "
                             "Default: the template's sample-evidence/ folder, when it has one.")
    args = parser.parse_args(argv)

    manifest = json.loads((args.template / "template.json").read_text(encoding="utf-8"))
    dataset = json.loads(args.data.read_text(encoding="utf-8"))
    try:
        for a in template_assets(args.template, manifest):
            if not a["present"]:
                kind = "required" if a["required"] else "optional"
                print(f"note: {kind} image '{a['id']}' is not installed ({a['path']})", file=sys.stderr)
    except TemplateAssetError as exc:
        print(f"error: template.json: {exc}", file=sys.stderr)
        return 1
    evidence_dir = args.evidence
    if evidence_dir is None and (args.template / SAMPLE_EVIDENCE_DIR).is_dir():
        evidence_dir = args.template / SAMPLE_EVIDENCE_DIR

    def resolve(item: dict) -> Optional[Path]:
        if evidence_dir is None:
            return None
        return evidence_dir / Path(item["file"]).name

    try:
        files = render(
            args.template, manifest.get("entry", "report.qmd"), dataset,
            args.to or manifest.get("formats") or list(FORMATS),
            args.out or (args.template / "_output"),
            resolve_evidence=resolve, postprocess=manifest.get("postprocess"),
        )
    except RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for fmt, path in files.items():
        print(f"{fmt}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
