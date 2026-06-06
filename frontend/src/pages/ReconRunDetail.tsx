import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Cpu,
  CircleSlash,
  RefreshCw,
  RotateCcw,
  SquareArrowOutUpRight,
} from 'lucide-react';
import { StartReconDialog } from '../components/StartReconDialog';
import { useReconPlan } from '../hooks/useReconPlan';
import {
  ReconSessionDetail,
  ReconSessionRow,
  abandonReconSession,
  getReconSession,
  listReconSessions,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Alert, AlertDescription } from '../components/ui/alert';
import { DetailSkeleton } from '../components/PageSkeleton';
import { NextStepBanner } from '../components/NextStepBanner';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { ConfirmDialog } from '../components/ui/confirm-dialog';
import { WorkflowDetailHeader } from '../components/workflow/WorkflowDetailHeader';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { MetaField } from '../components/ui/meta-field';

type BadgeTone = 'success' | 'info' | 'destructive' | 'warning' | 'muted';

const statusTone = (s: string): BadgeTone => {
  const t = s.toLowerCase();
  if (t === 'active') return 'success';
  if (t === 'completed') return 'info';
  if (t === 'failed') return 'destructive';
  if (t === 'abandoned' || t === 'paused') return 'warning';
  return 'muted';
};

const uploadTone = (s: string): BadgeTone => {
  const t = s.toLowerCase();
  if (t === 'completed') return 'success';
  if (t === 'failed') return 'destructive';
  if (t === 'queued' || t === 'processing') return 'info';
  return 'muted';
};

