from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response, PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.session import get_db
from app.services.csv_utils import safe_csv_row
from app.api.downloads import attachment
from app.api.deps import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectRole

# Data egress. The documented policy is that VIEWERs cannot export project
# data; AUDITOR ("read-only, with audit visibility") and above may. Gate the
# whole router — every route here is under /projects/{project_id}, so the
# role check reads project_id from the path like get_current_project does.
router = APIRouter(dependencies=[
    Depends(get_current_user),
    Depends(require_project_role(ProjectRole.AUDITOR)),
])

_HOST_LIST_RESPONSES = {
    200: {
        "description": "Host list in the requested format. "
        "**txt** returns one IP per line (`text/plain`). "
        "**csv** returns ip_address,hostname,state rows (`text/csv`). "
        "**json** returns a JSON array of `{ip_address, hostname, state}` objects (`application/json`). "
        "All responses include a `Content-Disposition: attachment` header.",
        "content": {
            "text/plain": {
                "example": "10.0.0.1\n10.0.0.2\n10.0.0.3\n",
            },
            "text/csv": {
                "example": "ip_address,hostname,state\n10.0.0.1,web01.example.com,up\n",
            },
            "application/json": {
                "example": [
                    {"ip_address": "10.0.0.1", "hostname": "web01.example.com", "state": "up"},
                ],
            },
        },
    },
    401: {"description": "Not authenticated"},
    404: {"description": "Resource not found"},
}


