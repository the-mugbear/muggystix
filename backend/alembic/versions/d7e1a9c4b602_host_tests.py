"""Host-owned tests and evidence; convert legacy per-test records.

Run with all application writers stopped, after scripts/backup-db.sh.
Conversion is transactional. Unexpected historical shapes fail with their IDs
rather than silently dropping work. The next revision retires old tables.
"""
import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "d7e1a9c4b602"
down_revision = "e3a7c1d9f5b2"
branch_labels = None
depends_on = None

# Frozen DDL: never import application models from a historical migration.
HOST_TEST_DDL = [
  "\nCREATE TABLE host_tests (\n\tid SERIAL NOT NULL, \n\tproject_id INTEGER NOT NULL, \n\thost_id INTEGER NOT NULL, \n\tname_id INTEGER, \n\ttarget_fqdn VARCHAR(253), \n\ttool VARCHAR(100), \n\tdescription TEXT NOT NULL, \n\tcommand TEXT, \n\trationale TEXT NOT NULL, \n\texpected_result TEXT, \n\t\"references\" JSON, \n\tpriority VARCHAR(20) NOT NULL, \n\tlabel VARCHAR(255), \n\tstatus VARCHAR(20) NOT NULL, \n\tassigned_to_id INTEGER, \n\ttester_summary TEXT, \n\tsource VARCHAR(20) NOT NULL, \n\tagent_session_id INTEGER, \n\tcreated_by_user_id INTEGER, \n\tagent_model VARCHAR(100), \n\tagent_client VARCHAR(100), \n\tprompt_version VARCHAR(20), \n\tdismissed_by_id INTEGER, \n\tdismissed_at TIMESTAMP WITH TIME ZONE, \n\tdismissed_reason TEXT, \n\trequest_key VARCHAR(100) NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\trevision INTEGER DEFAULT '1' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_host_test_status CHECK (status IN ('proposed','in_progress','done','dismissed')), \n\tCONSTRAINT ck_host_test_priority CHECK (priority IN ('critical','high','medium','low','info')), \n\tCONSTRAINT ck_host_test_source CHECK (source IN ('agent','person')), \n\tCONSTRAINT uq_host_test_request UNIQUE (project_id, request_key), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, \n\tFOREIGN KEY(host_id) REFERENCES hosts_v2 (id) ON DELETE CASCADE, \n\tFOREIGN KEY(name_id) REFERENCES dns_names (id) ON DELETE SET NULL, \n\tFOREIGN KEY(assigned_to_id) REFERENCES users (id) ON DELETE SET NULL, \n\tFOREIGN KEY(agent_session_id) REFERENCES agent_sessions (id) ON DELETE SET NULL, \n\tFOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tFOREIGN KEY(dismissed_by_id) REFERENCES users (id) ON DELETE SET NULL\n)\n\n",
  "CREATE INDEX ix_host_test_project_status ON host_tests (project_id, status, id)",
  "CREATE INDEX ix_host_test_assignee ON host_tests (project_id, assigned_to_id, status)",
  "CREATE INDEX ix_host_test_host_status ON host_tests (host_id, status)",
  "CREATE INDEX ix_host_test_label ON host_tests (project_id, label)"
]


