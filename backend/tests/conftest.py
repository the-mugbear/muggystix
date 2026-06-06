import os
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db
from app.db import models
from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.models_auth import User, UserRole

# A real bcrypt hash so password-verifying endpoints (change-password,
# etc.) don't blow up with passlib UnknownHashError on a placeholder
# string.  Tests that need to authenticate as the fixture user can use
# TEST_USER_PASSWORD.
TEST_USER_PASSWORD = "Test-Password-123!"
TEST_USER_PW_HASH = get_password_hash(TEST_USER_PASSWORD)

# Import the non-default model modules so their tables are registered
# on the shared SQLAlchemy Base *before* create_all runs.  Without
# these imports, contract tests that touch TestPlan / ExecutionSession
# / AgentFeedback / LLMProvider / IntegrationCredential hit "no such
# table" errors because the declarative Base never saw them.
from app.db import (  # noqa: F401  (side-effect imports)
    models_agent,
    models_auth,
    models_integrations,
    models_llm,
    models_project,
    models_risk,
    models_vulnerability,
)

# ---------------------------------------------------------------------------
# Test database selection
# ---------------------------------------------------------------------------
# The suite prefers a real PostgreSQL database: it matches production
# semantics (strict typing, real FK enforcement, transactional DDL) and
# lets the Postgres-only code paths actually run — the raw pg_catalog SQL
# in delete_scan and the masscan batch-upsert parser are simply invisible
# to SQLite.  When no Postgres server is reachable the suite falls back to
# an in-memory SQLite DB so `pytest` still works on a bare host (at the
# cost of skipping the Postgres-only tests — see USING_POSTGRES).
#
# Resolution order:
#   1. $TEST_DATABASE_URL, if set (explicit override).
#   2. a "<app-db>_test" database on the app's own Postgres server, if
#      that server is reachable — auto-created if it doesn't exist.
#   3. in-memory SQLite.


def _ensure_database(url) -> None:
    """CREATE DATABASE url.database if it does not already exist.

    Connects to the always-present ``postgres`` maintenance DB; CREATE
    DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    """
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()


def _postgres_reachable(url) -> bool:
    try:
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect():
            pass
        admin.dispose()
        return True
    except Exception:
        return False


