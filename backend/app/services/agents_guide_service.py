"""The agent guide — where it lives, and its parts.

The guide is ``documentation/AGENT_GUIDE.md`` (not a root ``AGENTS.md``:
coding agents such as Codex and Cursor load that as instructions for working
ON the repository).  Compose mounts it at ``/app/AGENT_GUIDE.md``; a local
checkout reads it from ``documentation/``.  ``read_agent_guide`` is the one
resolver.

The guide is served whole or as one PART (``?part=<part>``): the sections
tagged for that part plus every ``shared`` section, so an agent reads the
reference for what it is doing and not the rest.  A part is a reading aid, not
a kind of session and not a permission — one project session does every kind
of work.  Or as one SECTION (``?section=<name>``, 2.519.0), with
``section=contents`` listing the sections and their sizes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

AGENT_GUIDE_FILENAME = "AGENT_GUIDE.md"

#: The parts a caller may ask for.  A section's tag is one of these or
#: ``shared``; ``tests/test_docs_contract.py`` pins the two to each other.
GUIDE_PARTS = ("testing", "reconnaissance", "assist", "remediation")

#: Values that mean "the whole guide", like leaving the argument out.
_FULL_GUIDE_VALUES = {"project"}


class UnknownGuidePart(ValueError):
    """``part`` named something that is not a part of the guide."""

    def __init__(self, value: str):
        self.value = value
        super().__init__(
            f"part must be one of {', '.join(GUIDE_PARTS)} "
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


#: ``section=contents`` — the guide's table of contents, not a section.
GUIDE_CONTENTS = "contents"


class UnknownGuideSection(ValueError):
    """``section`` named no heading of the guide."""

    def __init__(self, value: str, slugs: List[str]):
        self.value = value
        super().__init__(
            f"section must be {GUIDE_CONTENTS!r} or one of: {', '.join(slugs)}; "
            f"not understood: {value!r}."
        )


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def guide_sections(content: str) -> List[Tuple[str, str, str]]:
    """The guide's ``## `` sections as ``(slug, title, text)`` (2.519.0).

    The whole guide is 80 KB and one part up to 82 KB — more than an agent's
    terminal shows (prod feedback 2026-10-20) — so an agent can read the
    contents and then one section.  A section runs from its ``## `` heading to
    the next one; a heading inside a code block is not one.  The part markers
    are dropped, as ``slice_agents_md`` drops them."""
    sections: List[Tuple[str, str, List[str]]] = []
    in_code = False
    for line in content.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("## "):
            title = line[3:].strip()
            sections.append((_slug(title), title, [line]))
            continue
        if sections and not (_SECTION_START.search(line) or _SECTION_END.search(line)):
            sections[-1][2].append(line)
    return [(slug, title, "\n".join(lines).strip() + "\n") for slug, title, lines in sections]


def guide_contents(content: str) -> str:
    """The table of contents ``section=contents`` answers."""
    rows = [
        f"- `{slug}` — {title} ({max(1, round(len(text.encode('utf-8')) / 1024))} KB)"
        for slug, title, text in guide_sections(content)
    ]
    return (
        "# Agent guide — contents\n\n"
        "Read one section with `section=<name>` (the name in backticks), or one "
        "part of the guide with `part=`.\n\n" + "\n".join(rows) + "\n"
    )


def guide_section(content: str, section: str) -> str:
    """One section by its name, or the contents; anything else is refused."""
    wanted = section.strip().lower()
    if wanted == GUIDE_CONTENTS:
        return guide_contents(content)
    sections = guide_sections(content)
    for slug, _title, text in sections:
        if slug == wanted:
            return text
    raise UnknownGuideSection(section, [slug for slug, _t, _x in sections])


def slice_agents_md(content: str, part: Optional[str]) -> str:
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
      * ``part=None`` (or ``project``) returns the full file unchanged.
      * Any other value that is not in ``GUIDE_PARTS`` raises
        ``UnknownGuidePart``, which the route answers with a 422 naming the
        accepted values: a value that cannot be understood is refused, never
        answered with something else.

    Matching is case-insensitive and ignores surrounding whitespace.
    """
    if part is None:
        return content
    requested = part.strip().lower()
    if requested in _FULL_GUIDE_VALUES:
        return content
    if requested not in GUIDE_PARTS:
        raise UnknownGuidePart(part)

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
    # …and the rules themselves: every dropped section left its own `---`, so
    # the remediation part printed six in a row (native MCP run 2026-10-10).
    result = re.sub(r'(?:^---\n\n){2,}', '---\n\n', result, flags=re.MULTILINE)
    return result
