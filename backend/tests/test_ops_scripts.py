"""The operations scripts, run for real against a stand-in ``docker``.

``scripts/deploy.sh``, ``backup-db.sh`` and ``restore-db.sh`` had no tests:
their failure paths — the ones an operator meets on the worst day — were
checked by reading.  Here the REAL scripts run in a temporary project tree
with ``tests/ops_fake_docker.py`` first on PATH (it keeps a pretend host in a
JSON file and logs every call), so what is asserted is what the script did:
which commands, in which order, what it printed, how it exited.

Each test names the review finding it pins (branch review 2026-10-01, "Ops /
deploy / docs": B1, S1–S7).  No database, no Docker.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tarfile

import pytest

HERE = pathlib.Path(__file__).resolve()


def _scripts_dir() -> pathlib.Path | None:
    for candidate in (HERE.parents[1] / "scripts", HERE.parents[2] / "scripts"):
        if (candidate / "deploy.sh").is_file() and (candidate / "stack-lib.sh").is_file():
            return candidate
    return None


SCRIPTS = _scripts_dir()
pytestmark = [
    pytest.mark.skipif(SCRIPTS is None, reason="scripts/ is not mounted here"),
    pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed here"),
]

OLD_REV = "aaaa1111old"
NEW_REV = "bbbb2222new"
COMPLETE_DUMP = b"PGDMP fake table of contents\nrow data\nEND"
SERVICES = ("backend", "worker", "report-worker", "frontend")


class Project:
    """A temporary deployment tree with the real scripts and a fake docker."""

    def __init__(self, tmp_path: pathlib.Path):
        self.root = tmp_path / "proj"
        self.fake = tmp_path / "fake"
        self.bin = tmp_path / "bin"
        self.backups = tmp_path / "proj-db-backups"
        for d in (self.root, self.fake, self.bin, self.backups, self.root / "uploads",
                  self.root / "ssl" / "certs", self.root / "backend", self.root / "frontend"):
            d.mkdir(parents=True)
        shutil.copytree(SCRIPTS, self.root / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for script in (self.root / "scripts").glob("*.sh"):
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (self.root / ".env").write_text(
            "HOST_IP=127.0.0.1\nSECRET_KEY=a-real-secret\nPOSTGRES_USER=nmapuser\nPOSTGRES_DB=networkMapper\n"
        )
        (self.root / "ssl" / "certs" / "networkmapper.crt").write_text("cert")
        (self.root / "ssl" / "certs" / "networkmapper.key").write_text("key")
        # As a careful operator keeps them: deploy.sh's ownership check finds
        # nothing, so it asks nothing (its own tests loosen these).
        (self.root / ".env").chmod(0o600)
        (self.root / "ssl" / "certs" / "networkmapper.key").chmod(0o600)
        (self.root / "backend" / "Dockerfile").write_text("ARG PYTHON_IMAGE=python:3.11.16-slim-trixie\n")
        (self.root / "frontend" / "Dockerfile").write_text("ARG NODE_IMAGE=node:22.23.2-alpine\n")

        docker = self.bin / "docker"
        docker.write_text(f'#!/bin/sh\nexec python3 "{HERE.parent / "ops_fake_docker.py"}" "$@"\n')
        docker.chmod(0o755)
        if shutil.which("openssl") is None:   # deploy.sh's preflight only looks for it
            fake_openssl = self.bin / "openssl"
            fake_openssl.write_text("#!/bin/sh\nexit 0\n")
            fake_openssl.chmod(0o755)

        self.state = {
            "images": {
                "postgres:16.13": "sha256:pg",
                "python:3.11.16-slim-trixie": "sha256:py",
                "node:22.23.2-alpine": "sha256:node",
                **{f"proj-{s}:latest": f"sha256:new-{s}" for s in SERVICES},
                **{f"bluestick-rollback-{s}:previous": f"sha256:old-{s}" for s in SERVICES},
            },
            "service_images": {"db": "postgres:16.13", **{s: f"proj-{s}" for s in SERVICES}},
            "containers": {
                "db": {"id": "cid-db", "image": "sha256:pg", "status": "running", "restarts": 0},
                **{s: {"id": f"cid-{s}", "image": f"sha256:new-{s}", "status": "running", "restarts": 0}
                   for s in SERVICES},
            },
            "healthy_images": ["sha256:old-backend", "sha256:new-backend", "sha256:built"],
            "alembic_revision": OLD_REV,
            "restored_revision": OLD_REV,
            "fail": [],
        }

    # -- the pretend host ---------------------------------------------------
    def write_state(self) -> None:
        (self.fake / "state.json").write_text(json.dumps(self.state))

    def read_state(self) -> dict:
        return json.loads((self.fake / "state.json").read_text())

    def calls(self) -> list[str]:
        log = self.fake / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    # -- files --------------------------------------------------------------
    def make_dump(self, name: str = "nm-pgdump-20260101-000000.dump", body: bytes = COMPLETE_DUMP,
                  revision: str = OLD_REV, meta: bool = True, checksum: bool = True) -> pathlib.Path:
        dump = self.backups / name
        dump.write_bytes(body)
        if meta:
            lines = ["type=pgdump", f"alembic_revision={revision}", "key_fingerprint=unknown"]
            if checksum:
                lines += [f"bytes={len(body)}", f"sha256={hashlib.sha256(body).hexdigest()}"]
            (self.backups / (name + ".meta")).write_text("\n".join(lines) + "\n")
        return dump

    def rollback_state(self, dump: pathlib.Path | str | None, revision: str | None = OLD_REV,
                       marker: str | None = None) -> None:
        lines = [f"{s}|proj-{s}:latest" for s in SERVICES]
        if dump is not None:
            lines.append(f"PREDEPLOY_DB_DUMP|{dump}")
        if revision is not None:
            lines.append(f"PREDEPLOY_ALEMBIC_REVISION|{revision}")
        if marker is not None:
            lines.append(f"DEPLOY_IN_PROGRESS|{marker}")
        (self.root / ".deploy-rollback-state").write_text("\n".join(lines) + "\n")

    def state_file(self) -> str:
        path = self.root / ".deploy-rollback-state"
        return path.read_text() if path.exists() else ""

    # -- running a script ---------------------------------------------------
    def run(self, script: str, *args: str, stdin: str = "", env: dict | None = None,
            cwd: pathlib.Path | None = None):
        self.write_state()
        full_env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DOCKER_DIR": str(self.fake),
            "STACK_WAIT_INTERVAL": "0",
            "DEPLOY_HEALTH_TIMEOUT": "10",
            "RESTORE_HEALTH_TIMEOUT": "10",
            "MIN_FREE_GB": "0",
            "GIT_COMMIT": "test",
            **(env or {}),
        }
        full_env.pop("BACKUP_DIR", None)
        return subprocess.run(
            ["bash", str(self.root / "scripts" / script), *args],
            input=stdin, capture_output=True, text=True, env=full_env, cwd=cwd or self.root, timeout=120,
        )


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path)


def _index(calls: list[str], needle: str) -> int:
    for i, call in enumerate(calls):
        if needle in call:
            return i
    return -1


def _output(result) -> str:
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# B1 — option 7 across a migration
# ---------------------------------------------------------------------------

def test_rollback_restores_the_database_before_it_starts_the_previous_build(project):
    """The failed deploy migrated the schema.  The previous backend cannot
    start on it, so the restore has to come BEFORE any `up` of the backend —
    it used to come after, and was never reached."""
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV, marker="2026-01-01T00:00:00|sha256:new-backend")
    project.state["alembic_revision"] = NEW_REV

    result = project.run("deploy.sh", stdin="7\n")   # end of input = the default: restore

    out = _output(result)
    assert result.returncode == 0, out
    calls = project.calls()
    retag = _index(calls, "tag bluestick-rollback-backend:previous proj-backend:latest")
    stop = _index(calls, "compose stop backend worker report-worker frontend")
    restore = _index(calls, "pg_restore -U nmapuser -d networkMapper")
    first_backend_up = next(i for i, c in enumerate(calls)
                            if c.startswith("compose up -d") and "backend" in c)
    assert -1 < retag < stop < restore < first_backend_up, calls
    assert "compose up -d --no-build db backend" in calls[first_backend_up]
    assert _index(calls, "compose build") == -1
    assert f"is at schema revision {NEW_REV}" in out and OLD_REV in out
    assert "instance state: RUNNING" in out
    assert project.read_state()["alembic_revision"] == OLD_REV
    assert project.read_state()["containers"]["backend"]["image"] == "sha256:old-backend"
    assert "DEPLOY_IN_PROGRESS" not in project.state_file()


def test_rollback_starts_workers_only_after_the_backend_is_healthy(project):
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV)

    result = project.run("deploy.sh", stdin="7\n")

    assert result.returncode == 0, _output(result)
    ups = [c for c in project.calls() if c.startswith("compose up -d")]
    assert ups == [
        "compose up -d --no-build db",
        "compose up -d --no-build db backend",
        "compose up -d --no-build worker report-worker",
        "compose up -d --no-build",
    ]


def test_rollback_with_an_unchanged_schema_restores_nothing(project):
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV)

    result = project.run("deploy.sh", stdin="7\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert "No database restore is needed" in out
    assert _index(project.calls(), "pg_restore") == -1
    assert "instance state: RUNNING" in out


def test_rollback_refuses_to_start_the_previous_build_on_a_newer_schema(project):
    """Declining the restore does not start anything unless the operator
    types the explicit override."""
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV)
    project.state["alembic_revision"] = NEW_REV

    result = project.run("deploy.sh", stdin="7\nn\n")

    out = _output(result)
    assert result.returncode == 1
    assert "instance state: STOPPED" in out
    assert f'./scripts/restore-db.sh "{dump}"' in out
    assert not any(c.startswith("compose up -d") and "backend" in c for c in project.calls())
    assert _index(project.calls(), "pg_restore -U") == -1


def test_rollback_starts_on_a_newer_schema_only_when_told_to(project):
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV)
    project.state["alembic_revision"] = NEW_REV

    result = project.run("deploy.sh", stdin="7\nn\nSTART ANYWAY\n")

    assert result.returncode == 0, _output(result)
    assert "compose up -d --no-build db backend" in project.calls()
    assert _index(project.calls(), "pg_restore -U") == -1


def test_rollback_reports_a_backend_that_does_not_stay_up(project):
    """`up` failing and the backend crash-looping used to end the script
    silently under `set -e`; now it ends with the state and the next step."""
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV)
    project.state["fail"] = [{"match": r"compose up -d --no-build db backend", "rc": 1}]
    project.state["containers"]["backend"]["status"] = "exited"
    project.state["healthy_images"] = []

    result = project.run("deploy.sh", stdin="7\n")

    out = _output(result)
    assert result.returncode == 1
    assert "instance state: NOT RUNNING" in out
    assert "logs backend" in out
    assert "compose up -d --no-build worker report-worker" not in project.calls()


def test_rollback_says_what_to_do_when_the_restore_fails(project):
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV)
    project.state["alembic_revision"] = NEW_REV
    project.state["fail"] = [{"match": r"pg_restore -U nmapuser -d networkMapper", "rc": 1}]

    result = project.run("deploy.sh", stdin="7\n")

    out = _output(result)
    assert result.returncode == 1
    assert "pg_restore FAILED" in out
    assert "instance state: STOPPED" in out
    assert not any(c.startswith("compose up -d") and "backend" in c for c in project.calls())


# ---------------------------------------------------------------------------
# S5 — a re-run after a failed deploy keeps the good rollback point
# ---------------------------------------------------------------------------

def test_redeploy_after_a_failed_deploy_keeps_the_rollback_point(project):
    dump = project.make_dump()
    # The failed deploy built sha256:failed and migrated; the backend is down.
    project.rollback_state(dump, OLD_REV, marker="2026-01-01T00:00:00|sha256:failed")
    project.state["alembic_revision"] = NEW_REV
    project.state["containers"]["backend"]["image"] = "sha256:failed"
    project.state["healthy_images"] = ["sha256:built"]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 0, out
    calls = project.calls()
    assert not any(c.startswith("tag ") and "bluestick-rollback-" in c for c in calls), \
        "the failed build was snapshotted over the good one"
    assert _index(calls, "pg_dump") == -1, "a dump of the migrated database replaced the pre-deploy one"
    assert "KEEPING its rollback images" in out and "KEEPING the recorded pre-deploy database backup" in out
    assert project.read_state()["images"]["bluestick-rollback-backend:previous"] == "sha256:old-backend"
    state_file = project.state_file()
    assert f"PREDEPLOY_DB_DUMP|{dump}" in state_file
    assert "DEPLOY_IN_PROGRESS" not in state_file          # this deploy ended healthy
    assert "Deployment complete" in out


def test_deploy_leaves_the_in_progress_marker_when_the_backend_does_not_come_up(project):
    project.state["healthy_images"] = []
    project.state["backend_status_after_up"] = {"status": "exited"}

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "option 7" in out
    state_file = project.state_file()
    assert "DEPLOY_IN_PROGRESS|" in state_file and "sha256:built" in state_file
    assert "PREDEPLOY_ALEMBIC_REVISION|" + OLD_REV in state_file


def test_deploy_stops_waiting_when_the_migration_has_failed(project):
    """The failure line in the log ends the wait and names the report script,
    whatever the container's state says.  (A failed migration once left the
    container "running" with no restarts — uvicorn respawned its workers, each
    migrating at import and failing again — and the wait went on to its
    timeout, 2026-10-02.  The migration step now ends the container, but the
    line is still read first: it is there before the restart limit is.)"""
    project.state["healthy_images"] = []
    project.state["backend_logs"] = (
        "backend-1  | INFO  [alembic.runtime.migration] Running upgrade a -> b, one finding per issue\n"
        "backend-1  | CRITICAL: DATABASE MIGRATION FAILED — NOTHING WAS CHANGED: the upgrade was rolled back\n"
    )

    result = project.run("deploy.sh", stdin="1\n", env={"DEPLOY_HEALTH_TIMEOUT": "600"})

    out = _output(result)
    assert result.returncode == 1
    assert "The schema migration FAILED" in out and "NOTHING WAS CHANGED" in out
    assert "migration-failure-report.sh" in out and "option 7" in out
    assert "DEPLOY_IN_PROGRESS|" in project.state_file()


def test_a_deploy_finished_by_hand_gets_a_new_rollback_point(project):
    """The wait timed out, the operator ran `up -d` when the backend came up:
    the marker is stale, and the next deploy snapshots what runs now."""
    dump = project.make_dump()
    project.rollback_state(dump, OLD_REV, marker="2026-01-01T00:00:00|sha256:new-backend")

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert "finished by hand" in out
    assert any(c.startswith("tag ") and "bluestick-rollback-backend:previous" in c for c in project.calls())


# ---------------------------------------------------------------------------
# S2 / S4 — option 1 start order, and "healthy" only for the new build
# ---------------------------------------------------------------------------

def test_deploy_starts_the_backend_alone_then_the_workers_then_everything(project):
    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 0, out
    calls = project.calls()
    ups = [c for c in calls if c.startswith("compose up -d")]
    assert ups == ["compose up -d db backend", "compose up -d worker report-worker", "compose up -d"]
    assert _index(calls, "compose stop worker report-worker") < _index(calls, "compose up -d db backend")
    assert "Deployment complete" in out
    meta = next(project.backups.glob("nm-pgdump-*.dump.meta")).read_text()
    assert "sha256=" in meta and "bytes=" in meta


def test_deploy_does_not_call_the_old_build_healthy_when_up_recreated_nothing(project):
    project.state["fail"] = [{"match": r"compose up -d db backend", "rc": 1}]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "NOT recreated" in out and "PREVIOUS build" in out
    assert "Backend is healthy" not in out
    assert "Deployment complete" not in out
    assert "compose start worker report-worker" in project.calls()
    assert "DEPLOY_IN_PROGRESS|" in project.state_file()


def test_deploy_reports_a_failed_final_up_instead_of_exiting_silently(project):
    project.state["fail"] = [{"match": r"compose up -d$", "rc": 1}]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "The deploy is NOT complete" in out
    assert "Deployment complete" not in out


# ---------------------------------------------------------------------------
# Option 1 and the OLD database (owner report 2026-10-22; review 2026-10-22,
# B2-1): a db a reboot left down is started, alone; the rows a migration
# would refuse stop the deploy before anything is backed up or built.
# ---------------------------------------------------------------------------

def test_deploy_starts_a_stopped_database_alone_and_takes_a_dump(project):
    """Docker started the containers late after a reboot: option 1 used to
    refuse at the pre-deploy backup ("Start the stack ($DC up -d db)")."""
    project.state["containers"]["db"]["status"] = "exited"

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 0, out
    calls = project.calls()
    assert "starting the database ALONE" in out
    start_db = calls.index("compose up -d db")
    assert start_db < _index(calls, "pg_dump") < _index(calls, "compose build")
    assert list(project.backups.glob("nm-pgdump-*.dump")) and not list(project.backups.glob("nm-pgdata-*"))
    assert "PREDEPLOY_DB_DUMP|" in project.state_file()
    assert "Deployment complete" in out


def test_deploy_refuses_with_the_reason_when_the_database_will_not_start(project):
    project.state["containers"]["db"]["status"] = "exited"
    project.state["fail"] = [{"match": r"compose up -d db$", "rc": 1}]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "Starting the database failed" in out and "scripted failure" in out
    calls = project.calls()
    assert _index(calls, "pg_dump") == -1 and _index(calls, "compose build") == -1


def test_deploy_stops_before_the_backup_and_build_on_a_row_a_migration_refuses(project):
    project.state["refusals"] = {"f4c8b2d0e6a1": 3}

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "REFUSED — f4c8b2d0e6a1 API keys not bound to an agent session: 3" in out
    assert "Nothing was changed" in out and "DEPLOY_SKIP_REFUSAL_CHECK=1" in out
    calls = project.calls()
    assert _index(calls, "pg_dump") == -1 and _index(calls, "compose build") == -1
    assert "DEPLOY_IN_PROGRESS" not in project.state_file()


def test_deploy_stops_when_a_refusal_check_cannot_run_unless_told_to_skip(project):
    project.state["refusal_errors"] = ["b6e0d4f2a8c3"]

    refused = project.run("deploy.sh", stdin="1\n")

    out = _output(refused)
    assert refused.returncode == 1
    assert "1 of the 6 checks could not run" in out and "b6e0d4f2a8c3" in out
    assert _index(project.calls(), "compose build") == -1

    forced = project.run("deploy.sh", stdin="1\n", env={"DEPLOY_SKIP_REFUSAL_CHECK": "1"})

    assert forced.returncode == 0, _output(forced)
    assert "NOT counting the rows" in _output(forced)


# Option 2 (first-time setup) — the same staged start, never a bare `up --build -d`

def _first_time(project, **state):
    (project.root / ".env.example").write_text("HOST_IP=localhost\nSECRET_KEY=change-me\n")
    project.state.update(state)
    # "2" = first-time setup, "1" = the first address offered (127.0.0.1).
    return project.run("deploy.sh", stdin="2\n1\n")


def test_first_time_setup_builds_then_starts_the_backend_alone_then_the_rest(project):
    result = _first_time(project)

    out = _output(result)
    assert result.returncode == 0, out
    calls = project.calls()
    assert not any("up --build" in c for c in calls), calls
    ups = [c for c in calls if c.startswith("compose up")]
    assert ups == ["compose up -d db backend", "compose up -d worker report-worker", "compose up -d"]
    assert -1 < _index(calls, "compose build") < _index(calls, "compose up -d db backend")
    assert _index(calls, "compose stop worker report-worker") < _index(calls, "compose up -d db backend")
    assert "Backend is healthy" in out and "First-time setup complete" in out


def test_first_time_setup_says_so_when_the_build_fails(project):
    result = _first_time(project, fail=[{"match": r"compose build", "rc": 1}])

    out = _output(result)
    assert result.returncode == 1
    assert "The image build failed. Nothing was started" in out
    assert "First-time setup complete" not in out
    assert not any(c.startswith("compose up") for c in project.calls())


def test_first_time_setup_is_not_complete_when_the_backend_does_not_stay_up(project):
    """One `up --build -d` and a ten-second sleep printed "setup complete"
    over a backend that had exited — or ended the script silently."""
    result = _first_time(project, healthy_images=[], backend_status_after_up={"status": "exited"})

    out = _output(result)
    assert result.returncode == 1
    assert "first-time setup is NOT complete" in out and "logs backend" in out
    assert "First-time setup complete" not in out
    assert "compose up -d worker report-worker" not in project.calls()


def test_first_time_setup_reports_a_failed_final_up(project):
    result = _first_time(project, fail=[{"match": r"compose up -d$", "rc": 1}])

    out = _output(result)
    assert result.returncode == 1
    assert "First-time setup is NOT complete" in out and "First-time setup complete" not in out


def test_deploy_stops_before_building_when_the_database_image_cannot_be_had(project):
    # The running database is an older release; the newly pinned one is absent.
    del project.state["images"]["postgres:16.13"]
    project.state["images"]["postgres:16"] = "sha256:pg"
    project.state["fail"] = [{"match": r"compose pull db", "rc": 1}]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "postgres:16.13" in out and "could not be pulled" in out
    assert _index(project.calls(), "compose build") == -1
    assert not any(c.startswith("compose up") for c in project.calls())


def test_deploy_warns_before_the_build_about_a_base_image_this_host_lacks(project):
    del project.state["images"]["python:3.11.16-slim-trixie"]
    project.state["fail"] = [{"match": r"manifest inspect", "rc": 1}]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert "python:3.11.16-slim-trixie" in out and "OFFLINE" in out
    assert out.index("NOT in this host's image store") < out.index("Building images")


# ---------------------------------------------------------------------------
# S6 / S7 / Minor — deploy.sh odds and ends
# ---------------------------------------------------------------------------

def test_config_backup_succeeds_without_an_ssl_folder(project):
    shutil.rmtree(project.root / "ssl")

    result = project.run("deploy.sh", stdin="6\n")

    assert result.returncode == 0, _output(result)
    assert "Backed up .env" in result.stdout and "Done!" in result.stdout


def test_config_backup_does_not_claim_a_copy_that_failed(project):
    failing_cp = project.bin / "cp"
    failing_cp.write_text("#!/bin/sh\nexit 1\n")
    failing_cp.chmod(0o755)

    result = project.run("deploy.sh", stdin="6\n")

    out = _output(result)
    assert result.returncode != 0
    assert "Backed up .env" not in out
    assert "NOT backed up" in out


def test_nuclear_clean_asks_again_when_the_env_copy_failed(project):
    failing_cp = project.bin / "cp"
    failing_cp.write_text("#!/bin/sh\nexit 1\n")
    failing_cp.chmod(0o755)

    result = project.run("deploy.sh", stdin="4\nDELETE EVERYTHING\n")

    out = _output(result)
    assert result.returncode == 1
    assert "DELETE WITHOUT BACKUP" in out and "Nothing was removed" in out
    assert _index(project.calls(), "compose down") == -1
    assert (project.root / ".env").read_text().startswith("HOST_IP=")   # the real one, untouched


def test_an_aborted_nuclear_clean_leaves_no_temporary_env(project):
    (project.root / ".env").unlink()
    project.state["fail"] = [{"match": r"pg_dump", "rc": 1}]

    result = project.run("deploy.sh", stdin="4\nDELETE EVERYTHING\n")

    assert result.returncode == 1, _output(result)
    assert "Nothing was removed" in _output(result)
    assert not (project.root / ".env").exists()


def test_deploy_refuses_a_teardown_env(project):
    (project.root / ".env").write_text("HOST_IP=127.0.0.1\nSECRET_KEY=teardown\n")

    result = project.run("deploy.sh", stdin="1\n")

    assert result.returncode == 1
    assert "interrupted Nuclear clean" in _output(result)
    assert _index(project.calls(), "compose build") == -1


def test_a_prompt_at_end_of_input_says_so(project):
    result = project.run("deploy.sh", stdin="")

    assert result.returncode == 1
    assert "No answer on standard input" in _output(result)


# ---------------------------------------------------------------------------
# Ownership and permissions — checked before a deploy writes anything
# ---------------------------------------------------------------------------

PERMISSIONS_HEADING = "File ownership and permissions"


def _mode(path: pathlib.Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _fake_owner(project, *paths: str, uid: int = 0, name: str = "root") -> None:
    """A `stat` that says ``paths`` belong to another account — a test cannot
    chown — and is the real one for everything else."""
    real = shutil.which("stat")
    fake = project.bin / "stat"
    cases = "|".join(paths)
    fake.write_text(
        "#!/bin/sh\n"
        'last=""; for a in "$@"; do last="$a"; done\n'
        f'case "$last" in {cases})\n'
        f'  case "$2" in %u) echo {uid}; exit 0;; %U) echo {name}; exit 0;; esac;;\n'
        "esac\n"
        f'exec {real} "$@"\n'
    )
    fake.chmod(0o755)


def test_a_tree_kept_as_it_should_be_is_asked_nothing(project):
    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert PERMISSIONS_HEADING not in out
    assert "Deployment complete" in out


def test_a_readable_env_and_key_are_fixed_when_the_operator_says_so(project):
    env, key = project.root / ".env", project.root / "ssl" / "certs" / "networkmapper.key"
    env.chmod(0o644)
    key.chmod(0o640)

    result = project.run("deploy.sh", stdin="1\nf\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert PERMISSIONS_HEADING in out
    assert ".env (mode 644) can be read by other accounts" in out and "chmod 600" in out
    assert "TLS private key ssl/certs/networkmapper.key (mode 640)" in out
    assert "Fix the 2 this script can fix itself [f]" in out
    assert (_mode(env), _mode(key)) == (0o600, 0o600)
    assert "Ownership and permissions are as they should be." in out
    assert "Deployment complete" in out


def test_stop_changes_nothing_and_builds_nothing(project):
    env = project.root / ".env"
    env.chmod(0o644)

    result = project.run("deploy.sh", stdin="1\ns\n")

    out = _output(result)
    assert result.returncode == 1
    assert "Stopped before anything was changed" in out
    assert _mode(env) == 0o644
    assert _index(project.calls(), "compose build") == -1
    assert _index(project.calls(), "pg_dump") == -1   # no backup either: the steps after the check


def test_continue_goes_on_and_leaves_the_files_alone(project):
    env = project.root / ".env"
    env.chmod(0o644)

    result = project.run("deploy.sh", stdin="1\nc\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert "Continuing with the above as it is." in out and "Deployment complete" in out
    assert _mode(env) == 0o644


def test_with_no_operator_the_findings_are_said_and_the_deploy_goes_on(project):
    """A piped deploy is not stopped by a finding (none keeps the application
    from running) — and it is not silent about it either."""
    (project.root / ".env").chmod(0o644)

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert PERMISSIONS_HEADING in out
    assert "No answer on standard input — taking the default: 'c'" in out
    assert "Deployment complete" in out


def test_an_answer_that_is_none_of_the_three_is_asked_again(project):
    (project.root / ".env").chmod(0o644)

    result = project.run("deploy.sh", stdin="1\nyes\ns\n")

    out = _output(result)
    assert result.returncode == 1
    assert "Answer f, c or s." in out and out.count(PERMISSIONS_HEADING) == 2


def test_a_certificate_folder_that_is_roots_is_named_with_the_command_and_not_offered_as_a_fix(project):
    """What needs another account's rights is never attempted and never a
    sudo prompt: the command is printed, and the operator goes on or stops."""
    _fake_owner(project, "ssl", "ssl/certs", "ssl/certs/networkmapper.crt", "ssl/certs/networkmapper.key")

    result = project.run("deploy.sh", stdin="1\ns\n")

    out = _output(result)
    assert result.returncode == 1
    assert "The certificate folder is not" in out and "ssl/certs/networkmapper.key (root)" in out
    assert "nginx reads it whoever owns it, so root is not needed there" in out
    assert "sudo chown -R" in out and f"{project.root}/ssl" in out
    assert "None of these can be fixed from here" in out and "[f]" not in out
    assert "sudo " not in "\n".join(project.calls())


def test_an_uploads_folder_that_is_roots_is_named(project):
    (project.root / "uploads").mkdir(exist_ok=True)
    _fake_owner(project, "uploads")

    result = project.run("deploy.sh", stdin="1\ns\n")

    out = _output(result)
    assert result.returncode == 1
    assert "uploads/ belongs to root" in out and "chown -R 999:999" in out


def test_security_status_lists_the_findings_and_asks_nothing(project):
    clean = project.run("deploy.sh", stdin="5\n")
    assert clean.returncode == 0, _output(clean)
    assert "File ownership and permissions: nothing to fix" in _output(clean)

    (project.root / ".env").chmod(0o644)
    result = project.run("deploy.sh", stdin="5\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert "File ownership and permissions: 1 to look at" in out and "chmod 600" in out
    assert "[c]" not in out
    assert _mode(project.root / ".env") == 0o644


def test_running_as_root_is_said_with_how_to_hand_the_folder_back(project):
    """Reported under option 5 here: a test is not root, and the deploy
    options go on to chown as root would."""
    real = shutil.which("id")
    fake = project.bin / "id"
    fake.write_text(f'#!/bin/sh\n[ "$1" = "-u" ] && {{ echo 0; exit 0; }}\nexec {real} "$@"\n')
    fake.chmod(0o755)

    result = project.run("deploy.sh", stdin="5\n", env={"SUDO_USER": "deployer"})

    out = _output(result)
    assert result.returncode == 0, out
    assert "This script is running as root. Nothing in it needs root" in out
    assert "chown -R deployer:" in out and "chown -R 999:999" in out


def test_no_script_opens_a_folder_to_every_account():
    """deploy.sh used to `chmod 1777` two upload folders when it ran without
    root; the containers' entrypoint takes the tree over instead, and closes
    a folder an earlier deploy opened."""
    assert "chmod 1777" not in (SCRIPTS / "deploy.sh").read_text(encoding="utf-8")
    entrypoint = SCRIPTS.parent / "backend" / "docker-entrypoint.sh"
    if entrypoint.is_file():        # not in the image's own copy of the tree
        text = entrypoint.read_text(encoding="utf-8")
        assert "-perm -o+w" in text and "chmod 750" in text


def test_a_deploy_goes_on_when_uploads_is_the_containers_alone(project):
    """Once the containers have taken uploads/ over it is mode 700 and theirs:
    the deploy account cannot look inside, and `mkdir -p uploads/…` there
    ended the script under `set -e` without a word.  The entrypoint makes the
    folders; the script leaves them to it."""
    uploads = project.root / "uploads"
    uploads.mkdir(exist_ok=True)
    for child in list(uploads.iterdir()):
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    uploads.chmod(0o000)                 # as another account's 700 folder is to this one
    try:
        result = project.run("deploy.sh", stdin="1\n")
    finally:
        uploads.chmod(0o755)

    out = _output(result)
    assert result.returncode == 0, out
    assert "Deployment complete" in out and "Permission denied" not in out
    assert list(uploads.iterdir()) == []


def test_the_uploads_tree_and_the_database_key_are_set_up_by_the_containers():
    """Two things no host script does any more, because the container that
    needs them does them on every start: uploads/ closed to other accounts,
    and the TLS pair handed to the postgres user."""
    root = SCRIPTS.parent
    entrypoint = root / "backend" / "docker-entrypoint.sh"
    if entrypoint.is_file():
        assert 'chmod 700 "$UPLOADS"' in entrypoint.read_text(encoding="utf-8")
    assert not (SCRIPTS / "postgres" / "init-ssl.sh").exists()
    assert not (SCRIPTS / "postgres" / "ensure-ssl.sh").exists()
    tls = (SCRIPTS / "postgres" / "start-with-tls.sh").read_text(encoding="utf-8")
    # On only for a pair PostgreSQL accepts; otherwise the database still starts.
    assert "ssl=on" in tls and "starting WITHOUT TLS" in tls and 'exec docker-entrypoint.sh "$@"' in tls
    # …and "without TLS" is said to the server too, over an `ssl = on` an old
    # step left in postgresql.auto.conf (review 2026-10-14, suspected).
    assert 'exec docker-entrypoint.sh "$@" -c ssl=off' in tls
    # Third review 2026-10-15, N8: an unreadable pair is said as such, and a
    # copy that fails under `set -e` no longer stops the database.
    assert '! -r "$CERT_SOURCE"' in tls
    assert '|| tls_off "the certificate could not be copied for the postgres user" "$@"' in tls
    assert "ensure-ssl" not in (SCRIPTS / "deploy.sh").read_text(encoding="utf-8")
    compose = root / "docker-compose.yml"
    if compose.is_file():
        text = compose.read_text(encoding="utf-8")
        assert 'entrypoint: ["bash", "/usr/local/bin/start-with-tls.sh"]' in text
        assert "init-ssl.sh" not in text


def _audit(tmp_path: pathlib.Path, setup: str = "") -> list[str]:
    """Source the shared check in ``tmp_path`` and print one line per finding:
    ``kind|auto``, then the owner class of .env."""
    script = (
        f'cd "{tmp_path}"\n{setup}\nsource "{SCRIPTS / "permissions-lib.sh"}"\naudit_permissions\n'
        'for i in "${!PERM_KIND[@]}"; do echo "${PERM_KIND[$i]}|${PERM_AUTO[$i]}"; done\n'
        'echo "class:$(perm_owner_class .env)"\n'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_the_one_check_names_each_finding_by_kind(tmp_path):
    """deploy.sh asks about these; the diagnostics bundle reports the same
    ones — by kind, because the sentences name accounts and paths."""
    (tmp_path / "ssl" / "certs").mkdir(parents=True)
    env, key = tmp_path / ".env", tmp_path / "ssl" / "certs" / "networkmapper.key"
    env.write_text("SECRET_KEY=x\n")
    key.write_text("key")
    env.chmod(0o600)
    key.chmod(0o600)
    assert _audit(tmp_path) == ["class:this account"]

    env.chmod(0o644)
    key.chmod(0o640)
    assert _audit(tmp_path) == [
        "env_readable_by_others|chmod600:.env",
        "tls_key_readable_by_others|chmod600:ssl/certs/networkmapper.key",
        "class:this account",
    ]


def test_the_containers_uid_is_said_with_the_name_this_host_gives_it():
    """Diagnostics bundle 7 (2026-10-15): after `chown 999:999 uploads`, `ls -l`
    showed "dnsmasq systemd-journal" and the owner read as a mistake.  The
    check says what the host calls the number, and that it is expected — with
    a name when the host has one, as a number when it has none."""
    def note(uid: int) -> str:
        script = f'source "{SCRIPTS / "permissions-lib.sh"}"\nCONTAINER_UID={uid}\nperm_container_uid_note\n'
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    named = note(0)                                   # every host names UID 0
    assert 'shows UID 0 as "root" on this host' in named and "expected" in named
    unnamed = note(54321)                             # no account has this number
    assert "as a number on this host" in unnamed and "expected" in unnamed
    lib = (SCRIPTS / "permissions-lib.sh").read_text(encoding="utf-8")
    assert "After the fix, $(perm_container_uid_note)." in lib
    upgrade = (SCRIPTS / "upgrade-instance.sh").read_text(encoding="utf-8")
    assert "the containers' user (UID 999 — this host calls that number" in upgrade


def test_the_bundle_reports_ownership_by_class_and_the_findings_by_kind():
    """permissions.txt must hold no account name and no path of the host: it
    prints `perm_owner_class` and PERM_KIND, never the owner's name or the
    sentences deploy.sh shows at the terminal."""
    script = (SCRIPTS / "collect-logs.sh").read_text(encoding="utf-8")
    start = script.index('"=== OWNERSHIP AND PERMISSIONS')
    section = script[start:script.index('"$LOG_DIR/permissions.txt"', start)]
    assert 'source "$SCRIPT_DIR/permissions-lib.sh"' in section and "audit_permissions" in section
    assert "perm_owner_class" in section and "PERM_KIND" in section
    for forbidden in ("PERM_WHAT", "PERM_FIX", "perm_owner_name", "id -un", "whoami", "$PWD", "PROJECT_ROOT"):
        assert forbidden not in section, forbidden
    # The containers' own lines are taken whatever --since says, and the queries stay behind the lock timeout.
    assert r"\[entrypoint\]|\[db-tls\]" in section and "psql_q" in section
    # deploy.sh keeps no copy of the check.
    deploy = (SCRIPTS / "deploy.sh").read_text(encoding="utf-8")
    assert "audit_permissions() {" not in deploy and 'source "$SCRIPT_DIR/permissions-lib.sh"' in deploy


def test_the_upgrade_does_not_read_an_unreadable_uploads_folder_as_empty():
    text = (SCRIPTS / "upgrade-instance.sh").read_text(encoding="utf-8")
    guard = text.index('! [[ -r "$src" && -w "$src" && -x "$src" ]]')
    assert guard < text.index('[[ -n "$(ls -A "$dst" 2>/dev/null)" ]]')
    # Second review 2026-10-14, H7: `mv -T` (a plain `mv` into a folder the
    # containers already made nested uploads/uploads/), and the folder named
    # is the one that failed, with its own owner.
    assert "sudo $verb '$src' '$dst'" in text and "sudo mv '$src'" not in text
    assert 'if [[ $COPY_UPLOADS -eq 1 ]]; then verb="cp -aT"; else verb="mv -T"; fi' in text
    assert 'bad="$dst"; else bad="$src"' in text and "stat -c '%U, UID %u' \"$bad\"" in text
    # Third review 2026-10-15, R4: since 2.495.2 uploads/ is the containers',
    # so the ordinary upgrade moves it through the database image on the
    # folder both trees share; the sudo line is the fallback, and with
    # --copy-uploads it copies (a move destroyed the copy asked for).
    assert 'docker run --rm --entrypoint sh -v "$parent:/trees" "$image"' in text
    assert '[[ $moved -eq 1 ]] || fail' in text


def test_low_disk_in_a_piped_deploy_is_a_stated_cancel(project):
    """upgrade-instance.sh pipes exactly "1\\n": the low-disk question then
    met end of input and the deploy ended without a word."""
    result = project.run("deploy.sh", stdin="1\n", env={"MIN_FREE_GB": "999999999"})

    out = _output(result)
    assert result.returncode == 1
    assert "taking the default" in out and "Deploy cancelled" in out
    assert _index(project.calls(), "compose build") == -1


def _template_asset_lister() -> str:
    """The Python that upgrade-instance.sh runs to list the template files an
    operator may have installed (the heredoc between ``<<'PY'`` and ``PY``)."""
    text = (SCRIPTS / "upgrade-instance.sh").read_text(encoding="utf-8")
    start = text.index("<<'PY'")
    start = text.index("\n", start) + 1
    return text[start:text.index("\nPY\n", start)]


def test_upgrade_carries_the_installed_files_of_a_template_that_extends_another(tmp_path):
    """A template that extends another and declares no assets of its own
    expects its base's list — under its OWN folder (branding is never
    inherited).  The contact report repeated the list only because this step
    read each manifest's own."""
    root = tmp_path / "report-templates"
    assets = [{"id": "logo", "path": "img/logo.png"}, {"id": "styles", "path": "branding/reference.docx"},
              {"id": "bad", "path": "../../etc/passwd"}]
    for name, manifest in (
        ("pentest", {"title": "base", "assets": assets}),
        ("contact-report", {"extends": "pentest"}),
        ("own-list", {"extends": "pentest", "assets": [{"id": "x", "path": "img/own.png"}]}),
        ("none", {"extends": "pentest", "assets": []}),
        ("orphan", {"extends": "../pentest"}),
        ("broken", None),
    ):
        (root / name).mkdir(parents=True)
        (root / name / "template.json").write_text("{" if manifest is None else json.dumps(manifest), encoding="utf-8")

    out = subprocess.run(
        ["python3", "-", str(tmp_path)], input=_template_asset_lister(), capture_output=True, text=True, check=True,
    ).stdout.split()

    assert sorted(out) == [
        "report-templates/contact-report/branding/reference.docx",
        "report-templates/contact-report/img/logo.png",
        "report-templates/own-list/img/own.png",
        "report-templates/pentest/branding/reference.docx",
        "report-templates/pentest/img/logo.png",
    ]


