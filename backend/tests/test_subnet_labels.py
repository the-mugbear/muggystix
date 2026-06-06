"""Subnet-label feature tests (v2.86.0).

Covers the test list the planning review asked for explicitly:
    * label CRUD authorization
    * duplicate names per project (409)
    * assignment idempotency (PUT/POST)
    * filtering hosts by one and multiple labels
    * AND behavior across host tags + subnet labels
    * cross-project isolation (a label in project A can't be referenced
      from project B)

The ``client`` fixture authenticates as the persisted admin
``test_user``, who passes ``require_project_role(ANALYST)`` by virtue
of the admin role.  Cross-project isolation tests use a SECOND project
created in-test (no fixture override) and rely on the project-scoped
URL — guessed-cross-project IDs should 404, not 403, because the
endpoint scopes lookups to the project in the URL.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db import models
from app.db.models_project import Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(db_session, slug: str) -> Project:
    p = Project(name=slug, slug=slug, description="iso", is_default=False)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_scope_with_subnet(db_session, project_id: int, cidr: str) -> tuple[int, int]:
    """Return (scope_id, subnet_id) for a freshly-minted scope + subnet."""
    scope = models.Scope(project_id=project_id, name="s", description="")
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr=cidr, description="")
    db_session.add(subnet)
    db_session.commit()
    return scope.id, subnet.id


def _make_host(db_session, project_id: int, ip: str) -> int:
    """Persist a Host and return its id."""
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add(h)
    db_session.commit()
    return h.id


def _map_host_to_subnet(db_session, host_id: int, subnet_id: int) -> None:
    db_session.add(models.HostSubnetMapping(host_id=host_id, subnet_id=subnet_id))
    db_session.commit()


def _base(project_id: int) -> str:
    return f"/api/v1/projects/{project_id}/scopes"


# ---------------------------------------------------------------------------
# Label CRUD
# ---------------------------------------------------------------------------


class TestSubnetLabelCRUD:
    def test_create_list_update_delete(self, client, db_session, test_project):
        # Empty list to start
        r = client.get(f"{_base(test_project.id)}/subnet-labels")
        assert r.status_code == 200, r.text
        assert r.json() == []

        # Create
        r = client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "prod", "color": "red"},
        )
        assert r.status_code == 201, r.text
        label = r.json()
        assert label["name"] == "prod"
        assert label["color"] == "red"
        assert label["subnet_count"] == 0
        assert label["host_count"] == 0
        label_id = label["id"]

        # List now returns the one row
        r = client.get(f"{_base(test_project.id)}/subnet-labels")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["id"] == label_id

        # Rename + recolor
        r = client.patch(
            f"{_base(test_project.id)}/subnet-labels/{label_id}",
            json={"name": "production", "color": "blue"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "production"
        assert r.json()["color"] == "blue"

        # Delete
        r = client.delete(f"{_base(test_project.id)}/subnet-labels/{label_id}")
        assert r.status_code == 204
        assert client.get(f"{_base(test_project.id)}/subnet-labels").json() == []

    def test_duplicate_name_per_project_returns_409(self, client, test_project):
        client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "dup", "color": None},
        )
        r = client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "dup", "color": None},
        )
        assert r.status_code == 409
        assert "already exists" in r.json()["detail"].lower()

    def test_empty_name_rejected(self, client, test_project):
        r = client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "   ", "color": None},
        )
        # Whitespace-only — pydantic accepts (min_length=1 satisfied by spaces),
        # the endpoint strips and rejects with 422.
        assert r.status_code == 422

    def test_update_missing_label_returns_404(self, client, test_project):
        r = client.patch(
            f"{_base(test_project.id)}/subnet-labels/999999",
            json={"name": "x"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


class TestSubnetLabelAssignment:
    def test_attach_detach_single_label(self, client, db_session, test_project):
        _, subnet_id = _make_scope_with_subnet(db_session, test_project.id, "10.0.0.0/24")
        label_id = client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "L1", "color": None},
        ).json()["id"]

        # Attach
        r = client.post(f"{_base(test_project.id)}/subnets/{subnet_id}/labels/{label_id}")
        assert r.status_code == 200
        assert r.json()["id"] == label_id

        # Idempotent — attaching again is a no-op (no 409, still returns the chip)
        r = client.post(f"{_base(test_project.id)}/subnets/{subnet_id}/labels/{label_id}")
        assert r.status_code == 200

        # Detach
        r = client.delete(f"{_base(test_project.id)}/subnets/{subnet_id}/labels/{label_id}")
        assert r.status_code == 204

        # Detaching a not-attached label is also idempotent (204, not 404)
        r = client.delete(f"{_base(test_project.id)}/subnets/{subnet_id}/labels/{label_id}")
        assert r.status_code == 204

    def test_replace_subnet_labels_idempotent(self, client, db_session, test_project):
        _, subnet_id = _make_scope_with_subnet(db_session, test_project.id, "10.0.1.0/24")
        l1 = client.post(f"{_base(test_project.id)}/subnet-labels", json={"name": "a"}).json()["id"]
        l2 = client.post(f"{_base(test_project.id)}/subnet-labels", json={"name": "b"}).json()["id"]
        l3 = client.post(f"{_base(test_project.id)}/subnet-labels", json={"name": "c"}).json()["id"]

        # First PUT: {l1, l2}
        r = client.put(
            f"{_base(test_project.id)}/subnets/{subnet_id}/labels",
            json={"label_ids": [l1, l2]},
        )
        assert r.status_code == 200
        ids_now = {row["id"] for row in r.json()}
        assert ids_now == {l1, l2}

        # Second PUT with same set is a no-op (still returns {l1, l2})
        r = client.put(
            f"{_base(test_project.id)}/subnets/{subnet_id}/labels",
            json={"label_ids": [l2, l1]},
        )
        assert {row["id"] for row in r.json()} == {l1, l2}

        # Replace with {l2, l3}: drops l1, keeps l2, adds l3
        r = client.put(
            f"{_base(test_project.id)}/subnets/{subnet_id}/labels",
            json={"label_ids": [l2, l3]},
        )
        assert {row["id"] for row in r.json()} == {l2, l3}

        # Empty set detaches everything
        r = client.put(
            f"{_base(test_project.id)}/subnets/{subnet_id}/labels",
            json={"label_ids": []},
        )
        assert r.json() == []

    def test_bulk_apply_one_label_across_subnets(self, client, db_session, test_project):
        _, s1 = _make_scope_with_subnet(db_session, test_project.id, "10.10.1.0/24")
        scope2 = models.Scope(project_id=test_project.id, name="s2", description="")
        db_session.add(scope2)
        db_session.flush()
        s2 = models.Subnet(scope_id=scope2.id, cidr="10.10.2.0/24", description="")
        s3 = models.Subnet(scope_id=scope2.id, cidr="10.10.3.0/24", description="")
        db_session.add_all([s2, s3])
        db_session.commit()

        label_id = client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "bulk", "color": None},
        ).json()["id"]

        r = client.post(
            f"{_base(test_project.id)}/subnet-labels/{label_id}/subnets",
            json={"subnet_ids": [s1, s2.id, s3.id]},
        )
        assert r.status_code == 200
        assert r.json()["subnet_count"] == 3

        # Re-applying the same set is a no-op
        r = client.post(
            f"{_base(test_project.id)}/subnet-labels/{label_id}/subnets",
            json={"subnet_ids": [s1, s2.id]},
        )
        assert r.status_code == 200
        assert r.json()["subnet_count"] == 3  # still 3, didn't go down


# ---------------------------------------------------------------------------
# Cross-project isolation
# ---------------------------------------------------------------------------


class TestCrossProjectIsolation:
    def test_label_id_from_other_project_is_404(self, client, db_session, test_project):
        # Build a second project + a label inside it (directly via the
        # session — there's no project-membership check overriding the
        # admin role for these endpoints, so we'd otherwise be able to
        # POST into proj_b through the same client).
        proj_b = _make_project(db_session, "iso-b")
        b_label = models.SubnetLabel(project_id=proj_b.id, name="b-only", color=None)
        db_session.add(b_label)
        db_session.commit()

        # Try to fetch / mutate b's label through proj_a's URL prefix.
        r = client.patch(
            f"{_base(test_project.id)}/subnet-labels/{b_label.id}",
            json={"name": "stolen"},
        )
        assert r.status_code == 404

        r = client.delete(f"{_base(test_project.id)}/subnet-labels/{b_label.id}")
        assert r.status_code == 404

    def test_subnet_id_from_other_project_is_404(self, client, db_session, test_project):
        proj_b = _make_project(db_session, "iso-c")
        _, b_subnet_id = _make_scope_with_subnet(db_session, proj_b.id, "192.168.50.0/24")

        # Create a label in proj_a — should not be attachable to a
        # subnet that lives in proj_b.
        a_label_id = client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "a-only"},
        ).json()["id"]

        r = client.post(
            f"{_base(test_project.id)}/subnets/{b_subnet_id}/labels/{a_label_id}",
        )
        assert r.status_code == 404

    def test_list_labels_only_returns_project_scoped_rows(
        self, client, db_session, test_project,
    ):
        # Create one label in test_project + one in a second project.
        client.post(
            f"{_base(test_project.id)}/subnet-labels",
            json={"name": "mine"},
        )
        proj_b = _make_project(db_session, "iso-d")
        db_session.add(models.SubnetLabel(project_id=proj_b.id, name="theirs", color=None))
        db_session.commit()

        # List from test_project should not surface "theirs".
        rows = client.get(f"{_base(test_project.id)}/subnet-labels").json()
        names = {r["name"] for r in rows}
        assert names == {"mine"}


# ---------------------------------------------------------------------------
# Host inventory filter
# ---------------------------------------------------------------------------


class TestHostsFilterBySubnetLabel:
    def _setup(self, db_session, project_id):
        """Two subnets, two hosts each; one host overlaps both subnets.
        Returns (label_a, label_b, host_in_a_only, host_in_b_only, host_in_both)."""
        _, s_a = _make_scope_with_subnet(db_session, project_id, "10.20.1.0/24")
        _, s_b = _make_scope_with_subnet(db_session, project_id, "10.20.2.0/24")

        h_a_only = _make_host(db_session, project_id, "10.20.1.10")
        h_b_only = _make_host(db_session, project_id, "10.20.2.10")
        h_both = _make_host(db_session, project_id, "10.20.99.10")

        _map_host_to_subnet(db_session, h_a_only, s_a)
        _map_host_to_subnet(db_session, h_b_only, s_b)
        _map_host_to_subnet(db_session, h_both, s_a)
        _map_host_to_subnet(db_session, h_both, s_b)

        # Create labels and attach.
        label_a = models.SubnetLabel(project_id=project_id, name="A", color="red")
        label_b = models.SubnetLabel(project_id=project_id, name="B", color="blue")
        db_session.add_all([label_a, label_b])
        db_session.flush()
        db_session.add_all([
            models.SubnetLabelAssignment(subnet_id=s_a, label_id=label_a.id),
            models.SubnetLabelAssignment(subnet_id=s_b, label_id=label_b.id),
        ])
        db_session.commit()
        return label_a.id, label_b.id, h_a_only, h_b_only, h_both

    def test_filter_by_one_label(self, client, db_session, test_project):
        label_a, _, h_a_only, _, h_both = self._setup(db_session, test_project.id)
        r = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/",
            params={"subnet_labels": str(label_a), "include_total": "true"},
        )
        assert r.status_code == 200, r.text
        ids = {h["id"] for h in r.json()["items"]}
        assert ids == {h_a_only, h_both}

    def test_filter_by_multiple_labels_or_semantics(self, client, db_session, test_project):
        label_a, label_b, h_a_only, h_b_only, h_both = self._setup(db_session, test_project.id)
        r = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/",
            params={"subnet_labels": f"{label_a},{label_b}"},
        )
        ids = {h["id"] for h in r.json()["items"]}
        assert ids == {h_a_only, h_b_only, h_both}

    def test_and_combinator_with_host_tags(self, client, db_session, test_project):
        """Host-tag filter and subnet-label filter intersect (AND).

        Setup: h_both is in label_A's subnet AND carries tag T; h_a_only
        is in label_A's subnet but has no tag.  Filtering by both should
        return only h_both.
        """
        label_a, _, h_a_only, _, h_both = self._setup(db_session, test_project.id)

        # Create a host tag and attach only to h_both.
        tag = models.HostTag(project_id=test_project.id, name="T", color=None)
        db_session.add(tag)
        db_session.flush()
        db_session.add(models.HostTagAssignment(host_id=h_both, tag_id=tag.id))
        db_session.commit()

        r = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/",
            params={"subnet_labels": str(label_a), "tags": str(tag.id)},
        )
        ids = {h["id"] for h in r.json()["items"]}
        assert ids == {h_both}, "AND between filter groups should drop the un-tagged host"

    def test_filter_data_distinct_host_count(self, client, db_session, test_project):
        """/hosts/filters/data must COUNT DISTINCT host_id per label.

        Setup: label_a is on subnet S; host H is mapped to S twice (via
        S itself and via a second subnet that ALSO carries label_a).
        Naive COUNT(assignment) would say host_count=2; the right answer
        is 1."""
        _, s_a = _make_scope_with_subnet(db_session, test_project.id, "10.30.1.0/24")
        scope2 = models.Scope(project_id=test_project.id, name="s2", description="")
        db_session.add(scope2); db_session.flush()
        s_a2 = models.Subnet(scope_id=scope2.id, cidr="10.30.2.0/24", description="")
        db_session.add(s_a2); db_session.commit()
        s_a2_id = s_a2.id  # capture before any expire (savepoint rollback resets it)
        host = _make_host(db_session, test_project.id, "10.30.1.5")
        _map_host_to_subnet(db_session, host, s_a)
        _map_host_to_subnet(db_session, host, s_a2_id)
        label = models.SubnetLabel(project_id=test_project.id, name="overlap", color=None)
        db_session.add(label); db_session.flush()
        db_session.add_all([
            models.SubnetLabelAssignment(subnet_id=s_a, label_id=label.id),
            models.SubnetLabelAssignment(subnet_id=s_a2_id, label_id=label.id),
        ])
        db_session.commit()

        r = client.get(f"/api/v1/projects/{test_project.id}/hosts/filters/data")
        assert r.status_code == 200
        labels_block = r.json().get("subnet_labels", [])
        ours = next((l for l in labels_block if l["id"] == label.id), None)
        assert ours is not None, f"label not in filter-data response: {labels_block}"
        assert ours["host_count"] == 1, "DISTINCT host_id required; got naive assignment count"

    def test_filter_by_label_from_other_project_returns_no_hosts(
        self, client, db_session, test_project,
    ):
        """Defense-in-depth: a guessed label ID from another project must
        not leak any hosts back, even if the IDs collide numerically.
        """
        _, mine_subnet = _make_scope_with_subnet(db_session, test_project.id, "10.40.1.0/24")
        host = _make_host(db_session, test_project.id, "10.40.1.5")
        _map_host_to_subnet(db_session, host, mine_subnet)

        proj_b = _make_project(db_session, "iso-filter")
        b_label = models.SubnetLabel(project_id=proj_b.id, name="b", color=None)
        db_session.add(b_label); db_session.commit()

        # Even though host is mapped to subnets in this project, the
        # label belongs to proj_b — the join must drop the result.
        r = client.get(
            f"/api/v1/projects/{test_project.id}/hosts/",
            params={"subnet_labels": str(b_label.id)},
        )
        assert r.status_code == 200
        assert r.json()["items"] == [], "cross-project label IDs must not leak hosts"
