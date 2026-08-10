"""Attach hosts to the network-attribution blocks that cover them.

The same shape as ``SubnetCorrelationService``: build one ``IPTrie`` of the
project's attribution blocks and walk the hosts once, rather than issuing a
containment query per host. ``IPTrie.add_subnet`` only reads ``.cidr``, so
``NetworkAttribution`` rows go in directly.

Runs after an RDAP ingest, and again when hosts appear later — a block
registered to the client covers addresses discovered tomorrow just as much as
those discovered today, so correlation can't be a one-shot at ingest.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Host
from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution
from app.services.ip_trie import IPTrie

logger = logging.getLogger(__name__)

_INSERT_BATCH = 5000


def correlate_project_attributions(db: Session, project_id: int) -> int:
    """(Re)build host↔attribution mappings for one project.

    Returns the number of host/attribution pairs written. Idempotent: the
    project's mappings are replaced wholesale, so a re-ingest with corrected
    blocks doesn't leave the previous answer behind.
    """
    blocks = (
        db.query(NetworkAttribution)
        .filter(NetworkAttribution.project_id == project_id)
        .all()
    )
    if not blocks:
        return 0

    trie = IPTrie()
    for block in blocks:
        trie.add_subnet(block)  # invalid CIDR is skipped + logged internally

    hosts = (
        db.query(Host.id, Host.ip_address)
        .filter(Host.project_id == project_id)
        .all()
    )

    pairs: list[dict] = []
    for host_id, ip_str in hosts:
        if not ip_str:
            continue
        # A host legitimately matches several blocks — its registry
        # attribution and its cloud attribution are different facts about the
        # same address, and a more-specific block can coexist with its
        # supernet. Keep them all; disagreement between them is the signal.
        for block in trie.find_matching_subnets(ip_str):
            pairs.append({"host_id": host_id, "attribution_id": block.id})

    block_ids = [b.id for b in blocks]
    db.query(HostNetworkAttribution).filter(
        HostNetworkAttribution.attribution_id.in_(block_ids)
    ).delete(synchronize_session=False)

    for i in range(0, len(pairs), _INSERT_BATCH):
        db.bulk_insert_mappings(HostNetworkAttribution, pairs[i : i + _INSERT_BATCH])
    db.commit()

    logger.info(
        "Attribution correlation: project %s — %d block(s) matched %d host mapping(s)",
        project_id, len(blocks), len(pairs),
    )
    return len(pairs)


def attributions_for_host(db: Session, host_id: int) -> list[NetworkAttribution]:
    """Every attribution covering one host, most specific block first.

    Most-specific-first because a /29 registration says more about who runs an
    address than the /8 it sits inside.
    """
    rows = (
        db.query(NetworkAttribution)
        .join(
            HostNetworkAttribution,
            HostNetworkAttribution.attribution_id == NetworkAttribution.id,
        )
        .filter(HostNetworkAttribution.host_id == host_id)
        .all()
    )

    def _prefix_len(row: NetworkAttribution) -> int:
        try:
            return int(str(row.cidr).split("/")[1])
        except (IndexError, ValueError):
            return 0

    return sorted(rows, key=_prefix_len, reverse=True)
