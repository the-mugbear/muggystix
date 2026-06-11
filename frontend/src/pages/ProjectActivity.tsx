/**
 * Project Activity — unified timeline of every agent session
 * (recon + plan generation + execution) for the active project.
 *
 * v3.0.0 — the one surface that aggregates the three workflows in time
 * order with model/tool/user attribution.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RefreshCw, Search, ExternalLink, Loader2 } from 'lucide-react';
import {
  AgentSessionKind,
  AgentSessionRow,
  AgentActivitySummary,
  listAgentSessions,
  getAgentSessionSummary,
  getAgentActivitySummary,
  ModelToolSummaryRow,
} from '../services/api';
import { safeFallback } from '../utils/uiStyles';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
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
import { cn } from '../utils/cn';

const KIND_OPTIONS: Array<{ value: '' | AgentSessionKind; label: string }> = [
  { value: '', label: 'All workflows' },
  { value: 'recon', label: 'Recon' },
  { value: 'plan_generation', label: 'Plan generation' },
  { value: 'execution', label: 'Execution' },
];

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'success' | 'warning' | 'info' | 'outline' | 'muted';

function kindBadgeVariant(kind: AgentSessionKind): BadgeVariant {
  switch (kind) {
    case 'recon':
      return 'secondary';
    case 'plan_generation':
      return 'info';
    case 'execution':
      return 'success';
  }
}

function statusBadgeVariant(status: string): BadgeVariant {
  const s = status.toLowerCase();
  if (s === 'active' || s === 'in_progress') return 'success';
  if (s === 'completed' || s === 'approved') return 'info';
  if (s === 'failed' || s === 'rejected') return 'destructive';
  if (s === 'paused' || s === 'pending_review' || s === 'draft') return 'warning';
  return 'muted';
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function fmtRelative(iso?: string | null): string {
  if (!iso) return '';
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 0) return 'just now';
    const sec = Math.floor(ms / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const day = Math.floor(hr / 24);
    return `${day}d ago`;
  } catch {
    return '';
  }
}

const ModelRollupCard: React.FC<{ rows: ModelToolSummaryRow[] | null }> = ({ rows }) => {
  if (!rows) {
    return (
      <Card className="mb-md">
        <CardContent className="flex items-center gap-xs p-md">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Loading model rollup…</p>
        </CardContent>
      </Card>
    );
  }
  if (rows.length === 0) {
    return (
      <Card className="mb-md">
        <CardHeader><CardTitle>Activity by agent / model</CardTitle></CardHeader>
        <CardContent>
          <p className="text-metadata text-muted-foreground">No agent sessions recorded for this project yet.</p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card className="mb-md">
      <CardHeader>
        <CardTitle>Activity by agent / model</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="mb-sm text-caption text-muted-foreground">
          Counts of recon / plan-generation / execution sessions for each agent identity. Use this
          to compare models running against the same project.
        </p>
        <div className="overflow-x-auto rounded-panel border border-border">
          <Table className="min-w-[600px]">
            <TableHeader>
              <TableRow>
                <TableHead>Model</TableHead>
                <TableHead>Tool / harness</TableHead>
                <TableHead className="w-20 text-right">Recon</TableHead>
                <TableHead className="w-24 text-right">Plan-gen</TableHead>
                <TableHead className="w-24 text-right">Execution</TableHead>
                <TableHead className="w-20 text-right">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r, idx) => (
                <TableRow key={`${r.generated_by_model ?? 'unknown'}-${r.generated_by_tool ?? 'unknown'}-${idx}`}>
                  <TableCell>
                    {r.generated_by_model ? (
                      <code className="font-mono text-caption">{r.generated_by_model}</code>
                    ) : (
                      <span className="text-caption text-muted-foreground">(not reported)</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {r.generated_by_tool || <span className="text-caption text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell className="text-right">{r.recon}</TableCell>
                  <TableCell className="text-right">{r.plan_generation}</TableCell>
                  <TableCell className="text-right">{r.execution}</TableCell>
                  <TableCell className="text-right font-semibold">{r.total}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
};

const ApiTile: React.FC<{ label: string; value: number | string; cls?: string }> = ({
  label,
  value,
  cls,
}) => (
  <Card>
    <CardContent className="p-sm text-center">
      <p className={cn('text-subheading font-semibold', cls)}>{value}</p>
      <p className="text-caption text-muted-foreground">{label}</p>
    </CardContent>
  </Card>
);

const ApiCallSummaryCard: React.FC<{
  summary: AgentActivitySummary | null;
  error?: boolean;
  onRetry?: () => void;
}> = ({ summary, error, onRetry }) => {
  const navigate = useNavigate();
  if (error) {
    // Distinct from loading: a failed fetch used to render the spinner
    // forever (the error was swallowed to null), indistinguishable from a
    // slow load and with no way to retry.
    return (
      <Card className="mb-md">
        <CardContent className="flex flex-wrap items-center justify-between gap-xs p-md">
          <p className="text-metadata text-muted-foreground">API-call analytics are currently unavailable.</p>
          {onRetry && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          )}
        </CardContent>
      </Card>
    );
  }
  if (!summary) {
    return (
      <Card className="mb-md">
        <CardContent className="flex items-center gap-xs p-md" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Loading API-call analytics…</p>
        </CardContent>
      </Card>
    );
  }
  if (summary.total_calls === 0) {
    return (
      <Card className="mb-md">
        <CardHeader><CardTitle>API-call analytics</CardTitle></CardHeader>
        <CardContent>
          <p className="text-metadata text-muted-foreground">
            No agent API calls recorded in the last {summary.window_days} days.
          </p>
        </CardContent>
      </Card>
    );
  }

  const maxDay = Math.max(1, ...summary.daily.map((d) => d.calls));
  const sb = summary.status_breakdown;
  const openSession = (s: { workflow: string; session_id: number }) => {
    if (s.workflow === 'recon') navigate(`/recon/runs/${s.session_id}`);
    else if (s.workflow === 'execution') navigate(`/executions/${s.session_id}`);
    else if (s.workflow === 'plan') navigate(`/test-plans/${s.session_id}`);
  };

  return (
    <Card className="mb-md">
      <CardHeader><CardTitle>API-call analytics</CardTitle></CardHeader>
      <CardContent>
        <p className="mb-sm text-caption text-muted-foreground">
          Every agent → BlueStick request over the last {summary.window_days} days, from the
          per-call audit log.
        </p>

        <div className="mb-sm grid grid-cols-2 gap-sm sm:grid-cols-3 lg:grid-cols-5">
          <ApiTile label="Total calls" value={summary.total_calls.toLocaleString()} />
          <ApiTile label="Agents" value={summary.distinct_agents.toLocaleString()} />
          <ApiTile label="2xx" value={sb.success.toLocaleString()} cls="text-success" />
          <ApiTile label="4xx" value={sb.client_error.toLocaleString()} cls="text-warning" />
          <ApiTile label="5xx" value={sb.server_error.toLocaleString()} cls="text-destructive" />
        </div>

        {summary.daily.length > 0 && (
          <div className="mb-sm">
            <p className="mb-xxs text-caption font-medium text-muted-foreground">Calls per day</p>
            <div className="flex h-16 items-end gap-[2px]">
              {summary.daily.map((d) => (
                <Tooltip key={d.day}>
                  <TooltipTrigger asChild>
                    {/* Focusable button (not a bare div) so keyboard + screen
                        readers can reach the daily value via aria-label; the
                        tooltip also opens on focus. */}
                    <button
                      type="button"
                      aria-label={`${d.day}: ${d.calls.toLocaleString()} call${d.calls === 1 ? '' : 's'}${d.errors > 0 ? `, ${d.errors.toLocaleString()} error${d.errors === 1 ? '' : 's'}` : ''}`}
                      className={cn(
                        'min-w-[3px] flex-1 rounded-sm border-0 p-0',
                        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        d.errors > 0 ? 'bg-destructive' : 'bg-info',
                      )}
                      style={{ height: `${Math.max(4, (d.calls / maxDay) * 100)}%` }}
                    />
                  </TooltipTrigger>
                  <TooltipContent>
                    {d.day}: {d.calls.toLocaleString()} call{d.calls === 1 ? '' : 's'}
                    {d.errors > 0 ? `, ${d.errors.toLocaleString()} error${d.errors === 1 ? '' : 's'}` : ''}
                  </TooltipContent>
                </Tooltip>
              ))}
            </div>
          </div>
        )}

        {summary.by_workflow.length > 0 && (
          <div className="mb-sm flex flex-wrap gap-xs">
            {summary.by_workflow.map((w) => (
              <Badge key={w.workflow} variant="outline">
                {w.workflow}: {w.calls.toLocaleString()}
              </Badge>
            ))}
          </div>
        )}

        {summary.busiest_sessions.length > 0 && (
          <div>
            <p className="mb-xxs text-caption font-medium text-muted-foreground">
              Busiest sessions
            </p>
            <ul className="flex flex-col gap-xxs">
              {summary.busiest_sessions.slice(0, 5).map((s) => {
                const linkable = s.workflow === 'recon' || s.workflow === 'execution' || s.workflow === 'plan';
                return (
                  <li key={`${s.workflow}-${s.session_id}`} className="flex flex-wrap items-center gap-xs">
                    <Badge variant="muted">{s.workflow}</Badge>
                    <span className="text-metadata">
                      #{s.session_id} · <strong>{s.calls.toLocaleString()}</strong> call
                      {s.calls === 1 ? '' : 's'}
                    </span>
                    {linkable && (
                      <Button size="sm" variant="ghost" onClick={() => openSession(s)}>
                        Open
                        <ExternalLink className="ml-xxs size-3" aria-hidden />
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

const ProjectActivity: React.FC = () => {
  const navigate = useNavigate();
  const [rows, setRows] = useState<AgentSessionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<ModelToolSummaryRow[] | null>(null);
  const [apiSummary, setApiSummary] = useState<AgentActivitySummary | null>(null);
  const [apiSummaryError, setApiSummaryError] = useState(false);
  // Grows on "Load older runs" so the unified timeline isn't silently capped.
  const [limit, setLimit] = useState(200);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const [kindFilter, setKindFilter] = useState<'' | AgentSessionKind>('');
  const [modelFilter, setModelFilter] = useState('');
  const [toolFilter, setToolFilter] = useState('');

  const knownModels = useMemo(() => {
    if (!summary) return [];
    const set = new Set<string>();
    summary.forEach((r) => r.generated_by_model && set.add(r.generated_by_model));
    return Array.from(set).sort();
  }, [summary]);
  const knownTools = useMemo(() => {
    if (!summary) return [];
    const set = new Set<string>();
    summary.forEach((r) => r.generated_by_tool && set.add(r.generated_by_tool));
    return Array.from(set).sort();
  }, [summary]);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    setApiSummaryError(false);
    try {
      const filters: Record<string, string | number> = { limit };
      if (kindFilter) filters.kind = kindFilter;
      if (modelFilter) filters.model = modelFilter;
      if (toolFilter) filters.tool = toolFilter;
      // API-call analytics is best-effort — its failure must not blank
      // the session timeline; record the error so the card shows an
      // "unavailable + Retry" state instead of an endless spinner.
      const [list, sum, apiSum] = await Promise.all([
        listAgentSessions(filters),
        getAgentSessionSummary(),
        getAgentActivitySummary().catch(() => { setApiSummaryError(true); return null; }),
      ]);
      setRows(list.sessions);
      setTotal(list.total);
      setSummary(sum.summary);
      setApiSummary(apiSum);
    } catch (e: unknown) {
      setError(formatApiError(e, 'Failed to load project activity.'));
    } finally {
      setLoading(false);
    }
  }, [kindFilter, modelFilter, toolFilter, limit]);

  useEffect(() => {
    fetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchAll, refreshNonce]);

  const drillInto = (row: AgentSessionRow) => {
    if (row.kind === 'execution') {
      navigate(`/executions/${row.id}`);
    } else if (row.kind === 'plan_generation' && row.test_plan_id != null) {
      navigate(`/test-plans/${row.test_plan_id}`);
    } else if (row.kind === 'recon') {
      navigate(`/recon/runs/${row.id}`);
    }
  };

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex items-start justify-between gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title">Agent Runs</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Every agent session against this project — recon, plan generation, and execution — in
            time order, with model + tool + user attribution.
          </p>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              onClick={() => setRefreshNonce((n) => n + 1)}
              disabled={loading}
              aria-label="Refresh project activity"
            >
              <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Refresh</TooltipContent>
        </Tooltip>
      </div>

      <ApiCallSummaryCard
        summary={apiSummary}
        error={apiSummaryError}
        onRetry={() => setRefreshNonce((n) => n + 1)}
      />

      <ModelRollupCard rows={summary} />

      <Card className="mb-md">
        <CardContent className="flex flex-wrap items-end gap-sm p-md">
          <div className="w-48">
            <Label htmlFor="pa-kind">Workflow</Label>
            <Select
              value={kindFilter || 'all'}
              onValueChange={(v) =>
                setKindFilter(v === 'all' ? '' : (v as AgentSessionKind))
              }
            >
              <SelectTrigger id="pa-kind"><SelectValue /></SelectTrigger>
              <SelectContent>
                {KIND_OPTIONS.map((o) => (
                  <SelectItem key={o.value || 'all'} value={o.value || 'all'}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="w-52">
            <Label htmlFor="pa-model">Model</Label>
            <Select
              value={modelFilter || 'all'}
              onValueChange={(v) => setModelFilter(v === 'all' ? '' : v)}
            >
              <SelectTrigger id="pa-model"><SelectValue placeholder="All models" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All models</SelectItem>
                {knownModels.map((m) => (
                  <SelectItem key={m} value={m}>{m}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="w-48">
            <Label htmlFor="pa-tool">Tool</Label>
            <Select
              value={toolFilter || 'all'}
              onValueChange={(v) => setToolFilter(v === 'all' ? '' : v)}
            >
              <SelectTrigger id="pa-tool"><SelectValue placeholder="All tools" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All tools</SelectItem>
                {knownTools.map((t) => (
                  <SelectItem key={t} value={t}>{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ml-auto flex items-center gap-xs">
            {loading && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
            <p className="text-caption text-muted-foreground">
              {rows.length} of {total} shown
            </p>
            {rows.length < total && !loading && (
              <Button size="sm" variant="outline" onClick={() => setLimit((l) => l + 200)}>
                Load older runs
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table className="min-w-[1000px]">
              <TableHeader>
                <TableRow>
                  {/* v2.43.2 — widened Status to w-32 because the widest
                      badge ("in_progress") overflowed w-24's 96px and
                      visually punched into the Started column.  Other
                      widths unchanged. */}
                  <TableHead className="w-36">Workflow</TableHead>
                  <TableHead className="w-32">Status</TableHead>
                  <TableHead className="w-40">Started</TableHead>
                  <TableHead className="w-52">Model · Tool</TableHead>
                  <TableHead className="w-36">User · Agent</TableHead>
                  <TableHead>Subject</TableHead>
                  <TableHead className="w-16" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={`${r.kind}-${r.id}`}>
                    <TableCell>
                      <Badge variant={kindBadgeVariant(r.kind)}>
                        {r.kind === 'plan_generation' ? 'plan-gen' : r.kind}
                      </Badge>
                    </TableCell>
                    <TableCell className="overflow-hidden">
                      {/* whitespace-nowrap prevents the badge from
                          wrapping mid-status (e.g. "in" + "_progress") */}
                      <Badge variant={statusBadgeVariant(r.status)} className="whitespace-nowrap">
                        {r.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="text-caption text-muted-foreground">
                            {fmtRelative(r.started_at) || '—'}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{fmtTime(r.started_at)}</TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      {r.generated_by_model ? (
                        <div className="flex flex-wrap gap-xxs">
                          <Badge variant="outline">{r.generated_by_model}</Badge>
                          {r.generated_by_tool && (
                            <Badge variant="outline">{r.generated_by_tool}</Badge>
                          )}
                        </div>
                      ) : (
                        <span className="text-caption text-muted-foreground">(not reported)</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <p className="text-metadata">{safeFallback(r.user_username, '—')}</p>
                      {r.agent_name && (
                        <p className="text-caption text-muted-foreground">{r.agent_name}</p>
                      )}
                    </TableCell>
                    <TableCell>
                      {r.kind === 'recon' && r.scope_id != null && <span>Scope #{r.scope_id}</span>}
                      {(r.kind === 'plan_generation' || r.kind === 'execution') &&
                        r.test_plan_id != null && <span>Plan #{r.test_plan_id}</span>}
                    </TableCell>
                    <TableCell>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => drillInto(r)}
                            aria-label={`Open ${r.kind} session ${r.id}`}
                            disabled={
                              r.kind === 'recon' ? r.scope_id == null : r.test_plan_id == null
                            }
                          >
                            <ExternalLink className="size-4" aria-hidden />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Open</TooltipContent>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
                {!loading && rows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} className="py-xl text-center">
                      <Search className="mx-auto mb-xs size-9 text-muted-foreground/50" aria-hidden />
                      <p className="text-metadata text-muted-foreground">
                        No agent sessions match the current filters.
                      </p>
                      {(kindFilter || modelFilter || toolFilter) && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setKindFilter('');
                            setModelFilter('');
                            setToolFilter('');
                          }}
                          className="mt-xs"
                        >
                          Clear filters
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default ProjectActivity;
