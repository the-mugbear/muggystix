"""Review 2026-10-01 B10 — the data-repair ledger.

A data repair corrects rows older code wrote.  Until this ledger nothing
recorded that one was owed or had been run; ``deploy.sh`` and ``status.sh``
now print the pending ones.  These tests pin the rules that make the
reminder trustworthy:

* a repair is pending until a run that settles it for the WHOLE instance;
* a ``--project`` run, or a dry run that found rows to correct, records nothing;
* the row is written in the repair's own transaction;
* every known repair names a script that exists and is documented.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from app.db.models_data_repairs import DataRepairRun
from app.services import data_repair_service as svc


def _scripts_dir():
    here = pathlib.Path(__file__).resolve()
    for candidate in (here.parents[1] / "scripts", here.parents[2] / "scripts"):
        if (candidate / "README.md").is_file():
            return candidate
    return None


def _names(repairs):
    return [repair.name for repair in repairs]


# --- the service -----------------------------------------------------------

def test_every_known_repair_is_pending_on_an_empty_ledger(db_session):
    assert _names(svc.pending(db_session)) == _names(svc.KNOWN_REPAIRS)
    assert len(svc.KNOWN_REPAIRS) >= 2


def test_recording_a_run_settles_only_that_repair(db_session):
    svc.record_run(db_session, "misconfig_backfill", applied_by="test",
                   rows_affected={"smb_signing_not_required": 3})

    assert _names(svc.pending(db_session)) == ["netexec_results_repair"]
    row = db_session.query(DataRepairRun).one()
    assert row.name == "misconfig_backfill"
    assert row.mode == svc.MODE_APPLY
    assert row.applied_by == "test"
    assert row.rows_affected == {"smb_signing_not_required": 3}
    assert row.applied_at is not None
    assert row.run_count == 1


def test_a_repeat_run_updates_the_one_row_and_counts(db_session):
    svc.record_run(db_session, "misconfig_backfill", applied_by="first", rows_affected={"a": 1})
    svc.record_run(db_session, "misconfig_backfill", applied_by="second", rows_affected={"a": 0})

    row = db_session.query(DataRepairRun).one()
    assert row.run_count == 2
    assert row.applied_by == "second"
    assert row.rows_affected == {"a": 0}


def test_an_unknown_repair_or_mode_is_refused(db_session):
    with pytest.raises(KeyError):
        svc.record_run(db_session, "not_a_repair", applied_by="test")
    with pytest.raises(ValueError):
        svc.record_run(db_session, "misconfig_backfill", applied_by="test", mode="dry_run")
    assert db_session.query(DataRepairRun).count() == 0


def test_the_ledger_lists_every_repair_with_its_command(db_session):
    svc.record_run(db_session, "netexec_results_repair", applied_by="test",
                   mode=svc.MODE_NOTHING_TO_REPAIR, rows_affected={"logins_cleared": 0})

    by_name = {row["name"]: row for row in svc.ledger(db_session)}

    assert set(by_name) == set(_names(svc.KNOWN_REPAIRS))
    assert by_name["netexec_results_repair"]["applied"] is True
    assert by_name["netexec_results_repair"]["mode"] == svc.MODE_NOTHING_TO_REPAIR
    assert by_name["misconfig_backfill"]["applied"] is False
    assert by_name["misconfig_backfill"]["applied_at"] is None
    for row in by_name.values():
        assert row["command"].startswith("docker compose exec backend python scripts/")


def test_the_deleting_repair_is_marked_destructive_and_has_a_dry_run():
    repair = svc.known_repair("netexec_results_repair")
    assert repair.destructive is True
    assert repair.command.endswith("--apply")
    assert "--apply" not in repair.preview_command
    assert svc.known_repair("misconfig_backfill").destructive is False


def test_repair_names_are_unique():
    names = _names(svc.KNOWN_REPAIRS)
    assert len(names) == len(set(names))


# --- the scripts -----------------------------------------------------------

def test_every_known_repair_names_a_documented_script():
    scripts = _scripts_dir()
    if scripts is None:
        pytest.skip("scripts/ is not mounted here")
    readme = (scripts / "README.md").read_text(encoding="utf-8")
    for repair in svc.KNOWN_REPAIRS:
        for command in (repair.command, repair.preview_command):
            script = next(part for part in command.split() if part.startswith("scripts/"))
            assert (scripts / script.split("/", 1)[1]).is_file(), command
            assert script.split("/", 1)[1] in readme, command
    assert "data_repairs.py" in readme


class _SharedSession:
    """Hands a script the test's session: its commits and rollbacks are real
    (inside the fixture's savepoint), its close() is not."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


def _run_script(monkeypatch, db_session, script_name, argv):
    scripts = _scripts_dir()
    if scripts is None:
        pytest.skip("scripts/ is not mounted here")
    spec = importlib.util.spec_from_file_location(
        f"_repair_script_{script_name.replace('.', '_')}", scripts / script_name,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SessionLocal", lambda: _SharedSession(db_session))
    monkeypatch.setattr(sys, "argv", [script_name, *argv])
    assert module.main() == 0
    return module


def test_the_backfill_script_records_an_instance_wide_run(monkeypatch, db_session, capsys):
    _run_script(monkeypatch, db_session, "backfill_misconfigs.py", [])

    row = db_session.query(DataRepairRun).one()
    assert row.name == "misconfig_backfill"
    assert row.mode == svc.MODE_APPLY
    assert row.applied_by == "scripts/backfill_misconfigs.py"
    assert "Recorded in the data-repair ledger" in capsys.readouterr().out


def test_a_project_limited_backfill_is_not_recorded(monkeypatch, db_session, test_project, capsys):
    _run_script(monkeypatch, db_session, "backfill_misconfigs.py", ["--project", str(test_project.id)])

    assert db_session.query(DataRepairRun).count() == 0
    assert "Not recorded" in capsys.readouterr().out


def test_a_clean_netexec_dry_run_settles_the_repair(monkeypatch, db_session, capsys):
    _run_script(monkeypatch, db_session, "repair_netexec_results.py", [])

    row = db_session.query(DataRepairRun).one()
    assert row.name == "netexec_results_repair"
    assert row.mode == svc.MODE_NOTHING_TO_REPAIR
    assert "nothing to repair" in capsys.readouterr().out


def test_a_netexec_dry_run_that_finds_rows_is_not_recorded(monkeypatch, db_session, test_project, capsys):
    from app.db import models

    # A host literally named "None" is one of the rows the repair clears.
    db_session.add(models.Host(project_id=test_project.id, ip_address="10.99.0.1", hostname="None"))
    db_session.commit()

    _run_script(monkeypatch, db_session, "repair_netexec_results.py", [])

    assert db_session.query(DataRepairRun).count() == 0
    assert db_session.query(models.Host).filter(models.Host.ip_address == "10.99.0.1").one().hostname == "None"
    assert "dry run that found rows" in capsys.readouterr().out


def test_applying_the_netexec_repair_records_it_with_its_counts(monkeypatch, db_session, test_project):
    from app.db import models

    db_session.add(models.Host(project_id=test_project.id, ip_address="10.99.0.2", hostname="None"))
    db_session.commit()

    _run_script(monkeypatch, db_session, "repair_netexec_results.py", ["--apply"])

    row = db_session.query(DataRepairRun).one()
    assert row.mode == svc.MODE_APPLY
    assert row.rows_affected["host_names_cleared"] == 1
    assert db_session.query(models.Host).filter(models.Host.ip_address == "10.99.0.2").one().hostname is None


def test_the_ledger_script_prints_pending_repairs_with_their_commands(monkeypatch, db_session, capsys):
    svc.record_run(db_session, "misconfig_backfill", applied_by="test")
    db_session.commit()

    _run_script(monkeypatch, db_session, "data_repairs.py", ["--pending"])

    out = capsys.readouterr().out
    assert "netexec_results_repair" in out
    assert "scripts/repair_netexec_results.py --apply" in out
    assert "dry run:" in out
    assert "misconfig_backfill" not in out
