"""Drop test plans and execution runs; host tests and evidence replace them.

The second half of the host-tests cutover (``d7e1a9c4b602`` created
``host_tests`` and converted every plan entry and execution result).  This
revision removes what nothing reads any more:

* tables ``imported_result_files``, ``host_sanity_checks``,
  ``test_execution_results``, ``test_plan_history``, ``execution_sessions``,
  ``test_plan_entries``, ``test_plans``;
* ``findings.exec_result_id`` (evidence records carry ``finding_id``),
  ``dns_records.exec_result_id`` (TESTED observations are keyed by
  ``evidence_record_id``), ``agent_feedback.test_plan_id`` /
  ``execution_session_id`` and ``agent_api_calls.test_plan_id`` /
  ``execution_session_id`` (both keep ``agent_session_id``);
* ``annotations.plan_id``: a note written on a plan moves to the plan's
  PROJECT — it is never copied onto the plan's hosts — and the
  exactly-one-target CHECK is rebuilt without the column;
* notifications that linked to a plan or a run keep their text and lose the
  link (the pages are gone).

Run ``scripts/backup-db.sh`` first, with the application stopped: sanity
checks, plan history, imported-result audit rows and the plan / run structure
have no replacement table, so the backup is the only copy.

The downgrade recreates every table, column, constraint and index EMPTY.  It
restores the schema, not the data: restoring the data needs the backup.
"""
import sqlalchemy as sa
from alembic import op

revision = "f4b8d2a6c917"
down_revision = "d7e1a9c4b602"
branch_labels = None
depends_on = None

_OLD_TABLES = (
    "imported_result_files",
    "host_sanity_checks",
    "test_execution_results",
    "test_plan_history",
    "execution_sessions",
    "test_plan_entries",
    "test_plans",
)

