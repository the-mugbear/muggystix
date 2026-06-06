import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import {
  EntryExecutionResultsResponse,
  ExecutionSessionSummary,
  HostSanityCheckRow,
  TestExecutionResultRow,
  getEntryExecutionResults,
  listExecutionSessions,
} from '../services/api';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

interface Props {
  planId: number;
  entryId: number;
  sessionId?: number | null;
  /**
   * Optional — when supplied, each test row shows the tool name from
   * `proposedTests[row.test_index]`, so reviewers can see at a glance
   * *which proposed test (and which tool) produced a given finding*.
   * Without this prop the row still shows the recorded `command_run`,
   * which is usually enough to infer the tool, but the explicit
   * attribution is clearer (and matters when commands are bare scripts).
   */
  proposedTests?: ReadonlyArray<unknown>;
  /**
   * When true, the panel fetches the plan's execution sessions and
   * renders an inline "Run" dropdown so the user can switch between
   * runs without leaving the page. Used by the host-detail per-entry
   * view (HostInspector), which has no external session picker. Leave
   * off for surfaces that already own session selection externally
   * (PlanTab / TestPlanLayout drive selection through the `sessionId`
   * prop). If `sessionId` is provided (controlled), it takes precedence
   * over the internal selection so external pickers stay authoritative.
   */
  showSessionPicker?: boolean;
}

const toolForIndex = (proposedTests: ReadonlyArray<unknown> | undefined, idx: number): string | null => {
  if (!proposedTests || idx < 0 || idx >= proposedTests.length) return null;
  const t = proposedTests[idx];
  if (t && typeof t === 'object' && 'tool' in t) {
    const tool = (t as { tool?: unknown }).tool;
    if (typeof tool === 'string' && tool.trim()) return tool;
  }
  return null;
};

type Tone = 'success' | 'warning' | 'destructive' | 'info' | 'muted';

const statusTone = (status: string): Tone => {
  const s = status.toLowerCase();
  if (s === 'executed') return 'success';
  if (s === 'failed') return 'destructive';
  if (s === 'pending_approval' || s === 'pending') return 'info';
  return 'muted';
};

const severityTone = (sev?: string | null): Tone => {
  if (!sev) return 'muted';
  const s = sev.toLowerCase();
  if (s === 'critical') return 'destructive';
  if (s === 'high') return 'warning';
  if (s === 'medium') return 'info';
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

const RawOutputBlock: React.FC<{ raw: string | null | undefined }> = ({ raw }) => {
  const [open, setOpen] = useState(false);
  if (!raw) {
    return (
      <p className="text-caption text-muted-foreground">No raw output recorded.</p>
    );
  }
  const lineCount = raw.split('\n').length;
  return (
    <div>
      <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)}>
        {open ? (
          <ChevronUp className="size-4" aria-hidden />
        ) : (
          <ChevronDown className="size-4" aria-hidden />
        )}
        {open ? 'Hide raw output' : `Show raw output (${lineCount} line${lineCount === 1 ? '' : 's'})`}
      </Button>
      {open && (
        <pre className="mt-xs max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-control border border-border bg-card p-sm font-mono text-caption">
          {raw}
        </pre>
      )}
    </div>
  );
};