def test_an_unreadable_repair_ledger_is_not_reported_as_nothing_pending(project):
    project.state["fail"] = [{"match": r"data_repairs\.py", "rc": 1}]

    result = project.run("deploy.sh", stdin="1\n")

    assert result.returncode == 0, _output(result)
    assert "Could not check the data-repair ledger" in _output(result)


def test_pending_repairs_are_listed(project):
    project.state["pending_repairs"] = "netexec_results_repair  docker compose exec backend …\n"

    result = project.run("deploy.sh", stdin="1\n")

    assert "Data repairs not yet applied" in _output(result)
    assert "netexec_results_repair" in _output(result)


def test_after_a_deploy_only_this_projects_untagged_images_are_pruned(project):
    project.run("deploy.sh", stdin="1\n")

    prunes = [c for c in project.calls() if c.startswith("image prune")]
    assert prunes == ["image prune -f --filter label=com.docker.compose.project=fakeproj"]


# ---------------------------------------------------------------------------
# S1 — a backup is read to its end; a restore checks it is the same file
# ---------------------------------------------------------------------------

def test_backup_refuses_a_truncated_dump_that_still_lists(project):
    """`pg_restore --list` reads only the table of contents, which is at the
    front of the file: a dump cut short still lists every entry."""
    project.state["dump_body"] = "PGDMP fake table of contents\nrow da"

    result = project.run("backup-db.sh")

    out = _output(result)
    assert result.returncode == 1
    assert "INCOMPLETE or corrupt" in out
    assert not list(project.backups.glob("nm-pgdump-*.dump"))


