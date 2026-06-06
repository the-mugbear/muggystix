from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.ports_of_interest import PORTS_OF_INTEREST, ports_by_number

logger = logging.getLogger(__name__)


SEVERITY_WEIGHTS = {
    VulnerabilitySeverity.CRITICAL: 6,
    VulnerabilitySeverity.HIGH: 4,
    VulnerabilitySeverity.MEDIUM: 2,
    VulnerabilitySeverity.LOW: 1,
    VulnerabilitySeverity.INFO: 0,
}


class RiskInsightService:
    """Aggregate high-risk exposure insights for dashboard consumption."""

    def __init__(self, db: Session):
        self.db = db
        self._ports_map = ports_by_number()

    def generate_insights(self, limit: int = 10, project_id: int = None) -> Dict[str, object]:
        port_summary = self._collect_port_exposure_summary(project_id=project_id)
        host_exposures = self._collect_host_exposures(limit=limit, project_id=project_id)
        vuln_hotspots = self._collect_vulnerability_hotspots(limit=limit, project_id=project_id)

        ranked_hosts = self._rank_hosts(host_exposures, vuln_hotspots, limit=limit, project_id=project_id)

        return {
            "ports_of_interest": {
                "summary": port_summary,
                "top_hosts": ranked_hosts,
            },
            "vulnerability_hotspots": vuln_hotspots,
        }

    def _collect_port_exposure_summary(self, project_id: int = None) -> List[Dict[str, object]]:
        port_numbers = [entry.port for entry in PORTS_OF_INTEREST]
        if not port_numbers:
            return []

        query = (
            self.db.query(
                models.Port.port_number,
                func.count(func.distinct(models.Port.host_id)).label("host_count"),
            )
            .join(models.Host, models.Port.host_id == models.Host.id)
            .filter(
                models.Port.state == "open",
                models.Port.port_number.in_(port_numbers),
            )
        )
        if project_id is not None:
            query = query.filter(models.Host.project_id == project_id)
        rows = query.group_by(models.Port.port_number).all()

        summary = []
        for row in rows:
            poi = self._ports_map.get(row.port_number)
            if not poi:
                continue
            summary.append(
                {
                    "port": poi.port,
                    "protocol": poi.protocol,
                    "label": poi.label,
                    "category": poi.category,
                    "weight": poi.weight,
                    "open_host_count": row.host_count,
                    "rationale": poi.rationale,
                    "recommended_action": poi.recommended_action,
                }
            )

        summary.sort(key=lambda item: (item["open_host_count"], item["weight"]), reverse=True)
        return summary

    def _collect_host_exposures(self, limit: int = 10, project_id: int = None) -> Dict[int, Dict[str, object]]:
        # Compute each host's port_score in SQL and let Postgres ORDER BY
        # + LIMIT pick the top `limit` candidates, instead of pulling every
        # (host, port-of-interest) row in the project into Python.  Pre-fix
        # this collector materialised the whole project's exposed-port
        # cross-product on every dashboard risk-widget load — fine at 100
        # hosts, hundreds of thousands of rows at 42k.  The downstream
        # _rank_hosts already truncates the vuln side to `limit`; this keeps
        # the port side symmetric and bounded.
        port_numbers = [entry.port for entry in PORTS_OF_INTEREST]
        if not port_numbers:
            return {}

        # CASE WHEN port_number = N THEN <weight> ... — the per-port weights
        # live in PORTS_OF_INTEREST (Python config), so build the mapping
        # into the aggregate rather than scoring in Python after a full scan.
        weight_whens = [
            (models.Port.port_number == number, poi.weight)
            for number, poi in self._ports_map.items()
        ]
        port_score = func.coalesce(
            func.sum(case(*weight_whens, else_=0)), 0
        ).label("port_score")

        score_query = (
            self.db.query(models.Port.host_id.label("host_id"), port_score)
            .join(models.Host, models.Port.host_id == models.Host.id)
            .filter(
                models.Port.state == "open",
                models.Port.port_number.in_(port_numbers),
            )
        )
        if project_id is not None:
            score_query = score_query.filter(models.Host.project_id == project_id)
        score_query = (
            score_query.group_by(models.Port.host_id)
            .order_by(port_score.desc())
            .limit(limit)
        )
        top_host_ids = [row.host_id for row in score_query.all()]
        if not top_host_ids:
            return {}

        # Fetch the port-of-interest detail rows only for the top hosts.
        rows = (
            self.db.query(
                models.Host.id,
                models.Host.ip_address,
                models.Host.hostname,
                models.Port.port_number,
                models.Port.service_name,
            )
            .join(models.Port, models.Port.host_id == models.Host.id)
            .filter(
                models.Host.id.in_(top_host_ids),
                models.Port.state == "open",
                models.Port.port_number.in_(port_numbers),
            )
            .all()
        )

        exposures: Dict[int, Dict[str, object]] = {}
        for row in rows:
            host_info = exposures.setdefault(
                row.id,
                {
                    "host_id": row.id,
                    "ip_address": row.ip_address,
                    "hostname": row.hostname,
                    "ports_of_interest": [],
                    "port_score": 0,
                },
            )

            poi = self._ports_map.get(row.port_number)
            if not poi:
                continue

            host_info["ports_of_interest"].append(
                {
                    "port": poi.port,
                    "protocol": poi.protocol,
                    "label": poi.label,
                    "service": row.service_name or "unknown",
                    "weight": poi.weight,
                    "category": poi.category,
                }
            )
            host_info["port_score"] += poi.weight

        return exposures

    def _collect_vulnerability_hotspots(self, limit: int, project_id: int = None) -> List[Dict[str, object]]:
        # v2.91.4 (third code review #4) — compute the weighted risk
        # score, severity bucket counts, ordering, and LIMIT in SQL so
        # only `limit` rows are returned to Python.  Pre-fix this
        # method materialised every (host_id, severity) aggregate
        # across the entire project, joined to every host's identity,
        # built a Python dict over the union, then sorted and sliced
        # — fine at 100 hosts, an O(N) full-scan at 100k.  The window-
        # style aggregate keeps Postgres in control of the truncation.
        critical = VulnerabilitySeverity.CRITICAL
        high = VulnerabilitySeverity.HIGH
        medium = VulnerabilitySeverity.MEDIUM
        low = VulnerabilitySeverity.LOW

        # COUNT(CASE WHEN severity = X) is portable to both Postgres
        # and the SQLite test backend; ``SUM(CASE WHEN ... THEN 1)``
        # would work too but COUNT communicates intent better here.
        crit_count = func.coalesce(
            func.sum(case((Vulnerability.severity == critical, 1), else_=0)), 0
        ).label("critical")
        high_count = func.coalesce(
            func.sum(case((Vulnerability.severity == high, 1), else_=0)), 0
        ).label("high")
        med_count = func.coalesce(
            func.sum(case((Vulnerability.severity == medium, 1), else_=0)), 0
        ).label("medium")
        low_count = func.coalesce(
            func.sum(case((Vulnerability.severity == low, 1), else_=0)), 0
        ).label("low")

        risk_score = func.coalesce(
            func.sum(
                case(
                    (Vulnerability.severity == critical, SEVERITY_WEIGHTS[critical]),
                    (Vulnerability.severity == high, SEVERITY_WEIGHTS[high]),
                    (Vulnerability.severity == medium, SEVERITY_WEIGHTS[medium]),
                    (Vulnerability.severity == low, SEVERITY_WEIGHTS[low]),
                    else_=0,
                )
            ),
            0,
        ).label("risk_score")

        query = (
            self.db.query(
                models.Host.id.label("host_id"),
                models.Host.ip_address,
                models.Host.hostname,
                crit_count,
                high_count,
                med_count,
                low_count,
                risk_score,
            )
            .join(Vulnerability, Vulnerability.host_id == models.Host.id)
            .filter(
                Vulnerability.severity.in_([critical, high, medium, low])
            )
        )
        if project_id is not None:
            query = query.filter(models.Host.project_id == project_id)

        query = (
            query
            .group_by(models.Host.id, models.Host.ip_address, models.Host.hostname)
            .order_by(risk_score.desc(), crit_count.desc(), high_count.desc())
            .limit(limit)
        )

        return [
            {
                "host_id": row.host_id,
                "ip_address": row.ip_address,
                "hostname": row.hostname,
                "critical": int(row.critical or 0),
                "high": int(row.high or 0),
                "medium": int(row.medium or 0),
                "low": int(row.low or 0),
                "risk_score": int(row.risk_score or 0),
            }
            for row in query.all()
        ]

    def _rank_hosts(
        self,
        exposures: Dict[int, Dict[str, object]],
        vuln_hotspots: List[Dict[str, object]],
        limit: int,
        project_id: int = None,
    ) -> List[Dict[str, object]]:
        if not exposures and not vuln_hotspots:
            return []

        hotspot_map = {entry["host_id"]: entry for entry in vuln_hotspots}

        combined: List[Dict[str, object]] = []
        host_ids = set(exposures.keys()) | set(hotspot_map.keys())
        if not host_ids:
            return []

        host_query = (
            self.db.query(models.Host.id, models.Host.ip_address, models.Host.hostname)
            .filter(models.Host.id.in_(host_ids))
        )
        if project_id is not None:
            host_query = host_query.filter(models.Host.project_id == project_id)
        host_rows = host_query.all()
        identity_map = {row.id: row for row in host_rows}

        for host_id in host_ids:
            identity = identity_map.get(host_id)
            if not identity:
                continue

            exposure = exposures.get(host_id, {
                "ports_of_interest": [],
                "port_score": 0,
            })
            hotspot = hotspot_map.get(host_id, {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "risk_score": 0,
            })

            combined.append(
                {
                    "host_id": host_id,
                    "ip_address": identity.ip_address,
                    "hostname": identity.hostname,
                    "ports_of_interest": exposure.get("ports_of_interest", []),
                    "critical": hotspot.get("critical", 0),
                    "high": hotspot.get("high", 0),
                    "medium": hotspot.get("medium", 0),
                    "low": hotspot.get("low", 0),
                    "risk_score": exposure.get("port_score", 0) + hotspot.get("risk_score", 0),
                    "port_score": exposure.get("port_score", 0),
                    "vulnerability_score": hotspot.get("risk_score", 0),
                }
            )

        combined.sort(key=lambda item: item["risk_score"], reverse=True)
        return combined[:limit]

