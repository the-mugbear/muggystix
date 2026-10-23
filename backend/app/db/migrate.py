"""The migration step: bring the database to Alembic head, then exit.

Run as:  ``python -m app.db.migrate``

The backend's and the ingestion worker's container commands run this BEFORE
they start their server or loop (``backend/Dockerfile`` CMD, the worker's
``command:`` in ``docker-compose.yml``); the report worker does not. Importing
``app.main`` or ``app.worker`` touches no schema: a script, a probe or the test
suite that imports the application migrates nothing.

Exit status 0 = the schema is at head. Anything else = it is not, and the
container command stops there instead of serving: the failure has already been
said once (``DATABASE MIGRATION FAILED …``, the line ``scripts/stack-lib.sh``
reads) by ``app.db.init``; Docker's restart policy runs the step again.
"""

from __future__ import annotations

import logging
import sys

from app.db.init import initialize_database


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    initialize_database()
    return 0


if __name__ == "__main__":
    sys.exit(main())
