"""The report workspace's override rules (owner, 2026-10-14; the proposal's
"What a field becomes", revised after its second review).

* A detail the author changed back to exactly what the draft follows follows
  again — but only a detail named in `PATCH`'s `edited`: every other stored
  override is kept as it is, even one that HAPPENS to equal the project's
  value now (the B → B → C case: the project moving to C later must not
  reach a draft that chose B).
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


def test_an_override_the_author_did_not_touch_survives_the_project_catching_up(client, test_project):
    client.put(f"{_base(test_project)}/profile", json={"classification": "A", "template": "pentest"})
    draft = _create(client, test_project)
    _patch(client, test_project, draft["id"], settings={"classification": "B"}, edited=["classification"])
    # The project changes to B: the draft's own B equals it now.
    client.patch(f"{_base(test_project)}/profile", json={"classification": "B"})
    # A title-only save sends the stored overrides and edits nothing else.
    after = _patch(client, test_project, draft["id"], title="Renamed",
                   settings={"classification": "B"}, edited=[])
    assert after["settings"] == {"classification": "B"}
    # The project moves on to C: the draft still prints its own B.
    client.patch(f"{_base(test_project)}/profile", json={"classification": "C"})
    assert client.get(f"{_base(test_project)}/{draft['id']}").json()["effective"]["classification"] == "B"


def test_an_edited_detail_changed_back_to_the_projects_follows_again(client, test_project):
    client.put(f"{_base(test_project)}/profile", json={
        "client_name": "Example Corp", "distribution": [{"name": "CISO", "email": "ciso@example.com"}],
        "template": "pentest"})
    draft = _create(client, test_project)
    _patch(client, test_project, draft["id"], settings={"client_name": "Other"}, edited=["client_name"])
    back = _patch(client, test_project, draft["id"],
                  settings={"client_name": "  Example Corp ", "distribution": [{"name": "CISO", "email": "ciso@example.com"}]},
                  edited=["client_name", "distribution"])
    assert back["settings"] == {}                                   # both follow the project again
    # The same values WITHOUT `edited` are kept as overrides (the older page's behaviour).
    kept = _patch(client, test_project, draft["id"], settings={"client_name": "Example Corp"})
    assert kept["settings"] == {"client_name": "Example Corp"}


def test_an_empty_list_override_is_kept_even_when_the_project_lists_nobody_too(client, test_project):
    client.put(f"{_base(test_project)}/profile", json={"distribution": [], "template": "pentest"})
    draft = _create(client, test_project)
    saved = _patch(client, test_project, draft["id"], settings={"distribution": []}, edited=["distribution"])
    assert saved["settings"] == {"distribution": []}


def test_an_unknown_edited_key_is_refused(client, test_project):
    draft = _create(client, test_project)
    r = client.patch(f"{_base(test_project)}/{draft['id']}", json={"settings": {}, "edited": ["nope"]})
    assert r.status_code == 422 and "nope" in r.text


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
