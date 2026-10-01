"""Two real database sessions, for tests of what happens when two requests race.

Review 2026-10-01: ``conftest.db_session`` binds every session of a test to ONE
connection inside a transaction that is rolled back, so a "second" session
never blocks on the first and never fails to see its uncommitted rows.  Row
locks, unique-index races and commit ordering are therefore invisible to the
ordinary fixtures — the double accept, the double start and the duplicate
finding were all found in production, not by a test.

This module gives a test two INDEPENDENT sessions, each on its own connection
to the per-run test database, that really commit.  Because their rows outlive
the test, everything is created under a project made by :meth:`TwoSessions.project`
and removed at teardown (the project's delete cascades through its hosts,
findings, tests and evidence; users are deleted after it).

Use::

    from tests.two_connections import two_sessions  # noqa: F401  (fixture)

    def test_race(two_sessions):
        project, user = two_sessions.project()
        ...
        results = two_sessions.race(
            lambda db: do_it(db, project.id),      # runs on session A
            lambda db: do_it(db, project.id),      # runs on session B
        )

``race`` runs the two callables on two threads, released together, each with
its own session, committing on success and rolling back on an exception; it
returns ``[result or exception, result or exception]``.  For a deterministic
interleaving use the sessions directly (``two_sessions.a`` / ``.b``): do the
first step on A without committing, start B on a thread, assert it is blocked,
commit A, join.

Postgres only — SQLite's in-memory database is one connection, so the fixture
skips there.  Do not combine with ``db_session`` writes that the two sessions
must see: that fixture's rows are never committed.
"""
from __future__ import annotations

import threading
import uuid
from typing import Callable, List, Tuple

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import get_password_hash
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole


class TwoSessions:
    def __init__(self, engine):
        self._engine = engine
        # The application's own session settings (autoflush off).
        self._factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.a: Session = self._factory()
        self.b: Session = self._factory()
        self._project_ids: List[int] = []
        self._user_ids: List[int] = []

    # -- committed fixtures -------------------------------------------------
    def project(self, role: str = ProjectRole.ADMIN.value) -> Tuple[Project, User]:
        """A committed project and a member of it, both removed at teardown.
        Returned detached-safe: read ``.id`` freely from any thread."""
        tag = uuid.uuid4().hex[:10]
        db = self._factory()
        try:
            user = User(
                username=f"race-{tag}", email=f"race-{tag}@example.com", full_name="Race Tester",
                hashed_password=get_password_hash("Race-Password-123!"),
                role=UserRole.MEMBER, is_active=True, is_verified=True,
            )
            project = Project(name=f"race-{tag}", slug=f"race-{tag}", description="two-connection test")
            db.add_all([user, project])
            db.flush()
            db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
            db.commit()
            self._project_ids.append(project.id)
            self._user_ids.append(user.id)
            db.refresh(project)
            db.refresh(user)
            db.expunge_all()
            return project, user
        finally:
            db.close()

    def commit(self, build: Callable[[Session], object]):
        """Run ``build(db)`` in a short session of its own and commit — for
        the rows both racers must see.  Returns what ``build`` returned,
        refreshed and detached."""
        db = self._factory()
        try:
            out = build(db)
            db.commit()
            if out is not None and hasattr(out, "__table__"):
                db.refresh(out)
            db.expunge_all()
            return out
        finally:
            db.close()

    def fresh(self) -> Session:
        """A third session, to read what the racers committed.  Close it."""
        return self._factory()

    # -- racing ---------------------------------------------------------------
    def race(self, first: Callable[[Session], object], second: Callable[[Session], object],
             timeout: float = 30.0) -> list:
        """Run ``first(a)`` and ``second(b)`` at the same moment on two
        threads.  Each commits when its callable returns and rolls back when
        it raises.  Returns the two results (an exception in place of a result
        for the one that raised).  A thread still running after ``timeout``
        fails the test — that is a deadlock or a lock nobody released."""
        barrier = threading.Barrier(2)
        results: list = [None, None]

        def run(index: int, db: Session, fn: Callable[[Session], object]) -> None:
            try:
                barrier.wait(timeout=timeout)
                results[index] = fn(db)
                db.commit()
            except BaseException as exc:  # noqa: BLE001 — handed back to the test
                db.rollback()
                results[index] = exc

        threads = [
            threading.Thread(target=run, args=(0, self.a, first), daemon=True),
            threading.Thread(target=run, args=(1, self.b, second), daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout)
        stuck = [i for i, t in enumerate(threads) if t.is_alive()]
        if stuck:
            pytest.fail(f"race(): session(s) {stuck} did not finish within {timeout}s — deadlock or a held lock")
        return results

    # -- teardown -------------------------------------------------------------
    def close(self) -> None:
        for db in (self.a, self.b):
            try:
                db.rollback()
            finally:
                db.close()
        with self._engine.begin() as conn:
            for pid in self._project_ids:
                conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": pid})
            for uid in self._user_ids:
                conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})


@pytest.fixture
def two_sessions(test_engine):
    """Two independent, really-committing sessions (see the module docstring)."""
    if test_engine.dialect.name != "postgresql":
        pytest.skip("needs two real connections; the SQLite fallback has one")
    pair = TwoSessions(test_engine)
    try:
        yield pair
    finally:
        pair.close()
