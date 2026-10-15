"""The report workspace's override rules (owner, 2026-10-14; the proposal's
"What a field becomes", revised after its second review).

* `PATCH`'s `settings` carries only the details CHANGED: a detail not sent
  keeps what is stored, so a page that read the draft a while ago never
  reverts someone else's override (review 2026-10-14, H1).
* A detail sent equal to exactly what the draft follows follows again — but
  only a detail SENT: every other stored override is kept as it is, even one
  that HAPPENS to equal the project's value now (the B → B → C case: the
  project moving to C later must not reach a draft that chose B).
* An override to an empty list ("this report lists nobody") is chosen on
  purpose and is never dropped.
* `GET …/{id}/followers` counts the OTHER drafts that follow each detail,
  for "Update project defaults…"; an addendum never follows its summary.
"""
from tests.test_client_reports import _base, _create, template_dir  # noqa: F401 — the fixture


def _patch(client, project, rid, **body):
    r = client.patch(f"{_base(project)}/{rid}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_stale_save_never_reverts_an_override_it_did_not_send(client, test_project):
    """H1: A and B have the draft open; B overrides Classification and saves;
    A, who never reloaded, edits Client and saves — B's override stays."""
    client.put(f"{_base(test_project)}/profile", json={"classification": "Project", "template": "pentest"})
    draft = _create(client, test_project)
    _patch(client, test_project, draft["id"], settings={"classification": "B's"})            # B
    after = _patch(client, test_project, draft["id"], settings={"client_name": "A's"})      # A
    assert after["settings"] == {"classification": "B's", "client_name": "A's"}
    # A title-only save changes no detail.
    assert _patch(client, test_project, draft["id"], title="Renamed")["settings"] == after["settings"]


def test_a_detail_sent_null_or_blank_follows_the_project_again(client, test_project):
    draft = _create(client, test_project)
    _patch(client, test_project, draft["id"], settings={"client_name": "Own", "classification": "Own",
                                                        "distribution": [{"name": "CISO"}]})
    after = _patch(client, test_project, draft["id"],
                   settings={"client_name": None, "classification": "  ", "distribution": None})
    assert after["settings"] == {}


def test_an_override_the_author_did_not_touch_survives_the_project_catching_up(client, test_project):
    client.put(f"{_base(test_project)}/profile", json={"classification": "A", "template": "pentest"})
    draft = _create(client, test_project)
    _patch(client, test_project, draft["id"], settings={"classification": "B"})
    # The project changes to B: the draft's own B equals it now.
    client.patch(f"{_base(test_project)}/profile", json={"classification": "B"})
    # A save of another detail leaves it alone.
    after = _patch(client, test_project, draft["id"], title="Renamed", settings={"client_name": "X"})
    assert after["settings"] == {"classification": "B", "client_name": "X"}
    # The project moves on to C: the draft still prints its own B.
    client.patch(f"{_base(test_project)}/profile", json={"classification": "C"})
    assert client.get(f"{_base(test_project)}/{draft['id']}").json()["effective"]["classification"] == "B"


def test_a_detail_sent_back_to_the_projects_follows_again(client, test_project):
    client.put(f"{_base(test_project)}/profile", json={
        "client_name": "Example Corp", "distribution": [{"name": "CISO", "email": "ciso@example.com"}],
        "template": "pentest"})
    draft = _create(client, test_project)
    _patch(client, test_project, draft["id"], settings={"client_name": "Other"})
    back = _patch(client, test_project, draft["id"],
                  settings={"client_name": "  Example Corp ", "distribution": [{"name": "CISO", "email": "ciso@example.com"}]})
    assert back["settings"] == {}                                   # both follow the project again


def test_an_empty_list_override_is_kept_even_when_the_project_lists_nobody_too(client, test_project):
    client.put(f"{_base(test_project)}/profile", json={"distribution": [], "template": "pentest"})
    draft = _create(client, test_project)
    saved = _patch(client, test_project, draft["id"], settings={"distribution": []})
    assert saved["settings"] == {"distribution": []}


def test_followers_count_the_other_drafts_that_follow_each_detail(client, test_project):
    client.put(f"{_base(test_project)}/profile", json={"executive_summary": "S.", "template": "pentest"})
    me = _create(client, test_project)
    follows = _create(client, test_project)
    overrides = _create(client, test_project)
    _patch(client, test_project, overrides["id"], settings={"client_name": "Own", "executive_summary": "Own."})
    counts = client.get(f"{_base(test_project)}/{me['id']}/followers").json()
    assert counts["client_name"] == 1                              # `follows`; not me, not `overrides`
    assert counts["executive_summary"] == 1
    assert counts["classification"] == 2
    assert set(counts) >= {"testers", "distribution", "disclaimer"}
    del follows
