/**
 * Evidence — "how much of the picture do we actually have?"
 *
 * The fourth posture tab. Where Posture / Patterns / Segments report WHAT was
 * observed and WHERE, this reports how much of the estate was assessed at all,
 * per assessment domain: an eligibility denominator (hosts the domain applies
 * to) and an assessed numerator (hosts that carry its evidence).
 *
 * v5.255.0 — the page leads with a domain × segment MATRIX instead of six
 * project-total cards: "web/TLS is 60% covered" did not say where the other 40%
 * was. Columns are the Overview grid's segments (sites, or subnets when the
 * project defines no site) plus the hosts outside every scoped subnet. A cell has
 * three states and no more — assessed, not assessed, not applicable. A project is
 * one assessment window: evidence does not go "stale" inside it, so nothing here
 * is coloured or ranked by age. Selecting a cell opens exactly its hosts, with
 * the step that closes the gap.
 *
 * UI-style-guide compliance: tables are table-fixed with truncating labels; no
 * page-level overflow; every state (loading / error / empty) renders a fallback.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, RefreshCw, ShieldAlert } from 'lucide-react';

import {
  getEvidenceCoverage,
  getEvidenceGaps,
  type EvidenceCoverageResponse,
  type EvidenceGapsResponse,
  type EvidenceMatrix,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { copyToClipboard } from '../utils/clipboard';
import { stashPlanSelection } from '../utils/planSelection';
import { formatApiError } from '../utils/apiErrors';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import PostureSection from '../components/posture/PostureSection';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import PostureEmpty from '../components/posture/PostureEmpty';
import { cn } from '../utils/cn';

const GAP_PREVIEW = 12;
/** Matrix columns shown before "the rest are on Segments" (largest first). */
const MATRIX_MAX_COLUMNS = 12;
/** Rows in the "largest gaps" list. */
const LARGEST_GAPS = 8;

/** What is selected: one cell, or (segment undefined) a domain's whole row. */
interface Selection {
  domain: string;
  domainLabel: string;
  segment?: string;
  segmentLabel?: string;
  gap: number;
  eligible: number;
  /** `all`: every listed host is outside the declared scope; `some`: a
   *  whole-project list that mixes them in. Drives the panel's caution. */
  outsideScope?: 'all' | 'some';
}

const UNMAPPED = 'unmapped';
const OUTSIDE_SCOPE_STEP =
  'Outside every scoped subnet. Confirm these hosts are in scope — e.g. reached through an in-scope name — before collecting anything more against them.';

/** "Is this segment outside the declared scope?" — only meaningful when the
 *  project HAS scoped subnets; with none, every host is unmapped and scope is
 *  simply not being used. */
const isUnscoped = (matrix?: EvidenceMatrix | null) => {
  const hasScoped = Boolean(matrix?.segments.some((s) => s.key !== UNMAPPED));
  return (segment?: string): boolean => hasScoped && segment === UNMAPPED;
};

const hatch: React.CSSProperties = {
  backgroundImage:
    'repeating-linear-gradient(135deg, hsl(var(--muted-foreground) / 0.18) 0 3px, transparent 3px 8px)',
};

/** One sequential scale for the MISSING share; hatched when nobody looked at all. */
const gapCellStyle = (eligible: number, assessed: number): React.CSSProperties => {
  if (eligible === 0 || assessed >= eligible) return {};
  if (assessed === 0) return hatch;
  const missing = (eligible - assessed) / eligible;
  return { backgroundColor: `hsl(var(--warning) / ${(0.1 + missing * 0.35).toFixed(2)})` };
};

/**
 * The selected gap as a list the operator can act on (v5.224.0; design review
 * item 4): the affected endpoints, the open ports that made them eligible, and
 * the step that closes the gap — copy the IPs for a scoped collection run, or
 * hand the hosts to the generate dialog as a fixed selection.
 */
