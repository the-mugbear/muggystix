import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  CircleSlash,
  RefreshCw,
  SquareArrowOutUpRight,
} from 'lucide-react';
import {
  AllEntryResultsResponse,
  EntryResultsBundle,
  abandonExecutionSession,
  getExecutionSessionById,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Alert, AlertDescription } from '../components/ui/alert';
import { DetailSkeleton } from '../components/PageSkeleton';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { ConfirmDialog } from '../components/ui/confirm-dialog';
import { WorkflowDetailHeader } from '../components/workflow/WorkflowDetailHeader';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

interface EntryRollup {
  total_tests: number;
  executed: number;
  failed: number;
  findings: number;
  critical_findings: number;
  sanity_passed: number | null;
  sanity_total: number;
  commands_run: number;
}

function rollupEntry(entry: EntryResultsBundle): EntryRollup {
  const total_tests = entry.tests.length;
  const executed = entry.tests.filter((t) => t.status === 'executed').length;
  const failed = entry.tests.filter((t) => t.status === 'failed').length;
  const findings = entry.tests.filter((t) => t.is_finding).length;
  const critical_findings = entry.tests.filter(
    (t) => t.is_finding && t.severity === 'critical',
  ).length;
  const commands_run = entry.tests.filter((t) => t.command_run).length;
  const sanity_total = entry.sanity_checks.length;
  const sanity_passed =
    sanity_total === 0 ? null : entry.sanity_checks.filter((c) => c.passed).length;
  return {
    total_tests,
    executed,
    failed,
    findings,
    critical_findings,
    sanity_passed,
    sanity_total,
    commands_run,
  };
}

