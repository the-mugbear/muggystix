"""Read and seed the tool registry.

The registry is DB-backed rather than a code constant on purpose: the point of
capturing an agent's tool suggestion is to close the loop faster than a release,
and if vetting means a pull request then the loop is only ever as fast as the
deploy cadence.  The curated seed still lives in version control
(``app/data/tool_registry_seed.json``) so the starting set is reviewable; the DB
is what an admin edits afterwards.

Seeding is **additive and non-destructive**.  A re-seed inserts tools that are
missing and leaves existing rows alone — an operator's approval decision, or an
edited description, must survive a redeploy.  That means a change to the seed
file updates nothing for existing deployments; changing a shipped tool's
metadata after the fact is deliberately an admin action, not a silent overwrite.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models_tools import (
    TOOL_APPROVED,
    TOOL_SUGGESTED,
    ToolRegistryEntry,
)

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "tool_registry_seed.json"

# Ceiling on the accumulated rationale for one suggested tool.  Roughly a dozen
# max-length asks — far more than a reviewer will read, far less than an
# unbounded column.
_MAX_RATIONALE_CHARS = 20_000


def load_seed() -> List[Dict[str, Any]]:
    if not SEED_PATH.exists():  # pragma: no cover - packaging guard
        logger.warning("tool registry seed missing at %s", SEED_PATH)
        return []
    with SEED_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def seed_registry(db: Session) -> int:
    """Insert any seed tools not already present.  Returns the number added."""
    existing = {name for (name,) in db.query(ToolRegistryEntry.name).all()}
    added = 0
    for row in load_seed():
        if row["name"] in existing:
            continue
        db.add(ToolRegistryEntry(**row))
        added += 1
    if added:
        db.commit()
        logger.info("tool registry: seeded %d tool(s)", added)
    return added


def list_tools(
    db: Session, *, status: Optional[str] = None, category: Optional[str] = None
) -> List[ToolRegistryEntry]:
    q = db.query(ToolRegistryEntry)
    if status:
        q = q.filter(ToolRegistryEntry.status == status)
    if category:
        q = q.filter(ToolRegistryEntry.category == category)
    return q.order_by(ToolRegistryEntry.category, ToolRegistryEntry.name).all()


def approved_tool_names(db: Session) -> set:
    """The set an auto-approval rule keys off — nothing else may run unprompted."""
    return {
        name
        for (name,) in db.query(ToolRegistryEntry.name)
        .filter(ToolRegistryEntry.status == TOOL_APPROVED)
        .all()
    }


def record_suggestion(
    db: Session,
    *,
    name: str,
    rationale: str,
    agent_id: Optional[int] = None,
    project_id: Optional[int] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
) -> ToolRegistryEntry:
    """Record an agent asking for a tool the registry doesn't approve.

    A suggestion is a **row in the same table**, not a note in a separate store:
    vetting is then a status change rather than a copy between systems, and the
    suggested tool shows up on the reference page next to the vetted ones,
    visibly unapproved. Re-suggesting a known tool appends the new rationale
    instead of creating a duplicate — the second ask is evidence of demand, not
    a new tool.
    """
    entry = db.query(ToolRegistryEntry).filter(ToolRegistryEntry.name == name).one_or_none()
    if entry is not None:
        if entry.status == TOOL_SUGGESTED and rationale:
            prior = entry.suggested_rationale or ""
            if rationale not in prior:
                merged = f"{prior}\n---\n{rationale}".strip()
                # Bounded: the ask is agent-supplied and the route that reaches
                # here is ungated by design, so an unbounded append is a text
                # column any authenticated agent could grow forever.  Keep the
                # most recent asks — a reviewer reads the latest demand, and the
                # earliest one is already reflected in the row existing at all.
                if len(merged) > _MAX_RATIONALE_CHARS:
                    merged = merged[-_MAX_RATIONALE_CHARS:]
                entry.suggested_rationale = merged
                db.commit()
        return entry

    entry = ToolRegistryEntry(
        name=name,
        # A suggestion has no curated prose yet; the agent's rationale stands in
        # until a human writes one, rather than leaving the row blank.
        description=description or f"Suggested by an agent. Rationale: {rationale}",
        category=category or "Uncategorised",
        status=TOOL_SUGGESTED,
        ingestible=False,
        suggested_rationale=rationale,
        suggested_by_agent_id=agent_id,
        suggested_in_project_id=project_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    logger.info("tool registry: new suggestion %r", name)
    return entry
