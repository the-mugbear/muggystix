"""Comprehensive review 2026-10-16, batch T6 — small operational fixes."""
from __future__ import annotations

import json

import pytest


# --- N14: the connection budget is checked both ways ------------------------

def test_the_documented_connection_budget_is_the_computed_one():
    from app.main import connection_budget

    # `.env.example` and `config.py` state 90 for the defaults.
    assert connection_budget(workers=4, pool=5, overflow=10, report_pool=3) == 90
    assert connection_budget(workers=8, pool=5, overflow=10, report_pool=3) == 150


def test_a_budget_over_the_servers_max_connections_is_warned_at_boot(monkeypatch):
    from app import main

    # The `app` logger does not propagate to the root (main.py), so caplog
    # would see nothing: the warnings are recorded where they are made.
    said = []
    monkeypatch.setattr(main.logger, "warning", lambda msg, *a: said.append(msg % a))
    monkeypatch.setenv("UVICORN_WORKERS", "8")
    main._check_pool_budget(server_max=120)
    assert any("(150: 8 API workers" in s and "max_connections (120)" in s for s in said), said

    said.clear()
    monkeypatch.setenv("UVICORN_WORKERS", "4")
    main._check_pool_budget(server_max=120)
    assert not [s for s in said if "max_connections" in s]


# --- N17: a deactivated account is not added; over-long names are a 422 ----

def test_a_deactivated_account_is_not_added_to_a_project(client, db_session, test_project):
    from app.db.models_auth import User, UserRole
    from app.db.models_project import ProjectMembership

    # An explicit id: the fixtures insert users with theirs, so the sequence
    # would hand out one already taken (as `test_project_audit._make_user`).
    gone = User(id=9_600_001, username="t6-deactivated", hashed_password="$2b$12$abcdefghijklmnopqrstuv",
                role=UserRole.MEMBER, is_active=False)
    db_session.add(gone)
    db_session.commit()
    r = client.post(f"/api/v1/projects/{test_project.id}/members", json={"user_id": gone.id, "role": "viewer"})
    assert r.status_code == 409, r.text
    assert "deactivated" in r.json()["detail"]
    assert db_session.query(ProjectMembership).filter_by(user_id=gone.id).count() == 0


def test_a_name_longer_than_its_column_is_a_422_naming_the_field(client):
    r = client.put("/api/v1/users/profile", json={"full_name": "x" * 101})
    assert r.status_code == 422, r.text
    assert "full_name" in r.text
    assert client.put("/api/v1/users/profile", json={"full_name": "x" * 100}).status_code == 200
    r = client.post("/api/v1/auth/register", json={"username": "u" * 51, "password": "A-long-Passw0rd!!"})
    assert r.status_code == 422, r.text
    assert "username" in r.text


# --- B-OPS-6: one record that fails is skipped, never the whole file -------

_IPS = ("10.88.0.1", "10.88.0.2", "10.88.0.3")
_BAD = "10.88.0.2"


def _files():
    from app.parsers.bloodhound_parser import BloodHoundParser
    from app.parsers.netexec_parser import NetexecParser
    from app.parsers.nikto_parser import NiktoParser
    from app.parsers.rustscan_parser import RustScanParser
    from app.parsers.smbmap_parser import SMBMapParser

    return {
        "rustscan": (RustScanParser, "r.txt", "".join(f"Open {ip}:22\n" for ip in _IPS)),
        "smbmap": (SMBMapParser, "s.txt", "".join(
            f"[+] IP: {ip}:445\tName: h{n}\tStatus: Authenticated\n" for n, ip in enumerate(_IPS))),
        "bloodhound": (BloodHoundParser, "b.json", json.dumps({"computers": [
            {"Properties": {"name": f"H{n}.CORP", "ipv4": ip}} for n, ip in enumerate(_IPS)]})),
        "nikto": (NiktoParser, "n.json", json.dumps([
            {"ip": ip, "port": "80", "vulnerabilities": [{"id": "999", "msg": "A test finding", "url": "/x", "method": "GET"}]}
            for ip in _IPS])),
        "netexec": (NetexecParser, "x.txt", "".join(
            f"SMB         {ip}        445    HOST-{n}           [*] Windows Server 2019 Build 17763 x64 "
            f"(name:HOST-{n}) (domain:corp.local) (signing:True) (SMBv1:False)\n" for n, ip in enumerate(_IPS))),
    }


@pytest.mark.parametrize("tool", ["rustscan", "smbmap", "bloodhound", "nikto", "netexec"])
def test_a_record_the_database_refuses_is_skipped_and_the_rest_imported(tool, db_session, test_project, tmp_path,
                                                                     monkeypatch):
    """A database error ABORTS the transaction: without a savepoint around
    the record, every later statement fails and the import fails after
    "skipping" it.  Each of these five failed the whole file."""
    from sqlalchemy import text

    from app.db.models import Host
    from app.services.host_deduplication_service import HostDeduplicationService

    real = HostDeduplicationService.find_or_create_host

    def refuse_one(self, ip_address, *a, **kw):
        if ip_address == _BAD:
            self.db.execute(text("SELECT 1/0"))  # a real database error
        return real(self, ip_address, *a, **kw)

    monkeypatch.setattr(HostDeduplicationService, "find_or_create_host", refuse_one)
    parser_class, name, body = _files()[tool]
    path = tmp_path / name
    path.write_text(body)
    parser = parser_class(db_session)
    parser.parse_file(str(path), name, project_id=test_project.id)
    db_session.commit()

    ips = {ip for (ip,) in db_session.query(Host.ip_address).filter(Host.ip_address.in_(_IPS))}
    assert ips == set(_IPS) - {_BAD}
    stats = parser.last_parse_stats
    assert stats["skipped"] >= 1 and stats["partial"] is True, stats