def test_backup_records_size_and_checksum(project):
    result = project.run("backup-db.sh")

    assert result.returncode == 0, _output(result)
    dump = next(project.backups.glob("nm-pgdump-*.dump"))
    meta = dict(line.split("=", 1) for line in (dump.parent / (dump.name + ".meta")).read_text().splitlines())
    assert meta["bytes"] == str(dump.stat().st_size)
    assert meta["sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest()
    assert meta["alembic_revision"] == OLD_REV
    uploads = project.backups / meta["uploads_archive"]
    assert meta["uploads_sha256"] == hashlib.sha256(uploads.read_bytes()).hexdigest()
    assert meta["uploads_bytes"] == str(uploads.stat().st_size)
    assert "full read" in meta["verified"]


# ---------------------------------------------------------------------------
# A failed backup says why (a remote host, 2026-10-20: "pg_dump failed." and
# nothing else — every tool's own message went to /dev/null)
# ---------------------------------------------------------------------------

def test_a_failed_pg_dump_prints_what_it_said(project):
    project.state["fail"] = [{"match": "pg_dump", "rc": 1}]

    result = project.run("backup-db.sh")

    out = _output(result)
    assert result.returncode == 1
    assert "pg_dump failed (exit 1)" in out
    assert "What it said:" in out and "scripted failure for: compose exec -T db pg_dump" in out
    assert not list(project.backups.glob("nm-pgdump-*.dump"))


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes anywhere")
def test_a_backup_folder_this_account_cannot_create_is_named_before_anything_is_dumped(project):
    """The default folder sits beside the project: a project in /opt, whose
    parent only root can write, failed with mkdir's one bare line."""
    project.backups.rmdir()
    parent = project.backups.parent
    parent.chmod(0o500)
    try:
        result = project.run("backup-db.sh")
    finally:
        parent.chmod(0o700)

    out = _output(result)
    assert result.returncode == 1
    assert f"Cannot create the backup folder {project.backups}" in out
    assert f"Its parent {parent}:" in out and "mode 500" in out and "Permission denied" in out
    assert "sudo install -d -m 700" in out and "BACKUP_DIR=" in out
    assert _index(project.calls(), "pg_dump") == -1


def test_compose_that_cannot_read_the_project_is_said_as_such(project):
    """An .env left unreadable fails every compose call; the script used to
    go on and blame the database volume."""
    project.state["fail"] = [{"match": "compose config --quiet", "rc": 1}]

    result = project.run("backup-db.sh")

    out = _output(result)
    assert result.returncode == 1
    assert "cannot read this project's configuration" in out and "scripted failure" in out
    assert "postgres volume" not in out
    assert _index(project.calls(), "pg_dump") == -1


def test_a_running_database_that_does_not_answer_is_not_copied_raw(project):
    project.state["fail"] = [{"match": "pg_isready", "rc": 2}]

    result = project.run("backup-db.sh")

    out = _output(result)
    assert result.returncode == 1
    assert "running but does not accept connections" in out and "scripted failure" in out
    assert _index(project.calls(), "/data:ro") == -1          # no volume snapshot of a live database
    assert not list(project.backups.glob("nm-pgdata-*"))


def test_a_stopped_database_is_snapshotted_and_said(project):
    project.state["containers"]["db"]["status"] = "exited"
    project.state["fail"] = [{"match": "pg_isready", "rc": 1}]

    result = project.run("backup-db.sh")

    out = _output(result)
    assert result.returncode == 0, out
    assert "The database container is exited — falling back to a raw volume snapshot." in out
    assert list(project.backups.glob("nm-pgdata-*.tar.gz"))


def test_a_failed_uploads_archive_prints_what_tar_said(project):
    project.state["fail"] = [{"match": "/uploads:ro", "rc": 2}]

    result = project.run("backup-db.sh")

    out = _output(result)
    assert result.returncode == 1
    assert "Archiving uploads/ failed (exit 2)" in out and "scripted failure for: run --rm" in out
    assert "BACKUP INCOMPLETE" in out


def test_a_deploy_whose_backup_fails_says_why_and_builds_nothing(project):
    project.state["fail"] = [{"match": "pg_dump", "rc": 1}]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "pg_dump failed (exit 1)" in out and "backup-db.sh says why above" in out
    assert "Refusing to deploy without a restorable backup" in out
    assert _index(project.calls(), "compose build") == -1


def test_a_deploy_records_the_dump_backup_db_reports(project):
    project.make_dump("nm-pgdump-20990101-000000.dump")   # newer by name, older than this run

    result = project.run("deploy.sh", stdin="1\n")

    assert result.returncode == 0, _output(result)
    recorded = [ln for ln in project.state_file().splitlines() if ln.startswith("PREDEPLOY_DB_DUMP|")]
    assert len(recorded) == 1 and "20990101" not in recorded[0]
    assert pathlib.Path(recorded[0].split("|", 1)[1]).is_file()


def test_security_status_says_that_a_containers_uploads_folder_is_as_it_should_be(project):
    """The correct state — another number's folder, mode 700, unlistable from
    the deploy account — read like a fault, and operators took it for one."""
    _fake_owner(project, "uploads", uid=999, name="dnsmasq")

    result = project.run("deploy.sh", stdin="5\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert "uploads/ is the containers' (UID 999" in out and "as it should be" in out
    assert "this account cannot list it, by design" in out
    assert "exec backend ls -la /app/uploads" in out
    assert "File ownership and permissions: nothing to fix" in out


# ---------------------------------------------------------------------------
# A folder of another name is another Compose project (remote bundle,
# 2026-10-20: every service "never started" while nginx answered — and the
# backup failed with "Could not uniquely identify this stack's postgres
# volume").  The upgrade is: move the folder aside, extract the new source
# into a new folder of the SAME name.
# ---------------------------------------------------------------------------

def _installed_elsewhere(project, tmp_path, *, with_tree: bool = True) -> pathlib.Path:
    where = tmp_path / "BlueStick"
    if with_tree:
        (where / "scripts").mkdir(parents=True)
        (where / "scripts" / "deploy.sh").write_text("#!/bin/bash\n")
    project.state["containers"] = {}
    project.state["other_stacks"] = [{"project": "acmecorp", "working_dir": str(where), "running": 5}]
    return where


def test_a_deploy_from_a_folder_of_another_name_changes_nothing_and_says_where_the_stack_is(project, tmp_path):
    where = _installed_elsewhere(project, tmp_path)

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 1
    assert "This folder is Compose project 'fakeproj'" in out and "which has no containers" in out
    assert f"'acmecorp', started from {where} — 5 container(s) running" in out
    assert f"{where} holds a BlueStick tree: run the script from there" in out
    assert "upgrade-instance.sh" in out and "COMPOSE_PROJECT_NAME=acmecorp" in out
    assert "Nothing was changed" in out
    calls = project.calls()
    assert _index(calls, "pg_dump") == -1 and _index(calls, "compose build") == -1 and _index(calls, "compose up") == -1


def test_a_stack_whose_folder_is_gone_is_told_to_put_the_new_source_at_that_path(project, tmp_path):
    where = _installed_elsewhere(project, tmp_path, with_tree=False)

    result = project.run("deploy.sh", stdin="2\n")

    out = _output(result)
    assert result.returncode == 1
    assert f"{where} is not there any more. Put the new source at that SAME path" in out


def test_a_second_stack_on_purpose_is_let_through_and_said(project, tmp_path):
    _installed_elsewhere(project, tmp_path)

    result = project.run("deploy.sh", stdin="1\n", env={"DEPLOY_SECOND_STACK": "1"})

    assert "deploying a SECOND BlueStick stack ('fakeproj')" in _output(result)


def test_another_stack_beside_this_folders_own_is_no_obstacle(project, tmp_path):
    project.state["other_stacks"] = [{"project": "acmecorp", "working_dir": str(tmp_path / "x"), "running": 5}]

    result = project.run("deploy.sh", stdin="1\n")

    out = _output(result)
    assert result.returncode == 0, out
    assert "This folder is Compose project" not in out and "Deployment complete" in out


def test_a_backup_from_a_folder_of_another_name_says_where_the_stack_is(project, tmp_path):
    where = _installed_elsewhere(project, tmp_path)

    result = project.run("backup-db.sh")

    out = _output(result)
    assert result.returncode == 1
    assert f"'acmecorp', started from {where}" in out and "No backup was taken." in out
    assert "postgres volume" not in out
    assert _index(project.calls(), "/data:ro") == -1


def test_the_bundle_counts_stacks_under_other_projects_without_naming_them(project, tmp_path):
    _installed_elsewhere(project, tmp_path)
    out_dir = tmp_path / "bundle"
    out_dir.mkdir()

    result = project.run("collect-logs.sh", "--no-feedback", "--out", str(out_dir))

    assert result.returncode == 0, _output(result)
    bundle = next(out_dir.glob("bluestick_diagnostics_*.tar.gz"))
    with tarfile.open(bundle) as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("/containers.txt"))
        text = tar.extractfile(member).read().decode()
    assert "other projects: 1 (their running containers: 5; started from: a-sibling-folder)" in text
    assert "this folder's project has 0 container(s)" in text
    assert "acmecorp" not in text and "BlueStick" not in text.replace("BlueStick under", "")


# ---------------------------------------------------------------------------
# collect-logs.sh writes its bundle where it was run, and never discards it
# ---------------------------------------------------------------------------

def test_the_bundle_is_written_where_the_script_was_run(project, tmp_path):
    here = tmp_path / "operator"
    here.mkdir()

    result = project.run("collect-logs.sh", "--no-feedback", cwd=here)

    out = _output(result)
    assert result.returncode == 0, out
    bundles = list(here.glob("bluestick_diagnostics_*.tar.gz"))
    assert len(bundles) == 1 and f"Anonymised diagnostics bundle: {bundles[0]}" in out
    assert not list(project.root.glob("bluestick_diagnostics_*.tar.gz"))


def test_the_bundle_goes_to_out(project, tmp_path):
    out_dir = tmp_path / "elsewhere"
    out_dir.mkdir()

    result = project.run("collect-logs.sh", "--no-feedback", "--out", str(out_dir))

    assert result.returncode == 0, _output(result)
    assert len(list(out_dir.glob("bluestick_diagnostics_*.tar.gz"))) == 1


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes anywhere")
def test_a_folder_this_account_cannot_write_gets_no_bundle_and_is_told_where_it_went(project, tmp_path):
    """A failed final move ended the script under `set -e`, and the EXIT trap
    deleted the finished bundle with its work folder."""
    here = tmp_path / "read-only"
    here.mkdir()
    here.chmod(0o500)
    try:
        result = project.run("collect-logs.sh", "--no-feedback", cwd=here)
    finally:
        here.chmod(0o700)

    out = _output(result)
    assert result.returncode == 0, out
    assert f"{here} is not writable" in out and f"written to {project.root} instead" in out
    assert len(list(project.root.glob("bluestick_diagnostics_*.tar.gz"))) == 1


def test_restore_refuses_a_dump_that_no_longer_matches_its_checksum(project):
    dump = project.make_dump()
    dump.write_bytes(COMPLETE_DUMP.replace(b"row data", b"row dat4"))   # same size, other bytes

    result = project.run("restore-db.sh", "--yes", str(dump))

    out = _output(result)
    assert result.returncode == 1
    assert "does not match the checksum" in out and "Nothing was changed" in out
    calls = project.calls()
    assert _index(calls, "compose stop") == -1 and _index(calls, "psql") == -1


def test_restore_refuses_a_truncated_dump_with_no_meta(project):
    dump = project.make_dump(body=b"PGDMP fake table of contents\nrow da", meta=False)

    result = project.run("restore-db.sh", "--yes", str(dump))

    out = _output(result)
    assert result.returncode == 1
    assert "INCOMPLETE or corrupt" in out and "Nothing was changed" in out
    assert _index(project.calls(), "compose stop") == -1


def test_restore_refuses_a_truncated_dump_by_its_recorded_size(project):
    dump = project.make_dump()
    dump.write_bytes(COMPLETE_DUMP[:20])

    result = project.run("restore-db.sh", "--yes", str(dump))

    assert result.returncode == 1
    assert "cut short or changed" in _output(result)


def test_restore_force_unverified_goes_ahead(project):
    dump = project.make_dump()
    dump.write_bytes(COMPLETE_DUMP.replace(b"row data", b"row dat4"))

    result = project.run("restore-db.sh", "--yes", "--force-unverified", str(dump))

    assert result.returncode == 0, _output(result)
    assert "--force-unverified: continuing" in _output(result)


# ---------------------------------------------------------------------------
# S3 / S4 — restore-db.sh never ends in silence
# ---------------------------------------------------------------------------

def test_restore_starts_staged_and_ends_with_a_summary(project):
    dump = project.make_dump()

    result = project.run("restore-db.sh", str(dump), stdin="RESTORE\n")

    out = _output(result)
    assert result.returncode == 0, out
    calls = project.calls()
    ups = [c for c in calls if c.startswith("compose up -d")]
    assert ups == ["compose up -d db", "compose up -d db backend",
                   "compose up -d worker report-worker", "compose up -d"]
    assert _index(calls, "pg_restore -U nmapuser") < _index(calls, "compose up -d db backend")
    assert "instance state: RUNNING" in out


def test_restore_names_the_safety_backup_when_pg_restore_fails(project):
    dump = project.make_dump()
    project.state["fail"] = [{"match": r"pg_restore -U nmapuser -d networkMapper", "rc": 1}]

    result = project.run("restore-db.sh", str(dump), stdin="RESTORE\n")

    out = _output(result)
    assert result.returncode == 1
    assert "pg_restore FAILED" in out and "instance state: STOPPED" in out
    safety = [p for p in project.backups.glob("nm-pgdump-*.dump") if p != dump]
    assert len(safety) == 1
    assert f'./scripts/restore-db.sh --no-safety-backup "{safety[0]}"' in out
    assert not any(c.startswith("compose up -d") and "backend" in c for c in project.calls())


def test_restore_reports_a_failed_start(project):
    dump = project.make_dump()
    project.state["fail"] = [{"match": r"compose up -d db backend", "rc": 1}]
    project.state["healthy_images"] = []
    project.state["containers"]["backend"]["status"] = "exited"

    result = project.run("restore-db.sh", str(dump), stdin="RESTORE\n")

    out = _output(result)
    assert result.returncode == 1
    assert "instance state: NOT RUNNING" in out
    assert "The restore itself succeeded" in out


def test_restore_no_start_leaves_the_app_stopped(project):
    dump = project.make_dump()

    result = project.run("restore-db.sh", "--yes", "--no-start", str(dump))

    assert result.returncode == 0, _output(result)
    assert not any(c.startswith("compose up -d") and "backend" in c for c in project.calls())
    assert "instance state: STOPPED" in _output(result)


def _dump_with_uploads(project) -> tuple[pathlib.Path, pathlib.Path]:
    dump = project.make_dump()
    archive = project.backups / "nm-uploads-20260101-000000.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("./evidence.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"data"))
    meta = project.backups / (dump.name + ".meta")
    meta.write_text(meta.read_text() + f"uploads_archive={archive.name}\n")
    return dump, archive


