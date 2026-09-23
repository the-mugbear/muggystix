/**
 * Oversight (5.258.0) — the global administrators' programme dashboard.
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
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';

import {
  getOversightDashboard,
  OversightProjectRow,
  OversightQuery,
  OversightResponse,
  OversightSeverity,
  OversightTesterRow,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Checkbox } from '../components/ui/checkbox';
import { Input } from '../components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { formatStatusLabel } from '../utils/statusMeta';
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

const Basis: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span className="text-caption uppercase tracking-wide text-muted-foreground">{children}</span>
);

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

const Stat: React.FC<{ label: string; value: string; lines: React.ReactNode[]; basis: string }> = ({ label, value, lines, basis }) => (
  <Card className="min-w-0">
    <CardContent className="flex flex-col gap-xxs p-md">
      <span className="text-caption text-muted-foreground">{label}</span>
      <span className="text-heading font-bold tabular-nums text-foreground">{value}</span>
      {lines.map((l, i) => <span key={i} className="text-caption text-muted-foreground">{l}</span>)}
      <Basis>{basis}</Basis>
    </CardContent>
  </Card>
);

// ---------------------------------------------------------------------------
// Severity block
// ---------------------------------------------------------------------------

const SeverityBlock: React.FC<{ data: OversightResponse }> = ({ data }) => {
  const s = data.summary.severity;
  const rows: Array<{ label: string; hint: string; values: (k: Sev) => string; strong?: boolean; indent?: boolean }> = [
    { label: 'Findings (issues)', hint: 'Distinct findings; one finding on many hosts counts once; false positives excluded',
      values: (k) => n(s.findings[k]), strong: true },
    { label: 'Scanner observations', hint: 'Scanner rows: one issue on one host',
      values: (k) => n(s.observations[k]) },
    { label: 'judged', hint: 'A finding covers the observation on its host (promoted, dismissed there, or accepted)',
      values: (k) => n(s.observations_judged[k]), indent: true },
    { label: 'not yet judged', hint: 'No finding covers the observation on its host yet',
      values: (k) => n(s.observations_unjudged[k]), indent: true, strong: true },
    { label: 'Defect rate (tested targets)', hint: `Share of the ${n(s.tested_targets)} tested targets with at least one finding at that severity`,
      values: (k) => rate(s.defect_rate[k]) },
  ];
  return (
    <Card>
      <CardContent className="p-md">
        <div className="mb-xs flex flex-wrap items-baseline justify-between gap-xs">
          <h2 className="text-body font-semibold text-foreground">Severity</h2>
          <Basis>Current · {n(s.finding_affected_targets)} affected targets</Basis>
        </div>
        <div className="overflow-x-auto">
          <Table aria-label="Findings and scanner observations by severity">
            <colgroup><col style={{ width: '34%' }} />{SEVS.map((k) => <col key={k} />)}</colgroup>
            <TableHeader>
              <TableRow>
                <TableHead><span className="sr-only">Measure</span></TableHead>
                {SEVS.map((k) => (
                  <TableHead key={k} className="text-right">
                    <span className="mr-xxs inline-block size-2 rounded-full align-middle" style={{ background: SEVERITY_HSL[k] }} aria-hidden />
                    {SEV_LABEL[k]}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.label}>
                  <TableCell className={r.indent ? 'pl-lg text-muted-foreground' : 'font-medium'} title={r.hint}>
                    <span className="block truncate">{r.indent ? `└ ${r.label}` : r.label}</span>
                  </TableCell>
                  {SEVS.map((k) => (
                    <TableCell key={k} className={`text-right tabular-nums ${r.strong ? 'font-semibold' : ''}`}>{r.values(k)}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="mt-xs text-caption text-muted-foreground">
          Findings are issues and observations are issue × host, so the rows are compared, never subtracted.
          "Not yet judged" is scanner output still waiting for an analyst. Informational and unknown severities are left out.
        </p>
      </CardContent>
    </Card>
  );
};

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

const ProjectsTable: React.FC<{
  rows: OversightProjectRow[];
  onOpen: (row: OversightProjectRow) => void;
  caption: string;
}> = ({ rows, onOpen, caption }) => (
  <div className="overflow-x-auto rounded-panel border border-border">
    <Table aria-label={caption} className="min-w-[1100px]">
      <colgroup>
        <col style={{ width: '18%' }} /><col style={{ width: '9%' }} /><col style={{ width: '11%' }} />
        <col style={{ width: '10%' }} /><col style={{ width: '9%' }} /><col style={{ width: '13%' }} />
        <col style={{ width: '8%' }} /><col style={{ width: '8%' }} /><col style={{ width: '14%' }} />
      </colgroup>
      <TableHeader>
        <TableRow>
          <TableHead>Project</TableHead>
          <TableHead>Window</TableHead>
          <TableHead>Project admins</TableHead>
          <TableHead title="Tested (in review or reviewed) of recorded targets — current">Targets tested</TableHead>
          <TableHead title="In review · reviewed — current">Review</TableHead>
          <TableHead title="Findings (issues) — current">Findings</TableHead>
          <TableHead title="Critical · high scanner observations no finding covers on their host — current">Not judged C·H</TableHead>
          <TableHead title="Share of tested targets with a critical or high finding — current">Defect C·H</TableHead>
          <TableHead>Activity · attention</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.id}>
            <TableCell>
              <button type="button" onClick={() => onOpen(r)} title={r.name}
                className="block max-w-full truncate text-left font-medium text-foreground hover:text-info focus:outline-none focus-visible:underline">
                {r.name}
              </button>
              <span className="text-caption text-muted-foreground">{formatStatusLabel(r.status)}</span>
            </TableCell>
            <TableCell className="text-caption tabular-nums">
              {day(r.start_date) ? <>{day(r.start_date)}<br />{day(r.end_date) ?? 'open'}</> : <span className="text-muted-foreground">No dates</span>}
            </TableCell>
            <TableCell>
              {r.admins.length
                ? <span className="block truncate" title={r.admins.join(', ')}>{r.admins.join(', ')}</span>
                : <Badge variant="destructive">No project admin</Badge>}
            </TableCell>
            <TableCell className="tabular-nums">
              {r.host_count ? <>{n(r.hosts_tested)} / {n(r.host_count)} <span className="text-muted-foreground">({pct(r.hosts_tested, r.host_count)})</span></> : <span className="text-muted-foreground">No targets</span>}
              {r.targets_added > 0 && <div className="text-caption text-muted-foreground">+{n(r.targets_added)} in period</div>}
            </TableCell>
            <TableCell className="tabular-nums">{n(r.hosts_in_review)} · {n(r.hosts_reviewed)}</TableCell>
            <TableCell><SevCells s={r.findings} /></TableCell>
            <TableCell><SevCells s={r.observations_unjudged} only={['critical', 'high']} /></TableCell>
            <TableCell className="tabular-nums">{rate(r.defect_rate.critical)} · {rate(r.defect_rate.high)}</TableCell>
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
          <TableRow><TableCell colSpan={9} className="py-md text-center text-muted-foreground">No matching projects.</TableCell></TableRow>
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
  const projectFilter = params.get('project') || ALL;
  const statusFilter = params.get('status') || ALL;
  const testerFilter = params.get('tester') || ALL;
  const overlap = params.get('overlap') === '1';
  const attn = params.get('attention') || '';

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
    project_id: projectFilter !== ALL ? [Number(projectFilter)] : [],
    status: statusFilter !== ALL ? [statusFilter] : [],
    tester_id: testerFilter !== ALL ? Number(testerFilter) : undefined,
    window_overlap: overlap,
  }), [range.start, range.end, projectFilter, statusFilter, testerFilter, overlap]);

  const [data, setData] = useState<OversightResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<ProjectSort>('critical');
  const [page, setPage] = useState(0);

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
  const activeFilters = projectFilter !== ALL || statusFilter !== ALL || testerFilter !== ALL || overlap || preset !== DEFAULT_PRESET || !!attn;

  const s = data?.summary;
  const pageRows = filteredProjects.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const pages = Math.max(1, Math.ceil(filteredProjects.length / PAGE_SIZE));

  return (
    <div className="space-y-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-heading font-bold text-foreground">Oversight</h1>
          <p className="text-metadata text-muted-foreground">All registered projects · Administrator overview</p>
        </div>
        <div className="flex items-center gap-xs text-caption text-muted-foreground">
          {data && <span>Updated {formatRelativeTime(data.generated_at, { justNowBelowMs: 60_000 })}{error ? ' · stale' : ''}</span>}
          <Button size="sm" variant="outline" onClick={() => setNonce((x) => x + 1)} disabled={loading}>
            <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
          </Button>
        </div>
      </div>

      {/* Filters — one shared cohort for every section below. */}
      <Card>
        <CardContent className="flex flex-col gap-sm p-md">
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
            <label className="flex flex-col gap-xxs text-caption text-muted-foreground">
              Project
              <Select value={projectFilter} onValueChange={(v) => setParam({ project: v === ALL ? null : v })}>
                <SelectTrigger className="w-52" aria-label="Project"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>All projects</SelectItem>
                  {(data?.project_options ?? []).map((o) => (
                    <SelectItem key={o.id} value={String(o.id)}><span className="block max-w-[16rem] truncate">{o.name}</span></SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
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
            Period: {periodLabel}. Dates filter activity; figures marked Current show the latest state.
          </p>
        </CardContent>
      </Card>

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

          <TabsContent value="overview" className="space-y-md">
            <div className="grid gap-sm sm:grid-cols-2 xl:grid-cols-4">
              <Stat label="Projects" value={n(s.projects_total)} basis="Current"
                lines={[`${n(s.projects_in_progress)} in progress`, `${n(s.projects_complete)} complete or archived`]} />
              <Stat label="Recorded targets" value={n(s.targets_through_end)} basis={throughLabel}
                lines={[`+${n(s.targets_added)} first recorded in the period`, 'Hosts; removed records are not included']} />
              <Stat label="Targets tested" value={`${n(s.targets_tested)} of ${n(s.targets_current)}`} basis="Current"
                lines={[`${pct(s.targets_tested, s.targets_current)} · ${n(s.targets_in_review)} in review`, `${n(s.reviews_concluded)} reviews concluded in the period`]} />
              <Stat label="Contributors" value={n(s.contributors)} basis="Selected period"
                lines={[`${n(s.imports)} scans imported`, s.unattributed_events ? `${n(s.unattributed_events)} actions with no recorded author` : 'Distinct people across projects']} />
            </div>

            <SeverityBlock data={data} />

            <Card>
              <CardContent className="flex flex-col gap-xs p-md">
                <div className="flex flex-wrap items-baseline justify-between gap-xs">
                  <h2 className="text-body font-semibold text-foreground">Needs attention now</h2>
                  <Basis>Current · groups overlap, never summed</Basis>
                </div>
                <div className="flex flex-wrap gap-sm">
                  {[
                    { code: 'critical', label: 'With critical', value: data.attention.critical_projects, unit: 'projects' },
                    { code: 'pending_review', label: 'Pending approvals', value: data.attention.pending_approval_plans, unit: 'plans' },
                    { code: 'blocked_session', label: 'Blocked runs', value: data.attention.blocked_runs, unit: 'runs' },
                    { code: 'no_admin', label: 'No project admin', value: data.attention.no_admin_projects, unit: 'projects' },
                    { code: 'quiet', label: 'Active but quiet', value: data.attention.quiet_projects, unit: 'projects' },
                    { code: 'no_data', label: 'No inventory', value: data.attention.no_inventory_projects, unit: 'projects' },
                  ].map((a) => (
                    <button key={a.code} type="button" onClick={() => setParam({ tab: 'projects', attention: a.code })}
                      className="flex flex-col items-start rounded-control border border-border px-sm py-xs text-left hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <span className="text-body font-bold tabular-nums text-foreground">{n(a.value)}</span>
                      <span className="text-caption text-muted-foreground">{a.label} · {a.unit}</span>
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>

            <section className="space-y-xs">
              <div className="flex flex-wrap items-baseline justify-between gap-xs">
                <h2 className="text-body font-semibold text-foreground">Projects in progress</h2>
                <Button size="sm" variant="link" onClick={() => setParam({ tab: 'projects', attention: null })}>View all projects →</Button>
              </div>
              {statusFilter === 'completed' || statusFilter === 'archived'
                ? <p className="text-caption text-muted-foreground">The status filter excludes projects in progress. <Button size="sm" variant="link" onClick={() => setParam({ status: null })}>Clear it</Button></p>
                : <ProjectsTable rows={inProgressPreview} onOpen={openProject} caption="Projects in progress, worst first" />}
            </section>

            <section className="space-y-xs">
              <div className="flex flex-wrap items-baseline justify-between gap-xs">
                <h2 className="text-body font-semibold text-foreground">Testers</h2>
                <Button size="sm" variant="link" onClick={() => setParam({ tab: 'testers' })}>View all testers →</Button>
              </div>
              <TestersTable rows={testerPreview} caption="Most active testers" expandable={false} />
            </section>

            <p className="text-caption text-muted-foreground">
              Organisation accounts · Current · all projects: {n(data.accounts.total)} registered,{' '}
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
                  <SelectItem value="critical">Critical, then high</SelectItem>
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
    </div>
  );
};

export default Oversight;