# Frozen DDL of the dropped tables — pg_dump of a database BUILT BY THE CHAIN
# up to d7e1a9c4b602, not of a long-lived one (the dev database still carried
# test_execution_results.sanity_override_reason, which c7d2e9f4a1b6 drops).
# Never import application models from a migration.
_RECREATE = (
    """CREATE TABLE execution_sessions (
    id integer NOT NULL,
    test_plan_id integer NOT NULL,
    agent_id integer,
    started_by_id integer,
    status character varying(20) NOT NULL,
    mode character varying(20) NOT NULL,
    bundle_id character varying(64),
    started_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    generated_by_model character varying(100),
    generated_by_tool character varying(100),
    prompt_version character varying(20),
    notes text,
    agent_session_id integer
)""",
    """CREATE SEQUENCE execution_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE execution_sessions_id_seq OWNED BY execution_sessions.id""",
    """CREATE TABLE host_sanity_checks (
    id integer NOT NULL,
    execution_session_id integer NOT NULL,
    entry_id integer NOT NULL,
    host_id integer NOT NULL,
    method character varying(30) NOT NULL,
    target_ip character varying(45) NOT NULL,
    port_checked integer,
    expected_value text,
    actual_value text,
    source_ip character varying(45),
    dns_result character varying(255),
    passed boolean NOT NULL,
    details text,
    checked_at timestamp with time zone DEFAULT now()
)""",
    """CREATE SEQUENCE host_sanity_checks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE host_sanity_checks_id_seq OWNED BY host_sanity_checks.id""",
    """CREATE TABLE imported_result_files (
    id integer NOT NULL,
    execution_session_id integer NOT NULL,
    test_plan_id integer NOT NULL,
    bundle_id character varying(64) NOT NULL,
    imported_by_id integer,
    filename character varying(255),
    file_sha256 character varying(64),
    results_count integer NOT NULL,
    sanity_checks_count integer NOT NULL,
    feedback_extracted boolean NOT NULL,
    parse_errors json,
    is_final boolean NOT NULL
)""",
    """CREATE SEQUENCE imported_result_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE imported_result_files_id_seq OWNED BY imported_result_files.id""",
    """CREATE TABLE test_execution_results (
    id integer NOT NULL,
    execution_session_id integer NOT NULL,
    entry_id integer NOT NULL,
    test_index integer NOT NULL,
    status character varying(20) NOT NULL,
    command_run text,
    raw_output text,
    findings_summary text,
    severity character varying(20),
    is_finding boolean NOT NULL,
    executed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    observed_ip character varying(45)
)""",
    """CREATE SEQUENCE test_execution_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE test_execution_results_id_seq OWNED BY test_execution_results.id""",
    """CREATE TABLE test_plan_entries (
    id integer NOT NULL,
    test_plan_id integer NOT NULL,
    host_id integer NOT NULL,
    priority character varying(20) NOT NULL,
    test_phase character varying(30) NOT NULL,
    proposed_tests json NOT NULL,
    rationale text NOT NULL,
    status character varying(20) NOT NULL,
    findings text,
    results_data json,
    notes text,
    assigned_to_id integer,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    name_id integer
)""",
    """CREATE SEQUENCE test_plan_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE test_plan_entries_id_seq OWNED BY test_plan_entries.id""",
    """CREATE TABLE test_plan_history (
    id integer NOT NULL,
    test_plan_id integer NOT NULL,
    entry_id integer,
    actor_type character varying(10) NOT NULL,
    actor_id integer NOT NULL,
    action character varying(30) NOT NULL,
    field_changed character varying(50),
    old_value text,
    new_value text,
    "timestamp" timestamp with time zone DEFAULT now()
)""",
    """CREATE SEQUENCE test_plan_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE test_plan_history_id_seq OWNED BY test_plan_history.id""",
    """CREATE TABLE test_plans (
    id integer NOT NULL,
    project_id integer NOT NULL,
    agent_id integer,
    created_by_user_id integer,
    version integer NOT NULL,
    title character varying(200) NOT NULL,
    description text,
    status character varying(20) NOT NULL,
    archive_reason text,
    filter_criteria json,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    generated_by_model character varying(100),
    generated_by_tool character varying(100),
    prompt_version character varying(20),
    source_kind character varying(30) DEFAULT 'unspecified'::character varying NOT NULL,
    source_host_ids json,
    source_plan_id integer,
    agent_session_id integer
)""",
    """CREATE SEQUENCE test_plans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE test_plans_id_seq OWNED BY test_plans.id""",
    """ALTER TABLE ONLY execution_sessions ALTER COLUMN id SET DEFAULT nextval('execution_sessions_id_seq'::regclass)""",
    """ALTER TABLE ONLY host_sanity_checks ALTER COLUMN id SET DEFAULT nextval('host_sanity_checks_id_seq'::regclass)""",
    """ALTER TABLE ONLY imported_result_files ALTER COLUMN id SET DEFAULT nextval('imported_result_files_id_seq'::regclass)""",
    """ALTER TABLE ONLY test_execution_results ALTER COLUMN id SET DEFAULT nextval('test_execution_results_id_seq'::regclass)""",
    """ALTER TABLE ONLY test_plan_entries ALTER COLUMN id SET DEFAULT nextval('test_plan_entries_id_seq'::regclass)""",
    """ALTER TABLE ONLY test_plan_history ALTER COLUMN id SET DEFAULT nextval('test_plan_history_id_seq'::regclass)""",
    """ALTER TABLE ONLY test_plans ALTER COLUMN id SET DEFAULT nextval('test_plans_id_seq'::regclass)""",
    """ALTER TABLE ONLY execution_sessions
    ADD CONSTRAINT execution_sessions_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY host_sanity_checks
    ADD CONSTRAINT host_sanity_checks_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY imported_result_files
    ADD CONSTRAINT imported_result_files_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY test_execution_results
    ADD CONSTRAINT test_execution_results_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY test_plan_entries
    ADD CONSTRAINT test_plan_entries_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY test_plan_history
    ADD CONSTRAINT test_plan_history_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY test_plans
    ADD CONSTRAINT test_plans_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY test_execution_results
    ADD CONSTRAINT uq_exec_result_session_entry_test UNIQUE (execution_session_id, entry_id, test_index)""",
    """ALTER TABLE ONLY test_plan_entries
    ADD CONSTRAINT uq_plan_host_name UNIQUE NULLS NOT DISTINCT (test_plan_id, host_id, name_id)""",
    """ALTER TABLE ONLY host_sanity_checks
    ADD CONSTRAINT uq_sanity_check_session_entry_method UNIQUE (execution_session_id, entry_id, method)""",
    """ALTER TABLE ONLY test_plans
    ADD CONSTRAINT uq_test_plan_project_version UNIQUE (project_id, version)""",
    """CREATE INDEX idx_entry_host_status ON test_plan_entries USING btree (host_id, status)""",
    """CREATE INDEX idx_entry_plan_status ON test_plan_entries USING btree (test_plan_id, status)""",
    """CREATE INDEX idx_exec_session_plan ON execution_sessions USING btree (test_plan_id)""",
    """CREATE INDEX idx_history_plan_time ON test_plan_history USING btree (test_plan_id, "timestamp")""",
    """CREATE INDEX idx_sanity_check_session ON host_sanity_checks USING btree (execution_session_id)""",
    """CREATE INDEX idx_test_plan_project_status ON test_plans USING btree (project_id, status)""",
    """CREATE INDEX idx_test_result_entry ON test_execution_results USING btree (entry_id)""",
    """CREATE INDEX ix_execution_sessions_agent_session_id ON execution_sessions USING btree (agent_session_id)""",
    """CREATE INDEX ix_execution_sessions_bundle_id ON execution_sessions USING btree (bundle_id)""",
    """CREATE INDEX ix_execution_sessions_id ON execution_sessions USING btree (id)""",
    """CREATE INDEX ix_execution_sessions_test_plan_id ON execution_sessions USING btree (test_plan_id)""",
    """CREATE INDEX ix_host_sanity_checks_execution_session_id ON host_sanity_checks USING btree (execution_session_id)""",
    """CREATE INDEX ix_host_sanity_checks_id ON host_sanity_checks USING btree (id)""",
    """CREATE INDEX ix_imported_result_files_bundle_id ON imported_result_files USING btree (bundle_id)""",
    """CREATE INDEX ix_imported_result_files_execution_session_id ON imported_result_files USING btree (execution_session_id)""",
    """CREATE INDEX ix_imported_result_files_id ON imported_result_files USING btree (id)""",
    """CREATE INDEX ix_test_execution_results_entry_id ON test_execution_results USING btree (entry_id)""",
    """CREATE INDEX ix_test_execution_results_execution_session_id ON test_execution_results USING btree (execution_session_id)""",
    """CREATE INDEX ix_test_execution_results_id ON test_execution_results USING btree (id)""",
    """CREATE INDEX ix_test_plan_entries_host_id ON test_plan_entries USING btree (host_id)""",
    """CREATE INDEX ix_test_plan_entries_id ON test_plan_entries USING btree (id)""",
    """CREATE INDEX ix_test_plan_entries_name_id ON test_plan_entries USING btree (name_id)""",
    """CREATE INDEX ix_test_plan_entries_test_plan_id ON test_plan_entries USING btree (test_plan_id)""",
    """CREATE INDEX ix_test_plan_history_id ON test_plan_history USING btree (id)""",
    """CREATE INDEX ix_test_plans_agent_id ON test_plans USING btree (agent_id)""",
    """CREATE INDEX ix_test_plans_agent_session_id ON test_plans USING btree (agent_session_id)""",
    """CREATE INDEX ix_test_plans_created_by_user_id ON test_plans USING btree (created_by_user_id)""",
    """CREATE INDEX ix_test_plans_id ON test_plans USING btree (id)""",
    """CREATE INDEX ix_test_plans_project_id ON test_plans USING btree (project_id)""",
    """CREATE UNIQUE INDEX uq_exec_session_plan_active ON execution_sessions USING btree (test_plan_id) WHERE ((status)::text = 'active'::text)""",
    """ALTER TABLE ONLY execution_sessions
    ADD CONSTRAINT execution_sessions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY execution_sessions
    ADD CONSTRAINT execution_sessions_started_by_id_fkey FOREIGN KEY (started_by_id) REFERENCES users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY execution_sessions
    ADD CONSTRAINT execution_sessions_test_plan_id_fkey FOREIGN KEY (test_plan_id) REFERENCES test_plans(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY execution_sessions
    ADD CONSTRAINT fk_execution_sessions_agent_session_id FOREIGN KEY (agent_session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY test_plans
    ADD CONSTRAINT fk_test_plans_agent_session_id FOREIGN KEY (agent_session_id) REFERENCES agent_sessions(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY test_plans
    ADD CONSTRAINT fk_test_plans_source_plan FOREIGN KEY (source_plan_id) REFERENCES test_plans(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY host_sanity_checks
    ADD CONSTRAINT host_sanity_checks_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES test_plan_entries(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY host_sanity_checks
    ADD CONSTRAINT host_sanity_checks_execution_session_id_fkey FOREIGN KEY (execution_session_id) REFERENCES execution_sessions(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY host_sanity_checks
    ADD CONSTRAINT host_sanity_checks_host_id_fkey FOREIGN KEY (host_id) REFERENCES hosts_v2(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY imported_result_files
    ADD CONSTRAINT imported_result_files_execution_session_id_fkey FOREIGN KEY (execution_session_id) REFERENCES execution_sessions(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY imported_result_files
    ADD CONSTRAINT imported_result_files_imported_by_id_fkey FOREIGN KEY (imported_by_id) REFERENCES users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY imported_result_files
    ADD CONSTRAINT imported_result_files_test_plan_id_fkey FOREIGN KEY (test_plan_id) REFERENCES test_plans(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY test_execution_results
    ADD CONSTRAINT test_execution_results_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES test_plan_entries(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY test_execution_results
    ADD CONSTRAINT test_execution_results_execution_session_id_fkey FOREIGN KEY (execution_session_id) REFERENCES execution_sessions(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY test_plan_entries
    ADD CONSTRAINT test_plan_entries_assigned_to_id_fkey FOREIGN KEY (assigned_to_id) REFERENCES users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY test_plan_entries
    ADD CONSTRAINT test_plan_entries_host_id_fkey FOREIGN KEY (host_id) REFERENCES hosts_v2(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY test_plan_entries
    ADD CONSTRAINT test_plan_entries_name_id_fkey FOREIGN KEY (name_id) REFERENCES dns_names(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY test_plan_entries
    ADD CONSTRAINT test_plan_entries_test_plan_id_fkey FOREIGN KEY (test_plan_id) REFERENCES test_plans(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY test_plan_history
    ADD CONSTRAINT test_plan_history_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES test_plan_entries(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY test_plan_history
    ADD CONSTRAINT test_plan_history_test_plan_id_fkey FOREIGN KEY (test_plan_id) REFERENCES test_plans(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY test_plans
    ADD CONSTRAINT test_plans_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY test_plans
    ADD CONSTRAINT test_plans_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY test_plans
    ADD CONSTRAINT test_plans_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE""",
)