def test_restore_whose_uploads_fail_ends_partly_restored(project):
    """Review 2026-10-22, A-R14: it printed an error, then "Restore complete"
    and exit 0."""
    dump, archive = _dump_with_uploads(project)
    project.state["fail"] = [{"match": r"xzf - -C /uploads", "rc": 2}]

    result = project.run("restore-db.sh", str(dump), stdin="RESTORE\n")

    out = _output(result)
    assert result.returncode == 4, out
    assert "PARTLY RESTORED" in out and "Restore complete" not in out
    assert "retry the files" in out and f'< "{archive}"' in out


def test_restore_no_start_whose_uploads_fail_says_so(project):
    dump, _ = _dump_with_uploads(project)
    project.state["fail"] = [{"match": r"xzf - -C /uploads", "rc": 2}]

    result = project.run("restore-db.sh", "--yes", "--no-start", str(dump))

    out = _output(result)
    assert result.returncode == 4, out
    assert "STOPPED — PARTLY RESTORED" in out


def test_rollback_whose_uploads_fail_starts_the_previous_build_and_says_partly_restored(project):
    dump, _ = _dump_with_uploads(project)
    project.rollback_state(dump, OLD_REV)
    project.state["alembic_revision"] = NEW_REV
    project.state["fail"] = [{"match": r"xzf - -C /uploads", "rc": 2}]

    result = project.run("deploy.sh", stdin="7\n")

    out = _output(result)
    assert result.returncode == 1, out
    assert "compose up -d --no-build db backend" in project.calls()
    assert "Rollback — instance state: RUNNING — PARTLY RESTORED" in out


