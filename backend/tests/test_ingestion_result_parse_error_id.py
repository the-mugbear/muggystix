"""
Ingestion results must carry the ParseError id separately from the job id.

`ingestion_jobs.id` and `parse_errors.id` are independent Postgres sequences
that overlap heavily — most ingestions succeed, so job ids climb past the
dense low range parse-error ids occupy. Passing a job id to
`GET /parse-errors/{error_id}` therefore does NOT 404; it returns whichever
parse error happens to share the number, and the UI presents it as that row's
detail. A wrong answer rendered confidently is worse than an error.

The list response used to resolve the parse error server-side and then drop
its id, leaving every caller to guess with `id`.
"""

from datetime import datetime, timezone

import pytest

from app.db import models


@pytest.fixture
def failed_job_with_parse_error(db_session, test_project):
    """A failed ingestion whose job id and parse-error id deliberately differ,
    so a test can't pass by accident when the two happen to coincide."""
    # Burn a couple of parse-error ids so the sequences diverge.
    for i in range(2):
        db_session.add(
            models.ParseError(
                project_id=test_project.id,
                filename=f"decoy-{i}.xml",
                error_type="ParseFailure",
                error_message="decoy",
                user_message="decoy",
            )
        )
    db_session.commit()

    parse_error = models.ParseError(
        project_id=test_project.id,
        filename="truncated.xml",
        error_type="XMLSyntaxError",
        error_message="Incomplete XML at line 4102",
        user_message="The scan file appears truncated.",
    )
    db_session.add(parse_error)
    db_session.commit()
    db_session.refresh(parse_error)

    job = models.IngestionJob(
        project_id=test_project.id,
        filename="stored-truncated.xml",
        storage_path="/app/uploads/ingestion_queue/stored-truncated.xml",
        original_filename="truncated.xml",
        status="failed",
        parse_error_id=parse_error.id,
        error_message="Incomplete XML at line 4102",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job, parse_error


def test_ingestion_results_expose_the_parse_error_id(
    client, test_project, failed_job_with_parse_error
):
    job, parse_error = failed_job_with_parse_error

    resp = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results")
    assert resp.status_code == 200, resp.text

    row = next((i for i in resp.json()["items"] if i["id"] == job.id), None)
    assert row is not None, "the failed job must appear in ingestion results"
    assert row["parse_error_id"] == parse_error.id, (
        "the ParseError id must be exposed; without it callers address the "
        "detail endpoint with the job id"
    )


def test_the_two_ids_are_not_interchangeable(
    client, test_project, failed_job_with_parse_error
):
    """The crux. If a caller substitutes the job id, it must NOT quietly
    resolve to some other project row and render as this file's error."""
    job, parse_error = failed_job_with_parse_error
    assert job.id != parse_error.id, "fixture must diverge the two sequences"

    correct = client.get(
        f"/api/v1/projects/{test_project.id}/parse-errors/{parse_error.id}"
    )
    assert correct.status_code == 200, correct.text
    assert correct.json()["filename"] == "truncated.xml"

    # Using the job id either 404s or returns a DIFFERENT record — never this
    # job's error. Both are failures for the caller; the point is that the
    # value is not a valid substitute.
    wrong = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/{job.id}")
    if wrong.status_code == 200:
        assert wrong.json()["id"] != parse_error.id, (
            "job id collided with an unrelated parse error — exactly the silent "
            "wrong-answer this separation prevents"
        )
    else:
        assert wrong.status_code == 404
