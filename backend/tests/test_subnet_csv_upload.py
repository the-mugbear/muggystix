"""Tests for the labelled-CSV subnet upload (/scopes/upload-subnets).

Pins the headline merge/accumulate behaviour that's easiest to regress:
subnets dedup by cidr, labels are ADDED not replaced across re-uploads, an
intra-file duplicate cidr merges its labels onto one subnet, and column 3
populates/updates the description.
"""
from __future__ import annotations

import io

from app.db import models
from app.db.models import Subnet, SubnetLabel, SubnetLabelAssignment


def _upload(client, project_id: int, content: str):
    return client.post(
        f"/api/v1/projects/{project_id}/scopes/upload-subnets",
        files={"file": ("subnets.csv", io.BytesIO(content.encode()), "text/csv")},
    )


def _labels_for(db, scope_id: int, cidr: str) -> set[str]:
    sub = db.query(Subnet).filter(Subnet.scope_id == scope_id, Subnet.cidr == cidr).one()
    rows = (
        db.query(SubnetLabel.name)
        .join(SubnetLabelAssignment, SubnetLabelAssignment.label_id == SubnetLabel.id)
        .filter(SubnetLabelAssignment.subnet_id == sub.id)
        .all()
    )
    return {r[0] for r in rows}


def test_csv_upload_labels_description_and_merge(client, db_session, test_project):
    # First upload: two subnets, labels, a description; one cidr appears twice
    # in-file with different labels → must merge onto a single subnet row.
    r1 = _upload(
        client, test_project.id,
        "subnet,labels,description\n"
        "10.50.0.0/24,prod dmz,UK DMZ\n"
        "10.50.1.0/24,lab,\n"
        "10.50.0.0/24,internet-facing,\n",  # dup cidr, extra label
    )
    assert r1.status_code == 200, r1.text
    scope_id = r1.json()["scope_id"]

    # One row per cidr (no duplicate from the in-file repeat).
    assert db_session.query(Subnet).filter(
        Subnet.scope_id == scope_id, Subnet.cidr == "10.50.0.0/24"
    ).count() == 1
    # The repeated cidr's labels merged onto the one subnet.
    assert _labels_for(db_session, scope_id, "10.50.0.0/24") == {"prod", "dmz", "internet-facing"}
    # Description from column 3.
    sub = db_session.query(Subnet).filter(Subnet.scope_id == scope_id, Subnet.cidr == "10.50.0.0/24").one()
    assert sub.description == "UK DMZ"

    # Re-upload: same subnet, a NEW label + an existing one + a new description.
    r2 = _upload(
        client, test_project.id,
        "10.50.0.0/24,prod owned,Updated DMZ\n",
    )
    assert r2.status_code == 200, r2.text

    # No duplicate subnet; labels ADDED (not replaced) — old set ∪ {owned}.
    assert db_session.query(Subnet).filter(
        Subnet.scope_id == scope_id, Subnet.cidr == "10.50.0.0/24"
    ).count() == 1
    assert _labels_for(db_session, scope_id, "10.50.0.0/24") == {
        "prod", "dmz", "internet-facing", "owned",
    }
    db_session.expire_all()
    sub = db_session.query(Subnet).filter(Subnet.scope_id == scope_id, Subnet.cidr == "10.50.0.0/24").one()
    assert sub.description == "Updated DMZ"  # description updates when provided
