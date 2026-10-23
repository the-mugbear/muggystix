"""Parsers isolate a record ONE way: ``parser_utils.write_record``.

Review 2026-10-22, B1-1: the per-record savepoint existed in about eight
hand-written copies that had drifted — most counted a cancel, a shutdown or a
stall raised inside a record as one skipped record and carried on.
``write_record`` (over ``record_savepoint``) re-raises it, restores the dedup
state and retries a lost race when asked.  This refuses a hand-written copy
coming back: no ``begin_nested()`` and no bare ``record_savepoint(...)`` in a
parser module other than ``parser_utils``, nor in the Nessus integration's
import path (an AST check, so a docstring that names them is fine).

Review 2026-10-23b, ING-8: the services an import writes through had five
more copies (the Nessus vulnerability writes, the dedup service's name
observations), their rollback failures handled three ways.  Those modules
open no ``begin_nested()`` either, except the dedup service's two
concurrent-insert savepoints, which catch the race and write again rather
than isolate a record; a batch that is retried finding by finding may hold
a bare ``record_savepoint``.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_CHECKED = sorted((APP / "parsers").glob("*.py")) + [APP / "services" / "nessus_integration_service.py"]
_HELPER = APP / "parsers" / "parser_utils.py"
_REFUSED = {"begin_nested", "record_savepoint"}

#: Service modules an import writes through: ``begin_nested()`` only in the
#: functions named (the IntegrityError race of find_or_create_*).
_SERVICES = {
    APP / "services" / "vulnerability_service.py": set(),
    APP / "services" / "host_deduplication_service.py": {"_find_or_create_host_inner", "find_or_create_port"},
}


def _called_name(call: ast.Call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def test_no_parser_writes_its_own_savepoint():
    found = []
    for path in _CHECKED:
        if path == _HELPER:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Call) and _called_name(node) in _REFUSED:
                found.append(f"{path.relative_to(APP)}:{node.lineno} {_called_name(node)}()")
    assert found == [], (
        "a skipped record is written with parser_utils.write_record, never a savepoint of its own:\n"
        + "\n".join(found)
    )


def test_the_import_services_write_no_savepoint_of_their_own():
    found = []
    for path, allowed in _SERVICES.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name in allowed:
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and _called_name(node) == "begin_nested":
                    found.append(f"{path.relative_to(APP)}:{node.lineno} in {function.name}()")
    assert sorted(set(found)) == [], (
        "a write an import isolates goes through parser_utils.write_record / record_savepoint:\n"
        + "\n".join(sorted(set(found)))
    )


def test_the_guard_reads_the_parsers():
    names = {p.name for p in _CHECKED}
    assert {"nmap_parser.py", "masscan_parser.py", "parser_utils.py"} <= names
    helper = ast.parse(_HELPER.read_text())
    assert any(isinstance(n, ast.FunctionDef) and n.name == "write_record" for n in helper.body)
    dedup = ast.parse((APP / "services" / "host_deduplication_service.py").read_text())
    defined = {n.name for n in ast.walk(dedup) if isinstance(n, ast.FunctionDef)}
    assert _SERVICES[APP / "services" / "host_deduplication_service.py"] <= defined
