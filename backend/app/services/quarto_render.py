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
  from ``data.json`` and parses it as GitHub Markdown with raw HTML off, then
  drops raw blocks, images, non-web links, headings and attributes.
* ``<< image(e) >>``, ``<< plain(v) >>`` and ``<< asset("logo") >>`` are the
  only other ways out of escaping, and each validates its input against a
  strict pattern.  ``asset`` prints a path from the template's OWN manifest
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
import string
import subprocess
import sys
import tempfile
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

# Template-author files that are not part of a filled report's source: the
# Jinja partials are already included in report.qmd (and are Jinja, not
# Quarto), and the sample data / Makefile drive the template, not the report.
_BUNDLE_SKIP_TOP = {
    "partials", "branding", "sample-data.json", "Makefile", "template.json", ".gitignore", ".luarc.json",
}

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
                   never run as Quarto source.  Edit the text here.
- evidence/        the screenshots marked "In report".
- reference.docx   the Word styles (fonts, captions, header/footer).
"""


FIELDS_FILTER = Path(__file__).with_name("quarto_fields.lua")

_PUNCT = set(string.punctuation)
_KEY = re.compile(r"^[a-z_]+(\.(\d+|[a-z_]+))*$")
_EVIDENCE = re.compile(r"^evidence/\d+\.(png|jpg|gif)$")
_WIDTH = re.compile(r"^\d+(\.\d+)?(in|cm|mm|px|%)$")
_PLAIN = re.compile(r"^[0-9A-Za-z .:+_-]*$")
_SKIP = {"_output", ".quarto", "__pycache__", ".git"}
# A template's own images (logo, cover art …) declared in template.json.  The
# path charset is narrow on purpose: ``asset()`` prints it unescaped into
# Markdown, so it must never hold a space, bracket, brace or quote.
_ASSET_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ASSET_PATH = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$")
ASSET_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")
# An asset may instead REPLACE one of the template's own files when installed
# (``"replaces": "reference.docx"`` — an operator's Word styles over the
# shipped ones).  Only these kinds of file can be replaced.
REPLACEABLE_EXTENSIONS = ASSET_EXTENSIONS + (".docx",)


class RenderError(RuntimeError):
    """Quarto (or the template) failed; the message is safe to show."""


class TemplateAssetError(ValueError):
    """template.json declares an asset it may not (the message names it)."""


def template_assets(template_dir: Path, manifest: Optional[dict] = None) -> List[dict]:
    """The images a template expects besides the findings' evidence, from
    ``template.json`` → ``assets``, each with ``present``: the file is in the
    folder and would be copied into the render (a regular file, no symlink on
    the way — ``_copy_template`` skips symlinks).

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
        out.append({
            "id": asset_id,
            "path": path,
            "label": str(entry.get("label") or asset_id),
            "description": str(entry.get("description") or ""),
            "note": str(entry.get("note") or ""),
            "required": bool(entry.get("required", False)),
            "formats": formats,
            "replaces": replaces or None,
            "present": _asset_present(template_dir, parts),
        })
    return out


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


def _apply_replacements(template_dir: Path, work: Path) -> None:
    """In the render's copy of the template, put each INSTALLED replacing
    asset in the place of the file it replaces (an operator's reference.docx
    over the shipped one).  An asset that is not installed changes nothing."""
    if not (template_dir / "template.json").is_file():
        return
    for a in template_assets(template_dir):
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


def missing_required_assets(template_dir: Path) -> List[dict]:
    """The declared, REQUIRED images whose file is not in the folder."""
    if not (template_dir / "template.json").is_file():
        return []
    try:
        return [a for a in template_assets(template_dir) if a["required"] and not a["present"]]
    except (TemplateAssetError, ValueError, OSError) as exc:
        raise RenderError(f"The template '{template_dir.name}' declares unusable assets: {exc}") from exc


def missing_assets_message(template_name: str, missing: List[dict]) -> str:
    where = ", ".join(f"report-templates/{template_name}/{a['path']} ({a['label']})" for a in missing)
    return (
        f"The '{template_name}' template needs image(s) that are not installed: {where}. "
        "Put the file(s) there on the server — the folder is mounted, no rebuild needed."
    )


def _asset_factory(template_dir: Path):
    assets = {a["id"]: a for a in template_assets(template_dir)} if (template_dir / "template.json").is_file() else {}

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


def _md_factory(dataset: dict):
    def md(obj: Any, field: Optional[str] = None, todo_text: Optional[str] = None, **kwargs) -> Markup:
        """A placeholder the Lua filter fills with the written Markdown at
        ``obj._path + "." + field`` (or the dotted path ``obj``) in data.json.
        With ``todo=`` (or ``todo_text=``), an empty value prints that TODO
        instead."""
        todo_text = kwargs.pop("todo", todo_text)
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
        value = _lookup(dataset, key)
        if not (isinstance(value, str) and value.strip()):
            return todo(todo_text, block=True) if todo_text else Markup("")
        return Markup(f'\n\n::: {{.bs-md key="{key}"}}\n:::\n\n')
    return md


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
    """An evidence image the renderer placed in the work directory.  With
    ``number``, the caption reads "Figure N: …" (v2.410.0 — the prototype's
    numbered figure captions, without Quarto cross-references and the wrapper
    tables they bring in Word)."""
    if not isinstance(item, dict) or not _EVIDENCE.match(str(item.get("file", ""))):
        raise RenderError("image() takes an item from a finding's `evidence` list.")
    if not _WIDTH.match(width):
        raise RenderError(f"image(): '{width}' is not a width.")
    if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number < 1):
        raise RenderError("image(): number must be a positive whole number.")
    caption = escape_md(item.get("caption") or "")
    if number is not None:
        caption = f"Figure {number}" + (f"\\: {caption}" if caption else "")
    return Markup(f'\n\n![{caption}]({item["file"]}){{width="{width}"}}\n\n')


