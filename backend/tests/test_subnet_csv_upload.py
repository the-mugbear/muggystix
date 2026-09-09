"""Tests for the labelled-CSV subnet upload (/scopes/upload-subnets).

Pins the headline merge/accumulate behaviour that's easiest to regress:
subnets dedup by cidr, labels are ADDED not replaced across re-uploads, an
intra-file duplicate cidr merges its labels onto one subnet, and column 3
populates/updates the description.
"""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.db import models
from app.db.models import Subnet, SubnetLabel, SubnetLabelAssignment
from app.api.deps import read_upload_capped


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
        "subnet,labels,description,site\n"
        "10.50.0.0/24,prod dmz,UK DMZ,London DC\n"
        "10.50.1.0/24,lab,,\n"
        "10.50.0.0/24,internet-facing,,\n",  # dup cidr, extra label
    )
    assert r1.status_code == 200, r1.text
    scope_id = r1.json()["scope_id"]

    # One row per cidr (no duplicate from the in-file repeat).
    assert db_session.query(Subnet).filter(
        Subnet.scope_id == scope_id, Subnet.cidr == "10.50.0.0/24"
    ).count() == 1
    # The repeated cidr's labels merged onto the one subnet.
    assert _labels_for(db_session, scope_id, "10.50.0.0/24") == {"prod", "dmz", "internet-facing"}
    # Description (col 3) + site (col 4).
    sub = db_session.query(Subnet).filter(Subnet.scope_id == scope_id, Subnet.cidr == "10.50.0.0/24").one()
    assert sub.description == "UK DMZ"
    assert sub.site == "London DC"

    # Re-upload: same subnet, a NEW label + an existing one + a new
    # description + a new site.
    r2 = _upload(
        client, test_project.id,
        "10.50.0.0/24,prod owned,Updated DMZ,Manchester DC\n",
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
    assert sub.site == "Manchester DC"  # site updates (last-wins) when provided


# ---------------------------------------------------------------------------
# Bounded upload read (audit: small-file routes read() before checking size).
# ---------------------------------------------------------------------------

def test_oversize_subnet_upload_is_rejected(client, test_project):
    """A file past the 2 MB cap is rejected — and, via read_upload_capped, the
    reject happens without materializing the whole upload in memory."""
    oversize = b"a" * (2 * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/scopes/upload-subnets",
        files={"file": ("subnets.csv", io.BytesIO(oversize), "text/csv")},
    )
    assert resp.status_code == 413, resp.text


def test_read_upload_capped_rejects_over_the_cap():
    uf = UploadFile(filename="x.txt", file=io.BytesIO(b"a" * (1024 + 1)))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(read_upload_capped(uf, 1024))
    assert ei.value.status_code == 413


def test_read_upload_capped_returns_a_small_file_whole():
    uf = UploadFile(filename="x.txt", file=io.BytesIO(b"hello world"))
    assert asyncio.run(read_upload_capped(uf, 1024)) == b"hello world"


def test_read_upload_capped_allows_exactly_the_cap():
    # cap is inclusive — exactly max_bytes is fine, max_bytes+1 is not.
    uf = UploadFile(filename="x.txt", file=io.BytesIO(b"a" * 1024))
    assert asyncio.run(read_upload_capped(uf, 1024)) == b"a" * 1024


# ---------------------------------------------------------------------------
# v2.326.0 — a scope file mixes subnets and domain names.  Domain rows go to
# scope_domains through the same upsert as the domains card; subnet rows are
# unchanged.  Neither kind ever creates the other.
# ---------------------------------------------------------------------------

def _upload_txt(client, project_id: int, content: str):
    return client.post(
        f"/api/v1/projects/{project_id}/scopes/upload-subnets",
        files={"file": ("scope.txt", io.BytesIO(content.encode()), "text/plain")},
    )


def _domains(db, scope_id: int) -> dict[str, bool]:
    rows = db.query(models.ScopeDomain).filter(models.ScopeDomain.scope_id == scope_id).all()
    return {d.domain: bool(d.include_subdomains) for d in rows}


def test_txt_upload_of_domains_only(client, db_session, test_project):
    # The reported failure: a plain domain list on the Scopes page.
    r = _upload_txt(
        client, test_project.id,
        "# client-supplied names\n"
        "portal.example.com\n"
        "*.dev.example.com\n"
        "Portal.Example.COM.\n",  # dup after normalisation
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subnets_added"] == 0
    assert body["domains_added"] == 2
    assert "2 domain(s)" in body["message"]
    assert "1 duplicate skipped" in body["message"]
    scope_id = body["scope_id"]
    assert _domains(db_session, scope_id) == {
        "portal.example.com": False,
        "dev.example.com": True,
    }
    # No subnet was invented from a name.
    assert db_session.query(Subnet).filter(Subnet.scope_id == scope_id).count() == 0


def test_csv_upload_mixes_subnets_and_domains(client, db_session, test_project):
    r = _upload(
        client, test_project.id,
        "entry,labels,description,site\n"
        "10.60.0.0/24,prod,UK DMZ,London DC\n"
        "app.example.com,,Customer portal,\n"
        "*.example.org,prod,,London DC\n",  # labels/site are subnet-only → ignored
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subnets_added"] == 1
    assert body["domains_added"] == 2
    assert "labels/site ignored on 1 domain row" in body["message"]
    scope_id = body["scope_id"]
    assert _domains(db_session, scope_id) == {"app.example.com": False, "example.org": True}
    desc = {
        d.domain: d.description
        for d in db_session.query(models.ScopeDomain).filter(models.ScopeDomain.scope_id == scope_id)
    }
    assert desc["app.example.com"] == "Customer portal"
    assert _labels_for(db_session, scope_id, "10.60.0.0/24") == {"prod"}

    # Re-upload with the exact name now as a wildcard: widens, never narrows.
    r2 = _upload(client, test_project.id, "*.app.example.com\n10.60.0.0/24\n")
    assert r2.status_code == 200, r2.text
    assert r2.json()["domains_added"] == 0
    assert "1 widened to include subdomains" in r2.json()["message"]
    assert _domains(db_session, scope_id)["app.example.com"] is True
    # And the subnet count didn't move.
    assert db_session.query(Subnet).filter(Subnet.scope_id == scope_id).count() == 1


def test_upload_rejects_a_row_that_is_neither_subnet_nor_domain(client, test_project):
    r = _upload_txt(client, test_project.id, "10.70.0.0/24\nnot a name!\n")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "line 2" in detail
    assert "not a subnet" in detail and "not a domain name" in detail

    # A URL or host:port is tolerated the way the domains card tolerates it;
    # an empty file is still a 400.
    r2 = _upload_txt(client, test_project.id, "# only comments\n")
    assert r2.status_code == 400
    assert "No valid subnets or domains" in r2.json()["detail"]
