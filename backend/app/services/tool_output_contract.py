"""Canonical tool → ingestible-output contract (single source of truth).

Every recon tool BlueStick can parse, mapped to the set of file extensions its
output must carry to be ingested.  This is the HUB that the human/agent-facing
copies of "how to run this tool for BlueStick" are checked against by
``tests/test_tool_command_consistency.py``:

  * the backend recon catalog — ``recon_planning_service.build_tool_catalog``
    (the agent's session-rendered commands),
  * the frontend Tool Reference page — ``RUN_COMMANDS`` in
    ``frontend/src/pages/ToolReference.tsx`` (the operator-facing commands),
  * the ``AGENTS.md`` "Supported upload formats" table, and
  * ``documentation/UPLOAD_FORMATS.md``.

Those four exist independently because they serve different consumers (a
session-parameterised agent command is not a generic operator template, and a
static docs page shouldn't fetch at runtime).  They can't share code — but they
must not disagree.  The contract is the referee: when any of them recommends a
command whose output extension isn't accepted here (i.e. the parser can't ingest
it), the consistency test fails instead of the drift shipping silently.

Keyed by tool BINARY name (matches the ``tool`` field in the recon catalog and
the ``RUN_COMMANDS`` keys).  ``exts`` is the set of accepted upload extensions
and is authoritative — it mirrors the parser registry / UPLOAD_FORMATS.md.  A
tool may legitimately be recommended with different-but-valid output across
sources (e.g. subfinder ``.txt`` in the agent catalog vs ``.json`` on the
reference page); the contract accepts BOTH, so the test tolerates that while
still catching a genuinely unparseable extension.  ``note`` documents tools
whose canonical output is a directory or stdout (no output-file flag to check).

When a parser's accepted extensions change, update this map in the same commit —
that is the point of the contract.
"""
from __future__ import annotations

from typing import Dict, Set

# Extensions BlueStick's ingestion pipeline recognises overall (magic-byte /
# suffix routing).  Used by the test to sanity-check that no contract entry
# lists an extension the pipeline can't route at all.
KNOWN_EXTENSIONS: Set[str] = {"xml", "gnmap", "json", "jsonl", "csv", "txt", "zip"}

TOOL_OUTPUT_CONTRACT: Dict[str, Dict[str, object]] = {
    # --- Port / host discovery ---
    "nmap": {"exts": {"xml", "gnmap"}},
    "masscan": {"exts": {"xml", "json", "txt"}},
    "rustscan": {"exts": {"xml", "txt"}},  # native .txt console, or piped nmap .xml
    "naabu": {"exts": {"json", "txt"}},
    # --- Web ---
    "httpx": {"exts": {"json", "jsonl"}},
    "whatweb": {"exts": {"json", "jsonl"}},
    "eyewitness": {"exts": {"json", "csv", "zip"}, "note": "default / -d directory output; no output-file flag"},
    "nikto": {"exts": {"json", "csv", "txt"}},
    "nuclei": {"exts": {"json"}},
    # unified dirbuster-family parser (tool name goes in the filename)
    "gobuster": {"exts": {"json", "csv", "txt"}},
    "feroxbuster": {"exts": {"json", "csv", "txt"}},
    "ffuf": {"exts": {"json", "csv", "txt"}},
    "dirsearch": {"exts": {"json", "csv", "txt"}},
    "dirb": {"exts": {"json", "csv", "txt"}},
    "wfuzz": {"exts": {"json", "csv", "txt"}, "note": "nonstandard '-f file,printer' output form"},
    # --- DNS / subdomains ---
    "subfinder": {"exts": {"json", "txt"}},
    "amass": {"exts": {"json", "txt"}},
    "dnsx": {"exts": {"json", "jsonl"}},
    # --- SMB / Windows / AD ---
    "smbmap": {"exts": {"json", "txt"}, "note": "default stdout; pipe/redirect to a file"},
    "netexec": {"exts": {"json", "txt"}, "note": "default stdout or --json"},
    "bloodhound-python": {"exts": {"json"}, "note": "default collector output (upload the JSON, not the ZIP)"},
}


def accepted_extensions(tool: str) -> Set[str]:
    """The set of upload extensions accepted for ``tool`` (empty if unknown)."""
    entry = TOOL_OUTPUT_CONTRACT.get(tool)
    if not entry:
        return set()
    return set(entry.get("exts", set()))  # type: ignore[arg-type]


def writes_output_file(tool: str) -> bool:
    """True when ``tool``'s canonical command writes a single output file whose
    extension can be parsed out of the command (i.e. NOT a directory/stdout tool
    or one with a nonstandard output form).  Those carry a ``note``; the drift
    test skips the extension check for them but requires it for everything else,
    so an unrecognised/changed output flag fails instead of silently passing."""
    entry = TOOL_OUTPUT_CONTRACT.get(tool)
    return bool(entry) and "note" not in entry
