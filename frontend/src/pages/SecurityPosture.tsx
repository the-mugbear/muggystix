/**
 * Security Posture — the manager-facing roll-up.
 *
 * A well-supported snapshot (not a time series — tests are rarely rerun): one
 * deterministic label + its reasons, four headline measures, where risk
 * concentrates, the ranked decisions, and the systemic/disposition/site
 * breakdowns. Composes the attention + systemic + finding + agent aggregates
 * (GET /posture); links DOWN into Insights / Systemic / Findings for the detail.
 *
 * UI-style-guide: tables are table-fixed with truncating cells; every state
 * (loading / error / empty) renders a safe fallback; no page-level overflow.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, ArrowUpRight, Clock, Eye, HelpCircle, Info, Loader2, RefreshCw, ShieldAlert,
  ShieldCheck, Telescope, Layers, UserCheck,
} from 'lucide-react';

import {
  getPosture, type PostureResponse, type PriorityItem, type Severity,
} from '../services/api';
import { familyCellHostsHref } from '../services/api/insights';
import { buildFindingsUrl, reviewedHostsUrl } from '../utils/drilldownLinks';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { Meter } from '../components/posture/PostureCharts';
import SeverityBar from '../components/ui/SeverityBar';
import DispositionPipeline from '../components/posture/DispositionPipeline';
import {
  SEVERITY_HSL, LABEL_TONE, PRIORITY_KIND,
} from '../components/posture/postureTheme';

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

// Plain-English "what is this / how it's derived" help — this is a management
// surface, so every metric explains itself on an explicit (i), not by making
// the operator guess. (Distinct from hiding the DATA behind hover.)
const InfoTip: React.FC<{ text: string }> = ({ text }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button type="button" aria-label="What is this and how is it derived?"
        className="inline-flex shrink-0 rounded text-muted-foreground/70 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Info className="size-3.5" aria-hidden />
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs text-left text-caption leading-snug">{text}</TooltipContent>
  </Tooltip>
);

// Evidence currency — how fresh the snapshot is. Stale/absent scans are
// themselves a posture signal, so this rides next to the headline.
const EvidenceCurrency: React.FC<{ evidence: PostureResponse['evidence'] }> = ({ evidence }) => {
  const days = evidence.scan_staleness_days;
  const text = evidence.scan_count === 0
    ? 'No scans yet'
    : days == null ? `${evidence.scan_count} scans`
      : days === 0 ? `${evidence.scan_count} scans · last today`
        : `${evidence.scan_count} scans · last ${days}d ago`;
  const stale = days != null && days >= 14;
  return (
    <span className={`inline-flex items-center gap-xxs text-caption ${stale || evidence.scan_count === 0 ? 'text-warning' : 'text-muted-foreground'}`}
      title="Evidence currency — how fresh this snapshot is">
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

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Security Posture</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            A management snapshot — the security condition, what changed, where weaknesses concentrate,
            and the highest-leverage next action. Every number is explainable and links to the detail.
          </p>
        </div>
        <div className="flex flex-col items-end gap-xs">
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
          </Button>
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
        <>
          {/* 1. The executive read — one conclusion + label + top reasons. */}
          <PostureLabelBanner data={data} />

          {/* 2. What changed — the in-engagement remediation trajectory. */}
          <RemediationFlow data={data} />

          {/* 3. Exposure vs. assurance — kept separate, never a single grade. */}
          <HeadlineMeasures data={data} />

          {/* 4. The systemic hero — where weaknesses concentrate (families × sites). */}
          <ConditionSegmentHeatmap data={data} />

          {/* 5. Highest-leverage actions beside finding disposition. The
              systemic detail now lives on the Patterns page; sites on Segments. */}
          <div className="grid items-start gap-md lg:grid-cols-2">
            <ManagementPriorities priorities={data.priorities} decisions={data.decisions} />
            <FindingDisposition data={data} />
          </div>

        </>
      ) : null}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Security condition — leads with one plain-language conclusion, with the
