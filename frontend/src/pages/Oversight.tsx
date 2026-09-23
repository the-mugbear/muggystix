/**
 * Oversight (5.258.0; Posture layout + charts 5.259.0) — the global
 * administrators' programme dashboard.  Laid out like the Posture Overview:
 * a lead sentence, one strip of quiet measures, then sections over thin rules
 * (PostureSection) — no cards.
 *
 * Every registered project, archived included, filtered by UTC dates, project,
 * status, tester and engagement window.  Portfolio stays the members' page;
 * the two read the same counting service, so a project shows the same numbers
 * on both.  Design: PORTFOLIO.md.
 *
 * Every figure is labelled CURRENT (latest state, whatever the dates),
 * SELECTED PERIOD (events inside the dates) or THROUGH [END] (recorded up to
 * the end date).  Findings are issues; scanner observations are issue × host;
 * the two are shown side by side and never subtracted.  Nothing here judges
 * the age of evidence, and there is no remediation dimension.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Copy, RefreshCw } from 'lucide-react';

import {
  getOversightDashboard,
  OversightProjectRow,
  OversightQuery,
  OversightResponse,
  OversightSeverity,
  OversightTesterRow,
  SeverityBasis,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import { InfoTip } from '../components/ui/info-tip';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureLead from '../components/posture/PostureLead';
import GrowthCharts from '../components/oversight/GrowthCharts';
import JudgmentBySeverity from '../components/oversight/JudgmentBySeverity';
import ShareSummaryDialog from '../components/oversight/ShareSummaryDialog';
import ProjectMultiSelect from '../components/oversight/ProjectMultiSelect';
import { Input } from '../components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { formatStatusLabel } from '../utils/statusMeta';
import { describeProjects, parseProjectIds, serializeProjectIds } from '../utils/oversightProjects';
import { formatApiError } from '../utils/apiErrors';
import { formatRelativeTime } from '../utils/relativeTime';
import {
  DATE_PRESETS, DEFAULT_PRESET, DatePreset, customRangeError, presetRange,
} from '../utils/oversightDates';
import { SEVERITY_HSL } from '../utils/severity';

const SEVS = ['critical', 'high', 'medium', 'low'] as const;
type Sev = typeof SEVS[number];
const SEV_LABEL: Record<Sev, string> = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };
const STATUSES = ['active', 'in_progress', 'completed', 'archived'];
const ALL = '__all__';
const PAGE_SIZE = 25;

const REASON_LABEL: Record<string, string> = {
  critical: 'Critical', high: 'High', pending_review: 'Pending approval',
  blocked_session: 'Blocked run', no_admin: 'No project admin', quiet: 'Active but quiet',
  no_data: 'No inventory',
};

const n = (v: number) => v.toLocaleString();
const pct = (num: number, den: number) => (den > 0 ? `${Math.round((100 * num) / den)}%` : '—');
const rate = (r: number | null) => (r == null ? '—' : `${r}%`);
const name = (t: { full_name: string | null; username: string }) => t.full_name || t.username;
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : null);

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

const SevCells: React.FC<{ s: OversightSeverity; only?: readonly Sev[] }> = ({ s, only = SEVS }) => (
  <span className="inline-flex flex-wrap gap-x-sm tabular-nums">
    {only.map((k) => (
      <span key={k} title={SEV_LABEL[k]} className={s[k] ? 'text-foreground' : 'text-muted-foreground'}>
        <span className="mr-xxs inline-block size-2 rounded-full align-middle" style={{ background: SEVERITY_HSL[k] }} aria-hidden />
        <span className="sr-only">{SEV_LABEL[k]} </span>{n(s[k])}
      </span>
    ))}
  </span>
);

// ---------------------------------------------------------------------------
// Projects table
// ---------------------------------------------------------------------------

type ProjectSort = 'critical' | 'name' | 'targets' | 'tested' | 'last_import';

const sortProjects = (rows: OversightProjectRow[], sort: ProjectSort): OversightProjectRow[] => {
  const byName = (a: OversightProjectRow, b: OversightProjectRow) => a.name.localeCompare(b.name);
  const crit = (r: OversightProjectRow) => r.findings.critical + r.observations_unjudged.critical;
  const high = (r: OversightProjectRow) => r.findings.high + r.observations_unjudged.high;
  const cmp: Record<ProjectSort, (a: OversightProjectRow, b: OversightProjectRow) => number> = {
    critical: (a, b) => crit(b) - crit(a) || high(b) - high(a) || byName(a, b),
    name: byName,
    targets: (a, b) => b.host_count - a.host_count || byName(a, b),
    tested: (a, b) => (b.host_count ? b.hosts_tested / b.host_count : -1) - (a.host_count ? a.hosts_tested / a.host_count : -1) || byName(a, b),
    last_import: (a, b) => (b.last_scan_at ?? '').localeCompare(a.last_scan_at ?? '') || byName(a, b),
  };
  return [...rows].sort(cmp[sort]);
};

const sum = (s: OversightSeverity) => s.critical + s.high + s.medium + s.low;
const plural = (v: number, one: string, many = `${one}s`) => `${n(v)} ${v === 1 ? one : many}`;

// Every column says what it counts in its label; the (i) gives the exact rule.
// Vocabulary (utils/findingStatus): findings are analysts' conclusions, one per
// issue; scanner observations are what the tools reported, one per issue per
// host. The two are never added together.
const COLUMN_INFO = {
  tested: 'Hosts in review or reviewed by anyone, of the hosts in the inventory — each counted once. Watching is not testing. Current.',
  review: 'Hosts in review · hosts reviewed. Current.',
  findings: "Findings are analysts' conclusions, one per issue however many hosts it is on (critical to low). Their state: under investigation = open or retest; confirmed = validated; closed = accepted risk or remediated — the three add up to the total. False positives are not results and are counted apart.",
  observations: 'What the scanners reported, one per issue per host, critical to low (informational left out). Judged = a finding covers the observation on its host (promoted, dismissed there, or accepted); not yet judged = nobody has decided on it. Judged + not yet judged = the total.',
  withFinding: 'Of the tested hosts (in review or reviewed), the share with at least one critical — or high — finding that is not a false positive on that host. Both sides are tested hosts only, so it never passes 100%. Current.',
} as const;

/** How many findings, and where each stands; severity underneath. */
const FindingsCell: React.FC<{ r: OversightProjectRow }> = ({ r }) => {
  const total = sum(r.findings);
  const st = r.finding_states;
  return (
    <>
      {total === 0 ? (
        <span className="text-muted-foreground">No findings</span>
      ) : (
        <>
          <p className="font-medium tabular-nums">{plural(total, 'finding')}</p>
          <ul className="text-caption tabular-nums text-muted-foreground">
            <li className={st.under_investigation ? 'text-foreground' : undefined}>{n(st.under_investigation)} under investigation</li>
            <li className={st.confirmed ? 'text-foreground' : undefined}>{n(st.confirmed)} confirmed</li>
            <li>{n(st.closed)} closed</li>
          </ul>
          <div className="mt-xxs text-caption"><SevCells s={r.findings} /></div>
        </>
      )}
      {r.findings_false_positive > 0 && (
        <p className="text-caption text-muted-foreground">+ {plural(r.findings_false_positive, 'false positive')}, not counted</p>
      )}
    </>
  );
};