@router.get(
    "/scope",
    responses={
        200: {
            "description": "The declared scope in the requested format. "
            "**cidrs** — one network per line (`text/plain`). "
            "**domains** — one domain per line, `*.example.com` where subdomains are included (`text/plain`). "
            "**csv** — `entry,labels,description,site`, networks then domains (`text/csv`). "
            "Each is a scope file the Scope page's upload reads back as the same entries.",
            "content": {
                "text/plain": {"example": "10.0.0.0/24\n10.0.1.5/32\n"},
                "text/csv": {"example": "entry,labels,description,site\r\n10.0.0.0/24,DMZ,Web tier,London DC\r\n*.example.com,,,\r\n"},
            },
        },
        401: {"description": "Not authenticated"},
    },
    summary="Export the declared scope (networks and domains)",
)
def export_scope(
    format_type: str = Query(
        default="cidrs",
        pattern="^(cidrs|domains|csv)$",
        description="Output format: cidrs (one network per line), domains (one per line), or csv (every entry)",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Export what the project's scope DECLARES — its networks and domains —
    not the hosts discovered in it (owner, 2026-10-22: those are the Hosts
    page's export, filtered by `scope:`; this route listed them until
    2.521.0).

    Every format is a scope file ``POST /scopes/upload-subnets`` reads back
    as the same entries (``SubnetParser.parse_scope_list`` / ``parse_scope_csv``):
    a domain that includes its subdomains is written ``*.example.com``, the
    upload's spelling of it, and the CSV's columns are the upload's.  One
    exception: labels are space-separated in that column, so a label name
    with a space in it comes back as two labels.

    A read: a project with no scope yet exports nothing, and none is created.
    """
    import csv, io
    from collections import defaultdict
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import INET
    from app.db import models as m
    from app.services.default_scope import project_scope

    scope = project_scope(db, project.id)
    subnets, domains = [], []
    if scope is not None:
        # Address order (10.0.0.2/32 before 10.0.0.10/32), as the Scope page
        # lists them.  Every writer stores ``ip_network``'s normal form, so
        # the cast cannot fail.
        order = (
            [cast(m.Subnet.cidr, INET), m.Subnet.id]
            if db.get_bind().dialect.name == "postgresql" else [m.Subnet.cidr, m.Subnet.id]
        )
        if format_type != "domains":
            subnets = (
                db.query(m.Subnet.id, m.Subnet.cidr, m.Subnet.description, m.Subnet.site)
                .filter(m.Subnet.scope_id == scope.id).order_by(*order).all()
            )
        if format_type != "cidrs":
            domains = (
                db.query(m.ScopeDomain.domain, m.ScopeDomain.include_subdomains, m.ScopeDomain.description)
                .filter(m.ScopeDomain.scope_id == scope.id).order_by(m.ScopeDomain.domain).all()
            )

    def _domain(name: str, include_subdomains: bool) -> str:
        return f"*.{name}" if include_subdomains else name

    if format_type == "cidrs":
        return PlainTextResponse(
            content="".join(f"{s.cidr}\n" for s in subnets),
            headers=attachment("project-scope_cidrs.txt"),
        )
    if format_type == "domains":
        return PlainTextResponse(
            content="".join(f"{_domain(d.domain, d.include_subdomains)}\n" for d in domains),
            headers=attachment("project-scope_domains.txt"),
        )

    labels = defaultdict(list)
    if subnets:
        for subnet_id, name in (
            db.query(m.SubnetLabelAssignment.subnet_id, m.SubnetLabel.name)
            .join(m.SubnetLabel, m.SubnetLabel.id == m.SubnetLabelAssignment.label_id)
            .join(m.Subnet, m.Subnet.id == m.SubnetLabelAssignment.subnet_id)
            .filter(m.Subnet.scope_id == scope.id)
            .order_by(m.SubnetLabel.name)
        ):
            labels[subnet_id].append(name)
    buf = io.StringIO()
    writer = csv.writer(buf)
    # Every data cell flows through safe_csv_row, so a description or site
    # starting with =/+/-/@/tab/CR cannot execute a formula when the file
    # is opened in Excel or LibreOffice (v2.91.4, third code review #1).
    # The upload's first-row rule skips the header: "entry" is a dotless word.
    writer.writerow(["entry", "labels", "description", "site"])
    for s in subnets:
        safe_csv_row(writer, [s.cidr, " ".join(labels[s.id]), s.description or "", s.site or ""])
    for d in domains:
        safe_csv_row(writer, [_domain(d.domain, d.include_subdomains), "", d.description or "", ""])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers=attachment("project-scope.csv"),
    )


@router.get(
    "/scan/{scan_id}",
    responses=_HOST_LIST_RESPONSES,
    summary="Export scan hosts",
)
def export_scan_hosts(
    scan_id: int,
    format_type: str = Query(
        default="txt",
        pattern="^(txt|csv|json)$",
        description="Output format: txt (one IP per line), csv, or json",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Export hosts seen in a specific scan.

    Default format is ``txt`` — one IP per line.
    """
    from app.db import models as m

    scan = db.query(m.Scan).filter(m.Scan.id == scan_id, m.Scan.project_id == project.id).first()
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")

    # The hosts the scan has history for, in address order (it sorted the
    # text: 10.0.0.10 before 10.0.0.2 — second review 2026-10-14, F2).
    from app.services.scope_targets_service import ip_order_by

    seen_by_scan = (
        select(m.HostScanHistory.id)
        .where(m.HostScanHistory.host_id == m.Host.id, m.HostScanHistory.scan_id == scan_id)
        .correlate(m.Host)
        .exists()
    )
    rows = (
        db.query(m.Host.ip_address, m.Host.hostname, m.Host.state)
        .filter(m.Host.project_id == project.id, seen_by_scan)
        .order_by(*ip_order_by(db))
        .all()
    )

    # The scan's own file name leads the download's; whatever it holds — any
    # script, a comma — the header helper sends it as RFC 6266 says.
    safe_name = scan.filename.replace(" ", "_").replace("/", "-")[:40] if scan.filename else f"scan_{scan_id}"

    if format_type == "txt":
        body = "\n".join(r.ip_address for r in rows) + ("\n" if rows else "")
        return PlainTextResponse(
            content=body,
            headers=attachment(f"{safe_name}_hosts.txt"),
        )

    if format_type == "csv":
        import csv, io
        buf = io.StringIO()
        writer = csv.writer(buf)
        # v2.91.4 — every cell through safe_csv_row, as the scope export's
        # CSV branch explains.
        writer.writerow(["ip_address", "hostname", "state"])
        for r in rows:
            safe_csv_row(writer, [r.ip_address, r.hostname or "", r.state or ""])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers=attachment(f"{safe_name}_hosts.csv"),
        )

    # JSON
    import json
    data = [
        {"ip_address": r.ip_address, "hostname": r.hostname, "state": r.state}
        for r in rows
    ]
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers=attachment(f"{safe_name}_hosts.json"),
    )