# --- B-OPS-4: one command bumps a version in all four places ---------------

def _bump_script():
    import importlib.util
    import pathlib

    here = pathlib.Path(__file__).resolve()
    for candidate in (here.parents[1] / "scripts", here.parents[2] / "scripts"):
        if (candidate / "bump-version.py").is_file():
            spec = importlib.util.spec_from_file_location("bump_version", candidate / "bump-version.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.skip("scripts/ is not mounted here")


def _tree(tmp_path, backend="2.5.9", frontend="5.4.1", compose_backend=None):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "platform_version.json").write_text(json.dumps({"backend": backend, "frontend": frontend}, indent=2) + "\n")
    (tmp_path / "frontend" / "package.json").write_text(
        json.dumps({"name": "f", "version": frontend, "dependencies": {"x": "1.0.0"}}, indent=2) + "\n")
    (tmp_path / "frontend" / "package-lock.json").write_text(json.dumps({
        "name": "f", "version": frontend, "lockfileVersion": 3,
        "packages": {"": {"name": "f", "version": frontend},
                     "node_modules/same": {"version": frontend}},
    }, indent=2) + "\n")
    cb = compose_backend or backend
    # The real file's shape since R10: the frontend image's two build args only.
    (tmp_path / "docker-compose.yml").write_text(
        f"BACKEND_VERSION: ${{APP_VERSION:-{cb}}}\nFRONTEND_VERSION: ${{FRONTEND_VERSION:-{frontend}}}\n")
    # The release notes (2.525.0): the real file's layout, one release.
    notes = tmp_path / "backend" / "app" / "data"
    notes.mkdir(parents=True)
    (notes / "release_notes.json").write_text(
        '{\n  "_about": "x",\n  "releases": [\n'
        f'    {{\n      "backend": "{backend}",\n      "frontend": "{frontend}",\n'
        '      "date": "2026-01-01",\n      "entries": [\n'
        '        {"kind": "fixed", "text": "Something."}\n      ]\n    }\n  ]\n}\n')
    return tmp_path


def test_the_bump_script_starts_the_releases_notes(tmp_path):
    """2.525.0 — the fifth place: an empty entry for the new pair, at the top,
    the file's layout kept; a pair that already has one is left alone."""
    from app.services import release_notes

    script = _bump_script()
    root = _tree(tmp_path)
    path = root / "backend" / "app" / "data" / "release_notes.json"
    assert script.main(["--root", str(root), "--backend", "minor"]) == 0
    releases = release_notes.check(json.loads(path.read_text()))
    assert [(r["backend"], r["frontend"], r["entries"]) for r in releases] == [
        ("2.6.0", "5.4.1", []), ("2.5.9", "5.4.1", [{"kind": "fixed", "text": "Something."}]),
    ]
    assert '{"kind": "fixed", "text": "Something."}' in path.read_text()   # untouched lines keep their layout

    # Bumped back to a pair the notes already have: nothing added.
    (root / "platform_version.json").write_text(json.dumps({"backend": "2.5.9", "frontend": "5.4.0"}) + "\n")
    for name in ("package.json", "package-lock.json"):
        p = root / "frontend" / name
        p.write_text(p.read_text().replace('"version": "5.4.1"', '"version": "5.4.0"', 2))
    compose = root / "docker-compose.yml"
    compose.write_text(compose.read_text().replace("2.6.0", "2.5.9").replace("5.4.1", "5.4.0"))
    before = path.read_text()
    assert script.main(["--root", str(root), "--frontend", "patch"]) == 0
    assert path.read_text() == before


def test_the_bump_script_changes_every_place_and_nothing_else(tmp_path):
    script = _bump_script()
    root = _tree(tmp_path)
    assert script.main(["--root", str(root), "--backend", "minor", "--frontend", "patch"]) == 0
    assert json.loads((root / "platform_version.json").read_text()) == {"backend": "2.6.0", "frontend": "5.4.2"}
    assert json.loads((root / "frontend" / "package.json").read_text())["version"] == "5.4.2"
    lock = json.loads((root / "frontend" / "package-lock.json").read_text())
    assert (lock["version"], lock["packages"][""]["version"]) == ("5.4.2", "5.4.2")
    assert lock["packages"]["node_modules/same"]["version"] == "5.4.1"   # a dependency that shares the number
    compose = (root / "docker-compose.yml").read_text()
    assert compose.count("${APP_VERSION:-2.6.0}") == 1 and compose.count("${FRONTEND_VERSION:-5.4.2}") == 1
    assert script.bump("2.9.9", "major") == "3.0.0"


def test_the_bump_script_refuses_a_tree_that_already_disagrees(tmp_path):
    script = _bump_script()
    root = _tree(tmp_path, compose_backend="2.5.8")
    before = {p: p.read_text() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as refused:
        script.main(["--root", str(root), "--backend", "patch"])
    assert "APP_VERSION" in str(refused.value)
    assert {p: p.read_text() for p in root.rglob("*") if p.is_file()} == before
