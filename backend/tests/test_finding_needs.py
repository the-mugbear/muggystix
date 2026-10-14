"""What a finding still needs before the report — ``services/finding_needs``.

The Findings list says on each row what the finding still needs (required
report text missing, a report image without a caption, proposals to decide)
and lists only those with ``needs=``.  Pins:

* each ``needs=`` value lists exactly the rows that say that need, paged
  through with a page smaller than the list, and the severity roll-up and the
  owner counts are the same selection;
* a report image without a caption is one that is ticked "In report", in a
  format the report prints, on a finding the report would include — on the
  finding's own comments or anywhere in its source-note thread; the SQL
  figure equals the report service's own lookup of a finding's images;
* Operations' "Findings that need me" says the three older needs in the same
  words and order as before it called the shared function, and now lists an
  owned finding whose report image has no caption as a ``write``;
* the agents' list is the page's: the same rows' needs, the same filter, the
  same refusal of a word neither understands;
* the report summary warns about the same images;
* a page of findings costs two statements, whatever it holds.
"""
from __future__ import annotations

import json
from typing import get_args

from app.core.config import settings
from app.db import models
from app.db.models import Annotation, NoteAttachment
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import AgentProposal
from app.services import finding_needs, report_images
from app.services.client_report_service import finding_image_attachments

from tests.test_finding_loading import selects

TEXT = dict(description="what it is", impact="why it matters", recommendation="what to do")


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    return host


def _finding(db, project, title, *, status="confirmed", owner_id=None, hosts=(), **over):
    f = Finding(
        project_id=project.id, title=title, severity="high", status=status, source="manual",
        owner_id=owner_id, **{**TEXT, **over},
    )
    db.add(f)
    db.flush()
    for host, host_status in hosts:
        db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status=host_status))
    db.flush()
    return f


def _comment(db, user, **where):
    note = Annotation(user_id=user.id, body="proof", **where)
    db.add(note)
    db.flush()
    return note


def _image(db, project, note, name, *, ticked=True, caption=None, ctype="image/png"):
    db.add(NoteAttachment(
        annotation_id=note.id, project_id=project.id, filename=name, content_type=ctype,
        size_bytes=1, storage_path=f"{note.id}/{name}", include_in_report=ticked, caption=caption,
    ))
    db.flush()


def _seed(db, project, user):
    """Eight findings; the dict names what each row should say."""
    host = _host(db, project, "10.61.0.1")
    other = _host(db, project, "10.61.0.2")
    expected = {}

    done = _finding(db, project, "Nothing owed")
    _image(db, project, _comment(db, user, finding_id=done.id), "captioned.png", caption="The login page")
    expected[done.id] = {}

    text = _finding(db, project, "Impact not written", impact="  \n")
    expected[text.id] = {"missing_text": 1}

    # Its own comments: two ticked without a caption (one holds only spaces);
    # the captioned, the un-ticked and the unprintable ones are not counted.
    own = _finding(db, project, "Images on comments")
    note = _comment(db, user, finding_id=own.id)
    _image(db, project, note, "a.png")
    _image(db, project, note, "b.jpg", caption="   ", ctype="image/jpeg")
    _image(db, project, note, "c.png", caption="A caption")
    _image(db, project, note, "d.png", ticked=False)
    _image(db, project, note, "e.webp", ctype="image/webp")
    expected[own.id] = {"uncaptioned_images": 2}

    # Its source-note thread: the root and a reply.
    root = _comment(db, user, host_id=host.id)
    reply = _comment(db, user, host_id=host.id, thread_root_id=root.id)
    thread = _finding(db, project, "Images on the source thread", evidence_annotation_id=root.id,
                      recommendation=None)
    _image(db, project, root, "root.png")
    _image(db, project, reply, "reply.png")
    expected[thread.id] = {"missing_text": 1, "uncaptioned_images": 2}

    # Not in the report: nothing is owed for it but the decision.
    investigating = _finding(db, project, "Under investigation", status="open", impact=None)
    _image(db, project, _comment(db, user, finding_id=investigating.id), "open.png")
    for _ in range(2):
        db.add(AgentProposal(
            project_id=project.id, kind="finding_text", status="pending", source="agent",
            payload={"value": "x"}, field="impact", finding_id=investigating.id,
        ))
    db.add(AgentProposal(
        project_id=project.id, kind="finding_text", status="rejected", source="agent",
        payload={"value": "x"}, field="impact", finding_id=investigating.id,
    ))
    expected[investigating.id] = {"proposals": 2}

    closed = _finding(db, project, "False positive", status="false_positive", description=None)
    _image(db, project, _comment(db, user, finding_id=closed.id), "fp.png")
    expected[closed.id] = {}

    # Every endpoint judged a false positive: the report leaves it out.
    excluded = _finding(db, project, "No live endpoint", impact=None,
                        hosts=[(host, "false_positive"), (other, "false_positive")])
    _image(db, project, _comment(db, user, finding_id=excluded.id), "gone.png")
    expected[excluded.id] = {}

    # One live endpoint keeps it in.
    kept = _finding(db, project, "One live endpoint",
                    hosts=[(host, "false_positive"), (other, "open")])
    _image(db, project, _comment(db, user, finding_id=kept.id), "kept.png")
    expected[kept.id] = {"uncaptioned_images": 1}

    db.commit()
    return expected


