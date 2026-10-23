"""A revision builds its indexes inside the migration's transaction.

``backend-db-and-tests.md``: never ``CONCURRENTLY`` / ``autocommit_block``.
The block commits every earlier revision mid-chain (the schema is stranded
past what the old image runs), an interrupted concurrent build leaves an
INVALID index that ``IF NOT EXISTS`` then skips, and an open snapshot stalls
boot — while nothing else is serving during a boot migration anyway.  That
was a rule to remember (review 2026-10-23, R-DB2); this reads the code of
every revision (its AST: comments and docstrings that explain the rule do not
count) and refuses the three spellings outside the revisions that already
shipped them, which are frozen — they ran on every installation and cannot
be rewritten.
"""
from __future__ import annotations

import ast
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: Revisions that shipped a concurrent build before the rule.  Frozen: this
#: set only ever shrinks (a revision squashed away), never grows.
SHIPPED_BEFORE_THE_RULE = frozenset({
    "b9d3e6a87f04",
    "c3f9a1b7e240",
    "f1d2c3b4a5e6",
})


def _docstring_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                yield body[0].value


def _concurrent_builds(source: str) -> list:
    tree = ast.parse(source)
    docs = {id(node) for node in _docstring_nodes(tree)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "autocommit_block":
            found.append(f"line {node.lineno}: autocommit_block")
        elif isinstance(node, ast.keyword) and node.arg == "postgresql_concurrently":
            found.append(f"line {node.value.lineno}: postgresql_concurrently=")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docs and "CONCURRENTLY" in node.value.upper()):
            found.append(f"line {node.lineno}: CONCURRENTLY in SQL")
    return found


def _revision_id(path: Path, tree_source: str) -> str:
    for node in ast.parse(tree_source).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "revision" for t in targets):
                return node.value.value
    raise AssertionError(f"{path.name} names no revision")


def _offenders():
    out = {}
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text()
        found = _concurrent_builds(source)
        if found:
            out[_revision_id(path, source)] = (path.name, found)
    return out


def test_no_new_revision_builds_an_index_outside_the_transaction():
    new = {rev: v for rev, v in _offenders().items() if rev not in SHIPPED_BEFORE_THE_RULE}
    assert new == {}, (
        "build the index inside the migration's transaction (plain op.create_index / "
        f"CREATE INDEX), never CONCURRENTLY or autocommit_block: {new}"
    )


def test_the_frozen_list_names_only_revisions_that_still_do_it():
    """A stale entry would let a new revision reuse its id unnoticed."""
    assert set(_offenders()) == SHIPPED_BEFORE_THE_RULE


def test_the_reader_sees_each_spelling_and_ignores_the_explanations():
    code = '''
"""Never CONCURRENTLY here."""
from alembic import op
# CREATE INDEX CONCURRENTLY would strand the chain
def upgrade():
    """Not CONCURRENTLY."""
    with op.get_context().autocommit_block():
        op.create_index("ix", "t", ["c"], postgresql_concurrently=True)
    op.execute("create index concurrently ix2 on t (c)")
'''
    found = sorted(f.split(": ", 1)[1] for f in _concurrent_builds(code))
    assert found == ["CONCURRENTLY in SQL", "autocommit_block", "postgresql_concurrently="]
    assert _concurrent_builds('"""CONCURRENTLY is explained here only."""\n') == []
