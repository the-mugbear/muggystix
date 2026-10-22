"""The Posture briefing — the Security Posture overview's argument as one
self-contained HTML file to hand out at a meeting.

It prints ``compute_posture`` (the page's own composition) in the page's
order: the conclusion and what it rests on, the three measures, where to focus
(``posture_focus``, the ranking the page draws), the decisions it leads to,
the family × segment grid, the widespread weaknesses behind the measure, and
where the promoted findings stand.  Nothing here is a second roll-up: a figure
the page shows and the briefing prints comes from the same key.

Light and printable on purpose (a handout, not a dashboard); no script, no
stylesheet, font or image fetched from anywhere; every value a person or a
tool wrote is escaped.  A snapshot of one assessment window — no remediation,
no freshness judgment: the import dates are provenance.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional

# The page's vocabulary (frontend components/posture/postureTheme.ts).
_LABEL_TEXT = {
    "action_required": "Action required",
    "needs_assessment": "Needs assessment",
    "insufficient_evidence": "Insufficient evidence",
    "no_urgent_signals": "No urgent signals",
}
_LABEL_COLOUR = {
    "action_required": "#b42318",
    "needs_assessment": "#b54708",
    "insufficient_evidence": "#667085",
    "no_urgent_signals": "#067647",
}
_SEVERITY_COLOUR = {
    "critical": "#b42318", "high": "#e04f16", "medium": "#dc9b04", "low": "#2e90fa", "info": "#98a2b3",
}
_PRIORITY_KIND = {
    "exposure": "Promoted findings", "ownership": "Assignment", "systemic": "Systemic",
    "site": "Site", "coverage": "Coverage", "triage": "Triage", "onboard": "Onboard",
}
_SPREAD = {"estate_wide": "estate-wide", "recurring": "recurring", "isolated": "isolated"}
# The status groups of the page's "Promoted findings".
_POPULATIONS = [
    ("Under investigation", ["open"]),
    ("Confirmed", ["confirmed"]),
    ("Closed", ["false_positive", "accepted_risk", "remediated"]),
]
_STATUS_TEXT = {"false_positive": "false positive", "accepted_risk": "accepted risk", "remediated": "remediated"}
# Grid columns printed before "the rest are on Segments" — the page's cap.
_GRID_MAX_COLUMNS = 12
# Ranked segments printed under "Where to focus" — the page's.
_FOCUS_ROWS = 5

_CSS = """
:root { --ink:#101828; --muted:#475467; --faint:#98a2b3; --rule:#d0d5dd; --soft:#f2f4f7; --heat:180,35,24; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 60rem; margin: 0 auto; padding: 40px 32px 56px; }
header { border-bottom: 2px solid var(--ink); padding-bottom: 14px; margin-bottom: 28px; }
.kicker { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0; }
h1 { font-size: 28px; line-height: 1.2; margin: 4px 0 6px; overflow-wrap: anywhere; }
.provenance { font-size: 13px; color: var(--muted); margin: 0; }
section { border-top: 1px solid var(--rule); padding: 22px 0 6px; break-inside: avoid-page; }
h2 { font-size: 17px; margin: 0 0 4px; }
.lede { color: var(--muted); font-size: 13.5px; margin: 0 0 14px; max-width: 46rem; }
.conclusion { border-left: 5px solid var(--tone); padding: 4px 0 4px 18px; margin-bottom: 26px; }
.label { font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--tone); margin: 0; }
.conclusion .headline { font-size: 21px; font-weight: 650; line-height: 1.35; margin: 6px 0 10px; overflow-wrap: anywhere; }
.reasons { list-style: none; padding: 0; margin: 0 0 10px; }
.reasons li { padding-left: 18px; position: relative; margin: 3px 0; overflow-wrap: anywhere; }
.dot { position: absolute; left: 0; top: .5em; width: 9px; height: 9px; border-radius: 50%; }
.rests { font-size: 13px; color: var(--muted); margin: 0; }
.measures { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--rule); }
.measure { padding: 16px 18px 14px 0; }
.measure + .measure { border-left: 1px solid var(--rule); padding-left: 18px; }
.measure .name { font-size: 12.5px; color: var(--muted); margin: 0; }
.measure .value { font-size: 30px; font-weight: 650; line-height: 1.15; margin: 2px 0; font-variant-numeric: tabular-nums; }
.measure .note { font-size: 12.5px; color: var(--muted); margin: 0; overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; table-layout: fixed; }
th { text-align: left; font-size: 12px; font-weight: 600; color: var(--muted); padding: 0 10px 6px 0; vertical-align: bottom; }
td { border-top: 1px solid var(--rule); padding: 8px 10px 8px 0; vertical-align: top; overflow-wrap: anywhere; }
.num { font-variant-numeric: tabular-nums; }
.sub { display: block; font-size: 12px; color: var(--muted); }
.bar { position: relative; height: 12px; background: var(--soft); border-radius: 2px; margin-top: 4px; }
.bar .fill { height: 100%; background: rgba(var(--heat), .6); border-radius: 2px; }
.bar.limited .fill { background: repeating-linear-gradient(135deg, rgba(var(--heat), .45) 0 3px, transparent 3px 7px);
  outline: 1px solid rgba(var(--heat), .6); }