/** Every scanner observation, and how many are judged; the critical and
 *  high ones still waiting underneath. */
const ObservationsCell: React.FC<{ r: OversightProjectRow }> = ({ r }) => {
  const total = sum(r.observations);
  const notYet = sum(r.observations_unjudged);
  if (total === 0) return <span className="text-muted-foreground">None imported</span>;
  return (
    <>
      <p className="font-medium tabular-nums">{n(total)} total</p>
      <p className="text-caption tabular-nums text-muted-foreground">
        {n(sum(r.observations_judged))} judged · <span className={notYet ? 'text-foreground' : undefined}>{n(notYet)} not yet judged</span>
      </p>
      {r.observations_unjudged.critical + r.observations_unjudged.high > 0 && (
        <p className="mt-xxs text-caption text-muted-foreground">
          not yet judged: <SevCells s={r.observations_unjudged} only={['critical', 'high']} />
        </p>
      )}
    </>
  );
};

const HeadWithInfo: React.FC<{ label: string; info: string }> = ({ label, info }) => (
  <span className="inline-flex items-start gap-xxs">
    <span>{label}</span>
    <InfoTip text={info} label={`About ${label.toLowerCase()}`} />
  </span>
);

const ProjectsTable: React.FC<{
  rows: OversightProjectRow[];
  onOpen: (row: OversightProjectRow) => void;
  caption: string;
}> = ({ rows, onOpen, caption }) => (
  // v5.270.1 — seven columns, not nine: the window and the project admins
  // are facts ABOUT the project, so they sit under its name.  At 1,180 px
  // minimum the table scrolled at a normal window width and hid "Activity ·
  // attention"; it now fits at 960 px.  No bordered box (sections, not cards).
  <div className="overflow-x-auto border-t border-border">
    <Table aria-label={caption} className="min-w-[960px]" style={{ tableLayout: 'fixed' }}>
      <colgroup>
        <col style={{ width: '19%' }} /><col style={{ width: '10%' }} /><col style={{ width: '8%' }} />
        <col style={{ width: '17%' }} /><col style={{ width: '17%' }} /><col style={{ width: '11%' }} />
        <col style={{ width: '18%' }} />
      </colgroup>
      <TableHeader>
        <TableRow>
          <TableHead><HeadWithInfo label="Project" info="The project, its status and engagement window, and its project admins (someone with the admin role on it)." /></TableHead>
          <TableHead><HeadWithInfo label="Targets tested" info={COLUMN_INFO.tested} /></TableHead>
          <TableHead><HeadWithInfo label="In review · reviewed" info={COLUMN_INFO.review} /></TableHead>
          <TableHead><HeadWithInfo label="Findings and their state" info={COLUMN_INFO.findings} /></TableHead>
          <TableHead><HeadWithInfo label="Scanner observations" info={COLUMN_INFO.observations} /></TableHead>
          <TableHead><HeadWithInfo label="Tested hosts with a finding" info={COLUMN_INFO.withFinding} /></TableHead>
          <TableHead>Activity · attention</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.id} className="align-top">
            <TableCell>
              <button type="button" onClick={() => onOpen(r)} title={r.name}
                className="block max-w-full truncate text-left font-medium text-foreground hover:text-info focus:outline-none focus-visible:underline">
                {r.name}
              </button>
              <span className="block truncate text-caption tabular-nums text-muted-foreground">
                {formatStatusLabel(r.status)} · {day(r.start_date) ? `${day(r.start_date)} – ${day(r.end_date) ?? 'open'}` : 'no dates'}
              </span>
              {r.admins.length
                ? <span className="block truncate text-caption text-muted-foreground" title={`Project admins: ${r.admins.join(', ')}`}>Admin: {r.admins.join(', ')}</span>
                : <span className="mt-xxs block"><Badge variant="destructive">No project admin</Badge></span>}
            </TableCell>
            <TableCell className="tabular-nums">
              {r.host_count ? <>{n(r.hosts_tested)} / {n(r.host_count)} <span className="text-muted-foreground">({pct(r.hosts_tested, r.host_count)})</span></> : <span className="text-muted-foreground">No targets</span>}
              {r.targets_added > 0 && <div className="text-caption text-muted-foreground">+{n(r.targets_added)} in period</div>}
            </TableCell>
            <TableCell className="tabular-nums">{n(r.hosts_in_review)} · {n(r.hosts_reviewed)}</TableCell>
            <TableCell data-testid="findings-cell"><FindingsCell r={r} /></TableCell>
            <TableCell data-testid="observations-cell"><ObservationsCell r={r} /></TableCell>
            <TableCell className="text-caption tabular-nums">
              {r.defect_rate.critical == null && r.defect_rate.high == null ? (
                <span className="text-muted-foreground">Nothing tested</span>
              ) : (
                <>
                  <p>{rate(r.defect_rate.critical)} critical</p>
                  <p>{rate(r.defect_rate.high)} high</p>
                </>
              )}
            </TableCell>
            <TableCell>
              <span className="block text-caption text-muted-foreground">
                {r.last_scan_at ? `Last import ${formatRelativeTime(r.last_scan_at, { absoluteAfterDays: 30 })}` : 'No imports'}
                {r.imports > 0 && ` · ${n(r.imports)} in period`}
              </span>
              <span className="mt-xxs flex flex-wrap gap-xxs">
                {/* "No project admin" is already the Project admins cell. */}
                {r.attention_reasons.filter((code) => code !== 'no_admin').map((code) => (
                  <Badge key={code} variant={code === 'critical' || code === 'blocked_session' ? 'destructive' : 'muted'}>
                    {REASON_LABEL[code] ?? code}
                  </Badge>
                ))}
              </span>
            </TableCell>
          </TableRow>
        ))}
        {rows.length === 0 && (
          <TableRow><TableCell colSpan={7} className="py-md text-center text-muted-foreground">No matching projects.</TableCell></TableRow>
        )}
      </TableBody>
    </Table>
  </div>
);

