#!/usr/bin/env bash
# The database container's health probe — and the ONE place the application's
# database role is made.  Mounted by docker-compose.yml; `restore-db.sh` and
# the migration round trip also run it.  Not run by hand.
#
# Why it exists.  The official image makes POSTGRES_USER the cluster's
# bootstrap superuser, and the application used to connect as it: one SQL
# injection or one compromised dependency in an application container could
# then run programs on the database host (COPY … TO PROGRAM), read its files
# (pg_read_file — the site's TLS key is in this container) and rewrite its
# configuration.  PostgreSQL will not take SUPERUSER away from a bootstrap
# role, so the application gets a role of its own:
#
#   * POSTGRES_APP_USER (default bluestick_app): signs in with
#     POSTGRES_PASSWORD, is not a superuser, and owns the application database
#     and everything in its public schema — enough to run the application and
#     every migration, nothing more;
#   * POSTGRES_USER stays what the scripts use from INSIDE this container
#     (backup, restore, diagnostics: the local socket asks for no password).
#     Its password is removed, so it cannot sign in over the network at all.
#
# Why the health probe.  The application services wait for this container to
# be healthy, so "healthy" has to mean "the application can sign in and owns
# its tables" — on a new installation, on one upgraded from the superuser
# days, after a restore of an older backup, and after POSTGRES_PASSWORD was
# changed in .env.  A step in deploy.sh would cover one of those four.  Each
# probe asks one question (is anything out of place?) and signs in once as
# the application; it changes something only when the answer is yes.
#
# Exit 0: the database accepts connections and the role is as described.
# Exit 1: not yet (still starting) or not possible — the reason is in the
# container's log.
set -uo pipefail

SUPER="${POSTGRES_USER:-nmapuser}"
DB="${POSTGRES_DB:-networkMapper}"
APP="${POSTGRES_APP_USER:-bluestick_app}"
APP_PASSWORD="${POSTGRES_PASSWORD:-nmappass}"
LOCK_TIMEOUT="${APP_ROLE_LOCK_TIMEOUT:-20s}"

# Said twice: on our own stderr (what `docker inspect` shows for a probe, and
# what a script that runs this sees), and in the SERVER's log — a probe cannot
# write to the container's output, but the server can be asked to (a LOG line
# reaches `docker compose logs db` whatever the client's message level).
say() {
    echo "[db-role] $*" >&2
    psql -X -q -U "$SUPER" -d postgres -v line="[db-role] $*" >/dev/null 2>&1 <<'SQL' || true
SELECT set_config('app_role.line', :'line', false) \g /dev/null
DO $$ BEGIN RAISE LOG '%', current_setting('app_role.line'); END $$;
SQL
}

# The real server listens on TCP; the one initdb runs to build a new volume
# listens on the socket only, and is about to be stopped.
pg_isready -q -h 127.0.0.1 -U "$SUPER" -d "$DB" || exit 1

if [[ "$APP" == "$SUPER" ]]; then
    say "POSTGRES_APP_USER and POSTGRES_USER are both '$APP'. The application must not"
    say "connect as the database's superuser: give POSTGRES_APP_USER another name in .env."
    exit 1
fi

# The local socket: no password is asked for inside this container.
super_sql() {
    psql -X -q -v ON_ERROR_STOP=1 -U "$SUPER" -d "$DB" -v app="$APP" -v db="$DB" -v super="$SUPER" "$@"
}

# What is out of place, as one number.  Extension members (pg_trgm's
# functions and operator classes) stay the superuser's and are not counted.
MISPLACED_SQL="
WITH app AS (SELECT oid FROM pg_roles WHERE rolname = :'app'),
member AS (SELECT classid, objid FROM pg_depend WHERE deptype = 'e')
SELECT
    (SELECT count(*) FROM pg_roles r WHERE r.rolname = :'app'
        AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreaterole
        AND NOT r.rolreplication AND NOT r.rolbypassrls) <> 1
 OR (SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname = :'super')
 OR (SELECT d.datdba IS DISTINCT FROM (SELECT oid FROM app) FROM pg_database d WHERE d.datname = :'db')
 OR (SELECT n.nspowner IS DISTINCT FROM (SELECT oid FROM app) FROM pg_namespace n WHERE n.nspname = 'public')
 OR EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace
        AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
        AND c.relowner IS DISTINCT FROM (SELECT oid FROM app)
        AND NOT EXISTS (SELECT 1 FROM member m WHERE m.classid = 'pg_class'::regclass AND m.objid = c.oid))
 OR EXISTS (SELECT 1 FROM pg_type t WHERE t.typnamespace = 'public'::regnamespace
        AND t.typtype IN ('e', 'd', 'r')
        AND t.typowner IS DISTINCT FROM (SELECT oid FROM app)
        AND NOT EXISTS (SELECT 1 FROM member m WHERE m.classid = 'pg_type'::regclass AND m.objid = t.oid))
 OR EXISTS (SELECT 1 FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace
        AND p.proowner IS DISTINCT FROM (SELECT oid FROM app)
        AND NOT EXISTS (SELECT 1 FROM member m WHERE m.classid = 'pg_proc'::regclass AND m.objid = p.oid))
"

