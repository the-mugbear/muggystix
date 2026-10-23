"""A finding's source note (``findings.evidence_annotation_id``) is legacy, read only.

Until 2026-10-01 a host note could be promoted into a finding, which kept a
pointer to the note's thread.  Notes are discussion since then and nothing
writes the pointer.  Owner, 2026-10-23: keep it as a READ-ONLY legacy path
while any finding carries it — the finding page's source thread
(``evidence_host_id``), the client report's images from that thread and the
agents' read still use it.  This refuses a new writer, so "nothing writes it"
stays true in code: no assignment to the attribute or its relationship, no
``Finding(...)`` built with it, no ``.values(...)`` / ``.update({...})`` that
sets it.  (The revision that back-filled it is frozen history and not read.)
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_FIELDS = {"evidence_annotation_id", "evidence_annotation"}


def _writes(tree):
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and t.attr in _FIELDS:
                yield node.lineno, "assignment"
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in ("Finding", "values") and any(k.arg in _FIELDS for k in node.keywords):
                yield node.lineno, f"{name}(evidence_annotation_id=…)"
            if name in ("update", "values"):
                for arg in node.args:
                    if isinstance(arg, ast.Dict) and any(
                        isinstance(k, ast.Attribute) and k.attr in _FIELDS for k in arg.keys
                    ):
                        yield node.lineno, f"{name}({{Finding.evidence_annotation_id: …}})"


def test_nothing_writes_a_findings_source_note():
    found = [
        f"{path.relative_to(APP)}:{line} {what}"
        for path in sorted(APP.rglob("*.py"))
        for line, what in _writes(ast.parse(path.read_text(), filename=str(path)))
    ]
    assert found == [], "findings.evidence_annotation_id is read-only legacy:\n" + "\n".join(found)


def test_the_guard_sees_a_write():
    sample = ast.parse(
        "finding.evidence_annotation_id = 3\n"
        "Finding(title='x', evidence_annotation_id=3)\n"
        "q.update({Finding.evidence_annotation_id: 3})\n"
    )
    assert [what for _line, what in _writes(sample)] == [
        "assignment", "Finding(evidence_annotation_id=…)", "update({Finding.evidence_annotation_id: …})",
    ]
