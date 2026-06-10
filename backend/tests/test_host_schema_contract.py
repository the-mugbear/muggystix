"""Contract test: the Host response schema must not strip computed review/
attention fields.

Regression guard for the code-review finding that ``conflict_count`` and
``changed_recently`` were computed in the list endpoint but absent from the
``Host`` response model, so ``response_model`` silently dropped them and the
frontend badges never rendered.  Pure (no DB).
"""
from app.schemas.schemas import Host


def test_host_schema_retains_computed_fields():
    h = Host(
        id=1,
        ip_address="10.0.0.1",
        conflict_count=3,
        changed_recently=True,
        finding_count=2,
        other_reviewers=[{"user_id": 3, "name": "Bob"}],
        reviewed_by=[{"user_id": 2, "name": "Alice"}],
        team_review_status="reviewed",
    )
    dumped = h.model_dump()
    # The two fields that were being stripped:
    assert dumped["conflict_count"] == 3
    assert dumped["changed_recently"] is True
    # Team-shared review state on the row:
    assert dumped["team_review_status"] == "reviewed"
    assert dumped["reviewed_by"][0]["name"] == "Alice"
    assert dumped["other_reviewers"][0]["name"] == "Bob"


def test_host_schema_defaults():
    h = Host(id=1, ip_address="10.0.0.2")
    dumped = h.model_dump()
    assert dumped["conflict_count"] == 0
    assert dumped["changed_recently"] is False
    assert dumped["reviewed_by"] == []
    assert dumped["team_review_status"] is None
    # Redesigned-table inputs default safely too.
    assert dumped["exploitable_count"] == 0
    assert dumped["primary_subnet"] is None
    assert dumped["primary_site"] is None
    assert dumped["first_seen"] is None
    assert dumped["last_seen"] is None


def test_host_schema_table_redesign_fields():
    h = Host(
        id=1,
        ip_address="10.0.0.3",
        exploitable_count=4,
        primary_subnet="10.0.0.0/24",
        primary_site="DC-East",
    )
    dumped = h.model_dump()
    assert dumped["exploitable_count"] == 4
    assert dumped["primary_subnet"] == "10.0.0.0/24"
    assert dumped["primary_site"] == "DC-East"