def _page(client, project, **params):
    r = client.get(f"/api/v1/projects/{project.id}/findings", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _all(client, project, **params):
    """The whole list, two rows at a time."""
    rows, offset = [], 0
    while True:
        body = _page(client, project, limit=2, offset=offset, **params)
        rows += body["items"]
        offset += 2
        if offset >= body["total"]:
            return rows, body


def _said(row):
    return {n["kind"]: n["count"] for n in row["needs"]}


def test_a_row_says_what_the_finding_still_needs(client, db_session, test_project, test_user):
    expected = _seed(db_session, test_project, test_user)
    rows, _ = _all(client, test_project)
    assert {r["id"]: _said(r) for r in rows} == expected
    by_title = {r["title"]: [n["text"] for n in r["needs"]] for r in rows}
    assert by_title["Images on the source thread"] == [
        "report text missing: recommendation", "2 report images without a caption",
    ]
    assert by_title["One live endpoint"] == ["1 report image without a caption"]
    # The Status column says "under investigation"; the row does not repeat it.
    assert by_title["Under investigation"] == ["2 proposals to decide"]
    # One finding is not a list row: its page shows each need in its section.
    one = client.get(f"/api/v1/projects/{test_project.id}/findings/{rows[0]['id']}").json()
    assert one["needs"] is None


def test_each_needs_filter_lists_exactly_the_rows_that_say_it(client, db_session, test_project, test_user):
    expected = _seed(db_session, test_project, test_user)
    for word in finding_needs.NEED_FILTERS:
        wanted = {
            fid for fid, said in expected.items()
            if (said if word == finding_needs.ANY_NEED else word in said)
        }
        assert wanted, word          # the fixture exercises every word
        rows, body = _all(client, test_project, needs=word)
        assert {r["id"] for r in rows} == wanted, word
        assert body["total"] == len(rows) == len(wanted), word
        # One selection: the roll-up and the Owner filter count the same list.
        assert sum(body["severity_counts"].values()) == body["total"], word
        assert sum(c["count"] for c in body["owner_counts"]) == body["total"], word
    # …and it combines with the other filters.
    assert _page(client, test_project, needs="any", status="active")["total"] == 5
    assert _page(client, test_project, needs="any", status="open")["total"] == 1


def test_the_uncaptioned_figure_is_the_report_services_own_lookup(db_session, test_project, test_user):
    _seed(db_session, test_project, test_user)
    findings = db_session.query(Finding).filter(Finding.project_id == test_project.id).all()
    attached = finding_image_attachments(
        db_session, [(f.id, f.evidence_annotation_id) for f in findings], marked_only=True,
    )
    reportable = {
        fid for (fid,) in db_session.query(Finding.id).filter(
            finding_needs.need_condition("uncaptioned_images") | ~finding_needs.need_condition("uncaptioned_images")
        )
    }
    assert reportable == {f.id for f in findings}     # the condition is never NULL
    needs = finding_needs.needs_of(db_session, [f.id for f in findings])
    listed = {
        fid for (fid,) in db_session.query(Finding.id).filter(
            finding_needs.need_condition("uncaptioned_images"))
    }
    for f in findings:
        by_lookup = sum(
            1 for att, _actor in attached.get(f.id, [])
            if report_images.printable(att) and report_images.caption_missing(att)
        )
        figure = needs[f.id].uncaptioned_images
        # Never more than the lookup finds; all of them when the report takes the finding.
        assert figure in (0, by_lookup), f.title
        assert (figure > 0) == (f.id in listed), f.title
    assert sum(n.uncaptioned_images for n in needs.values()) == 5


def test_a_page_of_needs_is_two_statements(db_session, test_project, test_user):
    _seed(db_session, test_project, test_user)
    for i in range(30):
        f = _finding(db_session, test_project, f"Bulk {i}", impact=None)
        _image(db_session, test_project, _comment(db_session, test_user, finding_id=f.id), f"{i}.png")
    db_session.commit()
    ids = [fid for (fid,) in db_session.query(Finding.id).filter(Finding.project_id == test_project.id)]
    with selects(db_session) as statements:
        needs = finding_needs.needs_of(db_session, ids)
    assert len(statements) == 2, statements
    assert len(needs) == len(ids) == 38


def test_operations_says_the_older_needs_as_before_and_lists_a_missing_caption(
    client, db_session, test_project, test_user,
):
    mine = dict(owner_id=test_user.id)
    # Under investigation with a proposal: the sentences and their order are
    # the ones this list wrote itself before `finding_needs`.
    decide = _finding(db_session, test_project, "Decide", status="open", **mine)
    db_session.add(AgentProposal(
        project_id=test_project.id, kind="finding_text", status="pending", source="agent",
        payload={"value": "x"}, field="impact", finding_id=decide.id,
    ))
    _finding(db_session, test_project, "Write", description=None, impact=" ", **mine)
    caption = _finding(db_session, test_project, "Caption", **mine)
    _image(db_session, test_project, _comment(db_session, test_user, finding_id=caption.id), "shot.png")
    _finding(db_session, test_project, "Written up", **mine)          # needs nothing: not listed
    db_session.commit()

    body = client.get(f"/api/v1/projects/{test_project.id}/workbench/findings").json()
    said = {i["title"]: [(n["kind"], n["text"]) for n in i["needs"]] for i in body["items"]}
    assert said == {
        "Decide": [("under_investigation", "under investigation"), ("proposals", "1 proposal to decide")],
        "Write": [("missing_text", "report text missing: description, impact")],
        "Caption": [("uncaptioned_images", "1 report image without a caption")],
    }
    assert body["need_counts"] == {"decide": 1, "write": 2, "retest": 0} and body["total_open"] == 3
    by_title = {i["title"]: i for i in body["items"]}
    assert by_title["Write"]["missing_text"] == ["description", "impact"]
    assert by_title["Caption"]["uncaptioned_images"] == 1
    assert by_title["Decide"]["pending_proposals"] == 1
    written = client.get(
        f"/api/v1/projects/{test_project.id}/workbench/findings", params={"need": "write"},
    ).json()
    assert {i["title"] for i in written["items"]} == {"Write", "Caption"}


def test_the_agents_list_is_the_pages(client, db_session, test_project, test_user):
    _seed(db_session, test_project, test_user)
    started = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "finding needs"},
    )
    assert started.status_code == 201, started.text
    headers = {"X-API-Key": started.json()["api_key"]}

    for word in (None, *finding_needs.NEED_FILTERS):
        params = {"limit": 100, **({"needs": word} if word else {})}
        agent = client.get("/api/v1/agent/assist/findings", headers=headers, params=params)
        assert agent.status_code == 200, agent.text
        page = _page(client, test_project, **params)
        assert agent.json()["total"] == page["total"], word
        assert {r["id"]: r["needs"] for r in agent.json()["items"]} == {
            r["id"]: r["needs"] for r in page["items"]
        }, word

    # A word neither understands is refused by both, in the same words.
    agent = client.get("/api/v1/agent/assist/findings", headers=headers, params={"needs": "captions"})
    page = client.get(f"/api/v1/projects/{test_project.id}/findings", params={"needs": "captions"})
    assert agent.status_code == page.status_code == 422
    assert agent.json()["detail"][0]["loc"] == page.json()["detail"][0]["loc"] == ["query", "needs"]

    from app.api.params import FindingNeedsParam
    from app.main import app

    def needs_param(path):
        (param,) = [p for p in app.openapi()["paths"][path]["get"]["parameters"] if p["name"] == "needs"]
        return param

    assert needs_param("/api/v1/agent/assist/findings") == needs_param(
        "/api/v1/projects/{project_id}/findings"
    )
    literal = get_args(get_args(FindingNeedsParam)[0])[0]
    assert get_args(literal) == finding_needs.NEED_FILTERS


def test_the_report_summary_warns_about_the_same_images(
    client, db_session, test_project, test_user, tmp_path, monkeypatch,
):
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx"],
    }))
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(folder.parent))
    expected = _seed(db_session, test_project, test_user)

    def warned():
        r = client.post(f"/api/v1/projects/{test_project.id}/client-reports", json={"kind": "full"})
        assert r.status_code == 201, r.text
        rows = r.json()["summary"]["uncaptioned_images"]
        assert all(w["ref"] and w["title"] for w in rows)
        return {w["id"]: w["count"] for w in rows}

    # A template that prints each finding's images: the Findings list's figures.
    (folder / "report.qmd").write_text(
        "---\ntitle: x\n---\n<% for f in findings %><% for e in f.evidence %>\n<< image(e) >>\n"
        "<% endfor %><% endfor %>\n"
    )
    assert warned() == {
        fid: said["uncaptioned_images"] for fid, said in expected.items() if "uncaptioned_images" in said
    }
    # A template that prints no image prints no file name either.
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    assert warned() == {}