def test_restore_without_the_typed_confirmation_changes_nothing(project):
    dump = project.make_dump()

    result = project.run("restore-db.sh", str(dump), stdin="")

    assert result.returncode == 1
    assert "Nothing was changed" in _output(result)
    assert _index(project.calls(), "compose stop") == -1


# ---------------------------------------------------------------------------
# The settings a deploy ends by showing (2026-10-08)
# ---------------------------------------------------------------------------

def _settings(out: str) -> dict:
    """{NAME: (value, source)} from the readout."""
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isupper() and parts[2].startswith("("):
            rows[parts[0]] = (parts[1], parts[2].strip("()"))
    return rows


def test_a_deploy_ends_by_showing_the_settings_in_force_and_where_each_comes_from(project):
    """An operator is told to change these after a look at the host's logs;
    the deploy shows them — the value in .env, else the default read from
    where it is defined, never a number repeated in the script."""
    (project.root / "docker-compose.yml").write_text(
        "services:\n  worker:\n    mem_limit: ${WORKER_MEM_LIMIT:-4g}\n"
        "  db:\n    command: -c shared_buffers=${PG_SHARED_BUFFERS:-256MB}\n"
    )
    (project.root / ".env.example").write_text(
        "# Seconds an import may go without progress.\n# INGESTION_STALL_TIMEOUT=1800\n"
        "# Sized for NESSUS (INGESTION_RETAIN_FILES_DAYS=99) in prose is not a default.\n"
    )
    env = project.root / ".env"
    env.write_text(env.read_text() + "WORKER_MEM_LIMIT=8g\n")

    result = project.run("deploy.sh", stdin="1\n", env={"BACKUP_KEEP": "25"})

    out = _output(result)
    assert result.returncode == 0, out
    assert out.index("Deployment complete") < out.index("Settings you can tune")
    rows = _settings(out)
    assert rows["WORKER_MEM_LIMIT"] == ("8g", ".env")
    assert rows["PG_SHARED_BUFFERS"] == ("256MB", "default")       # docker-compose.yml
    assert rows["INGESTION_STALL_TIMEOUT"] == ("1800", "default")  # .env.example's commented line
    assert rows["BACKUP_KEEP"] == ("25", "environment")
    assert rows["DEPLOY_HEALTH_TIMEOUT"] == ("10", "environment")  # the harness sets it
    assert rows["MIN_FREE_GB"] == ("0", "environment")
    assert rows["INGESTION_RETAIN_FILES_DAYS"] == ("built-in", "default")
    # Nothing that is a secret is ever in the readout.
    assert "a-real-secret" not in out
    assert not [name for name in rows if "SECRET" in name or "PASSWORD" in name or "KEY" in name]
    # (That every name shown has a default this repository defines is pinned
    # where the repository root can be read: frontend/src/tests/tunableSettings.test.ts.)


