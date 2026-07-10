"""Drift guard for the tool → ingestible-output contract.

`app.services.tool_output_contract.TOOL_OUTPUT_CONTRACT` is the single source of
truth for which file extensions each recon tool's output must carry to be
parseable.  Four independent, human/agent-facing copies describe "how to run a
tool for BlueStick", and they can't share code (a session-parameterised agent
command, a static operator-facing page, and two markdown tables):

  1. backend recon catalog — ``recon_planning_service.build_tool_catalog``
  2. frontend Tool Reference — ``RUN_COMMANDS`` in ``frontend/src/pages/ToolReference.tsx``
  3. ``AGENTS.md`` "Supported upload formats" table
  4. ``documentation/UPLOAD_FORMATS.md`` parser-coverage table

The invariant this test pins: **every output extension any source recommends or
documents for a tool must be in that tool's accepted set in the contract.**  The
contract is the permissive superset of valid formats; a source that drifts to an
extension outside it (i.e. one the parser can't ingest) fails here instead of
shipping a command whose output silently won't upload.

Sources 2–4 live outside the backend image (only ``backend/`` is copied in), so
run this with the repo root mounted to exercise every check::

    docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo/backend backend \\
        python -m pytest tests/test_tool_command_consistency.py -v

Under the default backend-only mount those files aren't visible and their checks
skip (never false-fail); the backend-catalog and contract-shape checks always run.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Set

import pytest

from app.services.recon_planning_service import build_tool_catalog
from app.services.tool_output_contract import (
    KNOWN_EXTENSIONS,
    TOOL_OUTPUT_CONTRACT,
    accepted_extensions,
    writes_output_file,
)


# --- Repo-root + source-file resolution ------------------------------------
def _looks_like_root(p: Path) -> bool:
    return (p / "AGENTS.md").is_file() and (p / "frontend").is_dir()


def _repo_root() -> Optional[Path]:
    """Locate the repo root (holds AGENTS.md + frontend/).

    Order: ``$BLUESTICK_REPO_ROOT`` (set when the repo is mounted alongside the
    backend-only image mount), then any ancestor of this file, then ``/repo``.
    Returns None under a backend-only mount with no repo — those source checks
    then skip rather than false-fail.
    """
    env = os.getenv("BLUESTICK_REPO_ROOT")
    if env and _looks_like_root(Path(env)):
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        if _looks_like_root(parent):
            return parent
    if _looks_like_root(Path("/repo")):
        return Path("/repo")
    return None


REPO_ROOT = _repo_root()


def _read(rel: str) -> Optional[str]:
    if REPO_ROOT is None:
        return None
    p = REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.is_file() else None


# --- Output-extension extraction -------------------------------------------
# Flags whose NEXT token is the output filename, across nmap/masscan/httpx/
# nuclei/etc.  Input-file flags (-l, -f, -iL, --input-file, -d, -H) are
# deliberately excluded so an input list (targets.txt, urls.txt) is never
# mistaken for the output.
_OUTPUT_FLAGS = {"-o", "-oX", "-oJ", "-oG", "-oL", "-je", "-json"}
_EXT_RE = re.compile(r"\.([A-Za-z0-9]+)$")


def output_extension(command: str) -> Optional[str]:
    """The extension of the file a command writes, or None if it has no
    output-file flag (directory/stdout tools, or an unrecognised form)."""
    toks = command.split()
    for i, tok in enumerate(toks):
        if tok in _OUTPUT_FLAGS and i + 1 < len(toks):
            # wfuzz-style "file,printer" — take the filename before the comma
            fn = toks[i + 1].split(",")[0]
            m = _EXT_RE.search(fn)
            if m:
                return m.group(1).lower()
        if tok.startswith("--log-json="):
            m = _EXT_RE.search(tok.split("=", 1)[1])
            if m:
                return m.group(1).lower()
    return None


# --- Contract shape sanity -------------------------------------------------
def test_contract_extensions_are_all_routable():
    """No contract entry may list an extension the pipeline can't route."""
    for tool, entry in TOOL_OUTPUT_CONTRACT.items():
        exts: Set[str] = set(entry["exts"])  # type: ignore[arg-type]
        assert exts, f"{tool}: empty extension set"
        unknown = exts - KNOWN_EXTENSIONS
        assert not unknown, f"{tool}: extensions not in KNOWN_EXTENSIONS: {unknown}"


# --- Source 1: backend recon catalog ---------------------------------------
def test_backend_catalog_matches_contract():
    catalog = build_tool_catalog(["10.0.0.0/24"], {"size_bucket": "small"})
    assert catalog, "build_tool_catalog returned nothing"
    for entry in catalog:
        tool = entry["tool"]
        assert tool in TOOL_OUTPUT_CONTRACT, (
            f"recon catalog tool '{tool}' has no TOOL_OUTPUT_CONTRACT entry — "
            f"add it (or the agent will recommend an output nothing verifies)."
        )
        exts = accepted_extensions(tool)
        # The declared output_format is effectively the target extension.
        fmt = entry.get("output_format")
        assert fmt in exts, (
            f"recon catalog '{tool}': output_format '{fmt}' not in contract {exts}"
        )
        # And the extension the command actually writes.
        cmd_ext = output_extension(entry["command"])
        if writes_output_file(tool):
            assert cmd_ext is not None, (
                f"recon catalog '{tool}': no recognised output-file flag in command "
                f"— an unparseable output form would ship undetected.\n  command: {entry['command']}"
            )
            assert cmd_ext in exts, (
                f"recon catalog '{tool}': command writes .{cmd_ext}, not in contract {exts}\n"
                f"  command: {entry['command']}"
            )
        elif cmd_ext is not None:
            assert cmd_ext in exts, (
                f"recon catalog '{tool}': command writes .{cmd_ext}, not in contract {exts}\n"
                f"  command: {entry['command']}"
            )