def upgrade():
    db = op.get_bind()

    # Nothing may still point at a result the first revision did not convert.
    stranded = db.execute(sa.text("""
        SELECT 'finding ' || f.id FROM findings f
         WHERE f.exec_result_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM evidence_records e WHERE e.finding_id = f.id)
        UNION ALL
        SELECT 'dns record ' || d.id FROM dns_records d
         WHERE d.exec_result_id IS NOT NULL AND d.evidence_record_id IS NULL
        LIMIT 5
    """)).scalars().all()
    if stranded:
        raise RuntimeError(
            "Rows still depend on an execution result with no evidence record: "
            + ", ".join(stranded) + ". Re-run revision d7e1a9c4b602 before this one."
        )

    # A plan's notes become project notes; the CHECK is rebuilt below.
    op.execute("ALTER TABLE annotations DROP CONSTRAINT IF EXISTS ck_annotations_exactly_one_target")
    op.execute("""
        UPDATE annotations a SET project_id = p.project_id, plan_id = NULL
          FROM test_plans p WHERE a.plan_id = p.id
    """)
    op.drop_index("ix_annotations_plan_id", table_name="annotations")
    op.drop_constraint("fk_annotations_plan_id", "annotations", type_="foreignkey")
    op.drop_column("annotations", "plan_id")
    op.execute(
        "ALTER TABLE annotations ADD CONSTRAINT ck_annotations_exactly_one_target "
        "CHECK (num_nonnulls(host_id, port_id, scan_id, scope_id, project_id, finding_id) = 1)"
    )

    # The pages these linked to are gone; the notification itself stays.
    op.execute("""
        UPDATE notifications SET source_type = NULL, source_id = NULL
         WHERE source_type IN ('test_plan', 'execution_session')
    """)

    op.drop_index("uq_dns_record_result_observation", table_name="dns_records")
    op.drop_index("ix_dns_records_exec_result_id", table_name="dns_records")
    op.drop_constraint("dns_records_exec_result_id_fkey", "dns_records", type_="foreignkey")
    op.drop_column("dns_records", "exec_result_id")

    op.drop_index("ix_findings_exec_result_id", table_name="findings")
    op.drop_constraint("findings_exec_result_id_fkey", "findings", type_="foreignkey")
    op.drop_column("findings", "exec_result_id")

    op.drop_constraint("agent_feedback_test_plan_id_fkey", "agent_feedback", type_="foreignkey")
    op.drop_constraint("agent_feedback_execution_session_id_fkey", "agent_feedback", type_="foreignkey")
    op.drop_column("agent_feedback", "test_plan_id")
    op.drop_column("agent_feedback", "execution_session_id")

    for index in (
        "idx_agent_api_call_plan_created", "idx_agent_api_call_exec_created",
        "ix_agent_api_calls_test_plan_id", "ix_agent_api_calls_execution_session_id",
    ):
        op.drop_index(index, table_name="agent_api_calls")
    op.drop_constraint("agent_api_calls_test_plan_id_fkey", "agent_api_calls", type_="foreignkey")
    op.drop_constraint("agent_api_calls_execution_session_id_fkey", "agent_api_calls", type_="foreignkey")
    op.drop_column("agent_api_calls", "test_plan_id")
    op.drop_column("agent_api_calls", "execution_session_id")

    for table in _OLD_TABLES:
        op.drop_table(table)