const fmtTime = (iso?: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

// ---------------------------------------------------------------------------
// Operator environment + preflight (v2.40.2 / frontend 4.3.4)
// ---------------------------------------------------------------------------
//
// Surfaces what the agent saw at the start of the session: the env probe
// (OS, shell, arch, python, agent identity) and the preflight tools_status
// table.  Without this, operators have to query the recon_sessions row in
// the DB to learn whether httpx was shadowed, masscan lacked CAP_NET_RAW,
// or rustscan was missing.

const TOOL_STATUS_TONE: Record<string, BadgeTone> = {
  ok: 'success',
  warn: 'warning',
  missing: 'destructive',
};
const TOOL_STATUS_ORDER: Record<string, number> = {
  missing: 0,
  warn: 1,
  ok: 2,
};

const EnvironmentSection: React.FC<{ detail: ReconSessionDetail }> = ({ detail }) => {
  const env = detail.environment;
  const sortedTools = useMemo(() => {
    if (!env?.tools_status?.length) return [];
    // Problem-first sort: missing → warn → ok, then alphabetical
    // within each bucket.  Operators care more about what's broken
    // than what's fine, so it leads.
    return [...env.tools_status].sort((a, b) => {
      const orderA = TOOL_STATUS_ORDER[String(a.status).toLowerCase()] ?? 99;
      const orderB = TOOL_STATUS_ORDER[String(b.status).toLowerCase()] ?? 99;
      if (orderA !== orderB) return orderA - orderB;
      return (a.name || '').localeCompare(b.name || '');
    });
  }, [env]);

  if (!env) {
    return (
      <Card className="mb-md">
        <CardContent className="p-md">
          <h2 className="mb-xxs flex items-center gap-xs text-subheading font-semibold">
            <Cpu className="size-4 text-muted-foreground" aria-hidden />
            Operator environment
          </h2>
          <Alert variant="warning">
            <AlertDescription>
              No environment probe was posted for this session. The agent should call{' '}
              <code className="font-mono text-caption">
                POST /agent/recon/sessions/{detail.summary.id}/environment
              </code>{' '}
              before running any commands so the operator's OS, shell, and tool inventory are
              recorded.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  const summary = detail.summary;
  // Compact key/value list of probe metadata. Build entries first and
  // filter null/empty so the layout stays tight on probes that didn't
  // include every optional field.
  const meta: Array<{ label: string; value: React.ReactNode }> = [
    { label: 'OS', value: env.os_family && [env.os_family, env.os_release].filter(Boolean).join(' · ') },
    { label: 'Shell', value: env.shell },
    { label: 'Arch', value: env.arch },
    { label: 'Python', value: env.python },
    { label: 'Agent model', value: summary.generated_by_model },
    { label: 'Agent tool', value: summary.generated_by_tool },
    { label: 'Prompt version', value: summary.prompt_version },
    { label: 'Probed at', value: env.probed_at ? fmtTime(env.probed_at) : null },
    { label: 'From IP', value: env.probed_from_ip },
  ].filter((row) => row.value);

  const problemCount = sortedTools.filter((t) => {
    const s = String(t.status).toLowerCase();
    return s === 'missing' || s === 'warn';
  }).length;

  return (
    <Card className="mb-md">
      <CardContent className="flex flex-col gap-sm p-md">
        <div>
          <h2 className="flex flex-wrap items-center gap-xs text-subheading font-semibold">
            <Cpu className="size-4 text-muted-foreground" aria-hidden />
            Operator environment
            {sortedTools.length > 0 && (
              <Badge variant={problemCount > 0 ? 'warning-outline' : 'success-outline'}>
                {sortedTools.length} tools
                {problemCount > 0 ? ` · ${problemCount} need attention` : ' · all ok'}
              </Badge>
            )}
          </h2>
          <p className="text-caption text-muted-foreground">
            What the agent saw at the start of this session — env probe + preflight checklist.
          </p>
        </div>

        {meta.length > 0 && (
          <div className="grid grid-cols-2 gap-x-md gap-y-sm sm:grid-cols-3 md:grid-cols-4">
            {meta.map((row) => (
              <MetaField key={row.label} label={row.label} value={row.value} />
            ))}
          </div>
        )}

        {env.notes && (
          <Alert variant="info">
            <AlertDescription className="break-words">{env.notes}</AlertDescription>
          </Alert>
        )}

        {sortedTools.length > 0 ? (
          <div className="overflow-x-auto rounded-control border border-border">
            <Table className="table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-44">Tool</TableHead>
                  <TableHead className="w-28">Status</TableHead>
                  <TableHead>Issue</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedTools.map((t) => {
                  const s = String(t.status).toLowerCase();
                  const tone = TOOL_STATUS_TONE[s] ?? 'muted';
                  return (
                    <TableRow key={t.name}>
                      <TableCell className="font-mono">{t.name}</TableCell>
                      <TableCell>
                        <Badge variant={tone}>{t.status}</Badge>
                      </TableCell>
                      <TableCell>
                        <span className="break-words text-metadata">
                          {t.issue ? t.issue : <span className="text-muted-foreground">—</span>}
                        </span>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="text-caption text-muted-foreground">
            The agent posted an environment probe but did not include a preflight{' '}
            <code className="font-mono">tools_status</code> list. Ask the agent to re-probe
            after running the preflight script.
          </p>
        )}
      </CardContent>
    </Card>
  );
};

const UploadsSection: React.FC<{
  detail: ReconSessionDetail;
  onLoadMore: () => void;
  loadingMore: boolean;
}> = ({ detail, onLoadMore, loadingMore }) => {
  const navigate = useNavigate();
  if (detail.uploads_total === 0) {
    return (
      <Card className="mb-md">
        <CardContent className="p-md">
          <h2 className="mb-xxs text-subheading font-semibold">Uploads</h2>
          <p className="text-metadata text-muted-foreground">
            No uploads submitted yet under this session.
          </p>
        </CardContent>
      </Card>
    );
  }
  const remaining = detail.uploads_total - detail.uploads.length;
  return (
    <Card className="mb-md">
      <CardContent className="p-md">
        <h2 className="text-subheading font-semibold">
          Uploads ({detail.uploads.length}
          {remaining > 0 ? ` of ${detail.uploads_total}` : ''})
        </h2>
        <p className="mb-sm text-caption text-muted-foreground">
          Per-file ingestion status, warnings, and the resulting scan row. <code className="font-mono">skipped</code>{' '}
          counts come from the v2.22.0 ingestion-quality columns — a "completed" job with non-zero
          skipped means the parser dropped malformed rows.
        </p>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Filename</TableHead>
                <TableHead className="w-28">Status</TableHead>
                <TableHead className="w-24 text-right">Skipped</TableHead>
                {/* Audit RSP·H8 — bound the warnings column width so
                    long parser warnings clamp instead of stretching
                    the row past the viewport. */}
                <TableHead className="w-1/4">Warnings / error</TableHead>
                <TableHead className="w-28">Scan</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.uploads.map((u) => (
                <TableRow key={u.job_id}>
                  <TableCell className="break-words font-mono text-caption">{u.filename}</TableCell>
                  <TableCell>
                    <Badge variant={uploadTone(u.status)}>{u.status}</Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    {u.skipped_count > 0 ? (
                      <Badge variant="outline" className="border-warning/40 text-warning">
                        {u.skipped_count}
                      </Badge>
                    ) : (
                      '—'
                    )}
                  </TableCell>
                  <TableCell>
                    {/* Audit RSP·H8 — wrap value so line-clamp applies
                        to a block child and long warnings wrap. */}
                    <div className="line-clamp-2 break-words">
                      {u.last_error || u.parser_warnings || (
                        <span className="text-caption text-muted-foreground">—</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    {u.scan_id ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => navigate(`/scans/${u.scan_id}`)}
                      >
                        #{u.scan_id}
                        <SquareArrowOutUpRight className="size-3" aria-hidden />
                      </Button>
                    ) : (
                      <span className="text-caption text-muted-foreground">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {remaining > 0 && (
          <div className="mt-sm flex items-center justify-center">
            <Button
              size="sm"
              variant="outline"
              onClick={onLoadMore}
              disabled={loadingMore}
              aria-label={`Load more uploads, ${remaining} remaining`}
            >
              {loadingMore ? 'Loading…' : `Load more (${remaining} remaining)`}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

/**
 * Run output panel — the v2.52.0 replacement for the old "Hosts
 * discovered" table.
 *
 * Why this changed: on a real recon (40k+ hosts in scope), the old
 * panel materialised every host into both the API response and the
 * DOM, which made the page take tens of seconds to load and freeze
 * the tab once rendered.  Inventory exists for browsing hosts; this
 * page exists for understanding what a run produced.  The split is
 * clean: stats live here, host browsing lives at /hosts (which has
 * the scan-id filter built in).
 *
 * The card renders three rollups from ``detail.host_stats``:
 *
 *   1. Top-line counts (hosts, hosts-with-open-ports) + a CTA to
 *      Inventory pre-filtered to this run's scans.
 *   2. Per-tool breakdown (distinct scans / hosts / ports each tool
 *      contributed) — answers "did httpx actually run?" quickly.
 *   3. Top services + top open ports (10 each) — answers "what
 *      services are running on the hosts this run touched".
 *
 * The Inventory CTA builds ``/hosts?scan_ids=<csv>`` from
 * ``detail.uploads[].scan_id`` so the user can browse the full host
 * list (with all of /hosts's filtering/pagination/virtualization)
 * with one click.  No fallback to "showing first N hosts inline" —
 * that's the trap we just stepped out of.
 */
const RunOutputCard: React.FC<{ detail: ReconSessionDetail }> = ({ detail }) => {
  const navigate = useNavigate();
  const stats = detail.host_stats;
  // v2.87.0 — uploads is now paginated, so the previous
  // `detail.uploads.map(u => u.scan_id)` would miss scans from
  // unloaded pages.  `all_scan_ids` is computed server-side from
  // every IngestionJob for this session and rides on the response
  // regardless of which uploads page is loaded.
  const scanIds = detail.all_scan_ids;

  const inventoryHref =
    scanIds.length > 0 ? `/hosts?scan_ids=${scanIds.join(',')}` : '/hosts';

  if (stats.host_count === 0) {
    return (
      <Card className="mb-md">
        <CardContent className="p-md">
          <h2 className="mb-xxs text-subheading font-semibold">Run output</h2>
          <p className="text-metadata text-muted-foreground">
            No hosts have landed in scope under this session yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mb-md">
      <CardContent className="flex flex-col gap-md p-md">
        <div className="flex flex-wrap items-start justify-between gap-sm">
          <div className="min-w-0">
            <h2 className="text-subheading font-semibold">Run output</h2>
            <p className="text-caption text-muted-foreground">
              Aggregate view of what this session's scans produced. Browse the full host
              list in Inventory — it's built for that workload.
            </p>
          </div>
          <Button
            size="sm"
            variant="default"
            onClick={() => navigate(inventoryHref)}
            disabled={scanIds.length === 0}
          >
            View {stats.host_count.toLocaleString()} hosts in Inventory
            <SquareArrowOutUpRight className="ml-xxs size-3" aria-hidden />
          </Button>
        </div>

        <div className="grid grid-cols-1 gap-sm sm:grid-cols-2 lg:grid-cols-3">
          <MetaField
            label="Distinct hosts"
            value={stats.host_count.toLocaleString()}
          />
          <MetaField
            label="Hosts with open ports"
            value={stats.host_count_with_open_ports.toLocaleString()}
          />
          <MetaField
            label="Tools that contributed"
            value={stats.by_tool.length.toString()}
          />
        </div>

        {stats.by_tool.length > 0 && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold uppercase tracking-wide text-muted-foreground">
              By tool
            </h3>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead className="w-24 text-right">Scans</TableHead>
                    <TableHead className="w-24 text-right">Hosts</TableHead>
                    <TableHead className="w-24 text-right">Ports</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.by_tool.map((t) => (
                    <TableRow key={t.tool_name}>
                      <TableCell className="font-mono">{t.tool_name}</TableCell>
                      <TableCell className="text-right">
                        {t.scan_count.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right">
                        {t.host_count.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right">
                        {t.port_count.toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 gap-md lg:grid-cols-2">
          {stats.top_services.length > 0 && (
            <div>
              <h3 className="mb-xs text-metadata font-semibold uppercase tracking-wide text-muted-foreground">
                Top services
              </h3>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Service</TableHead>
                    <TableHead className="w-24 text-right">Hosts</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.top_services.map((s) => (
                    <TableRow key={s.service_name}>
                      <TableCell className="font-mono">{s.service_name}</TableCell>
                      <TableCell className="text-right">
                        {s.host_count.toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {stats.top_open_ports.length > 0 && (
            <div>
              <h3 className="mb-xs text-metadata font-semibold uppercase tracking-wide text-muted-foreground">
                Top open ports
              </h3>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Port</TableHead>
                    <TableHead className="w-24 text-right">Hosts</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.top_open_ports.map((p) => (
                    <TableRow key={`${p.port_number}/${p.protocol}`}>
                      <TableCell className="font-mono">
                        {p.port_number}/{p.protocol}
                      </TableCell>
                      <TableCell className="text-right">
                        {p.host_count.toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

const PlansSection: React.FC<{
  detail: ReconSessionDetail;
  onLoadMore: () => void;
  loadingMore: boolean;
}> = ({ detail, onLoadMore, loadingMore }) => {
  const navigate = useNavigate();
  const remaining = detail.plans_total - detail.plans_generated.length;
  return (
    <Card className="mb-md">
      <CardContent className="p-md">
        <h2 className="text-subheading font-semibold">
          Plans generated from this recon
          {detail.plans_total > 0 && (
            <span className="ml-xxs text-caption text-muted-foreground">
              ({detail.plans_generated.length}
              {remaining > 0 ? ` of ${detail.plans_total}` : ''})
            </span>
          )}
        </h2>
        <p className="mb-sm text-caption text-muted-foreground">
          Test plans drafted with this run as their source (via{' '}
          <code className="font-mono">source_recon_session_id</code>). Older plans created before v3
          alpha.3 carry no source provenance and won't appear here even if their entries cover hosts
          this session discovered.
        </p>
        {detail.plans_total === 0 ? (
          <p className="text-metadata text-muted-foreground">
            No plans drafted from this recon yet.
          </p>
        ) : (
          <ul className="flex flex-col gap-xxs">
            {detail.plans_generated.map((plan) => (
              <li
                key={plan.plan_id}
                className="flex flex-wrap items-center gap-xs"
              >
                <Badge variant={statusTone(plan.status)}>{plan.status}</Badge>
                <p className="min-w-0 flex-1 truncate text-metadata">
                  <strong>#{plan.plan_id}</strong> v{plan.version} · {plan.title || '—'}{' '}
                  <span className="text-caption text-muted-foreground">
                    · {plan.entry_count} entr{plan.entry_count === 1 ? 'y' : 'ies'}
                    {plan.generated_by_model && ` · by ${plan.generated_by_model}`}
                  </span>
                </p>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => navigate(`/test-plans/${plan.plan_id}`)}
                >
                  Open
                  <SquareArrowOutUpRight className="size-3" aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
        )}
        {remaining > 0 && (
          <div className="mt-sm flex items-center justify-center">
            <Button
              size="sm"
              variant="outline"
              onClick={onLoadMore}
              disabled={loadingMore}
              aria-label={`Load more plans, ${remaining} remaining`}
            >
              {loadingMore ? 'Loading…' : `Load more (${remaining} remaining)`}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

const CompareSection: React.FC<{
  currentId: number;
  currentScopeId: number;
  siblings: ReconSessionRow[];
}> = ({ currentId, currentScopeId, siblings }) => {
  const navigate = useNavigate();
  const others = siblings.filter((s) => s.id !== currentId);
  const sameScope = others.filter((s) => s.scope_id === currentScopeId);
  const preferred = sameScope[0] ?? others[0] ?? null;

  return (
    <Card className="mb-md">
      <CardContent className="flex flex-col gap-sm p-md">
        <div className="flex flex-wrap items-start justify-between gap-sm">
          <div className="min-w-0 flex-1">
            <h2 className="text-subheading font-semibold">Compare with another recon</h2>
            <p className="text-caption text-muted-foreground">
              Diff host coverage and per-host ports side by side.
            </p>
          </div>
          {preferred ? (
            <div className="flex flex-wrap gap-xs">
              <Button
                size="sm"
                onClick={() => navigate(`/recon/compare?a=${currentId}&b=${preferred.id}`)}
              >
                Compare with #{preferred.id}
                {preferred.generated_by_model && ` · ${preferred.generated_by_model}`}
                {!sameScope.includes(preferred) &&
                  preferred.scope_name &&
                  ` (scope ${preferred.scope_name})`}
              </Button>
              {others.length > 1 && (
                <Button size="sm" variant="outline" onClick={() => navigate('/recon/runs')}>
                  Pick from all {others.length + 1} runs
                </Button>
              )}
            </div>
          ) : (
            <Button size="sm" variant="outline" onClick={() => navigate('/recon/runs')}>
              Open Recon Runs
            </Button>
          )}
        </div>
        {!preferred && (
          <p className="text-metadata text-muted-foreground">
            This is the only recon session recorded for this project so far. Run another recon (any
            scope) and the Compare button will activate.
          </p>
        )}
      </CardContent>
    </Card>
  );
};

const ReconRunDetail: React.FC = () => {
  const { sessionId: sessionIdRaw } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const sessionId = sessionIdRaw ? parseInt(sessionIdRaw, 10) : NaN;

  const { hasPermission } = useAuth();
  const toast = useToast();
  const canAbandon = hasPermission('analyst');
  // Same role gate as Abandon — Resume mints a fresh agent key and
  // revokes the prior one, so it's an analyst-level affordance.
  const canResume = hasPermission('analyst');
  const recon = useReconPlan();
  const [detail, setDetail] = useState<ReconSessionDetail | null>(null);
  const [siblings, setSiblings] = useState<ReconSessionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [abandonOpen, setAbandonOpen] = useState(false);
  const [abandonReason, setAbandonReason] = useState('');
  const [abandonBusy, setAbandonBusy] = useState(false);

  const [siblingsError, setSiblingsError] = useState<string | null>(null);
  const [loadingMoreUploads, setLoadingMoreUploads] = useState(false);
  const [loadingMorePlans, setLoadingMorePlans] = useState(false);
  // v2.87.0 — auto-refresh (useVisibilityPoll) reads detail.uploads /
  // .plans_generated lengths to size the next reload so the poll
  // doesn't shrink the currently-loaded slices back to page 1.  Ref
  // lets `reload` read the live value without depending on `detail`
  // (which would restart the poll every state change).
  const detailRef = useRef<ReconSessionDetail | null>(null);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  // v2.87.0 — match the backend default + max (50 / 500).
  const UPLOADS_PAGE_SIZE = 50;
  const PLANS_PAGE_SIZE = 50;
  const MAX_PAGE_SIZE = 500;

  const reload = useCallback(async () => {
    if (!sessionId || Number.isNaN(sessionId)) {
      setError('Invalid session id in URL.');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    // v2.87.0 — preserve the currently-loaded child-list slices on
    // refresh: ask the server for at least as many rows as we already
    // had so a 10s auto-poll doesn't shrink the user's view.  Clamped
    // to MAX_PAGE_SIZE (server cap); a session with more child rows
    // than that needs a Load-more click after the refresh anyway.
    const prev = detailRef.current;
    const uploadsLimit = Math.min(
      Math.max(prev?.uploads.length ?? 0, UPLOADS_PAGE_SIZE),
      MAX_PAGE_SIZE,
    );
    const plansLimit = Math.min(
      Math.max(prev?.plans_generated.length ?? 0, PLANS_PAGE_SIZE),
      MAX_PAGE_SIZE,
    );
    // Parallel fetch (audit PRF·H6) — the two endpoints are
    // independent. Errors are reported per-section so the Detail
    // surface can still render when the sibling-list lookup fails
    // (audit FBK·H5).
    const [detailResult, siblingResult] = await Promise.allSettled([
      getReconSession(sessionId, { uploadsLimit, plansLimit }),
      listReconSessions({}),
    ]);
    if (detailResult.status === 'fulfilled') {
      setDetail(detailResult.value);
    } else {
      setError(formatApiError(detailResult.reason, 'Failed to load recon session.'));
    }
    if (siblingResult.status === 'fulfilled') {
      // v2.86.10 — listReconSessions now returns {items, total}; this
      // caller only needs the items array.
      setSiblings(siblingResult.value.items);
      setSiblingsError(null);
    } else {
      setSiblings([]);
      setSiblingsError(
        formatApiError(siblingResult.reason, 'Unable to load comparable recon sessions.'),
      );
    }
    setLoading(false);
  }, [sessionId]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Auto-refresh while the session is active (audit H2). Visibility-
  // gated (audit CRIT-19) so backgrounded tabs stop hammering the API
  // for hour-long stalled sessions.
  useVisibilityPoll(reload, detail?.summary.status === 'active' ? 10_000 : null);

  // v2.87.0 — Load more handlers for the two paginated child lists.
  // Each fetches the next page with skip = currently-loaded length;
  // server-side dedup isn't needed but a defensive client-side dedup
  // (by job_id / plan_id) guards a fast re-click before the previous
  // request settles.
  const loadMoreUploads = useCallback(async () => {
    if (!sessionId || Number.isNaN(sessionId) || !detail || loadingMoreUploads) return;
    const loaded = detail.uploads.length;
    if (loaded >= detail.uploads_total) return;
    setLoadingMoreUploads(true);
    try {
      const next = await getReconSession(sessionId, {
        uploadsSkip: loaded,
        uploadsLimit: UPLOADS_PAGE_SIZE,
        plansSkip: 0,
        plansLimit: detail.plans_generated.length || PLANS_PAGE_SIZE,
      });
      setDetail((prev) => {
        if (!prev) return next;
        const seen = new Set(prev.uploads.map((u) => u.job_id));
        const fresh = next.uploads.filter((u) => !seen.has(u.job_id));
        return { ...prev, uploads: [...prev.uploads, ...fresh], uploads_total: next.uploads_total };
      });
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to load more uploads.'));
    } finally {
      setLoadingMoreUploads(false);
    }
  }, [sessionId, detail, loadingMoreUploads, toast]);

  const loadMorePlans = useCallback(async () => {
    if (!sessionId || Number.isNaN(sessionId) || !detail || loadingMorePlans) return;
    const loaded = detail.plans_generated.length;
    if (loaded >= detail.plans_total) return;
    setLoadingMorePlans(true);
    try {
      const next = await getReconSession(sessionId, {
        plansSkip: loaded,
        plansLimit: PLANS_PAGE_SIZE,
        uploadsSkip: 0,
        uploadsLimit: detail.uploads.length || UPLOADS_PAGE_SIZE,
      });
      setDetail((prev) => {
        if (!prev) return next;
        const seen = new Set(prev.plans_generated.map((p) => p.plan_id));
        const fresh = next.plans_generated.filter((p) => !seen.has(p.plan_id));
        return {
          ...prev,
          plans_generated: [...prev.plans_generated, ...fresh],
          plans_total: next.plans_total,
        };
      });
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to load more plans.'));
    } finally {
      setLoadingMorePlans(false);
    }
  }, [sessionId, detail, loadingMorePlans, toast]);

  const handleAbandon = async () => {
    if (!detail) return;
    setAbandonBusy(true);
    try {
      const updated = await abandonReconSession(detail.summary.id, abandonReason);
      setDetail({ ...detail, summary: updated });
      setAbandonOpen(false);
      setAbandonReason('');
      toast.success(`Recon session #${updated.id} marked abandoned.`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to abandon recon session.'));
    } finally {
      setAbandonBusy(false);
    }
  };

  const isActive = detail?.summary.status?.toLowerCase() === 'active';

  return (
    <div className="p-md md:p-lg">
      {loading && !detail && <DetailSkeleton />}

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {detail && (
        <>
          <WorkflowDetailHeader
            onBack={() => navigate(-1)}
            backLabel="Back to recon runs"
            title={`Recon run #${detail.summary.id}`}
            badges={
              <>
                <Badge variant={statusTone(detail.summary.status)}>{detail.summary.status}</Badge>
                {detail.summary.is_stale && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge variant="warning" className="cursor-help">
                        Possibly interrupted
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      No agent API activity for 15+ minutes — this run may have been interrupted.
                      Use Resume to re-issue a key and continue.
                    </TooltipContent>
                  </Tooltip>
                )}
              </>
            }
            subtitle={
              <>
                <span>
                  Scope{' '}
                  <strong className="text-foreground">
                    #{detail.summary.scope_id}
                    {detail.summary.scope_name ? ` · ${detail.summary.scope_name}` : ''}
                  </strong>
                  {detail.summary.started_by_username && (
                    <> · started by {detail.summary.started_by_username}</>
                  )}
                  {detail.summary.started_at && <> · {fmtTime(detail.summary.started_at)}</>}
                  {detail.summary.completed_at && (
                    <> · completed {fmtTime(detail.summary.completed_at)}</>
                  )}
                  {detail.summary.last_activity_at && (
                    <> · last agent activity {fmtTime(detail.summary.last_activity_at)}</>
                  )}
                </span>
                {(detail.summary.generated_by_model || detail.summary.generated_by_tool) && (
                  <span className="mt-xxs block">
                    Executed by{' '}
                    <strong className="text-foreground">
                      {detail.summary.generated_by_model || 'unknown model'}
                    </strong>
                    {detail.summary.generated_by_tool && ` via ${detail.summary.generated_by_tool}`}
                    {detail.summary.prompt_version && ` (prompt ${detail.summary.prompt_version})`}
                  </span>
                )}
              </>
            }
            actions={
              <>
                {detail.summary.status === 'active' && canResume && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      recon.openForResume(
                        detail.summary.scope_id,
                        detail.summary.scope_name || `Scope #${detail.summary.scope_id}`,
                        detail.summary.id,
                      )
                    }
                  >
                    <RotateCcw className="size-4" aria-hidden /> Resume
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={reload}>
                  <RefreshCw className="size-4" aria-hidden /> Refresh
                </Button>
                {detail.summary.scope_id && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => navigate(`/scopes/${detail.summary.scope_id}`)}
                  >
                    Scope #{detail.summary.scope_id}
                    <SquareArrowOutUpRight className="size-3" aria-hidden />
                  </Button>
                )}
              </>
            }
            destructiveAction={
              // Abandon — operator escape hatch for sessions the agent
              // never closed via /agent/recon/complete.  Only on active
              // sessions and only for analyst-or-better.
              isActive && canAbandon ? (
                <Button
                  size="sm"
                  variant="warning-outline"
                  onClick={() => {
                    setAbandonReason('');
                    setAbandonOpen(true);
                  }}
                >
                  <CircleSlash className="size-4" aria-hidden /> Abandon
                </Button>
              ) : null
            }
          >
            <div className="grid grid-cols-2 gap-sm sm:grid-cols-4">
              {[
                { value: detail.summary.uploads_submitted, label: 'Uploads' },
                { value: detail.summary.scans_ingested, label: 'Scans ingested' },
                { value: detail.summary.hosts_discovered, label: 'Hosts discovered' },
                { value: detail.summary.ports_discovered, label: 'Open ports' },
              ].map((m) => (
                <Card key={m.label}>
                  <CardContent className="p-sm text-center">
                    <p className="text-section-title font-semibold">{m.value}</p>
                    <p className="text-caption text-muted-foreground">{m.label}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </WorkflowDetailHeader>

          {/* Abandon-confirmation dialog.  Reason is optional; we always
              stamp who-and-when in the session notes regardless. */}
          <ConfirmDialog
            open={abandonOpen}
            onOpenChange={setAbandonOpen}
            busy={abandonBusy}
            titleIcon={<CircleSlash className="size-5 text-warning" aria-hidden />}
            title={`Abandon recon session #${detail.summary.id}?`}
            description={
              <>
                Use this when the terminal-side agent never called{' '}
                <code className="font-mono">/agent/recon/complete</code> — e.g. the agent process
                died, you killed the terminal, or it just forgot. The session moves to{' '}
                <strong>abandoned</strong>; the rail stops surfacing it as live. Any uploads already
                submitted stay; this doesn't delete data.
              </>
            }
            reason={{
              value: abandonReason,
              onChange: setAbandonReason,
              placeholder: 'e.g. agent process died after 3 hosts',
              helpText:
                'Your username and the timestamp are recorded in the session notes either way — the reason just adds context.',
            }}
            confirmLabel="Abandon session"
            confirmIcon={<CircleSlash className="size-4" aria-hidden />}
            confirmVariant="warning"
            onConfirm={handleAbandon}
          />

          {isActive && (
            // FRX·M8: operators commonly worry the agent run dies if
            // they navigate away — surface the truth that the session
            // is server-side and re-findable.
            <p className="mb-md text-caption text-muted-foreground">
              Safe to navigate away — this session lives on the server. Find it later under
              Workflows → Recon Runs.
            </p>
          )}
          {/* Audit H1: the recon → test-plan hand-off was silent.
              When recon completes and no plan has been generated from
              it yet, prompt the user with a concrete next step
              instead of letting the page go quiet. */}
          {detail.summary.status === 'completed' &&
            (!detail.plans_generated || detail.plans_generated.length === 0) && (
              <NextStepBanner
                tone="success"
                title="Recon complete — ready for test-plan generation"
                body={
                  <span>
                    The host data populated by this run is available in the Hosts page. The next
                    step is to generate a test plan from it; the agent picks candidate hosts using
                    the data this recon collected.
                  </span>
                }
                primaryCta={{
                  label: 'Generate Test Plan',
                  onClick: () =>
                    navigate(
                      `/test-plans?generate=1&source_recon_session_id=${detail.summary.id}`,
                    ),
                }}
                secondaryCta={{ label: 'Open Hosts', onClick: () => navigate('/hosts') }}
              />
            )}
          <EnvironmentSection detail={detail} />
          <UploadsSection
            detail={detail}
            onLoadMore={loadMoreUploads}
            loadingMore={loadingMoreUploads}
          />
          <RunOutputCard detail={detail} />
          <PlansSection
            detail={detail}
            onLoadMore={loadMorePlans}
            loadingMore={loadingMorePlans}
          />
          <CompareSection
            currentId={detail.summary.id}
            currentScopeId={detail.summary.scope_id}
            siblings={siblings}
          />
        </>
      )}
      {/* Shared "Start Agentic Reconnaissance" dialog — drives the
          Resume affordance on this page (resume mode is selected when
          recon.resumeSessionId is set via openForResume). */}
      <StartReconDialog recon={recon} />
    </div>
  );
};

export default ReconRunDetail;
