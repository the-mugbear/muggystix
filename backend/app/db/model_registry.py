"""Single authoritative ORM model registry.

Importing this module imports every ``models*.py`` module for its side effect:
registering that module's tables on the shared ``Base.metadata``. It is the ONE
place that lists every model module — Alembic autogenerate (alembic/env.py), the
ingestion and report workers, and the test schema builder (tests/conftest.py)
all import THIS instead of each keeping their own copy of the list.

Those copies had already drifted three different ways before this existed:
env.py was missing ``models_attribution`` and ``models_tools`` (so autogenerate
proposed dropping ``network_attributions``, ``host_network_attributions`` and
``tool_registry`` — a data-loss migration waiting to be generated); the report
worker was missing ``models_tools``; and conftest was missing ``models_confidence``
and ``models_tools``. One import surface removes that entire class of defect.

Add a new ``models_*.py`` here and every consumer sees it at once. The
completeness guard in ``tests/test_model_registry.py`` fails if a ``models*.py``
module exists on disk that this file does not reference.
"""

# Re-exported so a consumer can do `from app.db.model_registry import Base` and
# get a Base guaranteed to have every table already registered on it.
from app.db.session import Base  # noqa: F401

# Side-effect imports. Order is irrelevant (relationships resolve lazily by
# string name), so keep it alphabetical — a missing module is then obvious in
# review, which is the whole point of having one list.
from app.db import (  # noqa: F401
    models,
    models_agent,
    models_attribution,
    models_auth,
    models_confidence,
    models_findings,
    models_integrations,
    models_llm,
    models_project,
    models_tools,
    models_vulnerability,
)

__all__ = ["Base"]
