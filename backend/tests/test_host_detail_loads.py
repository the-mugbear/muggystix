"""The host DETAIL query loads what it serialises, once (v2.368.2).

Code review finding 6. ``Host.vulnerabilities``, ``.attributes`` and ``.notes``
are ``lazy="selectin"`` on the model, so loading the entity fetched all three.
The handler then queried the vulnerabilities AGAIN with its informational
filter — which the eager copy had already defeated: every info row, each with
its plugin text, was loaded regardless — and took notes from the follow
service. Nothing read the eager copies. The list query has always carried
``noload`` for the three; the detail did not.

A relationship's selectin load is recognisable by ``host_id IN (…)`` bound to
``primary_keys`` — see ``_eager_loads``.
"""
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def _seed(db, project):
    host = models.Host(project_id=project.id, ip_address="10.66.0.1", state="up")
    db.add(host)
    db.flush()
    for i, severity in enumerate(
        [VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.INFO, VulnerabilitySeverity.INFO]
    ):
        db.add(Vulnerability(
            host_id=host.id, title=f"issue {i}", severity=severity,
            source=VulnerabilitySource.NESSUS, plugin_id=str(1000 + i),
        ))
    db.commit()
    return host


def _detail_statements(client, project, host, **params):
    statements: list = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", _record)
    try:
        r = client.get(f"/api/v1/projects/{project.id}/hosts/{host.id}", params=params)
    finally:
        event.remove(Engine, "after_cursor_execute", _record)
    assert r.status_code == 200, r.text
    return r.json(), statements


def _eager_loads(statements, table):
    # ``primary_keys`` is the bind parameter SQLAlchemy's selectin loader uses;
    # the handler's own grouped severity count also says ``host_id IN`` but
    # binds ``host_id_1``, and is wanted.
    return [s for s in statements if f"{table}.host_id IN" in s and "primary_keys" in s]


def test_detail_does_not_eager_load_what_it_queries_itself(client, db_session, test_project):
    host = _seed(db_session, test_project)
    body, statements = _detail_statements(client, test_project, host)

    for table in ("vulnerabilities", "annotations", "host_attributes"):
        assert _eager_loads(statements, table) == [], (
            f"the host entity eager-loaded {table}; the handler does not read it"
        )
    # …and what the response says is unchanged: info rows hidden, but counted.
    assert [v["title"] for v in body["vulnerabilities"]] == ["issue 0"]
    assert body["informational_count"] == 2


def test_include_info_still_returns_every_row(client, db_session, test_project):
    host = _seed(db_session, test_project)
    body, statements = _detail_statements(client, test_project, host, include_info="true")
    assert len(body["vulnerabilities"]) == 3
    assert _eager_loads(statements, "vulnerabilities") == []
