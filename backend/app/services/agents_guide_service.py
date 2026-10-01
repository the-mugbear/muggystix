"""The agent guide — where it lives, and slicing (extracted from main.py in v2.42.0).

The guide is ``documentation/AGENT_GUIDE.md`` (named ``AGENTS.md`` at the repo
root until v2.427.1 — renamed because coding agents such as Codex and Cursor
load a root ``AGENTS.md`` as instructions for working ON the repository).
Compose mounts it at ``/app/AGENT_GUIDE.md``; a local checkout reads it from
``documentation/``.  ``read_agent_guide`` is the one resolver.

Slicing filters the guide to the sections tagged for a given workflow so the
agent's context window stays lean.  See the public guide for the
section-marker syntax.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

AGENT_GUIDE_FILENAME = "AGENT_GUIDE.md"


def agent_guide_candidates() -> List[Path]:
    """Where the guide may be: the container mount, then the checkout."""
    return [
        Path("/app") / AGENT_GUIDE_FILENAME,
        Path(__file__).resolve().parents[3] / "documentation" / AGENT_GUIDE_FILENAME,
    ]


def read_agent_guide() -> Optional[str]:
    """The guide's text, or None when it is not mounted."""
    for p in agent_guide_candidates():
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return None


_SECTION_START = re.compile(
    r'<!--\s*agents:section\s+tags\s*=\s*"([^"]+)"\s*-->',
    re.IGNORECASE,
)
_SECTION_END = re.compile(r'<!--\s*agents:end\s*-->', re.IGNORECASE)

# "testing" replaced the plan-generation and execution slices in v2.442.0; the
# old names still resolve so an older client's request gets the right slice.
_WORKFLOW_ALIASES = {
    "plan": "testing",
    "plan_generation": "testing",
    "exec": "testing",
    "execution": "testing",
    "recon": "reconnaissance",
}

# v2.337.0 — a unified project session does every kind of work, so it gets the
# WHOLE guide rather than one workflow's slice. Treated like ``workflow=None``.
_FULL_GUIDE_WORKFLOWS = {"project"}


def slice_agents_md(content: str, workflow: Optional[str]) -> str:
    """Return only the sections of the agent guide tagged for the requested workflow.

    Sections are delimited by HTML comment markers that render invisible
    to Markdown viewers but are easy to parse server-side::

        <!-- agents:section tags="plan_generation,reconnaissance" -->
        …section body…
        <!-- agents:end -->

    Rules:
      * A section is included if its tag list contains the requested
        workflow OR the literal tag ``shared`` (shared sections apply
        to every workflow).
      * Untagged content between sections (headers, preamble, horizontal
        rules) is always included so the document still reads as a
        coherent guide.
      * ``workflow=None`` returns the full file unchanged.
      * Unknown workflow names match nothing, so only ``shared`` +
        untagged content is returned — a safer default than erroring.

    Case-insensitive short forms accepted: ``plan`` → ``plan_generation``,
    ``exec`` → ``execution``, ``recon`` → ``reconnaissance``.
    """
    if workflow is None or workflow.lower() in _FULL_GUIDE_WORKFLOWS:
        return content

    requested = _WORKFLOW_ALIASES.get(workflow.lower(), workflow.lower())

    out_lines: list[str] = []
    in_section = False
    include_current = True

    for line in content.split('\n'):
        m_start = _SECTION_START.search(line)
        m_end = _SECTION_END.search(line)

        if m_start:
            raw_tags = m_start.group(1)
            tags = {t.strip().lower() for t in raw_tags.split(',') if t.strip()}
            include_current = ('shared' in tags) or (requested in tags)
            in_section = True
            continue  # don't emit the marker line itself
        if m_end:
            in_section = False
            include_current = True
            continue

        if in_section:
            if include_current:
                out_lines.append(line)
        else:
            out_lines.append(line)

    # Collapse runs of 3+ blank lines that the filter may have introduced
    # (e.g. when a dropped section leaves a gap between two horizontal rules).
    result = '\n'.join(out_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result
