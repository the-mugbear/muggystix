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
import json
import os
import pathlib
import shutil
import stat
import subprocess

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
    def run(self, script: str, *args: str, stdin: str = "", env: dict | None = None):
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
            input=stdin, capture_output=True, text=True, env=full_env, cwd=self.root, timeout=120,
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


def test_low_disk_in_a_piped_deploy_is_a_stated_cancel(project):
    """upgrade-instance.sh pipes exactly "1\\n": the low-disk question then
    met end of input and the deploy ended without a word."""
    result = project.run("deploy.sh", stdin="1\n", env={"MIN_FREE_GB": "999999999"})

    out = _output(result)
    assert result.returncode == 1
    assert "taking the default" in out and "Deploy cancelled" in out
    assert _index(project.calls(), "compose build") == -1


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


def test_restore_without_the_typed_confirmation_changes_nothing(project):
    dump = project.make_dump()

    result = project.run("restore-db.sh", str(dump), stdin="")

    assert result.returncode == 1
    assert "Nothing was changed" in _output(result)
    assert _index(project.calls(), "compose stop") == -1


# ---------------------------------------------------------------------------
# Every script still parses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(p.name for p in SCRIPTS.rglob("*.sh")) if SCRIPTS else [])
def test_script_parses(name):
    path = next(SCRIPTS.rglob(name))
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
