"""Drift guard for the tool → ingestible-output contract.

`app.services.tool_output_contract.TOOL_OUTPUT_CONTRACT` is the single source of
truth for which file extensions each recon tool's output must carry to be
parseable.  Two independent, human-facing copies describe "how to run a tool
for BlueStick", and they can't share code (rows of the tool registry and a
markdown table).  The backend recon catalog was another until v2.434.0, when
BlueStick stopped handing agents commands to run, and the agent guide's
"Supported upload formats" table (source 3) left the guide in v2.433.1:

  2. the tool registry's ``run_command`` — ``app/data/tool_registry_seed.json``
     (v2.476.0; until then ``RUN_COMMANDS``, a table typed into
     ``frontend/src/pages/ToolReference.tsx``, which the page now reads from
     ``GET /references/tools``)
  4. ``documentation/UPLOAD_FORMATS.md`` parser-coverage table

The invariant this test pins: **every output extension any source recommends or
documents for a tool must be in that tool's accepted set in the contract.**  The
contract is the permissive superset of valid formats; a source that drifts to an
extension outside it (i.e. one the parser can't ingest) fails here instead of
shipping a command whose output silently won't upload.

The markdown table lives outside the backend image (only ``backend/`` is copied
in), so run this with the repo root mounted to exercise every check::

    docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo/backend backend \\
        python -m pytest tests/test_tool_command_consistency.py -v

Under the default backend-only mount that file isn't visible and its check
skips (never false-fails); the registry and contract-shape checks always run.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Set

import pytest

from app.services.tool_output_contract import (
    KNOWN_EXTENSIONS,
    TOOL_OUTPUT_CONTRACT,
    accepted_extensions,
    writes_output_file,
)


# --- Repo-root + source-file resolution ------------------------------------
def _looks_like_root(p: Path) -> bool:
    return (p / "documentation" / "AGENT_GUIDE.md").is_file() and (p / "frontend").is_dir()


def _repo_root() -> Optional[Path]:
    """Locate the repo root (holds documentation/AGENT_GUIDE.md + frontend/).

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


# --- (The backend recon catalog was Source 1; it went with the recon planning
# service in v2.434.0 — BlueStick no longer hands agents commands to run.)


# --- Source 2: the tool registry's run commands -----------------------------
def _registry_run_commands() -> dict:
    from app.services.tool_registry_service import load_seed

    return {row["name"]: row["run_command"] for row in load_seed() if row.get("run_command")}


def test_registry_run_commands_match_contract():
    run_commands = _registry_run_commands()
    assert len(run_commands) >= 20, "the seed carries almost no run commands — was a key renamed?"
    for tool, run in run_commands.items():
        assert tool in TOOL_OUTPUT_CONTRACT, (
            f"the registry's run command for '{tool}' has no contract entry"
        )
        exts = accepted_extensions(tool)
        cmd_ext = output_extension(run)
        if writes_output_file(tool):
            assert cmd_ext is not None, (
                f"run_command of '{tool}': no recognised output-file flag in the run "
                f"command — an unparseable output form would ship undetected.\n  run: {run}"
            )
            assert cmd_ext in exts, (
                f"run_command of '{tool}' writes .{cmd_ext}, not in contract {exts}\n"
                f"  run: {run}"
            )
        elif cmd_ext is not None:
            assert cmd_ext in exts, (
                f"run_command of '{tool}' writes .{cmd_ext}, not in contract {exts}\n"
                f"  run: {run}"
            )


def test_the_reference_page_keeps_no_run_commands_of_its_own():
    """One owner: the page renders the registry row's `run_command`."""
    tsx = _read("frontend/src/pages/ToolReference.tsx")
    if tsx is None:
        pytest.skip("frontend source not mounted — run with the repo root mounted")
    assert "RUN_COMMANDS" not in tsx


# --- (Source 3 was the agent guide's "Supported upload formats" table.  The
# table left the guide in v2.433.1 (a908a2a1) with the recon-run sections, and
# its check then matched no rows and skipped or failed.  The guide now names
# tools only, with no extensions; agents read `list_tools` (`ingestible`), and
# the extension list is UPLOAD_FORMATS.md, pinned below.)
_BACKTICK_EXT_RE = re.compile(r"`\.([A-Za-z0-9]+)`")


def _table_extensions(cell: str) -> Set[str]:
    """Extract extension tokens from a markdown cell.

    The table quotes extensions as ```.xml```; grab those regardless of the
    surrounding separators (commas and parentheticals like '`.xml` (normal)').
    Fall back to slash-split bare
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
