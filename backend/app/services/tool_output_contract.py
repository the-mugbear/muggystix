"""Canonical tool → ingestible-output contract (single source of truth).

Every recon tool BlueStick can parse, mapped to the set of file extensions its
output must carry to be ingested.  This is the HUB that the human/agent-facing
copies of "how to run this tool for BlueStick" are checked against by
``tests/test_tool_command_consistency.py``:

  * the tool registry's ``run_command`` (``app/data/tool_registry_seed.json``;
    the operator-facing commands the Tool Reference page shows — until
    v2.476.0 a table typed into that page), and
  * ``documentation/UPLOAD_FORMATS.md``.

Two more are gone: the agent guide's "Supported upload formats" table left the
guide in v2.433.1, and the backend recon catalog went with the recon planning
service in v2.434.0.  The two that remain can't share code — but they
must not disagree.  The contract is the referee: when either recommends a
command whose output extension isn't accepted here (i.e. the parser can't ingest
it), the consistency test fails instead of the drift shipping silently.

Keyed by tool BINARY name (the registry's ``name``).  ``exts`` is the set of accepted upload extensions
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
KNOWN_EXTENSIONS: Set[str] = {"xml", "gnmap", "json", "jsonl", "ndjson", "csv", "txt", "zip"}

TOOL_OUTPUT_CONTRACT: Dict[str, Dict[str, object]] = {
    # --- Port / host discovery ---
    "nmap": {"exts": {"xml", "gnmap", "txt"}},  # grepable saved as .txt is detected by content
    "masscan": {"exts": {"xml", "json", "txt"}},
    "rustscan": {"exts": {"xml", "txt"}},  # native .txt console, or piped nmap .xml
    "naabu": {"exts": {"json", "txt"}},
    # --- Web ---
    "httpx": {"exts": {"json", "jsonl", "ndjson"}},
    "whatweb": {"exts": {"json", "jsonl", "ndjson"}},
    "eyewitness": {"exts": {"json", "csv", "zip"}, "note": "default / -d directory output; no output-file flag"},
    "nikto": {"exts": {"json", "csv", "txt"}},
    "testssl": {"exts": {"json"}, "note": "--jsonfile / --jsonfile-pretty; ingests via TestsslParser into web_interfaces"},
    "nuclei": {"exts": {"json", "jsonl", "ndjson"}, "note": "-je (JSON array) or -jsonl; ingests via NucleiParser (v2.411.0)"},
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
    "dnsx": {"exts": {"json", "jsonl", "ndjson"}},
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
