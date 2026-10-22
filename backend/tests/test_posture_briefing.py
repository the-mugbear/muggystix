"""The Posture briefing (``GET /reports/posture.html``).

It prints the Security Posture overview's own composition (``compute_posture``)
in the page's order; these pin that order, that what it prints is the page's
answer (the conclusion, the focus sentence), that it escapes what people typed,
that it makes no outbound request, and the download name.

The systemic briefing it replaced (``/reports/systemic.html``) is a 404.
"""
from datetime import datetime, timezone
from urllib.parse import unquote

from app.services.posture_briefing import render_posture_briefing
from app.services.posture_service import compute_posture
from tests.test_report_systemic import estate_with_eol_blind_spot

AT = datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc)

SECTIONS = [
    "Critical / high findings, active", "Where to focus", "Decisions for this review",
    "Every family × site", "Recurring weaknesses", "Promoted findings",
]


def _briefing(db, project, name=None):
    posture = compute_posture(db, project.id, use_cache=False)
    return posture, render_posture_briefing(posture, project_name=name or project.name, generated_at=AT)


def test_the_briefing_tells_the_page_story_in_the_page_order(db_session, test_project):
    estate_with_eol_blind_spot(db_session, test_project.id)
    posture, doc = _briefing(db_session, test_project)
    positions = [doc.index(s) for s in SECTIONS]
    assert positions == sorted(positions)
    # The page's own answer, not a second roll-up.
    assert posture["conclusion"]["text"] in doc
    lead = posture["focus"]["lead_family"]
    assert lead is not None
    assert posture["focus"]["families"][lead][0]["sentence"] in doc
    assert "End-of-life operating systems" in doc
    assert "HQ" in doc and "Branch" in doc
    assert "22 October 2026, 09:30 UTC" in doc


def test_no_scope_reads_as_not_comparable_not_a_crash(db_session, test_project):
    _, doc = _briefing(db_session, test_project)
    assert "No scoped subnets" in doc
    assert "Every family ×" not in doc


def test_typed_names_stay_text_and_nothing_is_fetched(db_session, test_project):
    hostile = '<script>alert(1)</script>'
    estate_with_eol_blind_spot(db_session, test_project.id, site_names=(hostile, "Branch"))
    _, doc = _briefing(db_session, test_project, name=hostile)
    assert "<script" not in doc
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in doc
    # A handout: no script, no external stylesheet, font or image.
    assert "src=" not in doc and "<link" not in doc and "url(" not in doc and "@import" not in doc


def test_endpoint_downloads_the_briefing_named_for_the_project(client, db_session, test_project):
    estate_with_eol_blind_spot(db_session, test_project.id)
    test_project.name = "東京, 本社"
    db_session.flush()
    r = client.get(f"/api/v1/projects/{test_project.id}/reports/posture.html")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert "Security posture briefing" in r.text
    header = r.headers["content-disposition"]
    header.encode("latin-1")
    plain, extended = header.split("; filename*=UTF-8''")
    assert plain.startswith("attachment; filename=posture_briefing_") and plain.endswith(".html")
    assert "," not in header and '"' not in header
    assert unquote(extended).startswith("posture_briefing_東京--本社_")


def test_the_systemic_briefing_is_gone(client, test_project):
    assert client.get(f"/api/v1/projects/{test_project.id}/reports/systemic.html").status_code == 404