def upgrade():
    db = op.get_bind()
    for statement in HOST_TEST_DDL:
        op.execute(statement)
    op.add_column("evidence_records", sa.Column("host_test_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_evidence_host_test", "evidence_records", "host_tests", ["host_test_id"], ["id"], ondelete="SET NULL")
    op.add_column("evidence_records", sa.Column("request_key", sa.String(100), nullable=True))
    op.add_column("evidence_records", sa.Column("request_hash", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_evidence_request", "evidence_records", ["project_id", "request_key"])
    op.create_index("ix_evidence_host_test_created", "evidence_records", ["host_test_id", "created_at", "id"])
    op.add_column("dns_records", sa.Column("evidence_record_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_dns_evidence_record", "dns_records", "evidence_records", ["evidence_record_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_dns_records_evidence_record_id", "dns_records", ["evidence_record_id"])
    op.execute("CREATE UNIQUE INDEX uq_dns_record_evidence_observation ON dns_records (name_id, record_type, value, evidence_record_id) WHERE evidence_record_id IS NOT NULL")
    op.execute("CREATE TEMP TABLE host_test_conversion (entry_id integer, test_index integer, test_id integer, PRIMARY KEY(entry_id,test_index)) ON COMMIT DROP")
    op.execute("CREATE TEMP TABLE evidence_conversion (result_id integer PRIMARY KEY, evidence_id integer) ON COMMIT DROP")
    meta = sa.MetaData()
    tests = sa.Table("host_tests", meta, autoload_with=db)
    evidence = sa.Table("evidence_records", meta, autoload_with=db)
    rows = db.execute(sa.text("""
        SELECT e.*, p.project_id, p.title, p.status AS plan_status, p.agent_id,
               p.agent_session_id, p.created_by_user_id, p.generated_by_model,
               p.generated_by_tool, p.prompt_version, n.fqdn,
               s.started_by_id AS session_user
        FROM test_plan_entries e JOIN test_plans p ON p.id=e.test_plan_id
        LEFT JOIN dns_names n ON n.id=e.name_id
        LEFT JOIN agent_sessions s ON s.id=p.agent_session_id
        ORDER BY e.id
    """)).mappings()
    for entry in rows:
        proposed = entry["proposed_tests"]
        if not isinstance(proposed, list):
            raise RuntimeError(f"Entry {entry['id']} has a non-array proposed_tests; review it before migration")
        if not proposed:
            proposed = [{"description": entry["rationale"] or "Historical host task"}]
        for index, original in enumerate(proposed):
            spec = {"description": original} if isinstance(original, str) else original
            if not isinstance(spec, dict):
                raise RuntimeError(f"Entry {entry['id']} test {index} is not an object or string")
            state = {"completed": "done", "rejected": "dismissed", "proposed": "proposed", "in_progress": "in_progress"}.get(entry["status"])
            if state is None:
                raise RuntimeError(f"Entry {entry['id']} has unknown status {entry['status']}")
            if entry["plan_status"] == "archived":
                state = "dismissed"
            key = f"migration:entry:{entry['id']}:test:{index}"
            summary = "\n\n".join(str(v) for v in (entry["findings"], entry["notes"], json.dumps(entry["results_data"]) if entry["results_data"] else None) if v)
            values = dict(project_id=entry["project_id"], host_id=entry["host_id"],
                name_id=entry["name_id"], target_fqdn=entry["fqdn"],
                tool=spec.get("tool"), description=spec.get("description") or spec.get("tool") or entry["rationale"] or "Historical test",
                command=spec.get("command"), rationale=entry["rationale"] or "",
                expected_result=spec.get("expected_result"), references=spec.get("references"),
                priority=entry["priority"], label=entry["title"], status=state,
                assigned_to_id=entry["assigned_to_id"], tester_summary=summary or None,
                source="agent" if entry["agent_id"] or entry["agent_session_id"] else "person",
                agent_session_id=entry["agent_session_id"],
                created_by_user_id=entry["created_by_user_id"] or entry["session_user"],
                agent_model=entry["generated_by_model"], agent_client=entry["generated_by_tool"],
                prompt_version=entry["prompt_version"], request_key=key,
                request_hash=hashlib.sha256(key.encode()).hexdigest(), revision=1,
                dismissed_reason="Migrated archived/rejected work" if state == "dismissed" else None)
            if entry["created_at"]:
                values["created_at"] = entry["created_at"]
            if entry["updated_at"]:
                values["updated_at"] = entry["updated_at"]
            new_id = db.execute(tests.insert().values(**values).returning(tests.c.id)).scalar_one()
            db.execute(sa.text("INSERT INTO host_test_conversion VALUES (:e,:i,:t)"), dict(e=entry["id"], i=index, t=new_id))
    for result in db.execute(sa.text("""
        SELECT r.*, m.test_id, t.project_id, t.host_id, t.tool,
               s.agent_session_id, s.started_by_id, s.generated_by_model, s.generated_by_tool
        FROM test_execution_results r
        LEFT JOIN host_test_conversion m ON m.entry_id=r.entry_id AND m.test_index=r.test_index
        LEFT JOIN host_tests t ON t.id=m.test_id
        JOIN execution_sessions s ON s.id=r.execution_session_id ORDER BY r.id
    """)).mappings():
        if result["test_id"] is None:
            raise RuntimeError(f"Result {result['id']} points to missing test index; repair before migration")
        if result["status"] in ("pending", "pending_approval"):
            # Pending rows have no execution evidence. Reject anomalous rows
            # carrying actual output instead of discarding it.
            if result["raw_output"] or result["findings_summary"]:
                raise RuntimeError(f"Pending result {result['id']} carries output; review before migration")
            continue
        outcomes = {"executed": "finding" if result["is_finding"] else "inconclusive",
                    "failed": "failed", "skipped": "info", "not_applicable": "info"}
        if result["status"] not in outcomes:
            raise RuntimeError(f"Result {result['id']} has unknown status")
        finding_ids = list(db.execute(sa.text("SELECT id FROM findings WHERE exec_result_id=:id"), {"id": result["id"]}).scalars())
        if len(finding_ids) > 1:
            raise RuntimeError(f"Result {result['id']} backs multiple findings; resolve provenance before migration")
        raw = result["raw_output"]
        values = dict(project_id=result["project_id"], host_id=result["host_id"],
            host_test_id=result["test_id"], finding_id=finding_ids[0] if finding_ids else None,
            tool=result["tool"] or "historical", command=result["command_run"],
            outcome=outcomes[result["status"]],
            summary=f"Legacy {result['status']}: " + (result["findings_summary"] or "No summary recorded"),
            raw_output=raw, raw_output_preview=raw[:2000] if raw else None,
            raw_output_bytes=len(raw.encode("utf-8")) if raw else None,
            observed_ip=result["observed_ip"], executed_at=result["executed_at"],
            agent_session_id=result["agent_session_id"], recorded_by_user_id=result["started_by_id"],
            agent_model=result["generated_by_model"], agent_client=result["generated_by_tool"])
        if result["created_at"]:
            values["created_at"] = result["created_at"]
        evidence_id = db.execute(evidence.insert().values(**values).returning(evidence.c.id)).scalar_one()
        db.execute(sa.text("INSERT INTO evidence_conversion VALUES (:r,:e)"), {"r": result["id"], "e": evidence_id})
    op.execute("UPDATE dns_records d SET evidence_record_id=m.evidence_id FROM evidence_conversion m WHERE d.exec_result_id=m.result_id")
    missing = db.execute(sa.text("""
        SELECT id FROM dns_records WHERE exec_result_id IS NOT NULL AND evidence_record_id IS NULL
        UNION ALL SELECT f.id FROM findings f WHERE f.exec_result_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM evidence_conversion m WHERE m.result_id=f.exec_result_id)
    """)).first()
    if missing:
        raise RuntimeError("Unconverted finding/DNS result reference; review legacy data before migration")


def downgrade():
    # Evidence remains readable. Lossless restoration of plan/run structure
    # requires the pre-cutover backup, not a schema-only downgrade.
    op.drop_index("uq_dns_record_evidence_observation", table_name="dns_records")
    op.drop_column("dns_records", "evidence_record_id")
    # The evidence this revision made out of execution results goes back with
    # it (the results are still in their table at this point), so a later
    # upgrade converts them once, not twice.  Evidence recorded since against
    # a converted test stays, without its test link.  Done after the DNS
    # column is gone: its FK cascades.
    op.execute("""
        DELETE FROM evidence_records e USING host_tests t
         WHERE e.host_test_id = t.id AND t.request_key LIKE 'migration:entry:%'
           AND e.request_key IS NULL AND e.summary LIKE 'Legacy %'
    """)
    op.drop_index("ix_evidence_host_test_created", table_name="evidence_records")
    op.drop_constraint("uq_evidence_request", "evidence_records", type_="unique")
    for column in ("host_test_id", "request_key", "request_hash"):
        op.drop_column("evidence_records", column)
    op.drop_table("host_tests")
