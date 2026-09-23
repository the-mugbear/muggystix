/**
 * Security Posture — Overview: the assessment's argument on one page.
 *
 * A snapshot, not a time series, and it ends at the report (no remediation or
 * response tracking). Top to bottom: the conclusion and what it rests on, four
 * quiet measures, ONE ranked comparison ("Where to focus"), the decisions it
 * leads to, then the full family × site grid and the promoted findings as
 * reference. Sections over thin rules, not cards (v5.254.0). Composes the
 * attention + systemic + finding aggregates (GET /posture); links DOWN into
 * Segments / Patterns / Evidence / Findings for the detail.
 *
 * UI-style-guide: tables are table-fixed with truncating cells; every state
 * (loading / error / empty) renders a safe fallback; no page-level overflow.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, ArrowUpRight, Clock, FileText, HelpCircle, Loader2, RefreshCw,
  ShieldCheck, Telescope,
} from 'lucide-react';

import {
  getPosture, type PostureResponse, type PriorityItem, type Severity,
} from '../services/api';
import { downloadSystemicReport, gridCellHostsHref } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { buildFindingsUrl, buildHostsUrl, reviewedHostsUrl } from '../utils/drilldownLinks';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Button } from '../components/ui/button';
// Plain-English "what is this / how it's derived" help — every measure
// explains itself on an explicit (i), never on hover alone.
import { InfoTip } from '../components/ui/info-tip';
import SeverityBar from '../components/ui/SeverityBar';
import DispositionPipeline from '../components/posture/DispositionPipeline';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import FocusComparison from '../components/posture/FocusComparison';
import {
  SEVERITY_HSL, LABEL_TONE, PRIORITY_KIND,
} from '../components/posture/postureTheme';

/** Grid columns shown before "the rest are on Segments" (largest first). */
const GRID_MAX_COLUMNS = 12;

const LABEL_ICON = {
  action_required: AlertTriangle,
  needs_assessment: Telescope,
  insufficient_evidence: HelpCircle,
  no_urgent_signals: ShieldCheck,
} as const;

const SevDot: React.FC<{ severity: Severity }> = ({ severity }) => (
  <span className="inline-block size-2.5 shrink-0 rounded-full"
    style={{ background: SEVERITY_HSL[severity] }} aria-hidden />
);


// Provenance — how many scans the snapshot rests on and when the last one was
// imported. Deliberately NOT a freshness judgment (v5.255.0): a project answers
// "what was the posture during this assessment window", so an older import is
// not a warning. Only "no scans at all" is — there is nothing to read.
const EvidenceCurrency: React.FC<{ evidence: PostureResponse['evidence'] }> = ({ evidence }) => {
  const days = evidence.scan_staleness_days;
  const text = evidence.scan_count === 0
    ? 'No scans yet'
    : days == null ? `${evidence.scan_count} scans`
      : days === 0 ? `${evidence.scan_count} scans · last import today`
        : `${evidence.scan_count} scans · last import ${days}d ago`;
  return (
    <span className={`inline-flex items-center gap-xxs text-caption ${evidence.scan_count === 0 ? 'text-warning' : 'text-muted-foreground'}`}
      title="What this snapshot rests on">
      <Clock className="size-3" aria-hidden /> {text}
    </span>
  );
};

