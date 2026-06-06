"""AGENTS.md slicing — extracted from main.py in v2.42.0.

Filters AGENTS.md to the sections tagged for a given workflow so the
agent's context window stays lean.  See the public guide for the
section-marker syntax; this module is purely the parser + filter.
"""
from __future__ import annotations

import re
from typing import Optional


_SECTION_START = re.compile(
    r'<!--\s*agents:section\s+tags\s*=\s*"([^"]+)"\s*-->',
    re.IGNORECASE,
)
_SECTION_END = re.compile(r'<!--\s*agents:end\s*-->', re.IGNORECASE)

_WORKFLOW_ALIASES = {
    "plan": "plan_generation",
    "exec": "execution",
    "recon": "reconnaissance",
}


def slice_agents_md(content: str, workflow: Optional[str]) -> str:
    """Return only the sections of AGENTS.md tagged for the requested workflow.

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
    if workflow is None:
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
