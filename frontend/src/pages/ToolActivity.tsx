/**
 * Tool Activity — cross-project SOC-correlation surface.
 *
 * Answers "what tools were running at 14:32 UTC?" without forcing the
 * analyst to iterate projects.  Pairs with /api/v1/activity/scans-at
 * (point query) and /api/v1/activity/scans-between (window query).
 *
 * v1 scope: scans only.  v2 will fold in agent_api_calls and
 * recon_sessions via the same endpoint's kind discriminator.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, RefreshCw, Search, ExternalLink, AlertTriangle } from 'lucide-react';
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
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
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
import { ActivityTimeline } from '../components/ActivityTimeline';
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
  }
}

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

  const search = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getScansAt({
        ts: localInputToUtcIso(tsLocal),
        toleranceSeconds: tolerance,
      });
      setResponse(data);
      setProjectFilter(new Set()); // reset chip filter on new search
    } catch (err) {
      setError(formatApiError(err, 'Failed to load activity'));
      setResponse(null);
    } finally {
      setLoading(false);
    }
  }, [tsLocal, tolerance]);

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
  }, []);

  // Run both on mount — the page lands with the week timeline
  // populated and a fresh "now ± default tolerance" query in the
  // table below.
  useEffect(() => {
    search();
    loadWeek();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      const ts = new Date(localInputToUtcIso(tsLocal)).getTime();
      if (Number.isNaN(ts)) return null;
      return {
        start: new Date(ts - tolerance * 1000).toISOString(),
        end: new Date(ts + tolerance * 1000).toISOString(),
      };
    } catch {
      return null;
    }
  }, [tsLocal, tolerance]);

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
        <p className="mt-xs text-caption text-muted-foreground">
          Cross-project SOC correlation: spot activity visually in the
          past-7-day snapshot below, then drill in with a focused
          timestamp + tolerance query.  The focused window is rendered
          as a highlighted band on the snapshot.
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
        <ActivityTimeline
          items={filteredWeekItems}
          windowStart={weekRange.start}
          windowEnd={weekRange.end}
          onItemClick={navigateToItem}
          title={
            weekLoading
              ? 'Past 7 days · refreshing…'
              : (() => {
                  const returned = weekResponse?.items.length ?? 0;
                  const shown = filteredWeekItems.length;
                  const filterActive = projectFilter.size > 0;
                  // When truncated, prefix with `≥` so the headline
                  // count itself signals the cap — `total` from the
                  // backend is post-truncation and reports 500 even
                  // when thousands matched.
                  const truncated = !!weekResponse?.truncated;
                  const returnedLabel = truncated ? `≥${returned}` : `${returned}`;
                  const base = filterActive
                    ? `Past 7 days · showing ${shown} of ${returnedLabel} (filtered)`
                    : `Past 7 days · ${returnedLabel} activit${returned === 1 && !truncated ? 'y' : 'ies'}`;
                  // v4.22.0 — truncation reflects server-side cap.
                  // Local project filter chips don't reduce it; only
                  // tightening the window would.
                  return truncated
                    ? `${base} · showing first 500 — tighten the time range`
                    : base;
                })()
          }
          helperText={
            <p className="text-metadata text-muted-foreground">
              Visual scan of every scan / recon session / execution
              session across the projects you can see.  The blue band
              shows the current ±tolerance focus window.{' '}
              {queryWindow && !queryInsideWeek && (
                <span className="text-warning">
                  Your focus window is outside the past 7 days — the
                  band isn&apos;t visible on this snapshot.
                </span>
              )}
            </p>
          }
          highlightStart={queryInsideWeek ? queryWindow?.start ?? null : null}
          highlightEnd={queryInsideWeek ? queryWindow?.end ?? null : null}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle>Correlate to a timestamp</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-wrap items-end gap-md"
            onSubmit={(e) => {
              e.preventDefault();
              search();
            }}
          >
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
                void search();
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
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {response && (
        <>
          <div className="flex flex-wrap items-center gap-sm text-caption text-muted-foreground">
            <Clock className="size-4" aria-hidden />
            <span>
              Window: {fmt(response.window_start)} → {fmt(response.window_end)}
            </span>
            <span>•</span>
            <span>
              {response.total} scan{response.total === 1 ? '' : 's'} matched
              across {response.accessible_project_ids.length} accessible
              project{response.accessible_project_ids.length === 1 ? '' : 's'}
              {response.truncated && (
                <span className="ml-xs text-warning">
                  (capped — narrow the tolerance to see all)
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

          <Card>
            <CardContent className="p-0">
              {filteredItems.length === 0 ? (
                <p className="p-md text-caption text-muted-foreground">
                  No matching scans in this window. Widen the tolerance or
                  pick a different timestamp.
                </p>
              ) : (
                <Table style={{ tableLayout: 'fixed', width: '100%' }}>
                  <TableHeader>
                    <TableRow>
                      <TableHead style={{ width: '13%' }}>Start</TableHead>
                      <TableHead style={{ width: '13%' }}>End</TableHead>
                      <TableHead style={{ width: '7%' }}>Duration</TableHead>
                      <TableHead style={{ width: '14%' }}>Project</TableHead>
                      <TableHead style={{ width: '10%' }}>Tool</TableHead>
                      <TableHead style={{ width: '7%' }}>Hosts</TableHead>
                      <TableHead>Command</TableHead>
                      <TableHead style={{ width: '6%' }} />
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
                        <TableCell className="min-w-0 truncate">
                          {safeFallback(item.project_name)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={kindBadgeVariant(item.kind)}>
                            {item.label}
                          </Badge>
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {item.host_count ?? '—'}
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
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};

export default ToolActivity;
