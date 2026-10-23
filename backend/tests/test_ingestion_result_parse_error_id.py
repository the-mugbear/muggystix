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

    resp = client.get(f"/api/v1/projects/{test_project.id}/ingestion-results")
    assert resp.status_code == 200, resp.text

    row = next((i for i in resp.json()["items"] if i["id"] == job.id), None)
    assert row is not None, "the failed job must appear in ingestion results"
    assert row["parse_error_id"] == parse_error.id, (
        "the ParseError id must be exposed; without it callers address the "
        "detail endpoint with the job id"
    )


def test_deleting_a_parse_error_unlinks_its_jobs(client, db_session, test_project, failed_job_with_parse_error):
    """The FK's SET NULL unlinks the job (review 2026-10-23b, ING-10: the
    route did it again by hand); the answer still counts them."""
    job, parse_error = failed_job_with_parse_error
    resp = client.delete(f"/api/v1/projects/{test_project.id}/parse-errors/{parse_error.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["jobs_updated"] == 1
    db_session.expire_all()
    assert db_session.get(models.IngestionJob, job.id).parse_error_id is None
    assert db_session.get(models.ParseError, parse_error.id) is None


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


def test_a_binary_upload_failure_is_recorded(db_session, test_project):
    """v2.385.0 — a zip's bytes hold NUL, which survived the UTF-8 preview
    and which Postgres refuses in text: recording the failure raised, so the
    job read "Processing failed unexpectedly" instead of its real reason."""
    from app.services.parse_error_service import log_parse_error

    zipped = b"PK\x03\x04\x14\x00\x00\x00\x08\x00Requests.csv" + b"\x00" * 40
    error = log_parse_error(
        db_session, filename="eyewitness.zip", file_content=zipped,
        error=ValueError("EyeWitness zip bundle contained no .json report"),
        file_type="eyewitness_zip", project_id=test_project.id,
    )
    assert error.id is not None
    assert error.error_message == "EyeWitness zip bundle contained no .json report"
    assert "\x00" not in error.file_preview
    assert error.file_preview.startswith("Binary data: 504b0304")


@pytest.mark.parametrize("sort_order", ["asc", "desc"])
def test_rows_that_tie_on_the_sort_column_page_without_repeating_or_skipping(
    client, db_session, test_project, sort_order
):
    """Review 2026-10-10: sorted by a column every row shares (``status``),
    the order had no tiebreaker, so each page read was free to order the ties
    its own way — a row could be on two pages or on none.  The job id breaks
    ties, newest first, in either direction of the sort."""
    for i in range(25):
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename=f"stored-{i}.xml",
            storage_path=f"/app/uploads/ingestion_queue/stored-{i}.xml",
            original_filename=f"scan-{i}.xml", status="completed", tool_name="nmap",
        ))
    db_session.commit()
    ids = [
        row.id for row in db_session.query(models.IngestionJob.id)
        .filter(models.IngestionJob.project_id == test_project.id)
    ]
    assert len(ids) == 25

    paged = []
    for skip in (0, 10, 20):
        page = client.get(
            f"/api/v1/projects/{test_project.id}/ingestion-results",
            params={"sort_by": "status", "sort_order": sort_order, "skip": skip, "limit": 10},
        )
        assert page.status_code == 200, page.text
        assert page.json()["total"] == 25
        paged.extend(item["id"] for item in page.json()["items"])

    assert paged == sorted(ids, reverse=True)