// ---------------------------------------------------------------------------
// Testers table
// ---------------------------------------------------------------------------

const TestersTable: React.FC<{ rows: OversightTesterRow[]; caption: string; expandable?: boolean }> = ({ rows, caption, expandable = true }) => {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (id: number) => setOpen((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return (
    <div className="overflow-x-auto rounded-panel border border-border">
      <Table aria-label={caption} className="min-w-[900px]">
        <colgroup>
          <col style={{ width: '24%' }} /><col style={{ width: '9%' }} /><col style={{ width: '10%' }} />
          <col style={{ width: '9%' }} /><col style={{ width: '20%' }} /><col style={{ width: '9%' }} /><col style={{ width: '19%' }} />
        </colgroup>
        <TableHeader>
          <TableRow>
            <TableHead>Tester</TableHead>
            <TableHead title="Current memberships on in-progress projects">Projects</TableHead>
            <TableHead title="Hosts reviewed (current); in the period beneath">Reviewed</TableHead>
            <TableHead>In review</TableHead>
            <TableHead title="Findings (issues) on the hosts they reviewed or have in review — current">Findings on their targets</TableHead>
            <TableHead>Open tasks</TableHead>
            <TableHead>Last contribution</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((t) => (
            <React.Fragment key={t.user_id}>
              <TableRow>
                <TableCell>
                  <span className="flex min-w-0 items-center gap-xxs">
                    {expandable && t.projects.length > 0 && (
                      <button type="button" onClick={() => toggle(t.user_id)} aria-expanded={open.has(t.user_id)}
                        aria-label={`${open.has(t.user_id) ? 'Hide' : 'Show'} ${name(t)}'s projects`}
                        className="shrink-0 rounded-control p-xxs text-muted-foreground hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        {open.has(t.user_id) ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
                      </button>
                    )}
                    <span className="min-w-0 truncate font-medium" title={`${name(t)} (@${t.username})`}>{name(t)}</span>
                    {!t.is_active && <Badge variant="muted">Disabled</Badge>}
                  </span>
                </TableCell>
                <TableCell className="tabular-nums">{n(t.active_projects)}</TableCell>
                <TableCell className="tabular-nums">
                  {n(t.reviewed)}
                  {t.reviewed_in_period > 0 && <div className="text-caption text-muted-foreground">{n(t.reviewed_in_period)} in period</div>}
                </TableCell>
                <TableCell className="tabular-nums">{n(t.in_review)}</TableCell>
                <TableCell><SevCells s={t.findings} /></TableCell>
                <TableCell className="tabular-nums">{n(t.open_tasks)}</TableCell>
                <TableCell className="text-caption text-muted-foreground">
                  {formatRelativeTime(t.last_contribution_at, { absoluteAfterDays: 30, fallback: '—' })}
                </TableCell>
              </TableRow>
              {expandable && open.has(t.user_id) && t.projects.map((p) => (
                <TableRow key={`${t.user_id}-${p.project_id}`} className="bg-muted/30">
                  <TableCell className="pl-xl">
                    <span className="block truncate text-caption" title={p.project_name}>{p.project_name}</span>
                    <span className="text-caption text-muted-foreground">{p.role ? p.role : 'Former member'}</span>
                  </TableCell>
                  <TableCell />
                  <TableCell className="tabular-nums text-caption">
                    {n(p.reviewed)}{p.reviewed_in_period > 0 && ` (${n(p.reviewed_in_period)} in period)`}
                  </TableCell>
                  <TableCell className="tabular-nums text-caption">{n(p.in_review)}</TableCell>
                  <TableCell className="text-caption"><SevCells s={p.findings} /></TableCell>
                  <TableCell /><TableCell />
                </TableRow>
              ))}
            </React.Fragment>
          ))}
          {rows.length === 0 && (
            <TableRow><TableCell colSpan={7} className="py-md text-center text-muted-foreground">Nobody has a target in review or reviewed in these projects.</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const Oversight: React.FC = () => {
  const navigate = useNavigate();
  const { projects, selectProject } = useProject();
  const [params, setParams] = useSearchParams();

  const preset = (params.get('range') as DatePreset) || DEFAULT_PRESET;
  const tab = params.get('tab') || 'overview';
  // The project subset (`?projects=1,4,9`); empty = every project.
  const projectKey = params.get('projects') ?? params.get('project') ?? '';
  const projectIds = useMemo(() => parseProjectIds(new URLSearchParams({ projects: projectKey })), [projectKey]);
  const statusFilter = params.get('status') || ALL;
  const testerFilter = params.get('tester') || ALL;
  const overlap = params.get('overlap') === '1';
  const attn = params.get('attention') || '';
  const basis: SeverityBasis = params.get('basis') === 'period' ? 'period' : 'current';

  const range = useMemo(
    () => (preset === 'custom'
      ? { start: params.get('start') || undefined, end: params.get('end') || undefined }
      : presetRange(preset)),
    [preset, params],
  );
  const [draftStart, setDraftStart] = useState(range.start ?? '');
  const [draftEnd, setDraftEnd] = useState(range.end ?? '');
  const draftError = preset === 'custom' ? customRangeError(draftStart, draftEnd) : null;

  const query: OversightQuery = useMemo(() => ({
    start: range.start,
    end: range.end,
    project_id: projectIds,
    status: statusFilter !== ALL ? [statusFilter] : [],
    tester_id: testerFilter !== ALL ? Number(testerFilter) : undefined,
    window_overlap: overlap,
    severity_basis: basis,
  }), [range.start, range.end, projectIds, statusFilter, testerFilter, overlap, basis]);

  const [data, setData] = useState<OversightResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<ProjectSort>('critical');
  const [page, setPage] = useState(0);
  const [shareOpen, setShareOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getOversightDashboard(query)
      .then((r) => { if (!cancelled) setData(r); })
      .catch((err) => { if (!cancelled) setError(formatApiError(err, 'Failed to load Oversight.')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [query, nonce]);

  const setParam = useCallback((updates: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(updates).forEach(([k, v]) => (v == null || v === '' ? next.delete(k) : next.set(k, v)));
    setParams(next, { replace: true });
    setPage(0);
  }, [params, setParams]);

  const reset = () => { setParams(new URLSearchParams(), { replace: true }); setSearch(''); setPage(0); };

  const openProject = (row: OversightProjectRow) => {
    const proj = projects.find((p) => p.id === row.id);
    if (proj) selectProject(proj);
    navigate('/operations');
  };

  const filteredProjects = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    let rows = data.projects;
    if (attn) rows = rows.filter((r) => r.attention_reasons.includes(attn));
    if (q) rows = rows.filter((r) => r.name.toLowerCase().includes(q));
    return sortProjects(rows, sort);
  }, [data, search, attn, sort]);

  const inProgressPreview = useMemo(
    () => sortProjects((data?.projects ?? []).filter((r) => r.status === 'active' || r.status === 'in_progress'), 'critical').slice(0, 5),
    [data],
  );
  const testerPreview = useMemo(
    () => [...(data?.testers ?? [])].sort((a, b) => (b.reviewed + b.in_review) - (a.reviewed + a.in_review) || name(a).localeCompare(name(b))).slice(0, 5),
    [data],
  );
  const sortedTesters = useMemo(
    () => [...(data?.testers ?? [])].sort((a, b) => name(a).localeCompare(name(b))),
    [data],
  );

  const periodLabel = range.start || range.end
    ? `${range.start ?? 'the beginning'} – ${range.end ?? 'today'} (UTC)`
    : 'all time';
  const throughLabel = range.end ? `Through ${range.end}` : 'Through today';
  // The filters in words, for the copied summary (a pasted figure travels
  // without the page's filter row).
  const projectNames = useMemo(
    () => new Map((data?.project_options ?? []).map((o) => [o.id, o.name] as const)),
    [data],
  );
  // The subset in words, for the lead sentence and the copied summary.
  const projectScope = projectIds.length === 0
    ? null
    : `${projectIds.length === 1 ? 'Project' : `${projectIds.length} of ${projectNames.size || '?'} projects`}: ${describeProjects(projectIds, projectNames, 3)}`;
  const filterLabels = useMemo(() => {
    const out: string[] = [];
    if (projectIds.length) {
      const all = projectIds.map((id) => projectNames.get(id) ?? `#${id}`).join(', ');
      out.push(projectIds.length === 1 ? `Project: ${all}` : `Projects (${projectIds.length}): ${all}`);
    }
    if (statusFilter !== ALL) out.push(`Status: ${formatStatusLabel(statusFilter)}`);
    if (testerFilter !== ALL) {
      out.push(`Tester: ${data?.tester_options.find((o) => String(o.id) === testerFilter)?.name ?? `#${testerFilter}`}`);
    }
    if (overlap) out.push('Engagement window overlaps the period');
    return out;
  }, [data, projectIds, projectNames, statusFilter, testerFilter, overlap]);
  const activeFilters = projectIds.length > 0 || statusFilter !== ALL || testerFilter !== ALL || overlap || preset !== DEFAULT_PRESET || !!attn;

  const s = data?.summary;
  const pageRows = filteredProjects.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const pages = Math.max(1, Math.ceil(filteredProjects.length / PAGE_SIZE));

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Oversight</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            Every registered project for administrators: what is in progress, how much has been tested, what testing found,
            and who did the work. Every number is explained on its (i).
          </p>
        </div>
        <div className="flex flex-col items-end gap-xs">
          <div className="flex flex-wrap justify-end gap-xs">
            {/* v5.273.0 — the notebook's figures for these filters, to paste
                into an email or a chat. */}
            <Button size="sm" variant="outline" onClick={() => setShareOpen(true)} disabled={!data}>
              <Copy className="size-3.5" aria-hidden /> Copy summary
            </Button>
            <Button size="sm" variant="outline" onClick={() => setNonce((x) => x + 1)} disabled={loading}>
              <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
            </Button>
          </div>
          {data && (
            <span className="text-caption text-muted-foreground">
              Updated {formatRelativeTime(data.generated_at, { justNowBelowMs: 60_000 })}{error ? ' · showing the last figures that loaded' : ''}
            </span>
          )}
        </div>
      </div>

      {/* Filters — one row above everything they scope, no card around them. */}
      <div className="flex flex-col gap-xs border-b border-border pb-sm">
          <div className="flex flex-wrap items-end gap-sm">
            <label className="flex flex-col gap-xxs text-caption text-muted-foreground">
              Dates
              <Select value={preset} onValueChange={(v) => {
                if (v === 'custom') {
                  setParam({ range: 'custom', start: range.start ?? '', end: range.end ?? '' });
                  setDraftStart(range.start ?? ''); setDraftEnd(range.end ?? '');
                } else {
                  setParam({ range: v === DEFAULT_PRESET ? null : v, start: null, end: null });
                }
              }}>
                <SelectTrigger className="w-44" aria-label="Date range"><SelectValue /></SelectTrigger>
                <SelectContent>{DATE_PRESETS.map((p) => <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>)}</SelectContent>
              </Select>
            </label>
            {preset === 'custom' && (
              <>
                <label className="flex flex-col gap-xxs text-caption text-muted-foreground">
                  Start (UTC)
                  <Input type="date" value={draftStart} onChange={(e) => setDraftStart(e.target.value)} className="w-40" />
                </label>
                <label className="flex flex-col gap-xxs text-caption text-muted-foreground">
                  End (UTC)
                  <Input type="date" value={draftEnd} onChange={(e) => setDraftEnd(e.target.value)} className="w-40" />
                </label>
                <Button size="sm" disabled={!!draftError} onClick={() => setParam({ start: draftStart, end: draftEnd })}>Apply</Button>
              </>
            )}
            <div className="flex flex-col gap-xxs text-caption text-muted-foreground">
              Projects
              <ProjectMultiSelect
                options={data?.project_options ?? []}
                value={projectIds}
                onChange={(ids) => setParam({
                  projects: serializeProjectIds(ids, data?.project_options.length ?? 0),
                  project: null,
                })}
                disabled={!data && loading}
              />
            </div>
            <label className="flex flex-col gap-xxs text-caption text-muted-foreground">
              Status
              <Select value={statusFilter} onValueChange={(v) => setParam({ status: v === ALL ? null : v })}>
                <SelectTrigger className="w-40" aria-label="Status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>All statuses</SelectItem>
                  {STATUSES.map((st) => <SelectItem key={st} value={st}>{formatStatusLabel(st)}</SelectItem>)}
                </SelectContent>
              </Select>
            </label>
            <label className="flex flex-col gap-xxs text-caption text-muted-foreground">
              Tester
              <Select value={testerFilter} onValueChange={(v) => setParam({ tester: v === ALL ? null : v })}>
                <SelectTrigger className="w-44" aria-label="Tester"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Anyone</SelectItem>
                  {(data?.tester_options ?? []).map((o) => (
                    <SelectItem key={o.id} value={String(o.id)}><span className="block max-w-[14rem] truncate">{o.name}</span></SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            {activeFilters && <Button size="sm" variant="ghost" onClick={reset}>Reset</Button>}
          </div>
          {preset === 'custom' && draftError && <p className="text-caption text-destructive">{draftError}</p>}
          <label className="flex items-center gap-xs text-caption text-muted-foreground">
            <Checkbox checked={overlap} disabled={preset === 'all'}
              onCheckedChange={(v) => setParam({ overlap: v === true ? '1' : null })} />
            Only projects whose engagement window overlaps these dates (projects without dates are then left out)
          </label>
          <p className="text-caption text-muted-foreground">
            Period: {periodLabel}. Dates filter activity and growth; the other figures show the latest state unless they say otherwise.
          </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
            <span>{error}{data ? ' Showing the last figures that loaded.' : ''}</span>
            <Button size="sm" variant="outline" onClick={() => setNonce((x) => x + 1)}>Retry</Button>
          </AlertDescription>
        </Alert>
      )}

      {!data && loading && <p role="status" className="text-metadata text-muted-foreground">Loading Oversight…</p>}

      {data && s && (
        <Tabs value={tab} onValueChange={(v) => setParam({ tab: v === 'overview' ? null : v })}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="projects">Projects ({n(data.projects.length)})</TabsTrigger>
            <TabsTrigger value="testers">Testers ({n(data.testers.length)})</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-lg">
            {/* The lead: one plain sentence of fact — no label, no score. */}
            <PostureLead
              tone="info"
              restsOn={<>
                {n(s.severity.findings.critical + s.severity.findings.high)} critical and high findings;{' '}
                {n(s.severity.observations_unjudged.critical + s.severity.observations_unjudged.high)} critical and high
                scanner observations not yet judged.
              </>}
            >
              {projectScope && <>These figures cover {projectScope}.{' '}</>}
              {n(s.projects_in_progress)} project{s.projects_in_progress === 1 ? '' : 's'} in progress.{' '}
              {n(s.targets_tested)} of {n(s.targets_current)} targets tested ({pct(s.targets_tested, s.targets_current)}).
            </PostureLead>

            {/* Four quiet measures on one baseline (the Posture context strip). */}
            <div className="grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
              <PostureMeasure label="Projects" value={n(s.projects_total)}
                info="Registered projects matching the filters, archived included. In progress = active or in progress; complete = completed or archived. Current, whatever the dates.">
                <p className="truncate">{n(s.projects_in_progress)} in progress · {n(s.projects_complete)} complete</p>
              </PostureMeasure>
              <PostureMeasure label="Recorded targets" value={n(s.targets_through_end)}
                info={`Hosts in these projects' inventories, first recorded ${throughLabel.toLowerCase()} — one per IP per project; ports, CIDR ranges and DNS names never add targets. A host removed with its scan is not counted, so this is "recorded", not a lifetime total.`}>
                <p className="truncate">+{n(s.targets_added)} first recorded in the period</p>
              </PostureMeasure>
              <PostureMeasure label="Targets tested" value={`${n(s.targets_tested)} / ${n(s.targets_current)}`}
                info="Current hosts in review or reviewed by anyone, each counted once. Watching is not testing.">
                <p className="truncate">{pct(s.targets_tested, s.targets_current)} · {n(s.targets_in_review)} in review · {n(s.reviews_concluded)} reviews concluded in the period</p>
              </PostureMeasure>
              <PostureMeasure label="Contributors" value={n(s.contributors)}
                info="Distinct people who, in the period, uploaded a scan, wrote a note, recorded or re-dispositioned a finding, approved or rejected a plan, or concluded a host review — counted once across projects. Page views never count.">
                <p className="truncate">
                  {n(s.imports)} scans imported
                  {s.unattributed_events > 0 && ` · ${n(s.unattributed_events)} actions with no recorded author`}
                </p>
              </PostureMeasure>
            </div>

            <PostureSection
              title={<>Findings and scanner output <InfoTip text="Findings are analysts' conclusions, one per issue, false positives excluded. Scanner observations are what the tools reported, one per issue per host: judged when a finding covers the observation on its host (promoted, dismissed there, or accepted), otherwise not yet judged. &quot;Tested targets with a finding&quot; is the share of tested targets with at least one finding at that severity that is not a false positive there." /></>}
              description={basis === 'period'
                ? `Only findings and scanner observations first recorded ${periodLabel}; their judged state and the share of tested targets with a finding are today's.`
                : 'The latest state of every finding and scanner observation in these projects.'}
              actions={
                <div className="inline-flex rounded-control border border-border p-[2px]" role="group" aria-label="Severity figures">
                  {(['current', 'period'] as const).map((b) => (
                    <button key={b} type="button" aria-pressed={basis === b}
                      onClick={() => setParam({ basis: b === 'current' ? null : 'period' })}
                      className={`rounded-sm px-xs py-[2px] ${basis === b ? 'bg-accent font-medium text-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                      {b === 'current' ? 'Current' : 'First recorded in the period'}
                    </button>
                  ))}
                </div>
              }
            >
              <JudgmentBySeverity severity={s.severity} />
            </PostureSection>

            <PostureSection
              title="Target growth"
              description={`${periodLabel}, by UTC ${data.growth.unit}. Three charts on one date axis — a running total and two per-${data.growth.unit} counts are different scales.`}
            >
              <GrowthCharts unit={data.growth.unit} points={data.growth.points} />
            </PostureSection>

            <PostureSection title="Needs attention now" description="Current. The groups overlap, so they are never added up. Each count opens the projects behind it.">
              <ul className="flex flex-wrap gap-x-lg gap-y-xs text-metadata">
                {[
                  { code: 'critical', label: 'with a critical', value: data.attention.critical_projects, unit: 'projects' },
                  { code: 'pending_review', label: 'awaiting approval', value: data.attention.pending_approval_plans, unit: 'plans' },
                  { code: 'blocked_session', label: 'blocked', value: data.attention.blocked_runs, unit: 'runs' },
                  { code: 'no_admin', label: 'without a project admin', value: data.attention.no_admin_projects, unit: 'projects' },
                  { code: 'quiet', label: 'active but quiet for 14 days', value: data.attention.quiet_projects, unit: 'projects' },
                  { code: 'no_data', label: 'with no inventory', value: data.attention.no_inventory_projects, unit: 'projects' },
                ].map((a) => (
                  <li key={a.code}>
                    {a.value > 0 ? (
                      <button type="button" onClick={() => setParam({ tab: 'projects', attention: a.code })}
                        className="rounded text-left hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        <span className="font-semibold tabular-nums text-foreground">{n(a.value)}</span> {a.unit} {a.label}
                      </button>
                    ) : (
                      <span className="text-muted-foreground"><span className="tabular-nums">0</span> {a.unit} {a.label}</span>
                    )}
                  </li>
                ))}
              </ul>
            </PostureSection>

            <PostureSection
              title="Projects in progress"
              description="Worst first: critical, then high — findings or scanner output not yet judged."
              actions={<button type="button" className="text-info hover:underline" onClick={() => setParam({ tab: 'projects', attention: null })}>All projects →</button>}
            >
              {statusFilter === 'completed' || statusFilter === 'archived'
                ? <p className="text-caption text-muted-foreground">The status filter excludes projects in progress. <button type="button" className="text-info hover:underline" onClick={() => setParam({ status: null })}>Clear it</button></p>
                : <ProjectsTable rows={inProgressPreview} onOpen={openProject} caption="Projects in progress, worst first" />}
            </PostureSection>

            <PostureSection
              title="Testers"
              description="Most targets in review or reviewed first."
              actions={<button type="button" className="text-info hover:underline" onClick={() => setParam({ tab: 'testers' })}>All testers →</button>}
            >
              <TestersTable rows={testerPreview} caption="Most active testers" expandable={false} />
            </PostureSection>

            <p className="text-caption text-muted-foreground">
              Organisation accounts, all projects, current: {n(data.accounts.total)} registered,{' '}
              {n(data.accounts.enabled)} enabled, {n(data.accounts.disabled)} disabled,{' '}
              {n(data.accounts.without_membership)} without a project.
            </p>
          </TabsContent>

          <TabsContent value="projects" className="space-y-sm">
            <div className="flex flex-wrap items-center gap-sm">
              <Input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }}
                placeholder="Search this table" aria-label="Search projects in this table" className="w-64" />
              <Select value={sort} onValueChange={(v) => setSort(v as ProjectSort)}>
                <SelectTrigger className="w-52" aria-label="Sort projects"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="critical">Most critical, then high</SelectItem>
                  <SelectItem value="name">Name</SelectItem>
                  <SelectItem value="targets">Most targets</SelectItem>
                  <SelectItem value="tested">Highest share tested</SelectItem>
                  <SelectItem value="last_import">Most recent import</SelectItem>
                </SelectContent>
              </Select>
              {attn && (
                <Badge variant="outline" className="gap-xxs">
                  {REASON_LABEL[attn] ?? attn}
                  <button type="button" aria-label="Clear attention filter" onClick={() => setParam({ attention: null })} className="ml-xxs">×</button>
                </Badge>
              )}
              <span className="text-caption text-muted-foreground">
                {n(filteredProjects.length)} of {n(data.projects.length)} projects · search is local to this table
              </span>
            </div>
            <ProjectsTable rows={pageRows} onOpen={openProject} caption="All projects in the cohort" />
            {pages > 1 && (
              <div className="flex items-center justify-end gap-xs text-caption text-muted-foreground">
                <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
                <span>Page {page + 1} of {pages}</span>
                <Button size="sm" variant="outline" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>Next</Button>
              </div>
            )}
          </TabsContent>

          <TabsContent value="testers" className="space-y-sm">
            <p className="text-caption text-muted-foreground">
              A tester is anyone with a target in review or reviewed. Findings are counted through the targets each person worked on,
              so two reviewers of one host both get credit and these rows do not add up to the project totals.
              Removing a user also removes their review records.
            </p>
            <TestersTable rows={sortedTesters} caption="Testers" />
          </TabsContent>
        </Tabs>
      )}
      {data && (
        <ShareSummaryDialog
          open={shareOpen}
          onOpenChange={setShareOpen}
          data={data}
          periodLabel={periodLabel}
          filterLabels={filterLabels}
        />
      )}
    </div>
  );
};

export default Oversight;