# --- Source 2: frontend Tool Reference RUN_COMMANDS ------------------------
_RUN_BLOCK_RE = re.compile(r"RUN_COMMANDS[^=]*=\s*\{(?P<body>.*?)\n\};", re.S)
# key (optionally quoted) : { run: <'...' | "..."> ...
_RUN_ENTRY_RE = re.compile(
    r"""(?P<key>'[\w.\-]+'|"[\w.\-]+"|[\w.\-]+)\s*:\s*\{\s*"""
    r"""run:\s*(?P<q>['"])(?P<run>(?:\\.|(?!(?P=q)).)*)(?P=q)""",
    re.S,
)


def _parse_run_commands(tsx: str) -> dict:
    block = _RUN_BLOCK_RE.search(tsx)
    assert block, "Could not locate the RUN_COMMANDS object in ToolReference.tsx"
    out = {}
    for m in _RUN_ENTRY_RE.finditer(block.group("body")):
        key = m.group("key").strip("'\"")
        run = m.group("run").replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
        out[key] = run
    return out


def test_frontend_run_commands_match_contract():
    tsx = _read("frontend/src/pages/ToolReference.tsx")
    if tsx is None:
        pytest.skip("frontend source not mounted — run with the repo root mounted")
    run_commands = _parse_run_commands(tsx)
    assert run_commands, "parsed zero RUN_COMMANDS entries — regex drifted?"
    for tool, run in run_commands.items():
        assert tool in TOOL_OUTPUT_CONTRACT, (
            f"ToolReference RUN_COMMANDS['{tool}'] has no contract entry"
        )
        exts = accepted_extensions(tool)
        cmd_ext = output_extension(run)
        if writes_output_file(tool):
            assert cmd_ext is not None, (
                f"RUN_COMMANDS['{tool}']: no recognised output-file flag in the run "
                f"command — an unparseable output form would ship undetected.\n  run: {run}"
            )
            assert cmd_ext in exts, (
                f"RUN_COMMANDS['{tool}'] writes .{cmd_ext}, not in contract {exts}\n"
                f"  run: {run}"
            )
        elif cmd_ext is not None:
            assert cmd_ext in exts, (
                f"RUN_COMMANDS['{tool}'] writes .{cmd_ext}, not in contract {exts}\n"
                f"  run: {run}"
            )


# --- Source 3: AGENTS.md upload-formats table ------------------------------
# Display name in the table → contract key.
_AGENTS_TABLE_ALIASES = {
    "nmap": "nmap",
    "nmap (grepable)": "nmap",
    "masscan": "masscan",
    "rustscan → nmap": "rustscan",
    "httpx": "httpx",
    "eyewitness": "eyewitness",
    "nikto": "nikto",
    "naabu": "naabu",
    "nuclei": "nuclei",
    "bloodhound": "bloodhound-python",
    "netexec": "netexec",
}
_BACKTICK_EXT_RE = re.compile(r"`\.([A-Za-z0-9]+)`")


def _table_extensions(cell: str) -> Set[str]:
    """Extract extension tokens from a markdown cell.

    Both tables quote extensions as ```.xml```; grab those regardless of the
    surrounding separators (AGENTS.md uses ' / ', UPLOAD_FORMATS.md uses commas
    and parentheticals like '`.xml` (normal)').  Fall back to slash-split bare
    tokens if a cell has no backtick-quoted extension."""
    exts = {m.lower() for m in _BACKTICK_EXT_RE.findall(cell)}
    if exts:
        return exts
    for part in cell.split("/"):
        part = part.strip().strip("`")
        m = re.fullmatch(r"\.?([A-Za-z0-9]+)", part)
        if m:
            exts.add(m.group(1).lower())
    return exts


def test_agents_md_table_matches_contract():
    md = _read("AGENTS.md")
    if md is None:
        pytest.skip("AGENTS.md not mounted — run with the repo root mounted")
    checked = 0
    for line in md.splitlines():
        if not line.strip().startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 3:
            continue
        tool = _AGENTS_TABLE_ALIASES.get(cols[0].lower())
        if tool is None:
            continue  # header, separator, or a non-command row (nessus/openvas/dns)
        listed = _table_extensions(cols[1])
        # Every extension the table advertises must be accepted by the contract.
        extra = listed - accepted_extensions(tool)
        assert not extra, (
            f"AGENTS.md upload table row '{cols[0]}' lists .{extra} "
            f"not in contract for '{tool}' ({accepted_extensions(tool)})"
        )
        checked += 1
    assert checked >= 8, f"only matched {checked} AGENTS.md rows — table shape changed?"


# --- Source 4: documentation/UPLOAD_FORMATS.md -----------------------------
def test_upload_formats_md_within_contract():
    md = _read("documentation/UPLOAD_FORMATS.md")
    if md is None:
        pytest.skip("UPLOAD_FORMATS.md not mounted — run with the repo root mounted")
    # Match a contract tool to a table row by substring on the tool cell.
    aliases = {t: {t.replace("-python", "")} for t in TOOL_OUTPUT_CONTRACT}
    checked = 0
    for line in md.splitlines():
        if not line.strip().startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 2:
            continue
        cell = cols[0].lower()
        for tool, names in aliases.items():
            if any(name in cell for name in names):
                listed = _table_extensions(cols[1])
                extra = listed - accepted_extensions(tool)
                assert not extra, (
                    f"UPLOAD_FORMATS.md row '{cols[0]}' lists .{extra} "
                    f"not in contract for '{tool}' ({accepted_extensions(tool)})"
                )
                if listed:
                    checked += 1
    assert checked >= 8, f"only matched {checked} UPLOAD_FORMATS rows — table shape changed?"