# ---------------------------------------------------------------------------
# check.sh: the shard plan (the gate's backend step runs the test files in
# several containers at once — a file left out of every shard would be a test
# that silently stopped running)
# ---------------------------------------------------------------------------

class _ShardTree:
    """The real ``check.sh`` in a temporary tree with made-up test files.
    ``--plan-shards N`` prints the plan and starts nothing."""

    def __init__(self, tmp_path: pathlib.Path, files: dict[str, float | None]):
        self.root = tmp_path / "tree"
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPTS / "check.sh", self.root / "scripts" / "check.sh")
        self.tests = self.root / "backend" / "tests"
        self.tests.mkdir(parents=True)
        for name in files:
            path = self.tests / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def test_it():\n    pass\n")
        self.weights = self.root / "scripts" / "check-shard-weights.txt"
        self.weights.write_text(
            "# seconds file\n\n" + "".join(f"{w} {name}\n" for name, w in files.items() if w is not None)
        )

    def plan(self, shards: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "check.sh"), "--plan-shards", str(shards)],
            capture_output=True, text=True,
        )

    def shards(self, shards: int) -> dict[int, list[str]]:
        result = self.plan(shards)
        assert result.returncode == 0, _output(result)
        out: dict[int, list[str]] = {}
        for line in result.stdout.splitlines():
            shard, _weight, name = line.split("\t")
            out.setdefault(int(shard), []).append(name)
        return out


