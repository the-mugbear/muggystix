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
import React, { useCallback, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Loader2, RefreshCw, ShieldAlert } from 'lucide-react';

import {
  getEvidenceCoverage,
  getEvidenceGaps,
  type EvidenceCoverageResponse,
  type EvidenceMatrix,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { copyToClipboard } from '../utils/clipboard';
import { agentInstruction } from '../utils/agentRuns';
import AgentTaskButton from '../components/agent-sessions/AgentTaskButton';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import LastUpdated from '../components/LastUpdated';
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
 * hand exactly these hosts to the operator's agent to draft a plan.
 */
const GapPanel: React.FC<{ selection: Selection; onClose: () => void }> = ({ selection, onClose }) => {
  const toast = useToast();
  // One gap's hosts: another cell is another read, and never shows the
  // previous cell's hosts while it loads.
  const projectId = useProjectId();
  const gapsQuery = useQuery({
    queryKey: ['getEvidenceGaps', projectId, selection.domain, { segment: selection.segment }],
    queryFn: ({ signal }) => getEvidenceGaps(projectId, selection.domain, { segment: selection.segment, signal }),
  });
  const gaps = gapsQuery.data ?? null;
  const loading = gapsQuery.isPending;
  const error = queryErrorText(gapsQuery.error, 'Could not load the gap.');
  // (The panel is keyed by its cell, so "Show more" starts closed for each.)
  const [showAll, setShowAll] = useState(false);

  const where = selection.segmentLabel ?? 'the whole project';
  const copyIps = async () => {
    if (!gaps) return;
    const ok = await copyToClipboard(gaps.items.map((h) => h.ip_address).join('\n'));
    if (ok) toast.success(`Copied ${gaps.items.length} IP${gaps.items.length === 1 ? '' : 's'}${gaps.total > gaps.items.length ? ` (first ${gaps.items.length} of ${gaps.total})` : ''}`, { autoHideMs: 2500 });
    else toast.error('Could not copy to clipboard.');
  };

  // The scope caution travels with the hosts: the plan's rationale is what
  // the planner (human or agent) reads (2.374.4 review H7).
  const planRationale = gaps
    ? `${gaps.label} evidence missing in ${where}: ${gaps.action.text}${gaps.scope_caution ? ` ${gaps.scope_caution}` : ''}`
    : '';

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
          {/* The server decides from the declared scope (subnets AND names),
              host by host; the matrix column no longer does (review H7). */}
          {gaps.action.kind === 'confirm_scope' ? (
            <p className="mt-xs break-words text-caption text-warning" role="note">{gaps.action.text}</p>
          ) : (
            <p className="mt-xs break-words text-caption text-foreground">{gaps.action.text}</p>
          )}
          {gaps.scope_caution && (
            <p className="mt-xxs break-words text-caption text-warning" role="note">{gaps.scope_caution}</p>
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
              Showing the first {gaps.items.length} of {gaps.total.toLocaleString()} — Copy and Propose tests act on these {gaps.items.length}.
            </p>
          )}
          <div className="mt-xs flex flex-wrap gap-xs">
            <Button size="sm" variant="outline" onClick={() => void copyIps()} title="Copy the IPs as a target list for the collection step">
              Copy IPs
            </Button>
            {/* The collection step, handed to the operator's agent — only
                where the server says every listed host is in the declared
                scope: a task never names a host that still needs "confirm in
                scope", and with no scope declared none can be said to be. */}
            {gaps.action.kind === 'collect' && gaps.project_has_scope && !gaps.outside_scope && gaps.items.length > 0 && (
              <AgentTaskButton
                label="Collect with your agent"
                title="Hand these hosts to your agent to collect this evidence and upload it"
                instruction={agentInstruction.collectEvidence(gaps.items.map((h) => h.host_id), gaps.label)}
              />
            )}
            {/* 5.313.0 — handed to the operator's agent session as a task
                naming exactly these hosts. 5.320.0 — the agent proposes
                tests on each host (there is no plan to draft). */}
            <AgentTaskButton
              variant={gaps.action.kind === 'plan' ? 'default' : 'outline'}
              label="Propose tests"
              // A task names a fixed list of hosts: with none there is no task.
              disabled={gaps.items.length === 0}
              title={gaps.items.length === 0
                ? 'No hosts in this gap to hand over'
                : 'Hand these hosts to your agent to propose tests on them'}
              instruction={agentInstruction.proposeTests(gaps.items.map((h) => h.host_id), planRationale)}
            />
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
  // v5.294.0 (UX review) — hosts outside every scoped subnet are not a gap to
  // close: nobody has confirmed they are authorized. Their cells are drawn
  // neutral (no tint, no hatch), so the matrix never reads as "collect here".
  const unscoped = isUnscoped(matrix);
  const hasOutside = columns.some((s) => unscoped(s.key));
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
                <span className="line-clamp-2 break-words text-caption font-medium text-foreground"
                  title={unscoped(seg.key) ? `${seg.label} — confirm these hosts are in scope before collecting anything against them` : seg.label}>
                  {seg.label}
                </span>
                <span className="block text-caption text-muted-foreground">{seg.hosts.toLocaleString()} host{seg.hosts === 1 ? '' : 's'}</span>
                {unscoped(seg.key) && (
                  <span className="block text-caption italic text-muted-foreground">confirm in scope</span>
                )}
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
                      })}>
                      {total.numerator}/{total.denominator}
                    </button>
                  )}
                </td>
                {row.cells.slice(0, columns.length).map((cell, i) => {
                  const seg = columns[i];
                  const active = selection?.domain === row.domain && selection.segment === cell.segment;
                  const state = cell.eligible === 0 ? 'na' : cell.gap === 0 ? 'assessed' : cell.assessed === 0 ? 'none' : 'partial';
                  const outside = unscoped(cell.segment);
                  const title = state === 'na'
                    ? `${row.label} does not apply to any host in ${seg.label}`
                    : `${row.label} · ${seg.label}: ${cell.assessed} of ${cell.eligible} eligible hosts assessed`
                      + (outside && state !== 'assessed' ? ' — confirm these hosts are in scope first' : '');
                  return (
                    <td key={cell.segment} className="p-0 text-center align-middle">
                      <div className={cn('m-0.5 rounded px-xs py-1', active && 'ring-2 ring-ring')}
                        style={outside ? undefined : gapCellStyle(cell.eligible, cell.assessed)}
                        title={title} data-state={state} data-outside-scope={outside || undefined}>
                        {state === 'na' ? (
                          <span className="text-caption italic text-muted-foreground">n/a</span>
                        ) : state === 'assessed' ? (
                          <span className="tabular-nums text-muted-foreground">{cell.assessed}/{cell.eligible}</span>
                        ) : (
                          <button type="button"
                            className={cn(selectButton, outside ? 'text-muted-foreground' : 'font-medium text-foreground')}
                            aria-pressed={active}
                            aria-label={`${title} — show the ${cell.gap} not assessed`}
                            onClick={() => onSelect({
                              domain: row.domain, domainLabel: row.label,
                              segment: cell.segment, segmentLabel: seg.label,
                              gap: cell.gap, eligible: cell.eligible,
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
        Assessed / eligible hosts. Tinted = some not assessed (darker = more); hatched = none; n/a = does not apply.
        Select a tinted or hatched cell for its hosts and the step that closes the gap.
        {hasOutside && ' Hosts outside every scoped subnet are left untinted: they are not a gap to close until someone confirms they are in scope.'}
        {matrix.group_by === 'subnet' && ' No sites are defined, so hosts are grouped by their most-specific subnet.'}
      </p>
    </div>
  );
};

/**
 * Beside "assessed", never a different kind of it: of the hosts a vulnerability
 * scanner covered, how many it logged in to. It supports a judgment — a scan
 * that did not authenticate saw the host from outside, so few or no results
 * from it is weaker evidence — and each count opens exactly its hosts
 * (`vulnscan:` in the Hosts query).
 */
const CredentialedLine: React.FC<{ data: EvidenceCoverageResponse }> = ({ data }) => {
  const domain = data.domains.find((d) => d.key === 'vuln_assessment');
  const c = domain?.credentialed;
  if (!domain || !c || domain.coverage.numerator <= 0) return null;
  const count = (n: number, value: string, words: string) => (n > 0 ? (
    <Link to={`/hosts?q=${encodeURIComponent(`vulnscan:${value}`)}`} className={cn(selectButton, 'text-foreground')}
      aria-label={`${n.toLocaleString()} ${words} — open these hosts`}>
      {n.toLocaleString()}
    </Link>
  ) : <span className="tabular-nums">0</span>);
  return (
    <p className="mt-xs break-words text-caption text-muted-foreground" data-testid="credentialed-line">
      <span className="font-medium text-foreground">{domain.label}</span> —{' '}
      <span className="tabular-nums">{domain.coverage.numerator.toLocaleString()}</span> assessed:{' '}
      {count(c.credentialed, 'credentialed', 'credentialed')} credentialed,{' '}
      {count(c.not_credentialed, 'uncredentialed', 'not credentialed')} not credentialed,{' '}
      {count(c.credentials_not_stated, 'unstated', 'not stated')} not stated.{' '}
      An unauthenticated scan is weaker evidence; all three count as assessed.
    </p>
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
      Covers all {data.total_hosts.toLocaleString()} host{data.total_hosts === 1 ? '' : 's'} in the project, scoped or not
      (Patterns and the Posture grid count only scoped hosts). A gap is missing evidence, not a finding.
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
  // One project's coverage: another project's (or its selected cell) never
  // stays on screen — the key names the project, and `Layout` remounts the
  // page per project.
  const projectId = useProjectId();
  const coverage = useQuery({
    queryKey: ['getEvidenceCoverage', projectId],
    queryFn: ({ signal }) => getEvidenceCoverage(projectId, { signal }),
  });
  const data = coverage.data ?? null;
  const loading = coverage.isFetching;
  const error = queryErrorText(coverage.error, 'Could not load evidence coverage.');
  const loadedAt = useMemo(
    () => (coverage.dataUpdatedAt ? new Date(coverage.dataUpdatedAt) : null),
    [coverage.dataUpdatedAt],
  );
  const { refetch } = coverage;
  const reload = useCallback(() => { void refetch(); }, [refetch]);
  const [selection, setSelection] = useState<Selection | null>(null);

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
  // The server's "needs attention" rule (Operations, Scans, Ingestion
  // Results); a response without it is not counted, never read as zero.
  const attention = data?.data_quality.imports_needing_attention ?? null;
  const dismissed = data?.data_quality.imports_dismissed ?? 0;
  // A Largest-gaps row opens the same detail as its matrix cell, from its
  // name or its count.
  const openGap = (
    row: { domain: string; label: string },
    cell: { segment: string; gap: number; eligible: number },
    segmentLabel: string,
  ) => setSelection({
    domain: row.domain, domainLabel: row.label, segment: cell.segment,
    segmentLabel, gap: cell.gap, eligible: cell.eligible,
  });

  return (
    <div className="space-y-md p-md md:p-lg">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Evidence</h1>
        </div>
        <LastUpdated compact lastFetched={loadedAt} onRefresh={reload} isLoading={loading} label="evidence" />
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
            Evidence coverage is measured against the hosts in this project. Upload a scan or have your agent scan the scope, then come back.
          </PostureEmpty>
        ) : (
          <div className="space-y-lg">
            <EvidenceLead data={data} largest={largest[0]} />
            <PostureSection
              title="Where the gaps are"
            >
              {data.matrix ? (
                <CoverageMatrix data={data} matrix={data.matrix} selection={selection} onSelect={setSelection} />
              ) : (
                <p className="text-metadata text-muted-foreground">The per-segment breakdown is not available from this server version.</p>
              )}
              <CredentialedLine data={data} />
              {selection && (
                <GapPanel key={`${selection.domain}|${selection.segment ?? ''}`}
                  selection={selection} onClose={() => setSelection(null)} />
              )}
            </PostureSection>

            <PostureSection
              title={(
                <>
                  Largest gaps
                  <InfoTip text="The cells with the most eligible hosts not assessed, and the step that closes each. Ranked by hosts missing — not by age: this is one assessment window. Gaps on hosts outside the declared scope come last, and are not a collection task until someone confirms they are in scope." />
                </>
              )}
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
                            onClick={() => openGap(row, cell, segmentLabel)}>
                            {row.label}
                          </button>
                          <span className="block truncate text-caption text-muted-foreground" title={segmentLabel}>{segmentLabel}</span>
                        </td>
                        <td className="py-xs pr-md text-right tabular-nums text-foreground">
                          <button type="button"
                            className="rounded underline decoration-dotted decoration-muted-foreground underline-offset-4 hover:decoration-solid hover:decoration-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            aria-label={`${cell.gap.toLocaleString()} of ${cell.eligible.toLocaleString()} hosts not assessed — ${row.label} · ${segmentLabel}`}
                            onClick={() => openGap(row, cell, segmentLabel)}>
                            {cell.gap.toLocaleString()} <span className="text-muted-foreground">of {cell.eligible.toLocaleString()}</span>
                          </button>
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
                {attention == null ? 'imports needing attention could not be counted' : attention > 0 ? (
                  <Link to="/parse-errors?status=needs_attention" className="text-warning hover:underline">
                    {attention.toLocaleString()} import{attention === 1 ? '' : 's'} need{attention === 1 ? 's' : ''} attention — {attention === 1 ? 'its' : 'their'} data did not fully land →
                  </Link>
                ) : 'no import needs attention'}
                {dismissed > 0 && (
                  <>
                    {' · '}{dismissed.toLocaleString()} failed or partial import{dismissed === 1 ? '' : 's'} dismissed
                  </>
                )}
              </p>
            </PostureSection>
          </div>
        )
      ) : null}
    </div>
  );
};

export default Evidence;
