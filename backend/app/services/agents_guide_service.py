"""The agent guide — where it lives, and its parts.

The guide is ``documentation/AGENT_GUIDE.md`` (not a root ``AGENTS.md``:
coding agents such as Codex and Cursor load that as instructions for working
ON the repository).  Compose mounts it at ``/app/AGENT_GUIDE.md``; a local
checkout reads it from ``documentation/``.  ``read_agent_guide`` is the one
resolver.

The guide is served whole or as one PART (``?workflow=<part>``): the sections
tagged for that part plus every ``shared`` section, so an agent reads the
reference for what it is doing and not the rest.  A part is a reading aid, not
a kind of session and not a permission — one project session does every kind
of work.  The query argument is still named ``workflow`` (a wire name).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

AGENT_GUIDE_FILENAME = "AGENT_GUIDE.md"

#: The parts a caller may ask for.  A section's tag is one of these or
#: ``shared``; ``tests/test_docs_contract.py`` pins the two to each other.
GUIDE_PARTS = ("testing", "reconnaissance", "assist", "remediation")

#: Values that mean "the whole guide", like leaving the argument out.
#: ``project`` is a session's own workflow, which the MCP layer fills in.
_FULL_GUIDE_VALUES = {"project"}


class UnknownGuidePart(ValueError):
    """``workflow`` named something that is not a part of the guide."""

    def __init__(self, value: str):
        self.value = value
        super().__init__(
            f"workflow must be one of {', '.join(GUIDE_PARTS)} "
            f"(or omitted for the whole guide); not understood: {value!r}."
        )


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


def slice_agents_md(content: str, workflow: Optional[str]) -> str:
    """Return the sections of the agent guide tagged for the requested part.

    Sections are delimited by HTML comment markers that render invisible
    to Markdown viewers but are easy to parse server-side::

        <!-- agents:section tags="testing,assist" -->
        …section body…
        <!-- agents:end -->

    Rules:
      * A section is included if its tag list contains the requested part
        OR the literal tag ``shared``.
      * Untagged content between sections (the title, horizontal rules) is
        always included.
      * ``workflow=None`` (or ``project``) returns the full file unchanged.
      * Any other value that is not in ``GUIDE_PARTS`` raises
        ``UnknownGuidePart``, which the route answers with a 422 naming the
        accepted values: a value that cannot be understood is refused, never
        answered with something else.

    Matching is case-insensitive and ignores surrounding whitespace.
    """
    if workflow is None:
        return content
    requested = workflow.strip().lower()
    if requested in _FULL_GUIDE_VALUES:
        return content
    if requested not in GUIDE_PARTS:
        raise UnknownGuidePart(workflow)

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

    # Collapse runs of 3+ blank lines (and stacked horizontal rules' gaps)
    # that a dropped section leaves behind.
    result = '\n'.join(out_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result
