"""What BlueStick reads from each tool's output, and where it ends up (v2.411.0).

The data behind the "What BlueStick reads" reference page: for every import
format, each thing the tool reports, how far BlueStick takes it (one of
``LEVELS``), where an analyst sees it, and the known gaps.  An analyst who
suspects an upload carried something BlueStick hid or dropped looks it up
here instead of reading parser code.

The content lives in ``app/data/parser_coverage.json`` and is audited against
the parsers, so it must never claim more than they do.
``tests/test_parser_coverage.py`` keeps it honest where a machine can:

* every format the dispatcher can emit (``format_registry.FORMATS``) belongs
  to exactly one tool here, so a new parser cannot land undocumented;
* every ``stored_as`` names a real model attribute;
* the levels are consistent (a discarded signal stores nothing; an
  observation lands on ``Vulnerability``; a tool that claims observations has
  a parser that writes them, and one that writes them claims them);
* every tool the output contract and the tool registry call ingestible maps
  to a tool here.

When a parser changes what it keeps, change its rows (and remove a fixed gap)
in the same commit.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.services.format_registry import FORMATS

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "parser_coverage.json"

Level = Literal["observation", "field", "text", "stored", "discarded"]

# In order of how far BlueStick takes the value.  The page defines each level
# once from this list.
LEVELS: List[Dict[str, str]] = [
    {
        "id": "observation",
        "label": "Scanner observation",
        "description": (
            "Becomes a scanner observation on the host: listed under Findings › Scanner "
            "observations, can be promoted to a finding and so reach the client report."
        ),
    },
    {
        "id": "field",
        "label": "Field",
        "description": (
            "Stored as a value BlueStick understands: shown where noted, and usable in "
            "host filters, insights or exports. Not a scanner observation."
        ),
    },
    {
        "id": "text",
        "label": "Raw text",
        "description": (
            "Kept as the tool's own text and shown as-is. You can read it, but BlueStick "
            "does not interpret it: no observation, filter or insight uses its content."
        ),
    },
    {
        "id": "stored",
        "label": "Stored, not shown",
        "description": (
            "Kept in the database as raw data but not displayed anywhere in the interface."
        ),
    },
    {
        "id": "discarded",
        "label": "Discarded",
        "description": (
            "Not kept. The parser does not read it, or reads it and drops it (the note "
            "says which). If it matters, record it by hand as a host note or finding."
        ),
    },
]
LEVEL_IDS = tuple(level["id"] for level in LEVELS)


class CoverageSignal(BaseModel):
    what: str
    input: str
    level: Level
    stored_as: List[str] = Field(default_factory=list)
    shown: Optional[str] = None
    note: Optional[str] = None


class ToolCoverage(BaseModel):
    id: str
    name: str
    formats: List[str]
    # Tool-registry / output-contract names whose output this parser reads
    # (gobuster, ffuf … → the directory brute-force parser).
    registry_tools: List[str] = Field(default_factory=list)
    accepted_input: str
    signals: List[CoverageSignal]
    gaps: List[str] = Field(default_factory=list)
    # What the audit could not check against real output (no fixture).
    unverified: List[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def load_coverage() -> List[ToolCoverage]:
    with DATA_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return [ToolCoverage.model_validate(tool) for tool in payload["tools"]]


def coverage_payload() -> Dict[str, object]:
    """The page's data: the level definitions and every tool, with each
    format's label from the format registry."""
    tools = []
    for tool in load_coverage():
        data = tool.model_dump()
        data["formats"] = [
            {"file_type": ft, "label": FORMATS[ft].label if ft in FORMATS else ft}
            for ft in tool.formats
        ]
        tools.append(data)
    return {"levels": LEVELS, "tools": tools}