def plain(value: Any) -> Markup:
    """A value known to be plain (a date, a number, a reference) — for YAML
    and attributes, where backslash escapes do not apply."""
    text = "" if value is None else str(value)
    if not _PLAIN.match(text):
        raise RenderError(f"plain(): '{text[:40]}' contains characters that need escaping.")
    return Markup(text)


def jinja_environment(template_dir: Path, dataset: Optional[dict] = None) -> SandboxedEnvironment:
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
        asset = _asset_factory(template_dir)
    except (TemplateAssetError, ValueError, OSError) as exc:
        raise RenderError(f"The template '{template_dir.name}' declares unusable assets: {exc}") from exc
    env.globals.update(
        md=_md_factory(dataset) if dataset is not None else md,
        image=image, plain=plain, todo=todo, asset=asset,
    )
    return env


def render_source(template_dir: Path, entry: str, dataset: dict) -> str:
    env = jinja_environment(template_dir, dataset)
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
    """Copy each finding's evidence image into ``work/evidence`` and drop
    entries whose file cannot be found (the render would fail on them)."""
    missing = []
    (work / "evidence").mkdir(exist_ok=True)
    for finding in dataset.get("findings") or []:
        kept = []
        for item in finding.get("evidence") or []:
            if not _EVIDENCE.match(str(item.get("file", ""))):
                continue
            source = resolve(item)
            if source is None or not source.is_file():
                missing.append(str(item.get("caption") or item.get("file")))
                continue
            shutil.copyfile(source, work / item["file"])
            kept.append(item)
        finding["evidence"] = kept
    return missing


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


def quarto_version(quarto: str = "quarto") -> Optional[str]:
    with tempfile.TemporaryDirectory(prefix="bs-qv-") as tmp:
        try:
            out = subprocess.run(
                [quarto, "--version"], capture_output=True, text=True, timeout=60,
                env=_clean_env(Path(tmp)),
            )
        except (OSError, subprocess.SubprocessError):
            return None
    return out.stdout.strip()[:40] or None


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


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
) -> Dict[str, Path]:
    """Render ``dataset`` with the template into ``out_dir`` → {format: file}.

    ``strict_evidence`` (an ISSUED report): an evidence image that cannot be
    found fails the render instead of being dropped.  A draft preview drops it
    — the draft is live — but an issued report that silently lost an image
    would no longer be the document that was signed off (review 2026-09-23
    C4)."""
    formats = [f for f in formats if f in FORMATS]
    if not formats:
        raise RenderError("No format to render.")
    if not re.match(r"^[A-Za-z0-9._-]{1,120}$", basename):
        raise RenderError("Unusable output file name.")
    missing_assets = missing_required_assets(template_dir)
    if missing_assets:
        raise RenderError(missing_assets_message(template_dir.name, missing_assets))
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(json.dumps(dataset))  # a private copy; evidence is pruned below
    with tempfile.TemporaryDirectory(prefix="bs-report-") as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        _copy_template(template_dir, work)
        _apply_replacements(template_dir, work)
        (work / "_bluestick").mkdir(exist_ok=True)
        shutil.copyfile(FIELDS_FILTER, work / "_bluestick" / "fields.lua")
        missing = _place_evidence(dataset, work, resolve_evidence or (lambda _item: None))
        if missing and strict_evidence:
            raise RenderError(
                "Evidence images of this issued report are missing (deleted since it was "
                f"issued?): {', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}. "
                "Restore them, or revise the report."
            )
        (work / "data.json").write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
        source_name = "report.qmd"
        (work / source_name).write_text(render_source(template_dir, entry, dataset), encoding="utf-8")
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
                proc = subprocess.run(
                    [quarto, "render", source_name, "--to", to],
                    cwd=work, env=env, capture_output=True, text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise RenderError(f"Quarto took longer than {timeout}s to render {fmt}.")
            except OSError as exc:
                raise RenderError(f"Quarto could not be started ({exc}); is it installed?")
            if proc.returncode != 0 or not produced.is_file():
                raise RenderError(
                    f"Quarto failed to render {fmt} (exit {proc.returncode}):\n"
                    f"{_tail(proc.stderr or proc.stdout)}"
                )
            script = (postprocess or {}).get(fmt)
            if script:
                post = subprocess.run(
                    [sys.executable, str(work / script), str(produced), "-o", str(produced)],
                    cwd=work, env=env, capture_output=True, text=True, timeout=timeout,
                )
                if post.returncode != 0:
                    raise RenderError(f"The {fmt} post-processor failed:\n{_tail(post.stderr or post.stdout)}")
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
                        help="Folder holding the evidence images, named as in the data (12.png …).")
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