# Signing in the way the application does: over the network (this container's
# own address — loopback would be let in without a password), with the
# password from .env.
app_signs_in() {
    local address
    address="$(hostname -i 2>/dev/null | awk '{print $1}')"
    [[ -n "$address" ]] || return 1
    PGPASSWORD="$APP_PASSWORD" PGCONNECT_TIMEOUT=3 \
        psql -X -q -h "$address" -U "$APP" -d "$DB" -tAc "SELECT 1" >/dev/null 2>&1
}

misplaced="$(super_sql -tA <<< "$MISPLACED_SQL" 2>/dev/null)" || exit 1
if [[ "$misplaced" == "f" ]] && app_signs_in; then
    exit 0
fi

# A restore in progress makes its tables as the superuser, one statement at a
# time; moving them under it could deadlock with the statement that needs two
# of them.  `restore-db.sh` runs this script itself when pg_restore has ended
# (APP_ROLE_AFTER_RESTORE=1), before it starts the application.
if [[ -z "${APP_ROLE_AFTER_RESTORE:-}" ]]; then
    # On stdin: psql fills in :'db' there, and not in a -c command.
    restoring="$(super_sql -tA 2>/dev/null <<< "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'pg_restore' AND datname = :'db'")" || exit 1
    [[ "$restoring" == "0" ]] || exit 0
fi

# ── Something is out of place: put it right, in one transaction ─────────────
# The password travels as a psql variable and is quoted by the server
# (format %L); the three settings keep the statement that carries it out of
# the server log and out of pg_stat_statements.
if ! error="$(super_sql -v password="$APP_PASSWORD" -v lock_timeout="$LOCK_TIMEOUT" 2>&1 <<'SQL'
SET log_statement = 'none';
SET log_min_duration_statement = -1;
SET log_min_error_statement = 'panic';
SET pg_stat_statements.track_utility = off;
BEGIN;
SELECT set_config('lock_timeout', :'lock_timeout', true) \g /dev/null
SELECT format('CREATE ROLE %I LOGIN', :'app')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app') \gexec
SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
              :'app', :'password') \gexec
SELECT format('ALTER ROLE %I PASSWORD NULL', :'super') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db', :'app') \gexec
SELECT format('ALTER SCHEMA public OWNER TO %I', :'app') \gexec
SELECT set_config('app_role.name', :'app', true) \g /dev/null
DO $$
DECLARE
    app name := current_setting('app_role.name');
    app_oid oid := (SELECT oid FROM pg_roles WHERE rolname = app);
    item record;
BEGIN
    -- Tables first: an index, a column's own sequence and a row type follow
    -- their table.  Then whatever is still someone else's.
    FOR item IN
        SELECT c.oid::regclass AS name FROM pg_class c
         WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'f')
           AND c.relowner <> app_oid
           AND NOT EXISTS (SELECT 1 FROM pg_depend m WHERE m.deptype = 'e'
                            AND m.classid = 'pg_class'::regclass AND m.objid = c.oid)
         ORDER BY c.oid
    LOOP
        EXECUTE format('ALTER TABLE %s OWNER TO %I', item.name, app);
    END LOOP;
    FOR item IN
        SELECT c.oid::regclass AS name FROM pg_class c
         WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('v', 'm', 'S')
           AND c.relowner <> app_oid
           AND NOT EXISTS (SELECT 1 FROM pg_depend m WHERE m.deptype = 'e'
                            AND m.classid = 'pg_class'::regclass AND m.objid = c.oid)
         ORDER BY c.oid
    LOOP
        EXECUTE format('ALTER TABLE %s OWNER TO %I', item.name, app);
    END LOOP;
    FOR item IN
        SELECT t.oid::regtype AS name FROM pg_type t
         WHERE t.typnamespace = 'public'::regnamespace AND t.typtype IN ('e', 'd', 'r')
           AND t.typowner <> app_oid
           AND NOT EXISTS (SELECT 1 FROM pg_depend m WHERE m.deptype = 'e'
                            AND m.classid = 'pg_type'::regclass AND m.objid = t.oid)
         ORDER BY t.oid
    LOOP
        EXECUTE format('ALTER TYPE %s OWNER TO %I', item.name, app);
    END LOOP;
    FOR item IN
        SELECT p.oid::regprocedure AS name FROM pg_proc p
         WHERE p.pronamespace = 'public'::regnamespace AND p.proowner <> app_oid
           AND NOT EXISTS (SELECT 1 FROM pg_depend m WHERE m.deptype = 'e'
                            AND m.classid = 'pg_proc'::regclass AND m.objid = p.oid)
         ORDER BY p.oid
    LOOP
        EXECUTE format('ALTER ROUTINE %s OWNER TO %I', item.name, app);
    END LOOP;
END
$$;
COMMIT;
SQL
)"; then
    say "The application's database role '$APP' could not be set up; nothing was changed."
    say "${error//$'\n'/ | }"
    say "If this names a lock: an import or a report is holding a table. Stop the application"
    say "services (docker compose stop backend worker report-worker); this is tried again every few seconds."
    exit 1
fi

if app_signs_in; then
    say "The application connects as '$APP' (not a superuser), which owns database '$DB'."
    say "'$SUPER' is for the scripts, from inside this container only: it has no password."
    exit 0
fi
say "Role '$APP' is set up, but signing in as it with POSTGRES_PASSWORD was refused."
exit 1