// deterministic label as a chip and the top reasons as supporting detail.
// ---------------------------------------------------------------------------
const PostureLabelBanner: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const tone = LABEL_TONE[data.label];
  const Icon = LABEL_ICON[data.label];
  return (
    <Card className={`border-l-4 ${tone.borderClass} ${tone.tintClass}`}>
      <CardContent className="flex flex-col gap-sm p-md md:flex-row md:gap-lg">
        <div className="flex min-w-0 flex-1 items-start gap-sm">
          <Icon className={`mt-0.5 size-7 shrink-0 ${tone.textClass}`} aria-hidden />
          <div className="min-w-0">
            <p className="flex items-center gap-xxs text-caption uppercase tracking-wide text-muted-foreground">
              Security condition
              <InfoTip text="A deterministic label, not a score. Action required = any unowned critical/high finding, estate blind spot, or hot tier-1/2 site. Needs assessment = low review coverage, untriaged scan data, or a site coverage gap. Insufficient evidence = no scan evidence yet, so a clear reading can't be trusted. Otherwise No urgent signals. Operational queues (pending approvals, blocked runs) are shown separately and do not change this label." />
            </p>
            {/* The plain-language conclusion is the executive read — the lead. */}
            <p className="mt-xxs text-subheading font-semibold text-foreground break-words">
              {safeFallback(data.conclusion?.text, tone.text)}
            </p>
            <span className={`mt-xs inline-flex items-center gap-xxs text-caption font-medium ${tone.textClass}`}>
              <SevDot severity={data.label === 'action_required' ? 'critical'
                : data.label === 'needs_assessment' ? 'medium' : 'low'} />
              {tone.text}
            </span>
          </div>
        </div>
        <ul className="min-w-0 flex-1 space-y-xxs md:max-w-sm md:border-l md:border-border md:pl-lg">
          {data.reasons.length === 0 ? (
            <li className="text-caption text-muted-foreground">No outstanding signals.</li>
          ) : data.reasons.map((r, i) => (
            <li key={i} className="flex items-start gap-xs text-metadata text-foreground">
              <span className="mt-1"><SevDot severity={r.severity} /></span>
              <span className="min-w-0">{r.text}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Headline measures — four cards, each with a micro-visual.
// ---------------------------------------------------------------------------
// One consistent stat-card shell so the row reads as a set: label + icon, a
// big number, a thin supporting visual, then a caption. (Replaces the mixed
// donut-gauge / stacked-bar cards that looked off against each other.)
const StatCard: React.FC<{
  label: string;
  icon: React.ReactNode;
  value: React.ReactNode;
  info: string;
  visual?: React.ReactNode;
  children?: React.ReactNode;
  /** Drill-down for the headline number (§26) — renders it as a link. */
  to?: string;
  toLabel?: string;
}> = ({ label, icon, value, info, visual, children, to, toLabel }) => (
  <Card>
    <CardContent className="flex h-full flex-col gap-sm p-md">
      <div className="flex items-center justify-between gap-xs">
        <span className="flex items-center gap-xxs text-caption text-muted-foreground">
          {label} <InfoTip text={info} />
        </span>
        <span className="text-muted-foreground" aria-hidden>{icon}</span>
      </div>
      {to ? (
        <Link to={to} aria-label={toLabel ?? `${label} — view`}
          className="text-page-title font-bold tabular-nums leading-none text-foreground hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded">
          {value}
        </Link>
      ) : (
        <p className="text-page-title font-bold tabular-nums leading-none text-foreground">{value}</p>
      )}
      <div className="flex h-6 items-center">{visual}</div>
      <div className="mt-auto">{children}</div>
    </CardContent>
  </Card>
);

const HeadlineMeasures: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const h = data.headline;
  const ownPct = h.ownership.pct;
  const conditions = data.systemic.conditions;
  const ActiveFindingsCard = (
    <StatCard
      label="Active findings"
      icon={<ShieldAlert className="size-4" />}
      value={h.active_exposure.active_findings}
      info="Curated findings still open, confirmed, or in retest — issues an analyst has accepted as real. Excludes resolved (remediated / false-positive / accepted-risk) and raw scanner detections (counted separately below)."
      to={buildFindingsUrl({ status: 'active' })}
      toLabel={`${h.active_exposure.active_findings} active findings — view`}
      visual={<SeverityBar counts={h.active_exposure.by_severity} variant="compact"
        segmentHref={(sev) => buildFindingsUrl({ status: 'active', severity: sev })} />}
    >
      <p className="text-caption text-muted-foreground">
        curated · open / confirmed / retest ·{' '}
        <span title="Scanner-detected vulnerabilities — raw, not analyst-curated. Shown separately, never summed.">
          {h.detected_exposure.vuln_count.toLocaleString()} scanner-detected
        </span>
      </p>
    </StatCard>
  );

  const SystemicCard = (
    <StatCard
      label="Systemic weaknesses"
      icon={<Layers className="size-4" />}
      value={h.systemic.adopted ? h.systemic.blind_spot_count : '—'}
      info="Weaknesses that recur estate-wide (e.g. SMB signing disabled on many hosts). Counted as 'blind spots' when one condition spans a meaningful share of hosts AND most sites. Derived from the systemic-insights analysis; needs scoped subnets to assess."
      visual={h.systemic.adopted ? (
        <div className="flex flex-wrap items-center gap-1">
          {conditions.length === 0
            ? <span className="text-caption text-muted-foreground">no recurring conditions</span>
            : conditions.slice(0, 10).map((c) => (
              <span key={c.key} className="size-2.5 rounded-full"
                title={`${c.label} — ${Math.round(c.host_fraction * 100)}% of hosts`}
                style={{ background: c.is_blind_spot ? 'hsl(var(--destructive))' : 'hsl(var(--warning))' }} />
            ))}
        </div>
      ) : <span className="text-caption text-warning">Not assessed</span>}
    >
      {h.systemic.adopted ? (
        <Link to="/posture/patterns" className="inline-flex items-center gap-xxs text-caption text-info hover:underline">
          estate blind spots · {h.systemic.condition_count} condition{h.systemic.condition_count === 1 ? '' : 's'}
          <ArrowUpRight className="size-3" aria-hidden />
        </Link>
      ) : (
        <Link to="/scopes" className="inline-flex items-center gap-xxs text-caption text-info hover:underline">
          Needs scoped subnets <ArrowUpRight className="size-3" aria-hidden />
        </Link>
      )}
    </StatCard>
  );

  const CoverageCard = (
    <StatCard
      label="Assessment coverage"
      icon={<Eye className="size-4" />}
      value={h.review_coverage.pct == null ? '—' : `${h.review_coverage.pct}%`}
      info="Share of discovered hosts an analyst has marked Reviewed — derived as reviewed ÷ total hosts. 'Validated' counts hosts with a completed test (a stronger signal than review)."
      to={reviewedHostsUrl(true)}
      toLabel="Reviewed hosts — view"
      visual={<Meter pct={h.review_coverage.pct} color="hsl(var(--info))" />}
    >
      <p className="text-caption text-muted-foreground">
        {h.review_coverage.reviewed.toLocaleString()} / {h.review_coverage.total.toLocaleString()} hosts reviewed
        {' · '}{h.review_coverage.validated_hosts.toLocaleString()} validated
      </p>
      {h.review_coverage.total - h.review_coverage.reviewed > 0 && (
        <Link to={reviewedHostsUrl(false)} className="text-caption text-info hover:underline">
          {(h.review_coverage.total - h.review_coverage.reviewed).toLocaleString()} unreviewed →
        </Link>
      )}
    </StatCard>
  );

  const OwnershipCard = (
    <StatCard
      label="Ownership"
      icon={<UserCheck className="size-4" />}
      value={ownPct == null ? '—' : `${ownPct}%`}
      info="Share of active findings with an assigned owner — derived as owned ÷ active findings. Unowned findings have nobody accountable to drive them to closure."
      visual={<Meter pct={ownPct} color={ownPct != null && ownPct < 60 ? 'hsl(var(--warning))' : 'hsl(var(--success))'} />}
    >
      <p className="text-caption text-muted-foreground">
        {h.ownership.owned} owned
        {h.ownership.unowned > 0 && (
          <Link to={buildFindingsUrl({ status: 'active', owner: 'unowned' })}
            className="text-warning hover:underline"> · {h.ownership.unowned} unowned →</Link>
        )}
      </p>
    </StatCard>
  );

  // Exposure and assurance shown SEPARATELY — never collapsed into one grade.
  // Exposure = what's wrong (findings, systemic spread); Assurance = how well
  // we know (coverage, ownership). A clean exposure with weak assurance is not
  // the same as a genuinely clean estate.
  return (
    <div className="grid gap-md lg:grid-cols-2">
      <section className="space-y-sm">
        <h2 className="flex items-center gap-xxs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
          Exposure <InfoTip text="What's wrong: curated active findings and weaknesses that recur across the estate." />
        </h2>
        <div className="grid gap-md sm:grid-cols-2">{ActiveFindingsCard}{SystemicCard}</div>
      </section>
      <section className="space-y-sm">
        <h2 className="flex items-center gap-xxs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
          Assurance <InfoTip text="How well we know: how much of the estate has been reviewed/validated, and whether findings have owners. High exposure with low assurance means the picture is both bad and incomplete." />
        </h2>
        <div className="grid gap-md sm:grid-cols-2">{CoverageCard}{OwnershipCard}</div>
      </section>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Remediation flow — the in-engagement trajectory: current active backlog by
// age, plus remediated / reopened counts and the unowned backlog.
// ---------------------------------------------------------------------------
const AGE_BAND_LABEL: Array<{ key: keyof PostureResponse['remediation_flow']['active_age_bands']; label: string }> = [
  { key: 'le_7d', label: '≤ 7d' },
  { key: 'le_30d', label: '8–30d' },
  { key: 'le_90d', label: '31–90d' },
  { key: 'gt_90d', label: '> 90d' },
];

const RemediationFlow: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const rf = data.remediation_flow;
  const bands = rf.active_age_bands;
  const maxBand = Math.max(1, ...AGE_BAND_LABEL.map((b) => bands[b.key]));
  return (
    <Card>
      <CardContent className="flex flex-col gap-md p-md lg:flex-row lg:items-center lg:gap-lg">
        <div className="flex items-center gap-lg">
          <Stat label="Active" value={rf.active_total}
            info="Curated findings still open / confirmed / in retest." />
          <Stat label="Remediated" value={rf.remediated} tone="positive"
            info="Findings marked remediated in this engagement." />
          <Stat label="Reopened" value={rf.reopened} tone={rf.reopened > 0 ? 'warning' : undefined}
            info="Findings that returned to an active state after being resolved — remediation that didn't hold." />
          <Stat label="Unowned" value={rf.unowned_backlog} tone={rf.unowned_backlog > 0 ? 'warning' : undefined}
            info="Active findings with nobody accountable to drive them to closure."
            to={rf.unowned_backlog > 0 ? buildFindingsUrl({ status: 'active', owner: 'unowned' }) : undefined} />
        </div>
        {/* Backlog aging — how long the active findings have been open. */}
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-xxs text-caption text-muted-foreground">
            Active backlog by age
            <InfoTip text="How long the current active findings have been open (from when each was created). A backlog skewed to the right is aging — remediation is not keeping pace." />
          </p>
          {/* Number (top) + a FIXED-height bar area + label (bottom). The bar
              lives in its own 36px box so the column can't overflow its parent
              and spill the number over the header above it (the parent had a
              44px cap while number+bar+label needed ~68px). */}
          <div className="mt-xs flex items-end gap-sm">
            {AGE_BAND_LABEL.map((b) => {
              const n = bands[b.key];
              const h = Math.round((n / maxBand) * 36);
              const old = b.key === 'gt_90d' || b.key === 'le_90d';
              return (
                <div key={b.key} className="flex min-w-0 flex-1 flex-col items-center gap-xxs">
                  <span className="text-caption tabular-nums text-muted-foreground">{n}</span>
                  <div className="flex w-full items-end" style={{ height: 36 }}>
                    <div className="w-full rounded-t"
                      style={{ height: Math.max(2, h), backgroundColor: old && n > 0 ? 'hsl(var(--warning))' : 'hsl(var(--info))' }} />
                  </div>
                  <span className="text-caption text-muted-foreground">{b.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

// A compact inline stat used by the remediation strip.
const Stat: React.FC<{
  label: string; value: number; info: string;
  tone?: 'positive' | 'warning'; to?: string;
}> = ({ label, value, info, tone, to }) => {
  const toneClass = tone === 'positive' ? 'text-success' : tone === 'warning' ? 'text-warning' : 'text-foreground';
  const num = to
    ? <Link to={to} className={`${toneClass} hover:underline`}>{value.toLocaleString()}</Link>
    : <span className={toneClass}>{value.toLocaleString()}</span>;
  return (
    <div className="min-w-0">
      <p className="flex items-center gap-xxs text-caption text-muted-foreground">{label} <InfoTip text={info} /></p>
      <p className="text-subheading font-bold tabular-nums leading-none">{num}</p>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Condition × segment heatmap — the systemic hero. Rows are pattern families,
// columns are sites; each cell shows affected/assessed (not just a colour), and
// links to exactly those hosts.
// ---------------------------------------------------------------------------
// Cell tint scales with the affected fraction so the eye lands on the worst
// cells; the number is always shown (hover is never the only way to read it).
const heatCellStyle = (fraction: number): React.CSSProperties => {
  if (fraction <= 0) return {};
  // 0.12 → 0.55 alpha over the destructive token as the fraction climbs.
  const alpha = 0.12 + Math.min(1, fraction) * 0.43;
  return { backgroundColor: `hsl(var(--destructive) / ${alpha.toFixed(2)})` };
};

const ConditionSegmentHeatmap: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const hm = data.heatmap;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-xs">
          Where weaknesses concentrate
          <InfoTip text="Each row is a pattern family, each column a site. A cell shows affected / assessed hosts — how many of the site's hosts carry that family of weakness. Darker = a larger share affected. Click a cell to open exactly those hosts. 'Assessed' is the site's in-scope host count for now; per-domain coverage refines it later." />
        </CardTitle>
      </CardHeader>
      <CardContent>
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
            <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
              <thead>
                <tr>
                  <th className="w-[26%] p-xs text-left align-bottom text-caption font-medium text-muted-foreground">
                    Pattern family
                  </th>
                  {hm.segments.map((seg) => (
                    <th key={seg.key} className="p-xs text-center align-bottom">
                      <span className="block truncate text-caption font-medium text-foreground" title={seg.label}>
                        {seg.label}
                      </span>
                      <span className="block text-caption text-muted-foreground">n={seg.assessed}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {hm.rows.map((row) => (
                  <tr key={row.family}>
                    <td className="p-xs align-middle">
                      <span className="block truncate font-medium text-foreground" title={row.family_label}>
                        {row.family_label}
                      </span>
                    </td>
                    {row.cells.map((cell) => {
                      const href = cell.numerator > 0
                        ? familyCellHostsHref(row.conditions, cell.drilldown_filter?.site)
                        : null;
                      const label = `${cell.numerator}/${cell.denominator}`;
                      const title = `${row.family_label} — ${cell.numerator} of ${cell.denominator} hosts affected`;
                      const inner = cell.numerator === 0
                        ? <span className="text-muted-foreground">—</span>
                        : <span className="font-medium tabular-nums text-foreground">{label}</span>;
                      return (
                        <td key={cell.segment} className="p-0 text-center align-middle">
                          <div className="m-0.5 rounded px-xs py-1" style={heatCellStyle(cell.value)} title={title}>
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
          </div>
        )}
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Management priorities — the ranked decision list.
// ---------------------------------------------------------------------------
const ManagementPriorities: React.FC<{
  priorities: PriorityItem[];
  decisions: PostureResponse['decisions'];
}> = ({ priorities, decisions }) => (
  <Card>
    <CardHeader>
      <CardTitle className="flex items-center justify-between gap-xs">
        <span className="flex items-center gap-xs">
          Highest-leverage actions
          <InfoTip text="The ranked next actions, worst-first — the same signals that set the security condition above (unowned critical/high findings, estate blind spots, hot tier-1/2 sites, low review coverage, untriaged scan data). Operational queue counts are shown as badges but never change the strategic label." />
        </span>
        {(decisions.pending_approvals > 0 || decisions.blocked_sessions > 0) && (
          <span className="flex gap-xxs">
            {decisions.pending_approvals > 0 && (
              <Badge variant="info">{decisions.pending_approvals} to approve</Badge>
            )}
            {decisions.blocked_sessions > 0 && (
              <Badge variant="warning">{decisions.blocked_sessions} blocked</Badge>
            )}
          </span>
        )}
      </CardTitle>
    </CardHeader>
    <CardContent className="p-0">
      {priorities.length === 0 ? (
        <p className="p-md text-caption text-muted-foreground">Nothing demands a decision right now.</p>
      ) : (
        <ol className="divide-y divide-border">
          {priorities.map((p, i) => {
            const kind = PRIORITY_KIND[p.kind] ?? { label: p.kind, severity: p.severity };
            const row = (
              <div className="flex items-start gap-sm px-md py-sm">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-caption font-semibold tabular-nums text-muted-foreground">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-xs">
                    <SevDot severity={p.severity} />
                    <span className="min-w-0 truncate font-medium text-foreground" title={p.title}>
                      {p.title}
                    </span>
                    <Badge variant="muted" className="shrink-0">{kind.label}</Badge>
                  </div>
                  <p className="mt-xxs truncate text-caption text-muted-foreground" title={p.blast_radius}>
                    {p.blast_radius}
                    {p.owner && <span className="text-foreground"> · owner {p.owner}</span>}
                  </p>
                  <p className="mt-xxs truncate text-caption text-foreground" title={p.action}>
                    → {p.action}
                  </p>
                </div>
                {p.link && <ArrowUpRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />}
              </div>
            );
            return (
              <li key={`${p.kind}-${i}`}>
                {p.link
                  ? <Link to={p.link} className="block hover:bg-muted/50">{row}</Link>
                  : row}
              </li>
            );
          })}
        </ol>
      )}
    </CardContent>
  </Card>
);

// ---------------------------------------------------------------------------
// Finding disposition — scanner-confirmed kept visually separate.
// ---------------------------------------------------------------------------
const FindingDisposition: React.FC<{ data: PostureResponse }> = ({ data }) => {
  const d = data.disposition;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-xs">
          <span className="flex items-center gap-xs">
            Finding disposition
            <InfoTip text="Where curated findings sit in their lifecycle. Active = open / confirmed / retest; Resolved = remediated / false-positive / accepted-risk. The two figures above split active findings by ORIGIN (analyst-raised vs scanner-sourced), which is independent of status." />
          </span>
          <Link to="/findings" className="text-caption text-info hover:underline">Findings →</Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
        {/* Active split by SOURCE/origin, not disposition — never summed.
            (How a finding originated, independent of its confirmation status.) */}
        <div className="grid grid-cols-2 gap-sm">
          {/* No single "not scanner" predicate, so non-scanner stays passive —
              a plausible-but-wrong drill-down is worse than none (§26). */}
          <div className="rounded-control border border-border p-sm">
            <p className="text-page-title font-bold tabular-nums text-foreground">{d.non_scanner_active}</p>
            <p className="text-caption text-muted-foreground">non-scanner active</p>
          </div>
          <Link to={buildFindingsUrl({ status: 'active', source: 'scanner' })}
            className="rounded-control border border-dashed border-border p-sm hover:bg-muted/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <p className="text-page-title font-bold tabular-nums text-muted-foreground">{d.scanner_active}</p>
            <p className="text-caption text-muted-foreground">scanner-sourced active →</p>
          </Link>
        </div>
        <p className="text-caption text-muted-foreground">By origin (note / manual / execution vs scanner) — not confirmation status.</p>

        <DispositionPipeline byStatus={d.by_status}
          statusHref={(status) => buildFindingsUrl({ status: status as never })} />
      </CardContent>
    </Card>
  );
};

export default SecurityPosture;