.bar .tick { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--ink); }
.hatch { background: repeating-linear-gradient(135deg, rgba(102,112,133,.22) 0 3px, transparent 3px 8px); }
.why { border-left: 3px solid var(--rule); padding: 2px 0 2px 14px; margin: 14px 0 6px; }
.why p { margin: 0 0 6px; }
.caption { font-size: 12.5px; color: var(--muted); margin: 8px 0 0; }
.grid td, .grid th { text-align: center; padding: 6px 4px; }
.grid th:first-child, .grid td:first-child { text-align: left; }
.grid td.cell { font-variant-numeric: tabular-nums; }
.statuses { display: flex; flex-wrap: wrap; gap: 8px 32px; margin: 0 0 8px; padding: 0; list-style: none; }
.statuses .n { font-size: 22px; font-weight: 650; display: block; font-variant-numeric: tabular-nums; }
.statuses .s { font-size: 12.5px; color: var(--muted); }
footer { margin-top: 30px; border-top: 1px solid var(--rule); padding-top: 12px; font-size: 12px; color: var(--muted); }
footer dl { margin: 0 0 8px; } footer dt { font-weight: 600; color: var(--ink); } footer dd { margin: 0 0 6px; }
@media print { main { padding: 0; } body { font-size: 12.5px; } section { break-inside: avoid; } }
"""


def _e(value: Any) -> str:
    return escape("" if value is None else str(value))


def _n(value: Any) -> str:
    return f"{int(value or 0):,}"


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{int(value * 100 + 0.5)}%"


def _plural(count: int, word: str, plural: Optional[str] = None) -> str:
    return f"{_n(count)} {word if count == 1 else (plural or word + 's')}"


def _section(title: str, body: str, lede: str = "") -> str:
    lede_html = f'<p class="lede">{lede}</p>' if lede else ""
    return f"<section><h2>{_e(title)}</h2>{lede_html}{body}</section>"


def _dot(severity: str) -> str:
    return f'<span class="dot" style="background:{_SEVERITY_COLOUR.get(severity, "#98a2b3")}"></span>'


# --- the sections, in the page's order --------------------------------------

def _conclusion(p: Dict[str, Any]) -> str:
    label = p.get("label") or "insufficient_evidence"
    rc = p["headline"]["review_coverage"]
    estate = (p.get("systemic") or {}).get("estate") or {}
    in_scope = estate.get("hosts_in_scope") if (p.get("systemic") or {}).get("adopted") else None
    text = (p.get("conclusion") or {}).get("text") or _LABEL_TEXT[label]
    more = "".join(
        f"<li>{_dot(r.get('severity', ''))}{_e(r.get('text'))}</li>" for r in (p.get("reasons") or [])[1:]
    )
    reasons = f'<ul class="reasons">{more}</ul>' if more else ""
    rests = f"Rests on {_n(rc['reviewed'])} of {_n(rc['total'])} hosts reviewed"
    if in_scope is not None:
        rests += f" · {_n(in_scope)} of {_n(rc['total'])} hosts inside scoped subnets"
    return (
        f'<div class="conclusion" style="--tone:{_LABEL_COLOUR[label]}">'
        f'<p class="label">{_e(_LABEL_TEXT[label])}</p>'
        f'<p class="headline">{_e(text)}</p>'
        f'{reasons}'
        f'<p class="rests">{rests}.</p>'
        "</div>"
    )


def _measures(p: Dict[str, Any]) -> str:
    h = p["headline"]
    sev = h["active_exposure"]["by_severity"] or {}
    crit_high = int(sev.get("critical") or 0) + int(sev.get("high") or 0)
    rc = h["review_coverage"]
    unreviewed = max(0, rc["total"] - rc["reviewed"])
    systemic = h["systemic"]
    sev_line = " · ".join(
        f"{_n(sev.get(s))} {s}" for s in ("critical", "high", "medium", "low") if sev.get(s)
    ) or "none active"
    if systemic["adopted"]:
        weak_value = _n(systemic["blind_spot_count"])
        weak_note = ("no recurring conditions" if systemic["condition_count"] == 0
                     else f"of {_plural(systemic['condition_count'], 'recurring condition')} observed")
    else:
        weak_value, weak_note = "—", "not assessed — needs scoped subnets"
    review_note = f"{_pct(rc['pct'] / 100) if rc['pct'] is not None else 'no hosts yet'} · {_n(rc['validated_hosts'])} tested"
    if unreviewed:
        review_note += f" · {_n(unreviewed)} unreviewed"
    return (
        '<div class="measures">'
        '<div class="measure"><p class="name">Critical / high findings, active</p>'
        f'<p class="value">{_n(crit_high)}</p>'
        f'<p class="note">{_e(sev_line)}<br>{_n(h["active_exposure"]["active_findings"])} active findings · '
        f'{_n(h["detected_exposure"]["vuln_count"])} scanner observations, not yet judged, counted apart</p></div>'
        '<div class="measure"><p class="name">Hosts reviewed</p>'
        f'<p class="value">{_n(rc["reviewed"])} / {_n(rc["total"])}</p>'
        f'<p class="note">{review_note}</p></div>'
        '<div class="measure"><p class="name">Widespread weaknesses</p>'
        f'<p class="value">{weak_value}</p><p class="note">{weak_note}</p></div>'
        "</div>"
    )


def _focus(p: Dict[str, Any]) -> str:
    hm, focus = p.get("heatmap"), p.get("focus")
    lede = "Which segments carry the leading weakness disproportionately, and how complete the evidence behind each rate is."
    if not hm:
        return _section("Where to focus", '<p class="caption">No scoped subnets, so nothing can be compared by location.</p>', lede)
    lead = (focus or {}).get("lead_family")
    if not lead:
        return _section("Where to focus", (
            '<p class="caption">No recurring weakness was observed in any segment. That is only as strong '
            "as the evidence collected — the grid below shows which cells were never assessed.</p>"
        ), lede)
    row = next(r for r in hm["rows"] if r["family"] == lead)
    ranked: List[Dict[str, Any]] = focus["families"][lead]
    by_subnet = hm.get("group_by") == "subnet"
    unit = "subnet" if by_subnet else "site"
    body_rows = []
    for r in ranked[:_FOCUS_ROWS]:
        if r["state"] == "unassessed":
            figure, bar = '<em class="sub">not assessed</em>', '<div class="bar hatch"></div>'
        else:
            width = max(2 if r["affected"] else 0, (r["rate"] or 0) * 100)
            tick = ("" if r["rest_rate"] is None or len(ranked) == 1
                    else f'<span class="tick" style="left:calc({min(100, r["rest_rate"] * 100):.1f}% - 1px)"></span>')
            limited = " limited" if r["state"] == "limited" else ""
            bar = f'<div class="bar{limited}"><div class="fill" style="width:{width:.1f}%"></div>{tick}</div>'
            figure = f'{_n(r["affected"])} / {_n(r["assessed"])} · {_pct(r["rate"])}'
        note = f'<span class="sub">Limited comparison — {_e(r["limited_because"])}</span>' if r["state"] == "limited" else ""
        evidence = (f'{_n(r["assessed"])} of {_n(r["in_scope"])} in scope' if r["eligible"] is None
                    else f'{_n(r["eligible_assessed"])} of {_n(r["eligible"])} eligible')
        if r["unknown"]:
            evidence += f'<span class="sub">{_n(r["unknown"])} still unknown</span>'
        body_rows.append(
            f'<tr><td><strong>{_e(r["label"])}</strong></td>'
            f'<td><span class="num">{figure}</span>{bar}{note}</td>'
            f'<td class="num">{evidence}</td></tr>'
        )
    if len(ranked) == 1:
        caption = (f"Every in-scope host falls in this one {unit}, so there is nothing to compare it with — "
                   "this is a measurement, not a ranking.")
    else:
        caption = (
            "The black tick marks the rate across the rest of the assessed project. A comparison is limited below "
            f'{focus["limited_min_assessed"]} assessed hosts or {_pct(focus["limited_min_coverage"])} of eligible '
            f"hosts assessed — its counts stay, and it is ranked after the comparable {unit}s."
        )
        if len(ranked) > _FOCUS_ROWS:
            caption += f" {len(ranked) - _FOCUS_ROWS} more in the grid below."
    body = (
        f'<p class="lede" style="margin-top:-8px">Measure: <strong>{_e(row["family_label"])}</strong> — '
        f'{_plural(row["affected_total"], "host")} affected, assessed by {_e(row["evidence_domain_label"])} evidence.</p>'
        f'<table><thead><tr><th style="width:26%">{unit.capitalize()}</th>'
        f'<th>Affected / assessed</th><th style="width:26%">Evidence</th></tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table>'
        f'<p class="caption">{caption}</p>'
        f'<div class="why"><p>{_e(ranked[0]["sentence"])}</p>'
        '<p class="caption">A rate describes what was observed. It is not a cause: a deliberate legacy enclave, '
        "different asset roles or uneven scan depth can produce the same difference.</p></div>"
    )
    return _section("Where to focus", body, lede)


def _decisions(p: Dict[str, Any]) -> str:
    priorities = p.get("priorities") or []
    if not priorities:
        return _section("Decisions for this review", '<p class="caption">Nothing demands a decision right now.</p>')
    rows = []
    for item in priorities:
        kind = ("Assessment work — does not change the condition" if item.get("tier") == "work"
                else _PRIORITY_KIND.get(item.get("kind"), item.get("kind")))
        owner = f' · site owner {_e(item["owner"])}' if item.get("owner") else ""
        rows.append(
            f'<tr><td style="position:relative;padding-left:18px">{_dot(item.get("severity", ""))}'
            f'<strong>{_e(item.get("title"))}</strong><span class="sub">{_e(kind)}</span></td>'
            f'<td>{_e(item.get("blast_radius"))}{owner}</td><td>{_e(item.get("action"))}</td></tr>'
        )
    body = (
        '<table><thead><tr><th style="width:38%">Observed</th><th style="width:30%">Reach</th>'
        f'<th>Next step</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )
    return _section(
        "Decisions for this review", body,
        "What was observed, how far it reaches, and the next assessment step — ranked worst-first from the "
        "same signals that set the condition above.",
    )


def _grid(p: Dict[str, Any]) -> str:
    hm = p.get("heatmap")
    if not hm or not hm.get("rows") or not hm.get("segments"):
        return ""
    unit = "subnet" if hm.get("group_by") == "subnet" else "site"
    columns = hm["segments"][:_GRID_MAX_COLUMNS]
    head = "".join(
        f'<th>{_e(s["label"])}<span class="sub">{_n(s["in_scope"])} in scope</span></th>' for s in columns
    )
    body = []
    for row in hm["rows"]:
        cells = []
        for c in row["cells"][:len(columns)]:
            if c["unassessed"]:
                cells.append('<td class="cell hatch"><em class="sub">n/a</em></td>')
            elif c["affected"] == 0:
                cells.append(f'<td class="cell" style="color:var(--muted)">0/{_n(c["assessed"])}</td>')
            else:
                alpha = 0.12 + min(1.0, c["affected"] / c["assessed"]) * 0.43
                cells.append(
                    f'<td class="cell" style="background:rgba(var(--heat),{alpha:.2f})">'
                    f'<strong>{_n(c["affected"])}</strong>/{_n(c["assessed"])}</td>'
                )
        body.append(
            f'<tr><td><strong>{_e(row["family_label"])}</strong>'
            f'<span class="sub">via {_e(row["evidence_domain_label"])}</span></td>{"".join(cells)}</tr>'
        )
    total = p["headline"]["review_coverage"]["total"]
    in_scope = (p["systemic"].get("estate") or {}).get("hosts_in_scope", 0)
    outside = max(0, total - in_scope)
    lede = f"Covers the {_n(in_scope)} hosts inside scoped subnets, of {_n(total)} in the project."
    if outside:
        lede += f" {_plural(outside, 'host is', 'hosts are')} outside every scoped subnet and cannot appear here."
    caption = (
        f"Cells: affected / assessed hosts in the {unit}, where assessed = hosts with evidence that can detect the "
        "family. 0/N = checked, none found. Hatched n/a = unassessed — nobody looked, which is not the same as clean."
    )
    if len(hm["segments"]) > len(columns):
        caption = f"The {len(columns)} largest {unit}s of {len(hm['segments'])} are shown. " + caption
    first = 'style="width:16rem"' if len(columns) > 4 else 'style="width:40%"'
    table = (
        f'<table class="grid"><thead><tr><th {first}>Pattern family</th>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table><p class="caption">{caption}</p>'
    )
    return _section(f"Every family × {unit}", table, lede)


def _weaknesses(p: Dict[str, Any]) -> str:
    systemic = p.get("systemic") or {}
    conditions = systemic.get("conditions") or []
    if not systemic.get("adopted") or not conditions:
        return ""
    rows = []
    for c in conditions:
        spread = _SPREAD.get(c.get("classification"), "estate-wide" if c.get("is_blind_spot") else "localised")
        rows.append(
            f'<tr><td><strong>{_e(c.get("label"))}</strong><span class="sub">{_e(spread)}</span></td>'
            f'<td class="num">{_n(c.get("affected_hosts"))} ({_pct(c.get("host_fraction") or 0)})'
            f'<span class="sub">{_plural(c.get("subnet_spread") or 0, "subnet")} · '
            f'{_plural(c.get("site_spread") or 0, "site")}</span></td>'
            f'<td>{_e(c.get("recommended_action"))}</td></tr>'
        )
    body = (
        '<table><thead><tr><th style="width:34%">Condition</th><th style="width:22%">Hosts</th>'
        f'<th>Worth checking</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )
    return _section(
        "Recurring weaknesses", body,
        "Conditions observed across more than one host, widest first. Estate-wide ones are the widespread "
        "weaknesses counted above. A spread is an observation; its cause is a hypothesis to check.",
    )


def _findings(p: Dict[str, Any]) -> str:
    d = p["disposition"]
    by_status = d.get("by_status") or {}
    total = sum(by_status.values())
    if total == 0:
        return _section("Promoted findings", '<p class="caption">No findings recorded yet.</p>')
    items = []
    for name, statuses in _POPULATIONS:
        count = sum(int(by_status.get(s) or 0) for s in statuses)
        split = ""
        if len(statuses) > 1 and count:
            split = " · ".join(f"{_n(by_status[s])} {_STATUS_TEXT[s]}" for s in statuses if by_status.get(s))
            split = f'<span class="s"> ({split})</span>'
        items.append(f'<li><span class="n">{_n(count)}</span><span class="s">{name}</span>{split}</li>')
    analyst = d.get("non_scanner_active") or 0
    body = (
        f'<ul class="statuses">{"".join(items)}</ul>'
        f'<p class="caption">Of the active findings, {_n(analyst)} {"was" if analyst == 1 else "were"} raised by an '
        f'analyst and {_n(d.get("scanner_active"))} promoted from a scanner observation. Closed is a recorded '
        "conclusion (false positive, accepted risk or remediated), not a measure of improved security.</p>"
    )
    return _section("Promoted findings", body, "Where the findings that feed the report stand.")


def _provenance(p: Dict[str, Any], generated_at: datetime) -> str:
    evidence = p.get("evidence") or {}
    scans = int(evidence.get("scan_count") or 0)
    days = evidence.get("days_since_last_import")
    line = "No scans imported yet" if scans == 0 else _plural(scans, "scan")
    if scans and days is not None:
        line += " · last import today" if days == 0 else f" · last import {_plural(days, 'day')} before this briefing"
    return f"{generated_at.strftime('%d %B %Y, %H:%M')} UTC · {line}"


def render_posture_briefing(posture: Dict[str, Any], *, project_name: str, generated_at: datetime,
                            version: str = "") -> str:
    """The briefing for one project's ``compute_posture`` answer."""
    sections = "".join((
        _measures(posture),
        _focus(posture),
        _decisions(posture),
        _grid(posture),
        _weaknesses(posture),
        _findings(posture),
    ))
    built_by = f"BlueStick {_e(version)}" if version else "BlueStick"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Security posture — {_e(project_name)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header>
<p class="kicker">Security posture briefing</p>
<h1>{_e(project_name)}</h1>
<p class="provenance">{_provenance(posture, generated_at)}</p>
</header>
{_conclusion(posture)}
{sections}
<footer>
<dl>
<dt>The condition is a label, not a score</dt>
<dd>Action required: an active critical or high finding, an estate-wide weakness, or a critical finding at a
tier-1 or tier-2 site. Needs assessment: low review coverage, scanner observations nobody has judged, or a site
below its expected host count. Insufficient evidence: no scan evidence yet, so a quiet reading cannot be trusted.
Otherwise, no urgent signals.</dd>
<dt>Scanner observations are not findings</dt>
<dd>What the tools reported, per host, before an analyst judged it. They are counted beside the findings and
never added to them.</dd>
</dl>
A snapshot of this assessment as of the time above. Prepared with {built_by}.
</footer>
</main>
</body>
</html>"""