const GapPanel: React.FC<{ selection: Selection; onClose: () => void }> = ({ selection, onClose }) => {
  const toast = useToast();
  const navigate = useNavigate();
  const [gaps, setGaps] = useState<EvidenceGapsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setGaps(null); setError(null); setShowAll(false);
    getEvidenceGaps(selection.domain, { segment: selection.segment, signal: controller.signal })
      .then((g) => { if (!controller.signal.aborted) setGaps(g); })
      .catch((e) => { if (!controller.signal.aborted) setError(formatApiError(e, 'Could not load the gap.')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selection.domain, selection.segment]);

  const where = selection.segmentLabel ?? 'the whole project';
  const copyIps = async () => {
    if (!gaps) return;
    const ok = await copyToClipboard(gaps.items.map((h) => h.ip_address).join('\n'));
    if (ok) toast.success(`Copied ${gaps.items.length} IP${gaps.items.length === 1 ? '' : 's'}${gaps.total > gaps.items.length ? ` (first ${gaps.items.length} of ${gaps.total})` : ''}`, { autoHideMs: 2500 });
    else toast.error('Could not copy to clipboard.');
  };

  const planThese = () => {
    if (!gaps) return;
    const ok = stashPlanSelection({
      host_ids: gaps.items.map((h) => h.host_id),
      rationale: `${gaps.label}: ${gaps.action.text}`,
      summary: `${gaps.items.length} hosts in ${where} with no ${gaps.label.toLowerCase()} evidence (Evidence page)`,
      taken_at: new Date().toISOString(),
    });
    // Navigating without the selection opened an UNRESTRICTED generate
    // dialog — a plan over the whole project instead of these hosts.
    if (!ok) {
      toast.error('Could not hand these hosts to the plan dialog (browser storage unavailable).');
      return;
    }
    navigate('/test-plans?generate=1&source=selection');
  };

  return (
    // A bordered panel on purpose: it is the page's one selected-detail surface.
    <div className="mt-sm rounded-panel border border-border p-sm" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-xs">
        <p className="min-w-0 break-words text-metadata text-foreground">
          <span className="font-semibold">{selection.domainLabel}</span> · <span className="break-all">{where}</span> —{' '}
          <span className="tabular-nums">{selection.gap.toLocaleString()} of {selection.eligible.toLocaleString()}</span>{' '}
          eligible host{selection.eligible === 1 ? '' : 's'} not assessed
        </p>
        <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
      </div>
      {loading && (
        <p className="mt-xs inline-flex items-center gap-xs text-caption text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading…
        </p>
      )}
      {error && <p className="mt-xs break-words text-caption text-destructive">{error}</p>}
      {gaps && (
        <>
          {/* The collection step is advice for IN-SCOPE hosts. Copy IPs and Plan
              these stay available — the analyst may know the hosts are in scope
              through a name — but never without saying what they are. */}
          {selection.outsideScope === 'all' || gaps.action.kind === 'confirm_scope' ? (
            <p className="mt-xs break-words text-caption text-warning" role="note">
              {gaps.action.kind === 'confirm_scope' ? gaps.action.text : OUTSIDE_SCOPE_STEP}
            </p>
          ) : (
            <p className="mt-xs break-words text-caption text-foreground">{gaps.action.text}</p>
          )}
          {selection.outsideScope === 'some' && (
            <p className="mt-xxs break-words text-caption text-warning" role="note">
              This whole-project list includes hosts outside every scoped subnet. Select a subnet or site column to leave them out.
            </p>
          )}
          <ul className="mt-xs grid gap-x-lg gap-y-xxs sm:grid-cols-2 xl:grid-cols-3" aria-label={`Hosts without ${gaps.label} evidence`}>
            {(showAll ? gaps.items : gaps.items.slice(0, GAP_PREVIEW)).map((h) => (
              <li key={h.host_id} className="flex min-w-0 flex-wrap items-baseline gap-x-xs text-caption">
                <Link to={`/hosts/${h.host_id}`} className="font-mono text-foreground hover:underline">{h.ip_address}</Link>
                {h.hostname && <span className="min-w-0 truncate text-muted-foreground" title={h.hostname}>{h.hostname}</span>}
                {h.ports.length > 0 && (
                  <span className="font-mono text-muted-foreground">{h.ports.join(', ')}</span>
                )}
              </li>
            ))}
          </ul>
          {gaps.items.length > GAP_PREVIEW && (
            <button type="button" onClick={() => setShowAll((v) => !v)}
              className="mt-xxs rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {showAll ? 'Show fewer' : `Show ${gaps.items.length - GAP_PREVIEW} more`}
            </button>
          )}
          {/* The buttons act on the hosts LISTED; say so when that is not all of them. */}
          {gaps.total > gaps.items.length && (
            <p className="mt-xxs text-caption text-muted-foreground">
              Showing the first {gaps.items.length} of {gaps.total.toLocaleString()} — Copy and Plan act on these {gaps.items.length}.
            </p>
          )}
          <div className="mt-xs flex flex-wrap gap-xs">
            <Button size="sm" variant="outline" onClick={() => void copyIps()} title="Copy the IPs as a target list for the collection step">
              Copy IPs
            </Button>
            <Button size="sm" variant={gaps.action.kind === 'plan' ? 'default' : 'outline'} onClick={planThese}
              title="Hand these hosts to the generate dialog as a fixed selection">
              Plan these
            </Button>
          </div>
        </>
      )}
    </div>
  );
};

// A dotted underline at rest: a figure that opens a list has to look like it
// does (in the first screenshot nothing distinguished it from a plain count).
const selectButton = 'rounded tabular-nums underline decoration-dotted decoration-muted-foreground underline-offset-4 hover:decoration-solid hover:decoration-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

const CoverageMatrix: React.FC<{
  data: EvidenceCoverageResponse;
  matrix: EvidenceMatrix;
  selection: Selection | null;
  onSelect: (s: Selection) => void;
}> = ({ data, matrix, selection, onSelect }) => {
  const columns = matrix.segments.slice(0, MATRIX_MAX_COLUMNS);
  const hidden = matrix.segments.length - columns.length;
  const unit = matrix.group_by === 'subnet' ? 'subnet' : 'site';
  const totals = new Map(data.domains.map((d) => [d.key, d.coverage]));
  const unscoped = isUnscoped(matrix);
  return (
    <div className="overflow-x-auto">
      {/* Sized to its columns, not stretched across the page. */}
      <table className="border-collapse text-metadata"
        style={{ tableLayout: 'fixed', width: `min(100%, calc(24rem + ${columns.length} * 9rem))` }}>
        <thead>
          <tr>
            <th className="p-xs text-left align-bottom text-caption font-medium text-muted-foreground" style={{ width: '16rem' }}>
              Assessment domain
            </th>
            <th className="p-xs text-center align-bottom text-caption font-medium text-muted-foreground" style={{ width: '8rem' }}>
              Whole project
            </th>
            {columns.map((seg) => (
              <th key={seg.key} className="p-xs text-center align-bottom">
                {/* Two lines before clamping: "Outside scoped subnets" was cut
                    to "Outside scoped subn…" in a 9rem column. */}
                <span className="line-clamp-2 break-words text-caption font-medium text-foreground" title={seg.label}>{seg.label}</span>
                <span className="block text-caption text-muted-foreground">{seg.hosts.toLocaleString()} host{seg.hosts === 1 ? '' : 's'}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.rows.map((row) => {
            const total = totals.get(row.domain);
            const totalGap = total ? Math.max(0, total.denominator - total.numerator) : 0;
            const rowSelected = selection?.domain === row.domain && !selection.segment;
            return (
              <tr key={row.domain} className="border-t border-border/60">
                <td className="p-xs align-middle">
                  <span className="block truncate font-medium text-foreground" title={row.label}>{row.label}</span>
                </td>
                <td className={cn('p-xs text-center align-middle', rowSelected && 'bg-muted/60')}>
                  {!total || total.denominator === 0 ? (
                    <span className="text-caption italic text-muted-foreground">n/a</span>
                  ) : totalGap === 0 ? (
                    <span className="tabular-nums text-muted-foreground">{total.numerator}/{total.denominator}</span>
                  ) : (
                    <button type="button" className={cn(selectButton, 'font-medium text-foreground')} aria-pressed={rowSelected}
                      aria-label={`${row.label}, whole project: ${total.numerator} of ${total.denominator} eligible hosts assessed — show the ${totalGap} not assessed`}
                      onClick={() => onSelect({
                        domain: row.domain, domainLabel: row.label, gap: totalGap, eligible: total.denominator,
                        outsideScope: row.cells.some((c) => unscoped(c.segment) && c.gap > 0) ? 'some' : undefined,
                      })}>
                      {total.numerator}/{total.denominator}
                    </button>
                  )}
                </td>
                {row.cells.slice(0, columns.length).map((cell, i) => {
                  const seg = columns[i];
                  const active = selection?.domain === row.domain && selection.segment === cell.segment;
                  const state = cell.eligible === 0 ? 'na' : cell.gap === 0 ? 'assessed' : cell.assessed === 0 ? 'none' : 'partial';
                  const title = state === 'na'
                    ? `${row.label} does not apply to any host in ${seg.label}`
                    : `${row.label} · ${seg.label}: ${cell.assessed} of ${cell.eligible} eligible hosts assessed`;
                  return (
                    <td key={cell.segment} className="p-0 text-center align-middle">
                      <div className={cn('m-0.5 rounded px-xs py-1', active && 'ring-2 ring-ring')}
                        style={gapCellStyle(cell.eligible, cell.assessed)} title={title} data-state={state}>
                        {state === 'na' ? (
                          <span className="text-caption italic text-muted-foreground">n/a</span>
                        ) : state === 'assessed' ? (
                          <span className="tabular-nums text-muted-foreground">{cell.assessed}/{cell.eligible}</span>
                        ) : (
                          <button type="button" className={cn(selectButton, 'font-medium text-foreground')} aria-pressed={active}
                            aria-label={`${title} — show the ${cell.gap} not assessed`}
                            onClick={() => onSelect({
                              domain: row.domain, domainLabel: row.label,
                              segment: cell.segment, segmentLabel: seg.label,
                              gap: cell.gap, eligible: cell.eligible,
                              outsideScope: unscoped(cell.segment) ? 'all' : undefined,
                            })}>
                            {cell.assessed}/{cell.eligible}
                          </button>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-xs text-caption text-muted-foreground">
        {hidden > 0 && (
          <span className="text-foreground">
            Showing the {columns.length} largest of {matrix.segments.length} columns —{' '}
            <Link to="/posture/segments" className="text-info hover:underline">all {unit}s on Segments</Link>.{' '}
          </span>
        )}
        Cells: assessed / eligible hosts. Tinted = some eligible hosts not assessed (darker = a larger share);
        hatched = none assessed; n/a = the domain applies to no host there. Select a tinted or hatched cell for its hosts.
        {matrix.group_by === 'subnet' && ' No sites are defined, so hosts are grouped by their most-specific subnet.'}
      </p>
    </div>
  );
};

/** The answer first: how many domains are complete, and the largest gap. */
const EvidenceLead: React.FC<{
  data: EvidenceCoverageResponse;
  largest?: { row: { label: string }; cell: { gap: number; eligible: number }; segmentLabel: string; outside: boolean };
}> = ({ data, largest }) => {
  const applicable = data.domains.filter((d) => d.coverage.denominator > 0);
  const complete = applicable.filter((d) => d.coverage.numerator >= d.coverage.denominator);
  const tone: LeadTone = applicable.length === 0 ? 'neutral'
    : complete.length === applicable.length ? 'clear'
      : complete.length === 0 ? 'critical' : 'warning';
  return (
    <PostureLead tone={tone} restsOn={<>
      Covers all {data.total_hosts.toLocaleString()} host{data.total_hosts === 1 ? '' : 's'} in the project, inside a scoped
      subnet or not — Patterns and the Posture grid count only hosts inside scoped subnets, so their totals can be smaller.
      A gap is missing evidence, not a finding: nothing observed where nobody looked is unknown, not clean.
    </>}>
      {applicable.length === 0
        ? 'No assessment domain applies to the hosts found so far.'
        : <>
          {complete.length} of {applicable.length} assessment domain{applicable.length === 1 ? '' : 's'} cover every eligible host
          {largest && !largest.outside
            ? <>; the largest gap is {largest.row.label} in <span className="break-all">{largest.segmentLabel}</span> —{' '}
              {largest.cell.gap.toLocaleString()} of {largest.cell.eligible.toLocaleString()} hosts not assessed.</>
            : '.'}
        </>}
    </PostureLead>
  );
};

const Evidence: React.FC = () => {
  const { currentProject } = useProject();
  const [data, setData] = useState<EvidenceCoverageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getEvidenceCoverage({ signal: controller.signal })
      .then((d) => { if (!controller.signal.aborted) { setData(d); setError(null); } })
      .catch((e) => { if (!controller.signal.aborted) setError(formatApiError(e, 'Could not load evidence coverage.')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [currentProject?.id, nonce]);

  // Another project's coverage (or its selected cell) must not stay on screen.
  useEffect(() => { setData(null); setError(null); setSelection(null); }, [currentProject?.id]);

  // Every cell with a gap, largest first — ranked over ALL columns, not only the
  // ones the matrix has room to draw.
  // Hosts outside every scoped subnet, in a project that HAS scoped subnets, are
  // not something to go and scan: nobody has confirmed they are authorized. Their
  // gaps are listed after the in-scope ones and get a different next step.
  const unscoped = useMemo(() => isUnscoped(data?.matrix), [data]);
  const largest = useMemo(() => {
    const m = data?.matrix;
    if (!m) return [];
    const labels = new Map(m.segments.map((s) => [s.key, s.label]));
    return m.rows
      .flatMap((row) => row.cells
        .filter((c) => c.gap > 0)
        .map((c) => ({ row, cell: c, segmentLabel: labels.get(c.segment) ?? c.segment, outside: unscoped(c.segment) })))
      .sort((a, b) => Number(a.outside) - Number(b.outside)
        || b.cell.gap - a.cell.gap || a.row.label.localeCompare(b.row.label));
  }, [data, unscoped]);
  const actions = useMemo(() => new Map((data?.domains ?? []).map((d) => [d.key, d.action?.text])), [data]);

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Evidence</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            How much of the picture this assessment actually has — which hosts carry evidence in each domain that applies
            to them, and where the gaps are.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={reload} disabled={loading}>
          <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
        </Button>
      </div>

      {loading && !data ? (
        <div className="flex items-center gap-xs" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Assessing evidence coverage…</p>
        </div>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load evidence</AlertTitle>
          <AlertDescription>
            <p className="break-words">{error}</p>
            <Button size="sm" variant="outline" className="mt-xs" onClick={reload}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : data ? (
        data.total_hosts === 0 ? (
          <PostureEmpty Icon={ShieldAlert} title="No hosts yet" action={{ to: '/scans', label: 'Upload a scan' }}>
            Evidence coverage is measured against the hosts in this project. Upload a scan or run recon, then come back.
          </PostureEmpty>
        ) : (
          <div className="space-y-lg">
            <EvidenceLead data={data} largest={largest[0]} />
            <PostureSection
              title="Where the gaps are"
              description="Each domain by site (or subnet): assessed ÷ eligible hosts. Select a tinted or hatched cell for its hosts and the step that closes the gap."
            >
              {data.matrix ? (
                <CoverageMatrix data={data} matrix={data.matrix} selection={selection} onSelect={setSelection} />
              ) : (
                <p className="text-metadata text-muted-foreground">The per-segment breakdown is not available from this server version.</p>
              )}
              {selection && <GapPanel selection={selection} onClose={() => setSelection(null)} />}
            </PostureSection>

            <PostureSection
              title="Largest gaps"
              description="The cells with the most eligible hosts not assessed, and the step that closes each. Ranked by hosts missing — not by age: this is one assessment window. Gaps on hosts outside the declared scope come last, and are not a collection task until someone confirms they are in scope."
            >
              {largest.length === 0 ? (
                <p className="text-metadata text-muted-foreground">Every eligible host carries evidence in every domain that applies to it.</p>
              ) : (
                <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
                  <thead>
                    <tr className="text-left text-caption text-muted-foreground">
                      <th className="w-[34%] pb-xxs pr-md font-medium">Domain · where</th>
                      <th className="w-[16%] pb-xxs pr-md text-right font-medium">Not assessed</th>
                      <th className="pb-xxs font-medium">Next step</th>
                    </tr>
                  </thead>
                  <tbody>
                    {largest.slice(0, LARGEST_GAPS).map(({ row, cell, segmentLabel, outside }) => (
                      <tr key={`${row.domain}-${cell.segment}`} className="border-t border-border/60 align-top">
                        <td className="py-xs pr-md">
                          <button type="button" className="block w-full min-w-0 truncate rounded text-left font-medium text-foreground underline decoration-dotted decoration-muted-foreground underline-offset-4 hover:decoration-solid hover:decoration-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            title={`${row.label} · ${segmentLabel}`}
                            onClick={() => setSelection({
                              domain: row.domain, domainLabel: row.label, segment: cell.segment,
                              segmentLabel, gap: cell.gap, eligible: cell.eligible,
                              outsideScope: outside ? 'all' : undefined,
                            })}>
                            {row.label}
                          </button>
                          <span className="block truncate text-caption text-muted-foreground" title={segmentLabel}>{segmentLabel}</span>
                        </td>
                        <td className="py-xs pr-md text-right tabular-nums text-foreground">
                          {cell.gap.toLocaleString()} <span className="text-muted-foreground">of {cell.eligible.toLocaleString()}</span>
                        </td>
                        <td className={cn('py-xs text-caption', outside ? 'text-warning' : 'text-foreground')}>
                          {outside ? (
                            <span className="line-clamp-3 break-words" title={OUTSIDE_SCOPE_STEP}>{OUTSIDE_SCOPE_STEP}</span>
                          ) : (
                            <span className="line-clamp-2 break-words" title={actions.get(row.domain)}>{actions.get(row.domain) ?? '—'}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {largest.length > LARGEST_GAPS && (
                <p className="mt-xs text-caption text-muted-foreground">
                  {largest.length - LARGEST_GAPS} smaller gap{largest.length - LARGEST_GAPS === 1 ? '' : 's'} not listed — every one is a cell in the matrix above.
                </p>
              )}
            </PostureSection>

            <PostureSection title="How each domain is judged">
              <dl className="grid gap-x-lg gap-y-xs md:grid-cols-2">
                {data.domains.map((d) => (
                  <div key={d.key} className="min-w-0">
                    <dt className="truncate text-metadata font-medium text-foreground" title={d.label}>{d.label}</dt>
                    <dd className="break-words text-caption text-muted-foreground">{d.note}</dd>
                  </div>
                ))}
              </dl>
            </PostureSection>

            <PostureSection
              title={<>
                Collection provenance
                <InfoTip text="Which tools' output was imported, and whether any file failed to parse (its data never landed). A scan count is provenance — it says nothing about how complete the assessment is; the matrix above does." />
              </>}
            >
              <div className="flex flex-wrap items-center gap-xs">
                {data.contributing_tools.length === 0 ? (
                  <span className="text-caption text-muted-foreground">No tools recorded yet.</span>
                ) : data.contributing_tools.map((t) => (
                  <Badge key={t.tool} variant="muted" className="max-w-[16rem] truncate" title={`${t.tool} — ${t.scans} scan${t.scans === 1 ? '' : 's'}`}>
                    {t.tool} · {t.scans}
                  </Badge>
                ))}
              </div>
              <p className="mt-xs text-caption text-muted-foreground">
                <span className="font-medium text-foreground">{data.data_quality.scans.toLocaleString()}</span> scan{data.data_quality.scans === 1 ? '' : 's'} imported ·{' '}
                {data.data_quality.parse_errors_unresolved > 0 ? (
                  <Link to="/parse-errors" className="text-warning hover:underline">
                    {data.data_quality.parse_errors_unresolved} unresolved parse error{data.data_quality.parse_errors_unresolved === 1 ? '' : 's'} — their data never landed →
                  </Link>
                ) : 'no unresolved parse errors'}
              </p>
            </PostureSection>
          </div>
        )
      ) : null}
    </div>
  );
};

export default Evidence;