_SHARD_FILES: dict[str, float | None] = {
    "test_slow.py": 40.0, "test_slower.py": 55.5, "test_mid_a.py": 12.0, "test_mid_b.py": 11.0,
    "test_quick_a.py": 0.4, "test_quick_b.py": 0.3, "test_quick_c.py": 0.2, "test_quick_d.py": 0.1,
    "test_new_a.py": None, "test_new_b.py": None,          # not in the weights: still run
    "nested/test_deeper.py": 3.0,                         # pytest collects it, so a shard gets it
}


@pytest.mark.parametrize("shards", [1, 2, 3, 4, 7, 11, 40])
def test_every_test_file_is_in_exactly_one_shard(tmp_path, shards):
    tree = _ShardTree(tmp_path, _SHARD_FILES)
    (tree.tests / "conftest.py").write_text("")           # not a test file
    (tree.tests / "helpers_test.py").write_text("")       # nor is this (python_files = test_*.py)

    planned = tree.shards(shards)

    everything = [name for names in planned.values() for name in names]
    assert sorted(everything) == sorted(_SHARD_FILES)      # each once, nothing else
    assert set(planned) <= set(range(1, shards + 1))


def test_the_shard_plan_is_the_same_every_time_and_balanced_by_seconds(tmp_path):
    tree = _ShardTree(tmp_path, _SHARD_FILES)

    first, second = tree.shards(2), tree.shards(2)

    assert first == second
    # Heaviest first, each to the lightest shard: the two slow files are apart,
    # and the two mid ones sit with the lighter of them.
    slower = next(s for s, names in first.items() if "test_slower.py" in names)
    slow = next(s for s, names in first.items() if "test_slow.py" in names)
    assert slower != slow
    assert {"test_mid_a.py", "test_mid_b.py"} <= set(first[slow])

    def load(names):
        return sum(_SHARD_FILES[n] if _SHARD_FILES[n] is not None else 1.0 for n in names)

    assert abs(load(first[1]) - load(first[2])) < 12.0     # by count alone it could be 80


