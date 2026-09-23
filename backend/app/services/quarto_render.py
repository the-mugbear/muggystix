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
* ``<< image(e) >>`` and ``<< plain(v) >>`` are the only other ways out of
  escaping, and both validate their input against a strict pattern.
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
    "pdf": ("typst", ".pdf", "application/pdf"),
}

FIELDS_FILTER = Path(__file__).with_name("quarto_fields.lua")

_PUNCT = set(string.punctuation)
_KEY = re.compile(r"^[a-z_]+(\.(\d+|[a-z_]+))*$")
_EVIDENCE = re.compile(r"^evidence/\d+\.(png|jpg|gif)$")
_WIDTH = re.compile(r"^\d+(\.\d+)?(in|cm|mm|px|%)$")
_PLAIN = re.compile(r"^[0-9A-Za-z .:+_-]*$")
_SKIP = {"_output", ".quarto", "__pycache__", ".git"}


class RenderError(RuntimeError):
    """Quarto (or the template) failed; the message is safe to show."""


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


def image(item: Any, width: str = "6in") -> Markup:
    """An evidence image the renderer placed in the work directory."""
    if not isinstance(item, dict) or not _EVIDENCE.match(str(item.get("file", ""))):
        raise RenderError("image() takes an item from a finding's `evidence` list.")
    if not _WIDTH.match(width):
        raise RenderError(f"image(): '{width}' is not a width.")
    caption = escape_md(item.get("caption") or "")
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
    env.globals.update(md=_md_factory(dataset) if dataset is not None else md, image=image, plain=plain, todo=todo)
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
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(json.dumps(dataset))  # a private copy; evidence is pruned below
    with tempfile.TemporaryDirectory(prefix="bs-report-") as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        _copy_template(template_dir, work)
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

        results: Dict[str, Path] = {}
        env = _clean_env(work)
        for fmt in formats:
            to, suffix, _media = FORMATS[fmt]
            produced = work / f"report{suffix}"
            if produced.exists():
                produced.unlink()
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