const TestResultRow: React.FC<{ row: TestExecutionResultRow; tool?: string | null }> = ({ row, tool }) => {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = row.command_run || row.raw_output || row.findings_summary || row.severity;
  return (
    <>
      <TableRow>
        <TableCell className="w-8">
          {hasDetail && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setExpanded((e) => !e)}
              aria-label="Toggle row detail"
              aria-expanded={expanded}
            >
              {expanded ? (
                <ChevronUp className="size-4" aria-hidden />
              ) : (
                <ChevronDown className="size-4" aria-hidden />
              )}
            </Button>
          )}
        </TableCell>
        <TableCell className="w-28">
          <p className="font-mono">#{row.test_index}</p>
          {tool && <p className="truncate text-caption text-muted-foreground" title={tool}>{tool}</p>}
        </TableCell>
        {/* w-32 (128px) overflowed for `pending_approval` (16 chars
            + chip padding ~180px).  Widened to w-44 (176px) + nowrap. */}
        <TableCell className="w-44">
          <Badge variant={statusTone(row.status)} className="whitespace-nowrap">
            {row.status}
          </Badge>
        </TableCell>
        <TableCell className="w-28">
          {row.severity ? (
            <Badge variant={severityTone(row.severity)} className="whitespace-nowrap">
              {row.severity}
            </Badge>
          ) : (
            <span className="text-caption text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="w-20">
          {row.is_finding ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <AlertCircle className="size-4 text-warning" aria-hidden />
              </TooltipTrigger>
              <TooltipContent>Finding recorded</TooltipContent>
            </Tooltip>
          ) : (
            <span className="text-caption text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="truncate">
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className={
                  row.command_run
                    ? 'font-mono text-caption'
                    : 'text-caption text-muted-foreground'
                }
              >
                {row.command_run || '— (no command recorded)'}
              </span>
            </TooltipTrigger>
            {row.command_run && <TooltipContent>{row.command_run}</TooltipContent>}
          </Tooltip>
        </TableCell>
        <TableCell className="w-44 whitespace-nowrap text-caption text-muted-foreground">
          {fmtTime(row.executed_at)}
        </TableCell>
      </TableRow>
      {hasDetail && expanded && (
        <TableRow>
          <TableCell colSpan={7} className="bg-accent p-md">
            <div className="flex flex-col gap-sm">
              {row.findings_summary && (
                <div>
                  <p className="text-caption text-muted-foreground">Findings summary</p>
                  <p className="whitespace-pre-wrap text-metadata">{row.findings_summary}</p>
                </div>
              )}
              {row.command_run && (
                <div>
                  <p className="text-caption text-muted-foreground">Command run</p>
                  <pre className="mt-xxs whitespace-pre-wrap break-all rounded-control border border-border bg-card p-xs font-mono text-caption">
                    {row.command_run}
                  </pre>
                </div>
              )}
              <RawOutputBlock raw={row.raw_output} />
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
};

const SanityCheckRowDisplay: React.FC<{ row: HostSanityCheckRow }> = ({ row }) => (
  <TableRow>
    <TableCell className="w-10">
      {row.passed ? (
        <CheckCircle2 className="size-4 text-success" aria-hidden />
      ) : (
        <AlertCircle className="size-4 text-destructive" aria-hidden />
      )}
    </TableCell>
    <TableCell className="w-36">
      <Badge variant="outline">{row.method}</Badge>
    </TableCell>
    <TableCell className="w-36 font-mono text-caption">
      {row.target_ip || '—'}
      {row.port_checked != null && `:${row.port_checked}`}
    </TableCell>
    <TableCell className="whitespace-pre-wrap break-words text-metadata">
      {row.details || row.actual_value || row.dns_result || '—'}
    </TableCell>
    <TableCell className="w-44 whitespace-nowrap text-caption text-muted-foreground">
      {fmtTime(row.checked_at)}
    </TableCell>
  </TableRow>
);

const EntryResultsPanel: React.FC<Props> = ({
  planId,
  entryId,
  sessionId,
  proposedTests,
  showSessionPicker = false,
}) => {
  const [data, setData] = useState<EntryExecutionResultsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  // Internal session selection — only consulted when the parent
  // doesn't pass `sessionId` (uncontrolled mode). External `sessionId`
  // always wins so PlanTab/RunsTab pickers stay authoritative.
  const [internalSessionId, setInternalSessionId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<ExecutionSessionSummary[] | null>(null);
  const externalControlled = sessionId !== undefined;
  const effectiveSessionId = externalControlled ? sessionId : internalSessionId;

  // Fetch the plan's session list once when the inline picker is enabled.
  useEffect(() => {
    if (!showSessionPicker || externalControlled) return;
    let cancelled = false;
    listExecutionSessions(planId)
      .then((resp) => {
        if (!cancelled) setSessions(resp.sessions);
      })
      .catch(() => {
        // Non-fatal — picker stays hidden, the panel still renders the
        // default-session results.
        if (!cancelled) setSessions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [planId, showSessionPicker, externalControlled]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getEntryExecutionResults(planId, entryId, effectiveSessionId ?? undefined);
      setData(result);
      // Sync the internal selection with whatever session the backend
      // picked by default (most-recent active/started), so the dropdown
      // reflects what's actually on screen instead of showing "select…".
      if (showSessionPicker && !externalControlled && internalSessionId == null && result.execution_session_id != null) {
        setInternalSessionId(result.execution_session_id);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load execution results.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [planId, entryId, effectiveSessionId, showSessionPicker, externalControlled, internalSessionId]);

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, entryId, effectiveSessionId, refreshNonce]);

  const sessionLabel = (s: ExecutionSessionSummary): string => {
    const when = s.started_at
      ? new Date(s.started_at).toLocaleString(undefined, {
          month: 'short',
          day: 'numeric',
          year: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        })
      : '—';
    const who = s.started_by_username ? ` · ${s.started_by_username}` : '';
    return `#${s.id} · ${s.status}${who} · ${when}`;
  };

  if (loading && !data) {
    return (
      <div className="flex items-center gap-xs py-xs text-metadata text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading execution results…
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!data) return null;

  const noSession = data.execution_session_id == null;
  const noResults = data.tests.length === 0 && data.sanity_checks.length === 0;

  return (
    <Card>
      <CardContent className="p-md">
        <div className="mb-sm flex flex-col gap-xs sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <p className="truncate text-subheading font-semibold">Test results</p>
            {/* Inline session picker — only when this surface (host detail)
                opts in AND there's more than one run to switch between.
                Otherwise we fall back to the static "Session #N — status"
                caption so externally-controlled callers (PlanTab) look
                unchanged. */}
            {showSessionPicker && !externalControlled && sessions && sessions.length > 1 ? (
              <div className="mt-xxs flex items-center gap-xs">
                <span className="text-caption text-muted-foreground">Run</span>
                <Select
                  value={effectiveSessionId != null ? String(effectiveSessionId) : ''}
                  onValueChange={(v) => setInternalSessionId(parseInt(v, 10))}
                  disabled={loading}
                >
                  <SelectTrigger
                    aria-label="Choose execution session for this entry"
                    className="h-7 max-w-[26rem]"
                  >
                    <SelectValue placeholder="Select a session" />
                  </SelectTrigger>
                  <SelectContent>
                    {sessions.map((s) => (
                      <SelectItem key={s.id} value={String(s.id)}>
                        {sessionLabel(s)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : (
              data.execution_session_id != null && (
                <p className="text-caption text-muted-foreground">
                  Session #{data.execution_session_id} — {data.execution_session_status}
                </p>
              )
            )}
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setRefreshNonce((n) => n + 1)}
                disabled={loading}
                aria-label="Refresh execution results"
              >
                <RefreshCw className="size-4" aria-hidden />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Refresh</TooltipContent>
          </Tooltip>
        </div>

        {noSession ? (
          <p className="text-metadata text-muted-foreground">
            No execution session yet. Use <strong>Execute with AI</strong> on this plan to mint an
            agent API key and start recording results.
          </p>
        ) : noResults ? (
          <p className="text-metadata text-muted-foreground">
            The execution session is open but the agent hasn&apos;t recorded any results for this
            entry yet.
          </p>
        ) : (
          <div className="flex flex-col gap-md">
            {data.sanity_checks.length > 0 && (
              <div>
                <p className="mb-xs text-metadata font-semibold">
                  Sanity checks ({data.sanity_checks.length})
                </p>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-10" />
                        <TableHead className="w-36">Method</TableHead>
                        <TableHead className="w-36">Target</TableHead>
                        <TableHead>Detail</TableHead>
                        <TableHead className="w-44">Checked at</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.sanity_checks.map((row) => (
                        <SanityCheckRowDisplay key={row.id} row={row} />
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            )}

            {data.tests.length > 0 && (
              <div>
                <p className="mb-xs text-metadata font-semibold">
                  Per-test results ({data.tests.length})
                </p>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-8" />
                        <TableHead className="w-28">Test</TableHead>
                        {/* See StatusCell — w-32 didn't fit
                            `pending_approval`; w-44 does. */}
                        <TableHead className="w-44">Status</TableHead>
                        <TableHead className="w-28">Severity</TableHead>
                        <TableHead className="w-20">Finding</TableHead>
                        <TableHead>Command</TableHead>
                        <TableHead className="w-44">Executed at</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.tests.map((row) => (
                        <TestResultRow
                          key={row.id}
                          row={row}
                          tool={toolForIndex(proposedTests, row.test_index)}
                        />
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default EntryResultsPanel;