def _make_sqlite_engine(url: str = "sqlite:///:memory:"):
    """SQLite engine wired for the test harness.

    StaticPool + check_same_thread=False is mandatory for an in-memory DB:
    without it every connection gets its *own* empty database, so the
    schema created on one connection is invisible to the test session's
    connection.
    """
    return create_engine(
        url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _resolve_test_engine():
    """Return ``(engine, using_postgres)`` per the resolution order above."""
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
        if url.get_backend_name().startswith("postgresql"):
            _ensure_database(url)
            return create_engine(url, poolclass=NullPool), True
        if url.get_backend_name().startswith("sqlite"):
            return _make_sqlite_engine(explicit), False
        return create_engine(url), False

    app_url = make_url(settings.DATABASE_URL)
    if app_url.get_backend_name().startswith("postgresql") and _postgres_reachable(app_url):
        test_url = app_url.set(database=f"{app_url.database}_test")
        _ensure_database(test_url)
        return create_engine(test_url, poolclass=NullPool), True

    return _make_sqlite_engine(), False


engine, USING_POSTGRES = _resolve_test_engine()
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session")
def test_engine():
    """Build the test schema once per session.

    create_all binds every table registered on ``models.Base`` — the
    side-effect imports above ensure all model modules are loaded first.
    On Postgres we drop first so a previous run's leftovers can't leak in;
    the in-memory SQLite DB is per-process so there is nothing to drop.
    """
    if USING_POSTGRES:
        models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Shared fixtures for the v2.9.x service-level tests.
# ---------------------------------------------------------------------------

@pytest.fixture
def test_project(db_session):
    """Return a persisted Project row usable by downstream fixtures."""
    from app.db.models_project import Project
    project = Project(
        name="test-project",
        slug="test-project",
        description="test",
        is_default=True,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def test_user(db_session):
    """Persisted admin User — the identity the ``client`` fixture
    authenticates as.  Must be a real row (not just an in-memory object):
    endpoints that create records referencing ``current_user.id`` would
    otherwise hit a foreign-key violation on a real database.
    """
    user = User(
        id=1,
        username="test-admin",
        email="admin@example.com",
        full_name="Test Admin",
        hashed_password=TEST_USER_PW_HASH,
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_agent(db_session, test_project, test_user):
    """Return a persisted Agent scoped to test_project + test_user."""
    from app.db.models_agent import Agent
    agent = Agent(
        name="test-agent",
        project_id=test_project.id,
        owner_id=test_user.id,
        description="contract test fixture",
        is_active=True,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture
def test_plan(db_session, test_project, test_agent):
    """Return a persisted TestPlan in 'approved' state ready for execution."""
    from app.db.models_agent import TestPlan, TestPlanStatus
    plan = TestPlan(
        project_id=test_project.id,
        agent_id=test_agent.id,
        version=1,
        title="contract test plan",
        description="fixture",
        status=TestPlanStatus.APPROVED.value,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.fixture
def db_session(test_engine):
    """Create a fresh database session for each test.

    Uses the standard SQLAlchemy "join-to-outer-transaction with
    nested savepoint" pattern so services that commit internally
    (``IntegrationService.create``, ``LLMProviderService.create``,
    etc — these are the #43 commit-boundary inconsistencies) still
    leave the test in a clean state.  The outer transaction owns
    the connection; service commits close savepoints, not the real
    transaction; teardown rolls back the outer transaction and
    wipes the whole test's data.

    See https://docs.sqlalchemy.org/en/20/orm/session_transaction.html
    ("Joining a Session into an External Transaction").
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    # Start a SAVEPOINT the session can rollback to on service commit.
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:
            # Service just committed its savepoint; start a fresh one
            # so the next service commit has something to land on.
            nested = connection.begin_nested()

    # v2.24.0 — the agent API call logger middleware writes via its own
    # SessionLocal().  Without this rebind the writes would target the
    # production DB (where the test's agent_id doesn't exist, so the FK
    # fails and the middleware silently drops the row).  Pointing
    # SessionLocal at our test connection makes the middleware writes
    # land in the same transactional sandbox as the test's db_session,
    # so they roll back at teardown — no cross-test leakage.
    from app.db import session as _session_module
    _orig_session_local = _session_module.SessionLocal
    _session_module.SessionLocal = lambda: TestingSessionLocal(bind=connection)

    yield session

    _session_module.SessionLocal = _orig_session_local
    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session, test_user):
    """Test client authenticated as the persisted admin ``test_user``.

    ``get_current_user`` returns the real persisted row (not an in-memory
    stand-in) so endpoints that write rows referencing the current user's
    id satisfy their foreign keys.
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    def override_get_current_user():
        return test_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_agent_rate_limit_state():
    """Clear the in-process agent rate-limit sliding-window between tests.

    ``deps._AGENT_RECENT_CALLS`` is a module-global keyed by agent_id; with
    per-test rollback, agent_ids repeat across tests, so a deque left
    populated by one test could spuriously trip the limiter in the next.
    """
    from app.api import deps
    with deps._AGENT_RECENT_CALLS_LOCK:
        deps._AGENT_RECENT_CALLS.clear()
    yield
    with deps._AGENT_RECENT_CALLS_LOCK:
        deps._AGENT_RECENT_CALLS.clear()


@pytest.fixture
def temp_file():
    """Create a temporary file for testing file uploads."""
    temp_fd, temp_path = tempfile.mkstemp()
    yield temp_path
    os.close(temp_fd)
    os.unlink(temp_path)


@pytest.fixture
def sample_nmap_xml():
    """Sample Nmap XML data for testing."""
    return '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nmaprun>
<nmaprun scanner="nmap" args="nmap -oX test.xml 192.168.1.1" start="1640995200" startstr="Sat Jan  1 00:00:00 2022" version="7.92" xmloutputversion="1.05">
    <scaninfo type="syn" protocol="tcp" numservices="1000" services="1-1000"/>
    <verbose level="0"/>
    <debugging level="0"/>
    <host starttime="1640995200" endtime="1640995210">
        <status state="up" reason="syn-ack" reason_ttl="0"/>
        <address addr="192.168.1.1" addrtype="ipv4"/>
        <hostnames>
            <hostname name="router.local" type="PTR"/>
        </hostnames>
        <ports>
            <extraports state="closed" count="998">
                <extrareasons reason="resets" count="998"/>
            </extraports>
            <port protocol="tcp" portid="22">
                <state state="open" reason="syn-ack" reason_ttl="0"/>
                <service name="ssh" product="OpenSSH" version="7.4" extrainfo="protocol 2.0" method="probed" conf="10"/>
            </port>
            <port protocol="tcp" portid="80">
                <state state="open" reason="syn-ack" reason_ttl="0"/>
                <service name="http" product="nginx" version="1.14.0" method="probed" conf="10"/>
            </port>
        </ports>
        <times srtt="1000" rttvar="1000" to="100000"/>
    </host>
</nmaprun>'''


@pytest.fixture
def sample_gnmap_data():
    """Sample gnmap data for testing."""
    return '''Nmap 7.92 scan initiated Mon Jul 15 10:30:01 2024 as: nmap -oG test.gnmap -sV -T4 192.168.1.1-2
Ports scanned: TCP(1000) UDP(0) SCTP(0) PROTOCOLS(0)

Host: 192.168.1.1 (router.local)	Status: Up
Host: 192.168.1.1 (router.local)	Ports: 22/open/tcp//ssh/OpenSSH 7.6p1/, 80/open/tcp//http/nginx 1.14.0/
Host: 192.168.1.2 (server.local)	Status: Up
Host: 192.168.1.2 (server.local)	Ports: 443/open/tcp//https/Apache httpd 2.4.29/
Nmap done at Mon Jul 15 10:30:25 2024; 2 IP addresses (2 hosts up) scanned in 24.12 seconds'''


@pytest.fixture
def sample_masscan_xml():
    """Sample Masscan XML data for testing."""
    return '''<?xml version="1.0"?>
<nmaprun scanner="masscan" start="1640995200" version="1.0.5" xmloutputversion="1.03">
<scaninfo type="syn" protocol="tcp" numservices="2" services="80,443"/>
<host endtime="1640995210">
    <address addr="192.168.1.100" addrtype="ipv4"/>
    <ports>
        <port protocol="tcp" portid="80">
            <state state="open" reason="syn-ack" reason_ttl="0"/>
        </port>
        <port protocol="tcp" portid="443">
            <state state="open" reason="syn-ack" reason_ttl="0"/>
        </port>
    </ports>
</host>
</nmaprun>'''


@pytest.fixture
def sample_eyewitness_json():
    """Sample EyeWitness JSON data for testing."""
    return '''{
    "version": "3.7.0",
    "results": [
        {
            "remote_system": "http://192.168.1.1:80",
            "protocol": "http",
            "hostname": "web.local",
            "ip": "192.168.1.1",
            "port": 80,
            "page_title": "Welcome to nginx!",
            "screenshot_path": "/opt/eyewitness/screenshots/192.168.1.1_80.png",
            "server_header": "nginx/1.14.0",
            "content_length": 612,
            "response_code": 200,
            "page_text": "Welcome to nginx! This is a test web server.",
            "category": "Uncategorized"
        },
        {
            "remote_system": "https://192.168.1.2:443",
            "protocol": "https",
            "hostname": "secure.local",
            "ip": "192.168.1.2",
            "port": 443,
            "page_title": "Secure Login",
            "screenshot_path": "/opt/eyewitness/screenshots/192.168.1.2_443.png",
            "server_header": "Apache/2.4.29",
            "content_length": 1024,
            "response_code": 200,
            "page_text": "Please enter your credentials to access the secure area.",
            "category": "Login"
        }
    ]
}'''