const fmtTime = (iso?: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

type BadgeTone = 'success' | 'info' | 'destructive' | 'warning' | 'muted';
const entryStatusTone = (s: string): BadgeTone => {
  if (s === 'completed') return 'success';
  if (s === 'in_progress') return 'info';
  if (s === 'rejected') return 'destructive';
  return 'muted';
};
const sessionStatusTone = (s: string): BadgeTone => {
  const t = (s || '').toLowerCase();
  if (t === 'active') return 'success';
  if (t === 'completed') return 'info';
  if (t === 'failed') return 'destructive';
  if (t === 'abandoned' || t === 'paused') return 'warning';
  return 'muted';
};

const EntryResultsTable: React.FC<{ bundle: AllEntryResultsResponse }> = ({ bundle }) => {
  const navigate = useNavigate();
  if (bundle.entries.length === 0) {
    return (
      <Card>
        <CardContent className="p-md text-metadata text-muted-foreground">
          This plan has no entries.
        </CardContent>
      </Card>
    );
  }
  const sorted = [...bundle.entries].sort((a, b) => {
    const ra = rollupEntry(a);
    const rb = rollupEntry(b);
    const ka =
      (ra.critical_findings > 0 ? 0 : 1) +
      (ra.findings > 0 ? 0 : 1) +
      (ra.failed > 0 ? 0 : 1);
    const kb =
      (rb.critical_findings > 0 ? 0 : 1) +
      (rb.findings > 0 ? 0 : 1) +
      (rb.failed > 0 ? 0 : 1);
    if (ka !== kb) return ka - kb;
    return a.entry_id - b.entry_id;
  });

  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-20">Entry</TableHead>
                <TableHead className="w-40">Host</TableHead>
                <TableHead>Hostname</TableHead>
                {/* w-28 (112px) overflowed for `in_progress` (11 chars
                    + chip padding ~135px).  w-36 (144px) clears every
                    value in entryStatusTone. */}
                <TableHead className="w-36">Status</TableHead>
                <TableHead className="w-28 text-right">Tests</TableHead>
                <TableHead className="w-32 text-right">Findings</TableHead>
                <TableHead className="w-32">Sanity</TableHead>
                <TableHead className="w-20" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((entry) => {
                const r = rollupEntry(entry);
                return (
                  <TableRow key={entry.entry_id}>
                    <TableCell className="font-mono">#{entry.entry_id}</TableCell>
                    {/* Audit RSP·H8 — w-40 Host column truncates so
                        IPv6 + long hostnames don't push the row past
                        the viewport with table-fixed. */}
                    <TableCell className="font-mono">
                      <span className="truncate block max-w-full">{entry.host_ip || '—'}</span>
                    </TableCell>
                    <TableCell>
                      <span className="truncate block max-w-full">{entry.host_hostname || '—'}</span>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={entryStatusTone(entry.entry_status)}
                        className="whitespace-nowrap"
                      >
                        {entry.entry_status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {r.executed}/{r.total_tests}
                      {r.failed > 0 && (
                        <span className="ml-xxs text-caption text-destructive">
                          ({r.failed} failed)
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {r.findings > 0 ? (
                        <div className="flex flex-wrap justify-end gap-xxs">
                          {r.critical_findings > 0 && (
                            <Badge variant="destructive">{r.critical_findings} crit</Badge>
                          )}
                          <Badge variant="outline" className="border-warning/40 text-warning">
                            {r.findings} total
                          </Badge>
                        </div>
                      ) : (
                        <span className="text-caption text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {r.sanity_passed === null ? (
                        <span className="text-caption text-muted-foreground">none recorded</span>
                      ) : (
                        <Badge
                          variant="outline"
                          className={
                            r.sanity_passed === r.sanity_total
                              ? 'border-success/40 text-success'
                              : 'border-warning/40 text-warning'
                          }
                        >
                          {r.sanity_passed}/{r.sanity_total} passed
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => navigate(`/test-plans/${bundle.plan_id}`)}
                      >
                        Open
                        <SquareArrowOutUpRight className="size-3" aria-hidden />
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
};

const ExecutionDetail: React.FC = () => {
  const { sessionId: sessionIdRaw } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const sessionId = sessionIdRaw ? parseInt(sessionIdRaw, 10) : NaN;

  const { hasPermission } = useAuth();
  const toast = useToast();
  const canAbandon = hasPermission('analyst');
  const [bundle, setBundle] = useState<AllEntryResultsResponse | null>(null);
  // v4.52.0 — `reload` (used by useVisibilityPoll) reads bundle.entries.length
  // to size the auto-refresh page.  We can't add `bundle` to reload's
  // deps without restarting the poll every state change, so the ref
  // gives the callback a stable read path.
  const bundleRef = useRef<AllEntryResultsResponse | null>(null);
  useEffect(() => {
    bundleRef.current = bundle;
  }, [bundle]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [abandonOpen, setAbandonOpen] = useState(false);
  const [abandonReason, setAbandonReason] = useState('');
  const [abandonBusy, setAbandonBusy] = useState(false);

  // v2.86.7 — paginate the entries bundle.  Pre-fix the page fetched
  // every entry's results + sanity checks on initial load — a
  // 5000-entry session shipped ~5000 entry-bundles every time the
  // ExecutionDetail page opened (and again on every 10s active-poll).
  // v4.52.0 — split into two page sizes (matching TestPlanLayout):
  // small initial page (50) for fast first paint, larger "Load more"
  // page (200) so each subsequent click amortizes its round-trip.
  const INITIAL_ENTRIES_PAGE_SIZE = 50;
  const LOAD_MORE_ENTRIES_PAGE_SIZE = 200;

  const reload = useCallback(async () => {
    if (!sessionId || Number.isNaN(sessionId)) {
      setError('Invalid session id in URL.');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      // v4.52.0 — auto-refresh while active keeps whatever the user
      // already loaded (clamped to the larger Load-more page so a
      // wide-open inspector doesn't re-fetch a tiny slice).  Cold
      // load uses the small initial page.
      const currentlyLoaded = bundleRef.current?.entries.length ?? 0;
      const entriesLimit = currentlyLoaded > INITIAL_ENTRIES_PAGE_SIZE
        ? Math.max(currentlyLoaded, LOAD_MORE_ENTRIES_PAGE_SIZE)
        : INITIAL_ENTRIES_PAGE_SIZE;
      const resp = await getExecutionSessionById(sessionId, {
        entriesLimit,
      });
      setBundle(resp);
    } catch (err) {
      setError(formatApiError(err, 'Failed to load execution session.'));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  const loadMoreEntries = useCallback(async () => {
    if (!bundle || loadingMore || !sessionId || Number.isNaN(sessionId)) return;
    const loaded = bundle.entries.length;
    const total = bundle.entries_total ?? loaded;
    if (loaded >= total) return;
    setLoadingMore(true);
    try {
      const next = await getExecutionSessionById(sessionId, {
        entriesSkip: loaded,
        entriesLimit: LOAD_MORE_ENTRIES_PAGE_SIZE,
      });
      setBundle((prev) => prev ? {
        ...next,
        entries: [
          ...prev.entries,
          ...next.entries.filter(
            (e) => !prev.entries.some((p) => p.entry_id === e.entry_id),
          ),
        ],
      } : next);
    } catch (err) {
      setError(formatApiError(err, 'Failed to load more entries.'));
    } finally {
      setLoadingMore(false);
    }
  }, [bundle, loadingMore, sessionId]);

  const totalEntries = bundle?.entries_total ?? bundle?.entries.length ?? 0;
  const loadedEntries = bundle?.entries.length ?? 0;
  const hasMoreEntries = loadedEntries < totalEntries;

  useEffect(() => {
    reload();
  }, [reload]);

  // Auto-refresh while the session is active — pre-audit (H2) the
  // page was a frozen snapshot until the user manually clicked
  // Refresh.  10s cadence matches Scans active-job polling. Now
  // visibility-gated (audit CRIT-19) so backgrounded tabs stop
  // hammering the API for hour-long stalled sessions.
  useVisibilityPoll(
    reload,
    bundle?.execution_session_status === 'active' ? 10_000 : null,
  );

  const handleAbandon = async () => {
    if (!bundle) return;
    setAbandonBusy(true);
    try {
      const updated = await abandonExecutionSession(
        bundle.execution_session_id,
        abandonReason,
      );
      setBundle({ ...bundle, execution_session_status: updated.status });
      setAbandonOpen(false);
      setAbandonReason('');
      toast.success(`Execution session #${updated.id} marked abandoned.`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to abandon execution session.'));
    } finally {
      setAbandonBusy(false);
    }
  };

  const sessionStatus = bundle?.execution_session_status?.toLowerCase();
  const isAbandonable =
    sessionStatus === 'active' || sessionStatus === 'paused';

  return (
    <div className="p-md md:p-lg">
      {loading && !bundle && <DetailSkeleton />}

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {bundle && (
        <>
          <WorkflowDetailHeader
            onBack={() => navigate(-1)}
            backLabel="Back to executions"
            title={`Execution session #${bundle.execution_session_id}`}
            badges={
              <Badge variant={sessionStatusTone(bundle.execution_session_status)}>
                {bundle.execution_session_status}
              </Badge>
            }
            subtitle={
              <>
                <span>
                  Plan <strong className="text-foreground">#{bundle.plan_id}</strong>
                  {bundle.started_by_username && <> · by {bundle.started_by_username}</>}
                  {bundle.started_at && <> · started {fmtTime(bundle.started_at)}</>}
                  {bundle.completed_at && <> · completed {fmtTime(bundle.completed_at)}</>}
                </span>
                {(bundle.generated_by_model || bundle.generated_by_tool) && (
                  <span className="mt-xxs block">
                    Executed by{' '}
                    <strong className="text-foreground">
                      {bundle.generated_by_model || 'unknown model'}
                    </strong>
                    {bundle.generated_by_tool && ` via ${bundle.generated_by_tool}`}
                    {bundle.prompt_version && ` (prompt ${bundle.prompt_version})`}
                  </span>
                )}
              </>
            }
            actions={
              <>
                <Button size="sm" variant="outline" onClick={reload}>
                  <RefreshCw className="size-4" aria-hidden /> Refresh
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => navigate(`/test-plans/${bundle.plan_id}`)}
                >
                  Plan #{bundle.plan_id}
                  <SquareArrowOutUpRight className="size-3" aria-hidden />
                </Button>
              </>
            }
            destructiveAction={
              // Abandon — operator escape hatch for sessions whose agent
              // never reached a terminal state.  Mirrors ReconRunDetail.
              isAbandonable && canAbandon ? (
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
          />

          {/* Abandon-confirmation dialog.  Reason is optional; the
              username + timestamp always land in the session notes. */}
          <ConfirmDialog
            open={abandonOpen}
            onOpenChange={setAbandonOpen}
            busy={abandonBusy}
            titleIcon={<CircleSlash className="size-5 text-warning" aria-hidden />}
            title={`Abandon execution session #${bundle.execution_session_id}?`}
            description={
              <>
                Use this when the terminal-side agent never reached the terminal state — e.g. the
                agent crashed mid-plan, you killed the terminal, or it just stopped responding. The
                session moves to <strong>abandoned</strong>; the rail stops surfacing it as live.
                Any results already submitted stay; this doesn't delete data.
              </>
            }
            reason={{
              value: abandonReason,
              onChange: setAbandonReason,
              placeholder: 'e.g. agent crashed after 4 of 12 entries',
              helpText:
                'Your username and the timestamp are recorded in the session notes either way — the reason just adds context.',
            }}
            confirmLabel="Abandon session"
            confirmIcon={<CircleSlash className="size-4" aria-hidden />}
            confirmVariant="warning"
            onConfirm={handleAbandon}
          />

          {sessionStatus === 'active' && (
            // FRX·M8: operators commonly worry the agent run dies if
            // they navigate away — surface the truth that the session
            // is server-side and re-findable.
            <p className="mb-md text-caption text-muted-foreground">
              Safe to navigate away — this session lives on the server. Find it later under
              Workflows → Executions.
            </p>
          )}
          <div className="mb-sm">
            <h2 className="text-subheading font-semibold">Entry results</h2>
            <p className="text-caption text-muted-foreground">
              One row per plan entry. Sorted by interestingness: critical findings first, then any
              findings, then failures, then completed entries.
            </p>
          </div>
          <EntryResultsTable bundle={bundle} />
          {/* v2.86.7 — Load more affordance when the entries bundle is
              larger than the first page.  Re-fetches from the same
              endpoint with entries_skip = current loaded count. */}
          {hasMoreEntries && (
            <div className="mt-md flex flex-col items-center gap-xs">
              <Button
                variant="outline"
                size="sm"
                onClick={loadMoreEntries}
                disabled={loadingMore}
              >
                {loadingMore
                  ? 'Loading…'
                  : `Load more (${totalEntries - loadedEntries} remaining)`}
              </Button>
              <p className="text-caption text-muted-foreground">
                Showing {loadedEntries} of {totalEntries} entries.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default ExecutionDetail;
