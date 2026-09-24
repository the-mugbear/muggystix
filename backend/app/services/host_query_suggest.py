"""Value suggestions for the Hosts query DSL — what a field can match here.

``GET /hosts/query/suggest?field=&prefix=`` backs the command bar's value
autocomplete.  The page's facet lists (``/hosts/filters/data``) are capped,
cascaded by the active filters and cover a handful of fields, so a rare port
or a CVE id could not be completed at all.  This asks the database instead:
the values of ONE field that contain what was typed, project-wide.

Rules every source follows:

* **Project-scoped**, always — several predicates (``org:``, ``tech:``…) rely
  on the outer host query for the project restriction; a suggestion has no
  outer query, so each source joins to the project itself.
* **A suggested value is one the plain insertion would match.** ``port:`` /
  ``service:`` / ``version:`` match OPEN ports unless a ``@state`` is named
  (v2.403.0), so those sources read open ports only — offering a closed-only
  port would insert a condition that matches nothing.
* ``count`` is the number of hosts carrying the value (``None`` where a count
  would mean nothing, e.g. a scan id).  It orders the list; it is not the
  match count of the insertion for substring fields.
* Fields with nothing to enumerate (``note:``, the time windows) are not
  sources: ``supported`` is false and the frontend offers its own hints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from sqlalchemy import String, cast, func, or_, true
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_auth import User
from app.db.models_project import ProjectMembership
from app.db.models_vulnerability import Vulnerability
from app.services.host_query_common import escape_like
from app.services.host_query_dsl import FIELD_BUILDERS

MAX_SUGGESTIONS = 50


@dataclass
class Suggestion:
    value: str
    label: Optional[str] = None
    count: Optional[int] = None

    def as_dict(self) -> dict:
        return {"value": self.value, "label": self.label, "count": self.count}


@dataclass
class _Ctx:
    db: Session
    project_id: int
    current_user: User
    needle: str
    limit: int

    def contains(self, column):
        """Case-insensitive substring match — how the predicates match."""
        return column.ilike(f"%{escape_like(self.needle)}%", escape="\\")

    def starts(self, column):
        return cast(column, String).ilike(f"{escape_like(self.needle)}%", escape="\\")


def _hosts_in_project(ctx: _Ctx):
    return models.Host.project_id == ctx.project_id


def _host_count():
    return func.count(func.distinct(models.Host.id))


def _grouped_host_values(ctx: _Ctx, column, *filters, join=None) -> List[Suggestion]:
    """``column``'s distinct non-blank values on project hosts, with the
    number of hosts carrying each, most common first."""
    n = _host_count()
    q = ctx.db.query(column, n)
    if join is not None:
        q = q.select_from(join).join(models.Host, models.Host.id == join.host_id)
    q = q.filter(_hosts_in_project(ctx), column.isnot(None), cast(column, String) != "", *filters)
    rows = q.group_by(column).order_by(n.desc(), column).limit(ctx.limit).all()
    return [Suggestion(str(v), count=int(c)) for v, c in rows]


# -- ports (open by default, like the predicates) -----------------------------

def _open_port_values(ctx: _Ctx, column, *filters) -> List[Suggestion]:
    n = func.count(func.distinct(models.Port.host_id))
    rows = (
        ctx.db.query(column, n)
        .join(models.Host, models.Host.id == models.Port.host_id)
        .filter(
            _hosts_in_project(ctx), models.Port.state == "open",
            column.isnot(None), cast(column, String) != "", *filters,
        )
        .group_by(column).order_by(n.desc(), column).limit(ctx.limit).all()
    )
    return [Suggestion(str(v).strip(), count=int(c)) for v, c in rows if str(v).strip()]


def _port(ctx: _Ctx) -> List[Suggestion]:
    if ctx.needle and not ctx.needle.isdigit():
        return []  # `port:` takes a number; a name belongs in service:
    col = models.Port.port_number
    return _open_port_values(ctx, col, *([ctx.starts(col)] if ctx.needle else []))


def _service(ctx: _Ctx) -> List[Suggestion]:
    col = models.Port.service_name
    return _open_port_values(ctx, col, ctx.contains(col))


def _version(ctx: _Ctx) -> List[Suggestion]:
    # "OpenSSH 7.4" — product and version together, as version: matches them.
    joined = func.trim(func.concat(
        func.coalesce(models.Port.service_product, ""), " ",
        func.coalesce(models.Port.service_version, ""),
    ))
    return _open_port_values(ctx, joined, ctx.contains(joined))


# -- host columns -------------------------------------------------------------

def _ip(ctx: _Ctx) -> List[Suggestion]:
    col = models.Host.ip_address
    rows = (
        ctx.db.query(col).filter(_hosts_in_project(ctx), ctx.starts(col))
        .order_by(col).limit(ctx.limit).all()
    )
    return [Suggestion(str(r[0])) for r in rows]


def _hostname(ctx: _Ctx) -> List[Suggestion]:
    return _grouped_host_values(ctx, models.Host.hostname, ctx.contains(models.Host.hostname))


def _os(ctx: _Ctx) -> List[Suggestion]:
    return _grouped_host_values(ctx, models.Host.os_name, ctx.contains(models.Host.os_name))


# -- web ----------------------------------------------------------------------

def _web_column(column) -> Callable[[_Ctx], List[Suggestion]]:
    def source(ctx: _Ctx) -> List[Suggestion]:
        return _grouped_host_values(
            ctx, column, ctx.contains(column), join=models.WebInterface,
        )
    return source


def _path(ctx: _Ctx) -> List[Suggestion]:
    return _grouped_host_values(
        ctx, models.WebPath.path, ctx.contains(models.WebPath.path), join=models.WebPath,
    )


def _tech(ctx: _Ctx) -> List[Suggestion]:
    """Technology names are elements of a JSON array per web interface."""
    WI = models.WebInterface
    dialect = ctx.db.bind.dialect.name if ctx.db.bind is not None else "postgresql"
    if dialect == "postgresql":
        tech = func.json_array_elements_text(WI.technologies).table_valued("name")
        n = func.count(func.distinct(WI.host_id))
        rows = (
            ctx.db.query(tech.c.name, n)
            .select_from(WI)
            .join(tech, true())
            .join(models.Host, models.Host.id == WI.host_id)
            .filter(
                _hosts_in_project(ctx),
                WI.technologies.isnot(None),
                func.json_typeof(WI.technologies) == "array",
                ctx.contains(tech.c.name),
            )
            .group_by(tech.c.name).order_by(n.desc(), tech.c.name).limit(ctx.limit).all()
        )
        return [Suggestion(name, count=int(c)) for name, c in rows if name]
    # SQLite (test fallback): aggregate in Python, as /hosts/filters/data does.
    hosts: Dict[str, set] = {}
    pairs = (
        ctx.db.query(WI.host_id, WI.technologies)
        .join(models.Host, models.Host.id == WI.host_id)
        .filter(_hosts_in_project(ctx), WI.technologies.isnot(None))
        .all()
    )
    lowered = ctx.needle.lower()
    for host_id, techs in pairs:
        for t in techs if isinstance(techs, list) else []:
            if t and lowered in str(t).lower():
                hosts.setdefault(str(t), set()).add(host_id)
    ranked = sorted(hosts.items(), key=lambda kv: (-len(kv[1]), kv[0]))[: ctx.limit]
    return [Suggestion(name, count=len(ids)) for name, ids in ranked]


# -- scanner observations -----------------------------------------------------

def _vuln_column(column) -> Callable[[_Ctx], List[Suggestion]]:
    def source(ctx: _Ctx) -> List[Suggestion]:
        return _grouped_host_values(ctx, column, ctx.contains(column), join=Vulnerability)
    return source


# -- analyst-defined names ----------------------------------------------------

def _tag(ctx: _Ctx) -> List[Suggestion]:
    T, A = models.HostTag, models.HostTagAssignment
    n = func.count(A.id)
    rows = (
        ctx.db.query(T.name, n)
        .outerjoin(A, A.tag_id == T.id)
        .filter(T.project_id == ctx.project_id, ctx.contains(T.name))
        .group_by(T.id, T.name).order_by(n.desc(), T.name).limit(ctx.limit).all()
    )
    return [Suggestion(name, count=int(c)) for name, c in rows]


def _label(ctx: _Ctx) -> List[Suggestion]:
    L, A, M = models.SubnetLabel, models.SubnetLabelAssignment, models.HostSubnetMapping
    n = func.count(func.distinct(M.host_id))
    rows = (
        ctx.db.query(L.name, n)
        .outerjoin(A, A.label_id == L.id)
        .outerjoin(M, M.subnet_id == A.subnet_id)
        .filter(L.project_id == ctx.project_id, ctx.contains(L.name))
        .group_by(L.id, L.name).order_by(n.desc(), L.name).limit(ctx.limit).all()
    )
    return [Suggestion(name, count=int(c)) for name, c in rows]


def _scoped_subnet_values(ctx: _Ctx, column, *filters) -> list:
    """``column`` over the project's scoped subnets, with the distinct hosts
    mapped into them (a subnet nothing maps to still counts: 0)."""
    M = models.HostSubnetMapping
    n = func.count(func.distinct(M.host_id))
    return (
        ctx.db.query(column, n)
        .select_from(models.Subnet)
        .join(models.Scope, models.Scope.id == models.Subnet.scope_id)
        .outerjoin(M, M.subnet_id == models.Subnet.id)
        .filter(models.Scope.project_id == ctx.project_id, *filters)
        .group_by(column).order_by(n.desc(), column).limit(ctx.limit).all()
    )


def _cidr(ctx: _Ctx) -> List[Suggestion]:
    rows = _scoped_subnet_values(ctx, models.Subnet.cidr, ctx.contains(models.Subnet.cidr))
    return [Suggestion(cidr, count=int(c)) for cidr, c in rows]


def _site(ctx: _Ctx) -> List[Suggestion]:
    site = models.Subnet.site
    rows = _scoped_subnet_values(
        ctx, site, site.isnot(None), func.trim(site) != "", ctx.contains(site),
    )
    out = [Suggestion(name, count=int(c)) for name, c in rows]
    if "none".startswith(ctx.needle.lower()):
        out.append(Suggestion("none", label="in a scoped subnet with no site"))
    return out


def _scan(ctx: _Ctx) -> List[Suggestion]:
    """The DSL takes the id; the operator knows the filename — match both."""
    S = models.Scan
    q = ctx.db.query(S.id, S.filename, S.tool_name).filter(S.project_id == ctx.project_id)
    if ctx.needle:
        by_id = [ctx.starts(S.id)] if ctx.needle.isdigit() else []
        q = q.filter(or_(ctx.contains(S.filename), ctx.contains(S.tool_name), *by_id))
    rows = q.order_by(S.created_at.desc(), S.id.desc()).limit(ctx.limit).all()
    return [
        Suggestion(str(i), label=f"{name} ({tool})" if tool else name)
        for i, name, tool in rows
    ]


def _user(ctx: _Ctx) -> List[Suggestion]:
    """``assigned:`` — the fixed words, then the project's members by
    username (the value assigned: resolves), with how many hosts each holds."""
    fixed = [
        Suggestion("me", label="assigned to you"),
        Suggestion("any", label="assigned to anyone"),
        Suggestion("none", label="assigned to nobody"),
    ]
    lowered = ctx.needle.lower()
    out = [s for s in fixed if s.value.startswith(lowered)]
    held = (
        ctx.db.query(models.HostFollow.user_id, func.count(func.distinct(models.HostFollow.host_id)).label("n"))
        .join(models.Host, models.Host.id == models.HostFollow.host_id)
        .filter(_hosts_in_project(ctx), models.HostFollow.assigned_at.isnot(None))
        .group_by(models.HostFollow.user_id)
        .subquery()
    )
    rows = (
        ctx.db.query(User.username, User.full_name, func.coalesce(held.c.n, 0))
        .join(ProjectMembership, ProjectMembership.user_id == User.id)
        .outerjoin(held, held.c.user_id == User.id)
        .filter(
            ProjectMembership.project_id == ctx.project_id,
            User.is_active.is_(True),
            or_(ctx.contains(User.username), ctx.contains(User.full_name)),
        )
        .order_by(func.coalesce(held.c.n, 0).desc(), User.username)
        .limit(ctx.limit).all()
    )
    out.extend(Suggestion(u, label=full or None, count=int(c)) for u, full, c in rows)
    return out[: ctx.limit]


# -- network attribution (RDAP) -----------------------------------------------

def _attribution(ctx: _Ctx, *columns, filters=()):
    from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution

    n = func.count(func.distinct(HostNetworkAttribution.host_id))
    return (
        ctx.db.query(*columns, n)
        .select_from(NetworkAttribution)
        .join(HostNetworkAttribution, HostNetworkAttribution.attribution_id == NetworkAttribution.id)
        .join(models.Host, models.Host.id == HostNetworkAttribution.host_id)
        .filter(_hosts_in_project(ctx), *filters)
        .group_by(*columns).order_by(n.desc(), *columns).limit(ctx.limit).all()
    )


def _org(ctx: _Ctx) -> List[Suggestion]:
    from app.db.models_attribution import NetworkAttribution as NA
    rows = _attribution(ctx, NA.org_name, filters=(NA.org_name.isnot(None), ctx.contains(NA.org_name)))
    return [Suggestion(v, count=int(c)) for v, c in rows if v]


def _country(ctx: _Ctx) -> List[Suggestion]:
    from app.db.models_attribution import NetworkAttribution as NA
    rows = _attribution(ctx, NA.country, filters=(NA.country.isnot(None), ctx.starts(NA.country)))
    return [Suggestion(v.upper(), count=int(c)) for v, c in rows if v]


def _asn(ctx: _Ctx) -> List[Suggestion]:
    from app.db.models_attribution import NetworkAttribution as NA

    digits = ctx.needle.upper().removeprefix("AS")
    match = [ctx.contains(NA.as_name)]
    if digits.isdigit():
        match.append(cast(NA.asn, String).like(f"{digits}%"))
    rows = _attribution(ctx, NA.asn, NA.as_name, filters=(NA.asn.isnot(None), or_(*match)))
    return [Suggestion(str(asn), label=name or None, count=int(c)) for asn, name, c in rows]


_SOURCES: Dict[str, Callable[[_Ctx], List[Suggestion]]] = {
    "port": _port,
    "service": _service,
    "version": _version,
    "ip": _ip,
    "hostname": _hostname,
    "os": _os,
    "tech": _tech,
    "header": _web_column(models.WebInterface.server_header),
    "webtitle": _web_column(models.WebInterface.title),
    "certorg": _web_column(models.WebInterface.cert_subject_org),
    "path": _path,
    "cve": _vuln_column(Vulnerability.cve_id),
    "vuln": _vuln_column(Vulnerability.title),
    "issue": _vuln_column(Vulnerability.issue_key),
    "tag": _tag,
    "label": _label,
    "cidr": _cidr,
    "site": _site,
    "scan": _scan,
    "user": _user,
    "org": _org,
    "asn": _asn,
    "country": _country,
}


def suggest(
    db: Session,
    *,
    project_id: int,
    current_user: User,
    field: str,
    prefix: str,
    limit: int = 20,
) -> dict:
    """Suggestions for ``field`` (name or alias) containing ``prefix``.

    ``supported`` is false for an unknown field or one with nothing to
    enumerate — the caller shows its own hints rather than "no values"."""
    spec = FIELD_BUILDERS.get((field or "").strip().lower())
    limit = max(1, min(int(limit), MAX_SUGGESTIONS))
    if spec is None:
        return {"field": field, "supported": False, "values": []}
    needle = (prefix or "").strip()
    if spec.enum_values:
        lowered = needle.lower()
        values = [
            Suggestion(v, label=spec.enum_descriptions.get(v)).as_dict()
            for v in spec.enum_values if lowered in v.lower()
        ][:limit]
        return {"field": spec.name, "supported": True, "values": values}
    source = _SOURCES.get(spec.value_source)
    if source is None:
        return {"field": spec.name, "supported": False, "values": []}
    ctx = _Ctx(db=db, project_id=project_id, current_user=current_user, needle=needle, limit=limit)
    return {
        "field": spec.name,
        "supported": True,
        "values": [s.as_dict() for s in source(ctx)],
    }


def supported_sources() -> List[str]:
    """The value sources this module can enumerate (pinned by a test so a new
    field's ``value_source`` cannot silently have no suggestions)."""
    return sorted(_SOURCES)
