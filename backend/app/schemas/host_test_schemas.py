"""The same host-test contract for people and agents."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Raw output kept per evidence record.  ``agent_evidence_service`` holds the
#: rule (and re-exports this); it lives here so the schema does not import a
#: service.
RAW_OUTPUT_MAX_BYTES = 5 * 1024 * 1024

TestStatus = Literal["proposed", "in_progress", "done", "dismissed"]
TestPriority = Literal["critical", "high", "medium", "low", "info"]


class TestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=10_000)
    command: Optional[str] = Field(None, max_length=10_000)
    expected_result: Optional[str] = Field(None, max_length=10_000)
    references: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("references")
    @classmethod
    def safe_references(cls, values):
        if any(len(v) > 2048 or not v.lower().startswith(("https://", "http://")) for v in values):
            raise ValueError("references must be HTTP(S) URLs of at most 2048 characters")
        return values

    @field_validator("*", mode="before")
    @classmethod
    def no_nul(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("NUL is not allowed in text")
        return value


class HostTestCreate(TestSpec):
    request_key: str = Field(..., min_length=1, max_length=100, description="Stable unique key for this test; reuse on retries only.")
    host_id: int = Field(..., gt=0)
    target_fqdn: Optional[str] = Field(None, max_length=253)
    rationale: str = Field(..., min_length=1, max_length=10_000)
    priority: TestPriority = "medium"
    label: Optional[str] = Field(None, max_length=255)
    assigned_to_id: Optional[int] = Field(None, gt=0)
    vulnerability_id: Optional[int] = Field(
        None, gt=0,
        description="The scanner observation on this host that the test is meant to confirm "
                    "(a vulnerability id from the host's detail). Links the test to that weakness.",
    )


class HostTestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tests: list[HostTestCreate] = Field(..., min_length=1, max_length=200)
    agent_model: Optional[str] = Field(None, max_length=100)


class HostTestUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(..., ge=1, description="Revision read by the caller; stale writes return 409.")
    status: Optional[TestStatus] = None
    assigned_to_id: Optional[int] = Field(None, gt=0)
    tester_summary: Optional[str] = Field(None, max_length=10_000)
    dismissed_reason: Optional[str] = Field(None, max_length=2000)

    @field_validator("tester_summary", "dismissed_reason")
    @classmethod
    def no_nul(cls, value):
        if value and "\x00" in value:
            raise ValueError("NUL is not allowed in text")
        return value


class HostTestResult(BaseModel):
    """What a person got when they ran a test (v2.443.0): one call records the
    evidence and moves the test on, so "tested" does not need an agent."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(..., ge=1, description="Revision read by the caller; stale writes return 409.")
    request_key: str = Field(..., min_length=1, max_length=100, description="Stable key for this result; a retry returns the record already stored.")
    outcome: Literal["finding", "no_finding", "inconclusive", "failed"]
    summary: str = Field(..., min_length=1, max_length=10_000)
    command: Optional[str] = Field(None, max_length=10_000, description="The command as run; defaults to the test's.")
    # Review 2026-10-01 R10 — capped by the evidence service (5 MB, a 413),
    # which measures the text BEFORE hashing it.  Deliberately no
    # ``max_length`` here: that turns the documented 413 into a 422 whose
    # body echoes the oversize input back.
    raw_output: Optional[str] = None
    observed_ip: Optional[str] = Field(None, max_length=45)

    # Every text field but ``raw_output``, whose NULs the evidence service
    # removes (tool output carries them; refusing a paste for one byte helps
    # nobody).  ``request_key`` was not covered: a NUL in it reached Postgres
    # and failed the request with a 500.
    @field_validator("request_key", "summary", "command", "observed_ip", mode="before")
    @classmethod
    def no_nul(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("NUL is not allowed in text")
        return value