const SecurityPosture: React.FC = () => {
  const { currentProject } = useProject();
  const [data, setData] = useState<PostureResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [reloadNonce, setReloadNonce] = useState(0);
  const toast = useToast();
  const [briefing, setBriefing] = useState(false);
  // "Create briefing" — the executive systemic report, from this page rather
  // than via the Hosts export detour. Synchronous standalone HTML; the
  // Overview has no site selection, so it is estate-wide here (Segments
  // offers the per-site variant).
  const createBriefing = useCallback(async () => {
    setBriefing(true);
    try {
      await downloadSystemicReport();
    } catch (e) {
      toast.error(formatApiError(e, 'Could not create the briefing.'));
    } finally {
      setBriefing(false);
    }
  }, [toast]);
  const load = useCallback(() => setReloadNonce((n) => n + 1), []);

  // Each fetch aborts the previous in-flight one — a rapid project switch
  // (A→B→A) or Refresh previously raced, letting a slower response win and
  // painting another project's posture onto this one. Keyed on the project id
  // so a switch re-fetches; the abort guard makes the last *intended* response
  // the one that lands.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getPosture({ signal: controller.signal })
      .then((d) => {
        if (controller.signal.aborted) return;
        setData(d); setError(null);
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setError(formatApiError(e, 'Could not load security posture.'));
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        setLoading(false);
      });
    return () => controller.abort();
  }, [currentProject?.id, reloadNonce]);

  // A project switch must not leave the PREVIOUS project's posture on screen
  // while the new one loads (a Refresh keeps the data; it is the same project).
  useEffect(() => { setData(null); setError(null); }, [currentProject?.id]);

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Security Posture</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            The assessment so far — the security condition, where weaknesses concentrate, and the
            highest-leverage next action. Every number is explainable and links to the detail.
          </p>
        </div>
        <div className="flex flex-col items-end gap-xs">
          <div className="flex items-center gap-xs">
            <Button size="sm" variant="outline" onClick={createBriefing} disabled={briefing}>
              {briefing
                ? <Loader2 className="size-3.5 animate-spin" aria-hidden />
                : <FileText className="size-3.5" aria-hidden />}
              Create briefing
            </Button>
            <Button size="sm" variant="outline" onClick={load} disabled={loading}>
              <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
            </Button>
          </div>
          {data && <EvidenceCurrency evidence={data.evidence} />}
        </div>
      </div>

      {loading && !data ? (
        <div className="flex items-center gap-xs" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Composing posture…</p>
        </div>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load posture</AlertTitle>
          <AlertDescription>
            <p className="break-words">{error}</p>
            <Button size="sm" variant="outline" className="mt-xs" onClick={load}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : data ? (
        // v5.254.0 — one argument, top to bottom: the conclusion and what it
        // rests on, four quiet measures on one baseline, ONE comparison, then
        // the decisions it leads to. Sections, not cards (PostureSection); the
        // full grid and the findings follow as reference. Target: conclusion,
        // comparison and the start of the decisions on the first screen.
        <div className="space-y-lg">
          <PostureConclusion data={data} />
          <ContextStrip data={data} />
          <WhereToFocus data={data} />
          <ReviewDecisions priorities={data.priorities} decisions={data.decisions} />
          <ConditionSegmentHeatmap data={data} />
          <PromotedFindings data={data} />
        </div>
      ) : null}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Security condition — leads with one plain-language conclusion, with the
// deterministic label as a chip and the top reasons as supporting detail.
// ---------------------------------------------------------------------------
const PostureConclusion: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const tone = LABEL_TONE[data.label];
  const Icon = LABEL_ICON[data.label];
  const rc = data.headline.review_coverage;
  const inScope = data.systemic.adopted ? data.systemic.estate?.hosts_in_scope : undefined;
  // The reasons after the first: the conclusion sentence IS the first one.
  const more = data.reasons.slice(1);
  return (
    <div className={`border-l-4 py-xs pl-md ${tone.borderClass}`}>
      <p className="flex items-center gap-xs text-caption uppercase tracking-wide text-muted-foreground">
        <Icon className={`size-4 shrink-0 ${tone.textClass}`} aria-hidden />
        <span className={`font-semibold ${tone.textClass}`}>{tone.text}</span>
        <InfoTip text="A deterministic label, not a score, and it follows what was OBSERVED — never who is assigned. Action required = any active critical/high finding, estate-wide weakness, or critical finding on a tier-1/2 site. Needs assessment = low review coverage, scanner observations nobody has judged, or a site below its expected host count. Insufficient evidence = no scan evidence yet, so a quiet reading can't be trusted. Otherwise No urgent signals. Unassigned findings, pending approvals and blocked runs are listed as work and do not change it." />
      </p>
      {/* The plain-language conclusion is the lead. */}
      <p className="mt-xxs break-words text-subheading font-semibold text-foreground">
        {safeFallback(data.conclusion?.text, tone.text)}
      </p>
      {more.length > 0 && (
        <ul className="mt-xs flex flex-wrap gap-x-lg gap-y-xxs">
          {more.map((r, i) => (
            <li key={i} className="flex min-w-0 items-start gap-xs text-metadata text-foreground">
              <span className="mt-1"><SevDot severity={r.severity} /></span>
              <span className="min-w-0 break-words">{r.text}</span>
            </li>
          ))}
        </ul>
      )}
      {/* What the conclusion rests on — beside it, not a trip to Evidence away. */}
      <p className="mt-xs break-words text-caption text-muted-foreground">
        Rests on: {rc.reviewed.toLocaleString()} of {rc.total.toLocaleString()} hosts reviewed
        {inScope != null && <> · {inScope.toLocaleString()} of {rc.total.toLocaleString()} hosts inside scoped subnets</>}
        {' · '}
        <Link to="/posture/evidence" className="text-info hover:underline">what has and hasn’t been assessed →</Link>
      </p>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Context strip — at most four quiet, linked measures on ONE baseline. They
// support the conclusion; they are not the page. (Were four stat cards, each
// with its own icon, meter and border; "Ownership" is gone — an unassigned
// finding is a row under Decisions, not a measure of the estate.)
// ---------------------------------------------------------------------------
// The measure itself is shared with Oversight (components/posture/PostureMeasure).
const Measure = PostureMeasure;

const ContextStrip: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const h = data.headline;
  const sev = h.active_exposure.by_severity;
  const criticalHigh = (sev.critical ?? 0) + (sev.high ?? 0);
  const unreviewed = h.review_coverage.total - h.review_coverage.reviewed;
  const needsEvidence = h.open_questions?.needs_evidence_hosts ?? 0;
  return (
    <div className="grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
      <Measure
        label="Critical / high findings, active"
        info="Promoted findings of critical or high severity that are under investigation or confirmed. Scanner observations nobody has judged are counted beside it, never added to it."
        value={criticalHigh.toLocaleString()}
        to={buildFindingsUrl({ status: 'active' })}
        toLabel={`${criticalHigh} critical or high active findings — view active findings`}
      >
        <SeverityBar counts={sev} variant="compact"
          segmentHref={(s) => buildFindingsUrl({ status: 'active', severity: s })} />
        <p className="mt-xxs truncate">
          {h.active_exposure.active_findings.toLocaleString()} active ·{' '}
          <span title="What the tools reported, per host, not yet judged by an analyst. Shown separately, never summed.">
            {h.detected_exposure.vuln_count.toLocaleString()} scanner observations
          </span>
        </p>
      </Measure>

      <Measure
        label="Hosts reviewed"
        info="Hosts an analyst has marked Reviewed, of every host in the project. It describes analyst activity — it is not proof a host was fully assessed; see Evidence for that. 'Tested' counts hosts with an executed test result."
        value={`${h.review_coverage.reviewed.toLocaleString()} / ${h.review_coverage.total.toLocaleString()}`}
        to={reviewedHostsUrl(true)}
        toLabel="Reviewed hosts — view"
      >
        <p className="truncate">
          {h.review_coverage.pct == null ? 'no hosts yet' : `${h.review_coverage.pct}%`}
          {' · '}{h.review_coverage.validated_hosts.toLocaleString()} tested
          {unreviewed > 0 && (
            <> · <Link to={reviewedHostsUrl(false)} className="text-info hover:underline">{unreviewed.toLocaleString()} unreviewed →</Link></>
          )}
        </p>
      </Measure>

      <Measure
        label="Still needs evidence"
        info="Hosts whose review concluded “needs more evidence” — an explicit record that a question is still open, not something inferred from counts. A host that went back into review no longer counts."
        value={needsEvidence.toLocaleString()}
        to={needsEvidence > 0 ? buildHostsUrl({ q: 'conclusion:needs_evidence' }) : undefined}
        toLabel={`${needsEvidence} hosts still needing evidence — view`}
      >
        <p className="truncate">{needsEvidence === 0 ? 'no open questions recorded' : 'open questions from finished reviews'}</p>
      </Measure>

      <Measure
        label="Widespread weaknesses"
        info="Conditions that recur across a meaningful share of hosts AND most sites (e.g. SMB signing disabled everywhere). Needs scoped subnets to assess. A spread is an observation; its cause is a hypothesis — see Patterns."
        value={h.systemic.adopted ? h.systemic.blind_spot_count.toLocaleString() : '—'}
        to={h.systemic.adopted ? '/posture/patterns' : undefined}
        toLabel="Widespread weaknesses — open Patterns"
      >
        {h.systemic.adopted ? (
          <p className="truncate">
            {h.systemic.condition_count === 0
              ? 'no recurring conditions'
              : `of ${h.systemic.condition_count} recurring condition${h.systemic.condition_count === 1 ? '' : 's'} observed`}
          </p>
        ) : (
          <Link to="/scopes" className="inline-flex items-center gap-xxs text-info hover:underline">
            Not assessed — needs scoped subnets <ArrowUpRight className="size-3" aria-hidden />
          </Link>
        )}
      </Measure>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Where to focus — the page's ONE primary visual: a ranked comparison for one
// named measure. When nothing recurs, or nothing is scoped, it says which.
// ---------------------------------------------------------------------------
const WhereToFocus: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const hm = data.heatmap;
  const hasAffected = Boolean(hm && hm.segments.length > 0 && hm.rows.some((r) => r.affected_total > 0));
  return (
    <PostureSection
      title="Where to focus"
      description="One measure at a time: which segments carry it disproportionately, and how complete the evidence behind each rate is."
    >
      {hm && hasAffected ? (
        <FocusComparison heatmap={hm} />
      ) : (
        <p className="text-metadata text-muted-foreground">
          {!hm
            ? <>No scoped subnets yet, so nothing can be compared by location. Group subnets into scopes and sites — see{' '}
              <Link to="/posture/segments" className="text-info hover:underline">Segments</Link>.</>
            : <>No recurring weakness was observed in any segment. That is only as strong as the evidence collected — the grid
              below shows which cells were never assessed.</>}
        </p>
      )}
    </PostureSection>
  );
};

// ---------------------------------------------------------------------------
// Condition × segment heatmap — the systemic hero. Rows are pattern families,
// columns are sites; each cell shows affected / in-scope hosts (not just a colour), and
// links to exactly those hosts.
// ---------------------------------------------------------------------------
// Cell tint scales with the affected fraction so the eye lands on the worst
// cells; the number is always shown (hover is never the only way to read it).
// Unassessed cells are hatched so they can never be mistaken for a clean 0/N:
// a diagonal stripe over the muted token, no heat.
const unassessedCellStyle: React.CSSProperties = {
  backgroundImage:
    'repeating-linear-gradient(135deg, hsl(var(--muted-foreground) / 0.18) 0 3px, transparent 3px 8px)',
};

const heatCellStyle = (fraction: number): React.CSSProperties => {
  if (fraction <= 0) return {};
  // 0.12 → 0.55 alpha over the destructive token as the fraction climbs.
  const alpha = 0.12 + Math.min(1, fraction) * 0.43;
  return { backgroundColor: `hsl(var(--destructive) / ${alpha.toFixed(2)})` };
};

export const ConditionSegmentHeatmap: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const hm = data.heatmap;
  // This grid covers hosts inside scoped subnets; the measures above it cover
  // every host in the project. Say so where the two meet, with the difference.
  const total = data.headline.review_coverage.total;
  const inScope = data.systemic.adopted ? data.systemic.estate?.hosts_in_scope : undefined;
  const unmapped = inScope == null ? null : Math.max(0, total - inScope);
  // Columns are sites — or subnets, in a project that defines no site.
  const unit = hm?.group_by === 'subnet' ? 'subnet' : 'site';
  // Largest first from the server. Past this many, columns only get narrower
  // and less readable; the rest are one click away on Segments, never dropped
  // from the comparison above (which ranks every column).
  const columns = hm ? hm.segments.slice(0, GRID_MAX_COLUMNS) : [];
  const hiddenColumns = hm ? hm.segments.length - columns.length : 0;
  return (
    <PostureSection
      title={<>
        Every family × {unit}
        <InfoTip text="Each row is a pattern family, each column a site (or, when the project defines no sites, a subnet). A cell shows affected / assessed hosts — assessed means the site's in-scope hosts that carry evidence in the domain that can detect this family (hover a row label for its domain), so 0 of N is 'checked, none found'. A hatched cell is unassessed: nobody looked, which is not the same as clean. Darker = a larger share affected. Click a cell to open exactly those hosts." />
      </>}
      description={inScope != null && (
        <>
          Covers the <span className="font-medium text-foreground">{inScope.toLocaleString()}</span> hosts
          inside scoped subnets, of {total.toLocaleString()} in the project.
          {unmapped ? ` ${unmapped.toLocaleString()} host${unmapped === 1 ? ' is' : 's are'} outside every scoped subnet and cannot appear here.` : ''}
        </>
      )}
      actions={<Link to="/posture/segments" className="text-info hover:underline">Segments →</Link>}
    >
        {!hm || hm.rows.length === 0 || hm.segments.length === 0 ? (
          <div className="py-lg text-center">
            <Telescope className="mx-auto mb-sm size-7 text-muted-foreground" aria-hidden />
            <p className="text-metadata text-foreground">
              {!hm ? 'No scoped subnets yet.' : 'No recurring weaknesses across your segments.'}
            </p>
            <p className="mx-auto mt-xxs max-w-sm text-caption text-muted-foreground">
              {!hm ? (
                <>Group subnets into scopes and sites to see weaknesses by location. See{' '}
                  <Link to="/posture/segments" className="text-info hover:underline">Segments</Link>.</>
              ) : 'Weaknesses that recur across sites will appear here as they are detected.'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            {/* Sized to its columns, not stretched to the page: with one or two
                columns a full-width table turned each cell into a banner. */}
            <table className="border-collapse text-metadata"
              style={{ tableLayout: 'fixed', width: `min(100%, calc(18rem + ${columns.length} * 9rem))` }}>
              <thead>
                <tr>
                  <th className="p-xs text-left align-bottom text-caption font-medium text-muted-foreground"
                    style={{ width: '18rem' }}>
                    Pattern family
                  </th>
                  {columns.map((seg) => (
                    <th key={seg.key} className="p-xs text-center align-bottom">
                      <span className="block truncate text-caption font-medium text-foreground" title={seg.label}>
                        {seg.label}
                      </span>
                      <span className="block text-caption text-muted-foreground">{seg.in_scope} in scope</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {hm.rows.map((row) => (
                  <tr key={row.family}>
                    <td className="p-xs align-middle">
                      <span className="block truncate font-medium text-foreground"
                        title={`${row.family_label} — assessed via ${row.evidence_domain_label} evidence`}>
                        {row.family_label}
                      </span>
                      <span className="block truncate text-caption text-muted-foreground" title={row.evidence_domain_label}>
                        via {row.evidence_domain_label}
                      </span>
                    </td>
                    {/* Cells come in segment order, so the first N are the shown columns. */}
                    {row.cells.slice(0, columns.length).map((cell) => {
                      const href = cell.affected > 0 ? gridCellHostsHref(row.conditions, cell) : null;
                      // Three states, three presentations: unassessed (hatched,
                      // "n/a"), assessed-and-clean ("—" over an assessed count),
                      // affected (count / assessed).
                      const title = cell.unassessed
                        ? `${row.family_label} — not assessed: no ${row.evidence_domain_label} evidence for this ${unit}'s hosts (${cell.in_scope} in scope)`
                        : `${row.family_label} — ${cell.affected} of ${cell.assessed} assessed hosts affected (${cell.in_scope} in scope)`;
                      const inner = cell.unassessed
                        ? <span className="text-caption italic text-muted-foreground">n/a</span>
                        : cell.affected === 0
                          ? <span className="text-muted-foreground">0/{cell.assessed}</span>
                          : <span className="font-medium tabular-nums text-foreground">{cell.affected}/{cell.assessed}</span>;
                      const style = cell.unassessed ? unassessedCellStyle : heatCellStyle(cell.value);
                      return (
                        <td key={cell.segment} className="p-0 text-center align-middle">
                          <div className="m-0.5 rounded px-xs py-1" style={style} title={title}
                            data-state={cell.unassessed ? 'unassessed' : cell.affected === 0 ? 'clean' : 'affected'}>
                            {href ? (
                              <Link to={href} className="hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                                aria-label={`${title} — view hosts`}>
                                {inner}
                              </Link>
                            ) : inner}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-xs text-caption text-muted-foreground">
              {hiddenColumns > 0 && (
                <span className="text-foreground">
                  Showing the {columns.length} largest {unit}s of {hm.segments.length} —{' '}
                  <Link to="/posture/segments" className="text-info hover:underline">all of them on Segments</Link>.{' '}
                </span>
              )}
              Cells: affected / assessed hosts in the {unit}, where assessed = hosts with evidence that
              can detect this family (the row's domain). 0/N = checked, none found. Hatched n/a =
              unassessed — nobody looked, which is not the same as clean.
            </p>
          </div>
        )}
    </PostureSection>
  );
};

// ---------------------------------------------------------------------------
// Management priorities — the ranked decision list.
// ---------------------------------------------------------------------------
const ReviewDecisions: React.FC<{
  priorities: PriorityItem[];
  decisions: PostureResponse['decisions'];
}> = ({ priorities, decisions }) => (
  <PostureSection
    title={<>
      Decisions for this review
      <InfoTip text="What was observed, how far it reaches, and the next assessment step — ranked worst-first from the same signals that set the security condition. Rows marked Assessment work (an unassigned finding) are things to do, and never change the condition; neither do pending approvals or blocked runs, which live in Operations." />
    </>}
    actions={(decisions.pending_approvals > 0 || decisions.blocked_sessions > 0) && (
      <Link to="/operations" className="text-info hover:underline">
        {[
          decisions.pending_approvals > 0 && `${decisions.pending_approvals} plan${decisions.pending_approvals === 1 ? '' : 's'} to approve`,
          decisions.blocked_sessions > 0 && `${decisions.blocked_sessions} blocked run${decisions.blocked_sessions === 1 ? '' : 's'}`,
        ].filter(Boolean).join(' · ')} in Operations →
      </Link>
    )}
  >
    {priorities.length === 0 ? (
      <p className="text-metadata text-muted-foreground">Nothing demands a decision right now.</p>
    ) : (
      <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
        <thead>
          <tr className="text-left text-caption text-muted-foreground">
            <th className="w-[38%] pb-xxs pr-md font-medium">Observed</th>
            <th className="w-[30%] pb-xxs pr-md font-medium">Reach</th>
            <th className="pb-xxs font-medium">Next step</th>
          </tr>
        </thead>
        <tbody>
          {priorities.map((p, i) => {
            const kind = PRIORITY_KIND[p.kind] ?? { label: p.kind, severity: p.severity };
            const title = (
              <span className="min-w-0 truncate font-medium text-foreground" title={p.title}>{p.title}</span>
            );
            return (
              <tr key={`${p.kind}-${i}`} className="border-t border-border/60 align-top" data-tier={p.tier ?? 'action'}>
                <td className="py-xs pr-md">
                  <div className="flex min-w-0 items-center gap-xs">
                    <SevDot severity={p.severity} />
                    {p.link
                      ? <Link to={p.link} className="flex min-w-0 hover:underline">{title}</Link>
                      : title}
                  </div>
                  <p className="mt-xxs truncate pl-[1.125rem] text-caption text-muted-foreground">
                    {p.tier === 'work' ? 'Assessment work — does not change the condition' : kind.label}
                  </p>
                </td>
                <td className="py-xs pr-md text-caption text-muted-foreground">
                  <span className="line-clamp-2 break-words" title={p.blast_radius}>
                    {p.blast_radius}
                    {p.owner && <span className="text-foreground"> · site owner {p.owner}</span>}
                  </span>
                </td>
                <td className="py-xs text-caption text-foreground">
                  <span className="line-clamp-2 break-words" title={p.action}>{p.action}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    )}
  </PostureSection>
);

// ---------------------------------------------------------------------------
// Finding disposition — scanner-confirmed kept visually separate.
// ---------------------------------------------------------------------------
const PromotedFindings: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const d = data.disposition;
  return (
    <PostureSection
      title={<>
        Promoted findings
        <InfoTip text="Where promoted findings stand — what feeds the report. Under investigation = open / retest; Confirmed = an analyst validated it; Closed = false positive, accepted risk or remediated — a recorded conclusion, each counted separately, and not a measure of improved security." />
      </>}
      actions={<Link to="/findings" className="text-info hover:underline">Findings →</Link>}
    >
      <DispositionPipeline byStatus={d.by_status}
        statusHref={(status) => buildFindingsUrl({ status: status as never })} />
      {/* Active split by ORIGIN, independent of status — never summed with it.
          No single "not scanner" predicate exists, so that figure stays
          passive: a plausible-but-wrong drill-down is worse than none (§26). */}
      <p className="mt-sm break-words text-caption text-muted-foreground">
        Of the active findings, <span className="font-medium tabular-nums text-foreground">{d.non_scanner_active}</span>{' '}
        {d.non_scanner_active === 1 ? 'was' : 'were'} raised by an analyst (note / manual / execution) and{' '}
        <Link to={buildFindingsUrl({ status: 'active', source: 'scanner' })} className="text-info hover:underline">
          <span className="tabular-nums">{d.scanner_active}</span> promoted from a scanner observation →
        </Link>
      </p>
    </PostureSection>
  );
};

export default SecurityPosture;