def downgrade():
    for statement in _RECREATE:
        op.execute(statement)

    op.add_column("agent_api_calls", sa.Column("test_plan_id", sa.Integer(), nullable=True))
    op.add_column("agent_api_calls", sa.Column("execution_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "agent_api_calls_test_plan_id_fkey", "agent_api_calls", "test_plans",
        ["test_plan_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "agent_api_calls_execution_session_id_fkey", "agent_api_calls", "execution_sessions",
        ["execution_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_agent_api_calls_test_plan_id", "agent_api_calls", ["test_plan_id"])
    op.create_index("ix_agent_api_calls_execution_session_id", "agent_api_calls", ["execution_session_id"])
    op.create_index("idx_agent_api_call_plan_created", "agent_api_calls", ["test_plan_id", "created_at"])
    op.create_index("idx_agent_api_call_exec_created", "agent_api_calls", ["execution_session_id", "created_at"])

    op.add_column("agent_feedback", sa.Column("test_plan_id", sa.Integer(), nullable=True))
    op.add_column("agent_feedback", sa.Column("execution_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "agent_feedback_test_plan_id_fkey", "agent_feedback", "test_plans",
        ["test_plan_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "agent_feedback_execution_session_id_fkey", "agent_feedback", "execution_sessions",
        ["execution_session_id"], ["id"], ondelete="SET NULL",
    )

    op.add_column("findings", sa.Column("exec_result_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "findings_exec_result_id_fkey", "findings", "test_execution_results",
        ["exec_result_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_findings_exec_result_id", "findings", ["exec_result_id"])

    op.add_column("dns_records", sa.Column("exec_result_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "dns_records_exec_result_id_fkey", "dns_records", "test_execution_results",
        ["exec_result_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_dns_records_exec_result_id", "dns_records", ["exec_result_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_dns_record_result_observation ON dns_records "
        "(name_id, record_type, value, exec_result_id) WHERE exec_result_id IS NOT NULL"
    )

    # Notes that were moved to their project stay there: which plan they
    # belonged to is not recorded anywhere but the backup.
    op.execute("ALTER TABLE annotations DROP CONSTRAINT IF EXISTS ck_annotations_exactly_one_target")
    op.add_column("annotations", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_annotations_plan_id", "annotations", "test_plans",
        ["plan_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_annotations_plan_id", "annotations", ["plan_id"])
    op.execute(
        "ALTER TABLE annotations ADD CONSTRAINT ck_annotations_exactly_one_target "
        "CHECK (num_nonnulls(host_id, port_id, scan_id, scope_id, plan_id, project_id, finding_id) = 1)"
    )