@pytest.mark.parametrize("weights", ["missing", "empty", "stale", "garbage"])
def test_no_weights_file_can_drop_a_test_file(tmp_path, weights):
    tree = _ShardTree(tmp_path, _SHARD_FILES)
    if weights == "missing":
        tree.weights.unlink()
    elif weights == "empty":
        tree.weights.write_text("")
    elif weights == "stale":                                # names only files that are gone
        tree.weights.write_text("9.0 test_removed_long_ago.py\n3.0 test_also_gone.py\n")
    else:
        tree.weights.write_text("not a number test_slow.py\n\x00\n12\n  \n-4 test_slower.py\n")

    planned = tree.shards(4)

    assert sorted(n for names in planned.values() for n in names) == sorted(_SHARD_FILES)
    assert "test_removed_long_ago.py" not in _output(tree.plan(4))


def test_a_plan_that_is_not_the_whole_suite_stops_the_step(tmp_path):
    """The completeness check itself: with the assignment broken so that it
    loses a file, the script refuses and names the file."""
    tree = _ShardTree(tmp_path, _SHARD_FILES)
    script = tree.root / "scripts" / "check.sh"
    text = script.read_text()
    intact = 'printf "%d\\t%s\\t%s\\n", best, $1, $2'
    assert text.count(intact) == 1
    script.write_text(text.replace(intact, 'if ($2 != "test_quick_c.py") ' + intact))

    result = tree.plan(4)

    assert result.returncode == 1
    assert "NOT the test suite" in result.stderr
    assert "> test_quick_c.py" in result.stderr
    assert result.stdout == ""                              # no plan is handed on


def test_shard_tallies_are_summed_into_one(tmp_path):
    result = subprocess.run(
        ["bash", str(SCRIPTS / "check.sh"), "--sum-tallies"], capture_output=True, text=True,
        input=(
            "3 failed, 970 passed, 2 skipped, 7 warnings in 81.20s (0:01:21)\n"
            "900 passed, 1 warning, 1 error in 5.20s\n"
            "===== 1000 passed, 4 skipped, 2 errors in 100.00s (0:01:40) =====\r\n"
            "5 deselected in 0.10s\n"
        ),
    )
    assert result.returncode == 0, _output(result)
    assert result.stdout.strip() == "3 failed, 2870 passed, 6 skipped, 5 deselected, 8 warnings, 3 errors"


def test_the_checked_in_shard_weights_name_only_test_files_once():
    """A stale line is harmless (it is ignored), but a line that is not
    '<seconds> <file>' or a file listed twice means the file was edited by
    hand and no longer says what was measured."""
    weights = SCRIPTS / "check-shard-weights.txt"
    assert weights.is_file(), "scripts/check-shard-weights.txt is the gate's shard balance; check.sh --update-shard-weights writes it"
    seen = []
    for line in weights.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        seconds, name = line.split()
        assert float(seconds) >= 0
        assert pathlib.PurePosixPath(name).name.startswith("test_") and name.endswith(".py"), line
        seen.append(name)
    assert len(seen) == len(set(seen))
    assert len(seen) > 100          # a truncated file would still run everything, badly balanced


# ---------------------------------------------------------------------------
# Every script still parses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(p.name for p in SCRIPTS.rglob("*.sh")) if SCRIPTS else [])
def test_script_parses(name):
    path = next(SCRIPTS.rglob(name))
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
