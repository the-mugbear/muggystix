"""Parsers isolate a record ONE way: ``parser_utils.write_record``.

Review 2026-10-22, B1-1: the per-record savepoint existed in about eight
hand-written copies that had drifted — most counted a cancel, a shutdown or a
stall raised inside a record as one skipped record and carried on.
``write_record`` (over ``record_savepoint``) re-raises it, restores the dedup
state and retries a lost race when asked.  This refuses a hand-written copy
coming back: no ``begin_nested()`` and no bare ``record_savepoint(...)`` in a
parser module other than ``parser_utils``, nor in the Nessus integration's
import path (an AST check, so a docstring that names them is fine).
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_CHECKED = sorted((APP / "parsers").glob("*.py")) + [APP / "services" / "nessus_integration_service.py"]
_HELPER = APP / "parsers" / "parser_utils.py"
_REFUSED = {"begin_nested", "record_savepoint"}


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


def test_the_guard_reads_the_parsers():
    names = {p.name for p in _CHECKED}
    assert {"nmap_parser.py", "masscan_parser.py", "parser_utils.py"} <= names
    helper = ast.parse(_HELPER.read_text())
    assert any(isinstance(n, ast.FunctionDef) and n.name == "write_record" for n in helper.body)
