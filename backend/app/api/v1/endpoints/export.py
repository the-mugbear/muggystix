from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response, PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text
from app.db.session import get_db
from app.services.csv_utils import safe_csv_row
from app.services.export_service import ExportService
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project

router = APIRouter(dependencies=[Depends(get_current_user)])

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
    "/scope/{scope_id}",
    responses=_HOST_LIST_RESPONSES,
    summary="Export scope hosts",
)
def export_scope_hosts(
    scope_id: int,
    format_type: str = Query(
        default="txt",
        pattern="^(txt|csv|json)$",
        description="Output format: txt (one IP per line), csv, or json",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Export hosts belonging to a scope.

    Default format is ``txt`` — one IP per line, suitable for feeding
    into other tools.
    """
    from app.db import models as m

    scope = db.query(m.Scope).filter(m.Scope.id == scope_id, m.Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail=f"Scope {scope_id} not found")

    rows = db.execute(sql_text("""
        SELECT DISTINCT h.ip_address, h.hostname, h.state
        FROM hosts_v2 h
        JOIN host_subnet_mappings hsm ON hsm.host_id = h.id
        JOIN subnets s ON s.id = hsm.subnet_id
        WHERE s.scope_id = :scope_id AND h.project_id = :project_id
        ORDER BY h.ip_address
    """), {"scope_id": scope_id, "project_id": project.id}).fetchall()

    safe_name = scope.name.replace(" ", "_").replace("/", "-")[:40]

    if format_type == "txt":
        body = "\n".join(r.ip_address for r in rows) + ("\n" if rows else "")
        return PlainTextResponse(
            content=body,
            headers={"Content-Disposition": f"attachment; filename={safe_name}_hosts.txt"},
        )

    if format_type == "csv":
        import csv, io
        buf = io.StringIO()
        writer = csv.writer(buf)
        # v2.91.4 (third code review #1) — every data cell flows through
        # safe_csv_row so a scanner-derived hostname starting with
        # =/+/-/@/tab/CR cannot execute a formula when the file is
        # opened in Excel or LibreOffice.  The shared ExportService
        # path was already protected (v2.86.4); these lighter-weight
        # routes were missed.
        writer.writerow(["ip_address", "hostname", "state"])
        for r in rows:
            safe_csv_row(writer, [r.ip_address, r.hostname or "", r.state or ""])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_hosts.csv"},
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
        headers={"Content-Disposition": f"attachment; filename={safe_name}_hosts.json"},
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

    rows = db.execute(sql_text("""
        SELECT DISTINCT h.ip_address, h.hostname, h.state
        FROM hosts_v2 h
        JOIN host_scan_history hsh ON hsh.host_id = h.id
        WHERE hsh.scan_id = :scan_id AND h.project_id = :project_id
        ORDER BY h.ip_address
    """), {"scan_id": scan_id, "project_id": project.id}).fetchall()

    safe_name = scan.filename.replace(" ", "_").replace("/", "-")[:40] if scan.filename else f"scan_{scan_id}"

    if format_type == "txt":
        body = "\n".join(r.ip_address for r in rows) + ("\n" if rows else "")
        return PlainTextResponse(
            content=body,
            headers={"Content-Disposition": f"attachment; filename={safe_name}_hosts.txt"},
        )

    if format_type == "csv":
        import csv, io
        buf = io.StringIO()
        writer = csv.writer(buf)
        # v2.91.4 — see /scope/{id}/hosts CSV branch above for context.
        writer.writerow(["ip_address", "hostname", "state"])
        for r in rows:
            safe_csv_row(writer, [r.ip_address, r.hostname or "", r.state or ""])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_hosts.csv"},
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
        headers={"Content-Disposition": f"attachment; filename={safe_name}_hosts.json"},
    )


@router.get(
    "/out-of-scope",
    responses=_HOST_LIST_RESPONSES,
    summary="Export out-of-scope hosts",
)
def export_out_of_scope_hosts(
    format_type: str = Query(
        default="txt",
        pattern="^(txt|csv|json)$",
        description="Output format: txt (one IP per line), csv, or json",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Export hosts that have no subnet/scope mapping.

    Default format is ``txt`` — one IP per line, suitable for feeding
    into other tools.
    """
    rows = db.execute(sql_text("""
        SELECT h.ip_address, h.hostname, h.state
        FROM hosts_v2 h
        WHERE h.project_id = :project_id
          AND NOT EXISTS (
            SELECT 1 FROM host_subnet_mappings hsm WHERE hsm.host_id = h.id
        )
        ORDER BY h.ip_address
    """), {"project_id": project.id}).fetchall()

    if format_type == "txt":
        body = "\n".join(r.ip_address for r in rows) + ("\n" if rows else "")
        return PlainTextResponse(
            content=body,
            headers={"Content-Disposition": "attachment; filename=out_of_scope_hosts.txt"},
        )

    if format_type == "csv":
        import csv, io
        buf = io.StringIO()
        writer = csv.writer(buf)
        # v2.91.4 — see /scope/{id}/hosts CSV branch above for context.
        writer.writerow(["ip_address", "hostname", "state"])
        for r in rows:
            safe_csv_row(writer, [r.ip_address, r.hostname or "", r.state or ""])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=out_of_scope_hosts.csv"},
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
        headers={"Content-Disposition": "attachment; filename=out_of_scope_hosts.json"},
    )
