/**
 * Tool Activity — cross-project SOC-correlation surface.
 *
 * Answers "was this signature, at this time, part of our testing?"
 * without forcing the analyst to iterate projects.  Pairs with
 * /api/v1/activity/scans-at (moment ± tolerance) and
 * /api/v1/activity/scans-between (range) — both take the same
 * attribution filters (v5.213.0): a tool name / command substring and a
 * single target IP, applied to the focused query AND the past-7-day
 * snapshot, so "when did nmap run this week?" is one filter away.
 *
 * Kinds: uploaded scans (scanner timestamps), recon and execution runs
 * (containers), and the per-command record — `test_result` (a command an
 * agent reported, with its tool and target host) and `sanity_check` (a
 * target-verification probe).
 *
 * Screenshot review 2026-09-23: the page leads with its cross-project scope;
 * the snapshot is a fixed-height binned chart (ActivityHistogram) rather than
 * a dot per activity; sections instead of cards; and the focused query no
 * longer runs "now ± 5 minutes" on arrival — it waits for the analyst, or for
 * a click on a chart column.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, RefreshCw, Search, ExternalLink, AlertTriangle, Info } from 'lucide-react';
import {
  ActivityItem,
  ActivityKind,
  ActivityResponse,
  getScansAt,
  getScansBetween,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';
import { safeFallback } from '../utils/uiStyles';
import { ActivityHistogram } from '../components/ActivityHistogram';
import { PostureSection, SectionCount } from '../components/posture/PostureSection';
import { useProject } from '../contexts/ProjectContext';

// v4.21.0 — finer-grained tolerance steps so the analyst can ramp from
// "exact moment" to "general hour" without skipping a useful range.
// Backend cap at 3600s; for wider context the week-snapshot timeline
// above the form gives 7 days of visual scanning.
const TOLERANCE_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 10, label: '± 10 seconds' },
  { value: 30, label: '± 30 seconds' },
  { value: 60, label: '± 1 minute' },
  { value: 120, label: '± 2 minutes' },
  { value: 300, label: '± 5 minutes (default)' },
  { value: 900, label: '± 15 minutes' },
  { value: 1800, label: '± 30 minutes' },
  { value: 3600, label: '± 1 hour' },
];

const WEEK_SECONDS = 7 * 24 * 3600;

const pad2 = (n: number) => String(n).padStart(2, '0');

function toLocalInput(d: Date): string {
  // <input type="datetime-local"> wants `YYYY-MM-DDTHH:MM` in local time.
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  );
}

function localInputToUtcIso(local: string): string {
  // datetime-local has no timezone suffix; JS interprets it in local TZ
  // when passed to `new Date()`.  Convert to UTC ISO before sending —
  // the backend treats naive timestamps as UTC, which would otherwise
  // shift the analyst's input by their local offset.
  return new Date(local).toISOString();
}

function fmt(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// Compact `YYYY-MM-DD HH:MM:SS` for table cells.  `toLocaleString()`
// produces "5/26/2026, 11:32:15 PM" (~22 chars) which overflows the
// 13%-wide Start/End columns at most viewport widths and visually
// bleeds into adjacent cells.  ISO-style packs the same info in 19
// chars and reads better at-a-glance for SOC correlation.
function fmtCompact(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
  );
}

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'success' | 'warning' | 'info' | 'outline' | 'muted';

function kindBadgeVariant(kind: ActivityKind): BadgeVariant {
  switch (kind) {
    case 'scan':
      return 'secondary';
    case 'recon_session':
      return 'info';
    case 'execution_session':
      return 'success';
    case 'test_result':
      return 'destructive';
    case 'sanity_check':
      return 'muted';
  }
}

const KIND_LABEL: Record<ActivityKind, string> = {
  scan: 'scan upload',
  recon_session: 'recon run',
  execution_session: 'execution run',
  test_result: 'command run',
  sanity_check: 'target probe',
};

type QueryMode = 'at' | 'between';

// v4.27.0 — routes are TOP-LEVEL (`/scans/:id`, `/recon/runs/:id`,
// `/executions/:id`).  There is no `/projects/:id/...` nested route
// surface — the API client reads the active project from
// `getCurrentProjectId()` and prefixes API calls with it.  Earlier
// versions of this helper assembled `/projects/${item.project_id}/…`
// URLs, which fell through the catch-all `/*` route, granted access
// in ProtectedRoute, and then rendered nothing because the inner
// `<Routes>` had no match — links appeared broken.
function deepLinkFor(item: ActivityItem): string {
  switch (item.kind) {
    case 'scan':
      return `/scans/${item.ref_id}`;
    case 'recon_session':
      return `/recon/runs/${item.ref_id}`;
    case 'execution_session':
      return `/executions/${item.ref_id}`;
    case 'test_result':
    case 'sanity_check':
      // Per-command rows belong to an execution run; that is the page
      // with the command, its output and the sanity checks around it.
      // `parent_id` is always set for these kinds (the run FK is NOT
      // NULL) — there is no sensible fallback: `ref_id` is the row's own
      // id, which is not an execution id.
      return `/executions/${String(item.parent_id)}`;
  }
}

function durationSeconds(start: string, end: string | null): string {
  if (!end) return '—';
  const s = new Date(start).getTime();
  const e = new Date(end).getTime();
  const secs = Math.max(0, Math.round((e - s) / 1000));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.round(secs / 60)}m`;
  return `${Math.round(secs / 3600)}h`;
}

export const ToolActivity: React.FC = () => {
  const navigate = useNavigate();
  const { projects, currentProject, selectProject } = useProject();

  // v4.27.0 — /tool-activity is cross-project by design, but the API
  // client and the detail pages both key their data fetch on the
  // active project (`getCurrentProjectId()` in services/api/client.ts).
  // Navigating to e.g. /scans/42 without first switching projects
  // would target the WRONG project's resource id (404 or, worse,
  // silently load a foreign id that happens to exist).  Switch first,
  // then navigate.  `selectProject` updates both the React state and
  // the module-level `_currentProjectId` synchronously, so the
  // destination page's first API call uses the right project.
  const navigateToItem = useCallback(
    (item: ActivityItem) => {
      if (item.project_id !== currentProject?.id) {
        const target = projects.find((p) => p.id === item.project_id);
        if (target) selectProject(target);
      }
      navigate(deepLinkFor(item));
    },
    [currentProject?.id, navigate, projects, selectProject],
  );

  // Default timestamp = "now, rounded to the minute"
  const [tsLocal, setTsLocal] = useState<string>(() => toLocalInput(new Date()));
  const [tolerance, setTolerance] = useState<number>(300);
  // v5.213.0 — a second query shape: a from/to range (≤ 7 days) for
  // "when did this tool run?" rather than "what ran at this moment?".
  const [mode, setMode] = useState<QueryMode>('at');
  const [fromLocal, setFromLocal] = useState<string>(() =>
    toLocalInput(new Date(Date.now() - 24 * 3600 * 1000)),
  );
  const [toLocal, setToLocal] = useState<string>(() => toLocalInput(new Date()));
  // v5.213.0 — attribution filters.  Applied server-side to the focused
  // query and to the week snapshot alike, so the snapshot answers "when
  // did <tool> touch <target> this week?" on its own.
  const [tool, setTool] = useState('');
  const [target, setTarget] = useState('');
  const [response, setResponse] = useState<ActivityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Post-search client-side project filter.  Empty set = show all
  // (we apply this AFTER the query so the analyst can drill in
  // without re-fetching).
  const [projectFilter, setProjectFilter] = useState<Set<number>>(new Set());

  // v4.21.0 — week-snapshot timeline state.  Independent of the
  // form's timestamp/tolerance; always shows the past 7 days so the
  // analyst can spot activity clusters visually before drilling in
  // with a focused query.  Re-fetched on Refresh, not on every form
  // submit (the snapshot is a context surface, not a query result).
  const [weekResponse, setWeekResponse] = useState<ActivityResponse | null>(null);
  const [weekLoading, setWeekLoading] = useState(false);
  // Lock the week's [start, end] at fetch time so the highlight band's
  // % positions don't drift while the user navigates.
  const [weekRange, setWeekRange] = useState<{ start: string; end: string } | null>(null);
  // v4.24.0 — surface week-snapshot failures.  Empty + zero is
  // ambiguous between "quiet week" and "backend failed"; the analyst
  // needs to know which.
  const [weekError, setWeekError] = useState<string | null>(null);

  // `range` runs a range query for exactly those instants (a chart bin was
  // chosen) without waiting for the form state it also sets to settle.
  const search = useCallback(async (range?: { from: string; to: string }) => {
    setLoading(true);
    setError(null);
    try {
      const attribution = { tool: tool || undefined, target: target || undefined };
      const data = range
        ? await getScansBetween({ from: range.from, to: range.to, ...attribution })
        : mode === 'at'
          ? await getScansAt({
              ts: localInputToUtcIso(tsLocal),
              toleranceSeconds: tolerance,
              ...attribution,
            })
          : await getScansBetween({
              from: localInputToUtcIso(fromLocal),
              to: localInputToUtcIso(toLocal),
              ...attribution,
            });
      setResponse(data);
      setProjectFilter(new Set()); // reset chip filter on new search
    } catch (err) {
      setError(formatApiError(err, 'Failed to load activity'));
      setResponse(null);
    } finally {
      setLoading(false);
    }
  }, [mode, tsLocal, tolerance, fromLocal, toLocal, tool, target]);

  const loadWeek = useCallback(async () => {
    setWeekLoading(true);
    setWeekError(null);
    try {
      const now = new Date();
      const weekAgo = new Date(now.getTime() - WEEK_SECONDS * 1000);
      const range = { start: weekAgo.toISOString(), end: now.toISOString() };
      setWeekRange(range);
      const data = await getScansBetween({
        from: range.start,
        to: range.end,
        tool: tool || undefined,
        target: target || undefined,
      });
      setWeekResponse(data);
    } catch (err) {
      // Don't gate the focused query on this — the snapshot is
      // supplementary.  But empty + zero is indistinguishable from a
      // quiet week, so capture the error and render it above the
      // timeline as a non-blocking warning.
      setWeekError(formatApiError(err, 'Past-7-day snapshot unavailable.'));
      setWeekResponse(null);
    } finally {
      setWeekLoading(false);
    }
  }, [tool, target]);

  // Only the snapshot loads on arrival. The focused query waits for the
  // analyst: it used to run "now ± 5 minutes" on mount, so the page always
  // opened on "0 activities matched … No activity in this window" — an
  // answer to a question nobody asked (screenshot review 2026-09-23).
  useEffect(() => {
    loadWeek();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A chart bin was chosen: correlate exactly that range, and show it in
  // the form so the query on screen is the one that ran.
  const correlateRange = useCallback(
    (fromIso: string, toIso: string) => {
      setMode('between');
      setFromLocal(toLocalInput(new Date(fromIso)));
      setToLocal(toLocalInput(new Date(toIso)));
      void search({ from: fromIso, to: toIso });
    },
    [search],
  );

  // The projects the snapshot covered — the server's answer, not the
  // switcher's list (which may not have loaded, or may differ for admins).
  const visibleProjectCount =
    weekResponse?.accessible_project_ids.length ?? response?.accessible_project_ids.length ?? projects.length;

  const projectsInResults = useMemo(() => {
    if (!response) return [] as Array<{ id: number; name: string; count: number }>;
    const byId = new Map<number, { id: number; name: string; count: number }>();
    for (const item of response.items) {
      const prev = byId.get(item.project_id);
      if (prev) {
        prev.count += 1;
      } else {
        byId.set(item.project_id, {
          id: item.project_id,
          name: item.project_name,
          count: 1,
        });
      }
    }
    return Array.from(byId.values()).sort((a, b) =>
      a.name.localeCompare(b.name),
    );
  }, [response]);

  const filteredItems = useMemo(() => {
    if (!response) return [];
    if (projectFilter.size === 0) return response.items;
    return response.items.filter((i) => projectFilter.has(i.project_id));
  }, [response, projectFilter]);

  // Week snapshot items — same project-filter as the table so the
  // analyst gets a consistent view when narrowing to specific
  // engagements.
  const filteredWeekItems = useMemo(() => {
    if (!weekResponse) return [];
    if (projectFilter.size === 0) return weekResponse.items;
    return weekResponse.items.filter((i) => projectFilter.has(i.project_id));
  }, [weekResponse, projectFilter]);

  // The form's current query window — converted to UTC ISO so the
  // week-snapshot timeline's highlight band knows where the focus is.
  const queryWindow = useMemo(() => {
    try {
      if (mode === 'between') {
        const s = new Date(localInputToUtcIso(fromLocal)).getTime();
        const e = new Date(localInputToUtcIso(toLocal)).getTime();
        if (Number.isNaN(s) || Number.isNaN(e) || e < s) return null;
        return { start: new Date(s).toISOString(), end: new Date(e).toISOString() };
      }
      const ts = new Date(localInputToUtcIso(tsLocal)).getTime();
      if (Number.isNaN(ts)) return null;
      return {
        start: new Date(ts - tolerance * 1000).toISOString(),
        end: new Date(ts + tolerance * 1000).toISOString(),
      };
    } catch {
      return null;
    }
  }, [mode, tsLocal, tolerance, fromLocal, toLocal]);

  const attributionActive = Boolean(tool.trim() || target.trim());

  // Truthy when the queried window falls within the past 7 days
  // (i.e. the highlight band will actually render on the snapshot).
  const queryInsideWeek = useMemo(() => {
    if (!queryWindow || !weekRange) return false;
    const qs = new Date(queryWindow.start).getTime();
    const qe = new Date(queryWindow.end).getTime();
    const ws = new Date(weekRange.start).getTime();
    const we = new Date(weekRange.end).getTime();
    return qe >= ws && qs <= we;
  }, [queryWindow, weekRange]);

  const toggleProject = (id: number) => {
    setProjectFilter((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div className="space-y-md p-md">
      <div>
        <h1 className="text-page-title">Tool Activity</h1>
        {/* The page is cross-project but sits in a project hub under the
            active project's header — say so before anything else. */}
        <p className="mt-xs break-words text-body font-medium text-foreground" data-testid="tool-activity-lead">
          Across {visibleProjectCount === 1 ? 'the 1 project' : `all ${visibleProjectCount} projects`} you can see
          {currentProject && visibleProjectCount !== 1 ? (
            <> — not only <span className="break-all">{currentProject.name}</span></>
          ) : null}
          .
        </p>
        <p className="mt-xxs flex flex-wrap items-center gap-xxs text-caption text-muted-foreground">
          Was this signature, at this time, part of our testing? Name the tool or
          target it fired on, then look at a moment or a range.
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="inline-flex items-center text-muted-foreground hover:text-foreground"
                aria-label="What is counted"
              >
                <Info className="size-4" aria-hidden />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">
              Scan uploads (at the scanner&rsquo;s own timestamps), recon and
              execution runs, and the per-command record: commands an agent
              reported running and the target probes before them, each with its
              host. The tool / target filters also narrow the past-7-day
              snapshot, so it shows when that tool ran.
            </TooltipContent>
          </Tooltip>
        </p>
      </div>

      {/* v4.24.0 — non-blocking warning when the snapshot fetch
          failed.  Without this the empty timeline frame is
          indistinguishable from a genuinely quiet week. */}
      {weekError && (
        <Alert variant="warning">
          <AlertTriangle className="size-4" aria-hidden />
          <AlertDescription>
            {weekError} The focused timestamp + tolerance query below still works —
            this only affects the past-7-day visual scan.
          </AlertDescription>
        </Alert>
      )}

      {/* Week-snapshot timeline — independent of the timestamp/tolerance
          form.  Always shows the past 7 days so the analyst can read
          activity density at-a-glance before specifying a focus. */}
      {weekRange && (
        <PostureSection
          title={
            <>
              Past 7 days
              {attributionActive && (
                <span className="min-w-0 break-all font-normal">
                  for {[tool.trim() && `“${tool.trim()}”`, target.trim()].filter(Boolean).join(' on ')}
                </span>
              )}
              <SectionCount>
                {weekLoading
                  ? 'refreshing…'
                  : (() => {
                      const returned = weekResponse?.items.length ?? 0;
                      // `≥` when capped: the backend's `total` is
                      // post-truncation and reads 500 even when thousands
                      // matched.
                      const truncated = !!weekResponse?.truncated;
                      const returnedLabel = truncated ? `≥${returned}` : `${returned}`;
                      const base =
                        projectFilter.size > 0
                          ? `showing ${filteredWeekItems.length} of ${returnedLabel} (filtered)`
                          : `${returnedLabel} activit${returned === 1 && !truncated ? 'y' : 'ies'}`;
                      return truncated ? `${base} · first 500 only — tighten the time range` : base;
                    })()}
              </SectionCount>
            </>
          }
          description={
            <>
              What started when, across the projects you can see
              {attributionActive ? ', narrowed to the tool / target below' : ''}.
              The shaded band is the Correlate window; click a column to
              correlate that range.{' '}
              {queryWindow && !queryInsideWeek && (
                <span className="text-warning">
                  The Correlate window is outside the past 7 days, so its band
                  is not on this chart.
                </span>
              )}
            </>
          }
        >
          <ActivityHistogram
            items={filteredWeekItems}
            windowStart={weekRange.start}
            windowEnd={weekRange.end}
            highlightStart={queryInsideWeek ? queryWindow?.start ?? null : null}
            highlightEnd={queryInsideWeek ? queryWindow?.end ?? null : null}
            onSelectBin={correlateRange}
          />
        </PostureSection>
      )}

      <PostureSection title="Correlate">
          <form
            className="flex flex-wrap items-end gap-md"
            onSubmit={(e) => {
              e.preventDefault();
              search();
              void loadWeek();
            }}
          >
            <div className="flex flex-col gap-xxs">
              <Label htmlFor="tool-input">Tool</Label>
              <Input
                id="tool-input"
                type="text"
                value={tool}
                onChange={(e) => setTool(e.target.value)}
                placeholder="e.g. nmap, masscan, ping"
                maxLength={100}
                className="w-[200px]"
              />
            </div>
            <div className="flex flex-col gap-xxs">
              <Label htmlFor="target-input">Target IP</Label>
              <Input
                id="target-input"
                type="text"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                placeholder="e.g. 10.0.0.5"
                maxLength={45}
                className="w-[180px]"
              />
            </div>
            <div className="flex flex-col gap-xxs">
              <Label htmlFor="mode-input">Look at</Label>
              <Select value={mode} onValueChange={(v) => setMode(v as QueryMode)}>
                <SelectTrigger id="mode-input" className="w-[160px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="at">A moment</SelectItem>
                  <SelectItem value="between">A range (≤ 7 days)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {mode === 'at' ? (
              <>
                <div className="flex flex-col gap-xxs">
                  <Label htmlFor="ts-input">Timestamp (local)</Label>
                  <Input
                    id="ts-input"
                    type="datetime-local"
                    value={tsLocal}
                    onChange={(e) => setTsLocal(e.target.value)}
                    step={1}
                    className="w-[240px]"
                    required
                  />
                </div>
                <div className="flex flex-col gap-xxs">
                  <Label htmlFor="tolerance-input">Tolerance</Label>
                  <Select
                    value={String(tolerance)}
                    onValueChange={(v) => setTolerance(Number(v))}
                  >
                    <SelectTrigger id="tolerance-input" className="w-[200px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TOLERANCE_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={String(opt.value)}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </>
            ) : (
              <>
                <div className="flex flex-col gap-xxs">
                  <Label htmlFor="from-input">From (local)</Label>
                  <Input
                    id="from-input"
                    type="datetime-local"
                    value={fromLocal}
                    onChange={(e) => setFromLocal(e.target.value)}
                    step={1}
                    className="w-[240px]"
                    required
                  />
                </div>
                <div className="flex flex-col gap-xxs">
                  <Label htmlFor="to-input">To (local)</Label>
                  <Input
                    id="to-input"
                    type="datetime-local"
                    value={toLocal}
                    onChange={(e) => setToLocal(e.target.value)}
                    step={1}
                    className="w-[240px]"
                    required
                  />
                </div>
              </>
            )}
            <Button type="submit" disabled={loading}>
              {/* v4.22.0 — keep Search icon during loading so this
                  button reads distinctly from the standalone Refresh
                  next to it; disabled state already conveys "busy". */}
              <Search className="size-4" aria-hidden />
              Correlate
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void loadWeek();
                // Re-run the focused query only if one has been run.
                if (response) void search();
              }}
              disabled={weekLoading || loading}
              aria-label="Refresh week snapshot"
            >
              <RefreshCw
                className={`size-4 ${weekLoading || loading ? 'animate-spin' : ''}`}
                aria-hidden
              />
              Refresh
            </Button>
          </form>

      {error && (
        <Alert variant="destructive" className="mt-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!response && !error && (
        <p className="mt-md text-metadata text-muted-foreground" data-testid="correlate-prompt">
          {loading
            ? 'Correlating…'
            : 'Pick a tool, address or time to correlate — or click a column in the chart above.'}
        </p>
      )}

      {response && (
        <div className="mt-md space-y-sm">
          <div className="flex flex-wrap items-center gap-sm text-caption text-muted-foreground">
            <Clock className="size-4" aria-hidden />
            <span>
              Window: {fmt(response.window_start)} → {fmt(response.window_end)}
            </span>
            <span>•</span>
            <span>
              {response.total} activit{response.total === 1 ? 'y' : 'ies'} matched
              across {response.accessible_project_ids.length} accessible
              project{response.accessible_project_ids.length === 1 ? '' : 's'}
              {attributionActive && (
                <>
                  {' '}for{' '}
                  {tool.trim() && <code className="font-mono">{tool.trim()}</code>}
                  {tool.trim() && target.trim() && ' on '}
                  {target.trim() && <code className="font-mono">{target.trim()}</code>}
                </>
              )}
              {response.truncated && (
                <span className="ml-xs text-warning">
                  (capped — narrow the window or filter by tool / target to see all)
                </span>
              )}
            </span>
          </div>

          {response.truncated && (
            <Alert variant="warning">
              <AlertTriangle className="size-4" aria-hidden />
              <AlertDescription>
                Result set was truncated. Narrow the tolerance window or
                filter by project to see specific activity.
              </AlertDescription>
            </Alert>
          )}

          {/* v4.21.0 — per-query timeline removed.  The week-snapshot
              timeline at the top renders this same focused window as
              a highlighted band, so a separate zoomed timeline here
              was duplicate information. */}

          {projectsInResults.length > 1 && (
            <div className="flex flex-wrap items-center gap-xs">
              <span className="text-caption text-muted-foreground">
                Filter:
              </span>
              {projectsInResults.map((p) => {
                const active = projectFilter.has(p.id);
                return (
                  <Badge
                    key={p.id}
                    variant={active ? 'default' : 'outline'}
                    onClick={() => toggleProject(p.id)}
                    className="cursor-pointer select-none whitespace-normal break-words"
                  >
                    {p.name} · {p.count}
                  </Badge>
                );
              })}
              {projectFilter.size > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setProjectFilter(new Set())}
                >
                  Clear
                </Button>
              )}
            </div>
          )}

          <div className="overflow-x-auto">
              {filteredItems.length === 0 ? (
                <p className="text-caption text-muted-foreground">
                  {attributionActive
                    ? 'Nothing recorded for that tool / target in this window: no scan observed the host, no agent reported a command or probe against it, and no run covered it. If a scan ran but was never uploaded, BlueStick cannot know about it.'
                    : 'No activity in this window. Widen the tolerance, pick a different time, or switch to a range.'}
                </p>
              ) : (
                <Table style={{ tableLayout: 'fixed', width: '100%' }}>
                  <TableHeader>
                    <TableRow>
                      <TableHead style={{ width: '13%' }}>Start</TableHead>
                      <TableHead style={{ width: '11%' }}>End</TableHead>
                      <TableHead style={{ width: '6%' }}>Duration</TableHead>
                      <TableHead style={{ width: '9%' }}>Kind</TableHead>
                      <TableHead style={{ width: '12%' }}>Project</TableHead>
                      <TableHead style={{ width: '10%' }}>Tool</TableHead>
                      <TableHead style={{ width: '11%' }}>Target</TableHead>
                      <TableHead>Command</TableHead>
                      <TableHead style={{ width: '5%' }} />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredItems.map((item: ActivityItem) => (
                      <TableRow key={`${item.kind}-${item.ref_id}`}>
                        {/* v4.21.0 — compact ISO-style timestamp
                            (toLocaleString overflowed the 13% column).
                            v4.22.0 — when start_time is the upload-time
                            fallback, italicise + prefix "≈" so the
                            analyst doesn't read it as execution time.
                            Tooltip explains the substitution. */}
                        <TableCell className="truncate tabular-nums">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span
                                className={
                                  item.start_time_is_fallback
                                    ? 'italic text-warning'
                                    : undefined
                                }
                              >
                                {item.start_time_is_fallback ? '≈ ' : ''}
                                {fmtCompact(item.start_time)}
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>
                              {item.start_time_is_fallback ? (
                                <>
                                  <div>{fmt(item.start_time)}</div>
                                  <div className="text-warning">
                                    Upload time — scanner didn&apos;t record a start_time.
                                  </div>
                                </>
                              ) : (
                                fmt(item.start_time)
                              )}
                            </TooltipContent>
                          </Tooltip>
                        </TableCell>
                        <TableCell className="truncate tabular-nums">
                          {item.has_end_time ? (
                            // Compact form is 19 chars and already
                            // unambiguous; the locale-string tooltip
                            // would duplicate information.  Truncated
                            // cells still show full value via native
                            // browser tooltip on the cell text.
                            <span title={fmt(item.end_time)}>{fmtCompact(item.end_time)}</span>
                          ) : (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Badge variant="outline" className="text-micro">
                                  no end_time
                                </Badge>
                              </TooltipTrigger>
                              <TooltipContent>
                                Tool didn&apos;t record an end timestamp;
                                treated as single-instant at start.
                              </TooltipContent>
                            </Tooltip>
                          )}
                        </TableCell>
                        <TableCell className="truncate tabular-nums">
                          {durationSeconds(item.start_time, item.end_time)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={kindBadgeVariant(item.kind)} className="whitespace-nowrap">
                            {KIND_LABEL[item.kind]}
                          </Badge>
                        </TableCell>
                        <TableCell className="min-w-0 truncate">
                          {safeFallback(item.project_name)}
                        </TableCell>
                        <TableCell className="min-w-0 truncate" title={item.label}>
                          {item.label}
                          {item.status && (item.kind === 'test_result' || item.kind === 'sanity_check') && (
                            <span className="ml-xxs text-caption text-muted-foreground">{item.status}</span>
                          )}
                        </TableCell>
                        <TableCell className="min-w-0 truncate font-mono text-caption tabular-nums" title={item.target ?? undefined}>
                          {item.target
                            ? item.target
                            : item.host_count != null
                              ? `${item.host_count} host${item.host_count === 1 ? '' : 's'}`
                              : '—'}
                        </TableCell>
                        <TableCell className="min-w-0 truncate">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="font-mono text-caption">
                                {item.secondary_label || '—'}
                              </span>
                            </TooltipTrigger>
                            {item.secondary_label && (
                              <TooltipContent className="max-w-[600px]">
                                {item.secondary_label}
                              </TooltipContent>
                            )}
                          </Tooltip>
                        </TableCell>
                        <TableCell>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => navigateToItem(item)}
                            aria-label="Open detail"
                          >
                            <ExternalLink className="size-4" aria-hidden />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
          </div>
        </div>
      )}
      </PostureSection>
    </div>
  );
};

export default ToolActivity;
