/**
 * /findings — the unified findings view (foundation phase 6).
 *
 * The project-wide roll-up of the finding spine: promoted notes (and, as
 * later phases reference them, scanner vulns + execution findings) in one
 * filterable list by status / severity. The destination that "matriculate
 * up" lands on.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, AlertTriangle } from 'lucide-react';

import {
  Finding,
  FindingFilters,
  FindingSeverity,
  FindingSource,
  FindingStatus,
  listFindings,
  setFindingStatus,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Checkbox } from '../components/ui/checkbox';
import { DataTablePagination } from '../components/ui/data-table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { Textarea } from '../components/ui/textarea';
import { FindingHistoryButton } from '../components/FindingHistoryButton';
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
import { safeFallback } from '../utils/uiStyles';
import { STATUS_LABEL, TERMINAL_STATUSES } from '../utils/findingStatus';

const SEVERITY_VARIANT: Record<FindingSeverity, string> = {
  critical: 'severity-critical',
  high: 'severity-high',
  medium: 'severity-medium',
  low: 'severity-low',
  info: 'muted',
};

type SummaryPrompt =
  | { kind: 'single'; findingId: number; status: FindingStatus; title: string }
  | { kind: 'bulk'; status: FindingStatus; ids: number[] };

const Findings: React.FC = () => {
  const toast = useToast();
  const { hasPermission } = useAuth();
  // Viewers may read findings but not dispose/select; analyst+ may triage.
  const canManage = hasPermission('analyst');
  const [confirmDialog, confirm] = useConfirm();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [total, setTotal] = useState(0);
  const [sevCounts, setSevCounts] = useState<Partial<Record<FindingSeverity, number>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<FindingStatus | 'all'>('all');
  const [severityFilter, setSeverityFilter] = useState<FindingSeverity | 'all'>('all');
  const [sourceFilter, setSourceFilter] = useState<FindingSource | 'all'>('all');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  // Bulk triage — selected finding ids on the current page.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkApplying, setBulkApplying] = useState(false);
  const [summaryPrompt, setSummaryPrompt] = useState<SummaryPrompt | null>(null);
  const [summaryText, setSummaryText] = useState('');

  const hasActiveFilters = statusFilter !== 'all' || severityFilter !== 'all' || sourceFilter !== 'all';

  // A filter change resets to the first page so we never sit on an
  // out-of-range page after the result set shrinks.
  useEffect(() => { setPage(0); }, [statusFilter, severityFilter, sourceFilter]);

  const filters = useMemo<FindingFilters>(() => {
    const f: FindingFilters = { limit: pageSize, offset: page * pageSize };
    if (statusFilter !== 'all') f.status = statusFilter;
    if (severityFilter !== 'all') f.severity = severityFilter;
    if (sourceFilter !== 'all') f.source = sourceFilter;
    return f;
  }, [statusFilter, severityFilter, sourceFilter, page, pageSize]);

  const fetchFindings = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listFindings(filters);
      setFindings(res.items);
      setTotal(res.total);
      setSevCounts(res.severity_counts ?? {});
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Failed to load findings.'));
      toast.error(formatApiError(err, 'Failed to load findings.'));
    } finally {
      setLoading(false);
    }
  }, [filters, toast]);

  useEffect(() => {
    fetchFindings();
  }, [fetchFindings]);

  const applyStatus = async (
    findingId: number, status: FindingStatus, title: string, summary?: string,
  ) => {
    try {
      const updated = await setFindingStatus(findingId, status, summary);
      setFindings((prev) =>
        prev
          // Drop a row that no longer matches the active status filter so the
          // filtered view stays truthful.
          .filter((f) => f.id !== findingId || statusFilter === 'all' || updated.status === statusFilter)
          .map((f) => (f.id === findingId ? updated : f)),
      );
      const short = title.length > 40 ? `${title.slice(0, 40)}…` : title;
      toast.success(`${short} → ${STATUS_LABEL[status]}`, { autoHideMs: 2500 });
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update finding status.'));
    }
  };

  const toggleSelected = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  // The actual bulk apply, with one optional rationale applied to every row's
  // history (the terminal-disposition audit trail).
  const runBulk = async (ids: number[], status: FindingStatus, summary?: string) => {
    setBulkApplying(true);
    try {
      const results = await Promise.allSettled(ids.map((id) => setFindingStatus(id, status, summary)));
      const updatedById = new Map<number, Finding>();
      let failed = 0;
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') updatedById.set(ids[i], r.value);
        else failed += 1;
      });
      setFindings((prev) =>
        prev
          // Drop rows that no longer match the active status filter.
          .filter((f) => !updatedById.has(f.id) || statusFilter === 'all' || updatedById.get(f.id)!.status === statusFilter)
          .map((f) => updatedById.get(f.id) ?? f),
      );
      setSelected(new Set());
      const ok = ids.length - failed;
      if (failed === 0) toast.success(`${ok} finding${ok === 1 ? '' : 's'} → ${STATUS_LABEL[status]}`, { autoHideMs: 2500 });
      else toast.warning(`${ok}/${ids.length} updated; ${failed} failed`);
    } finally {
      setBulkApplying(false);
    }
  };

  const applyBulkStatus = async (status: FindingStatus) => {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (TERMINAL_STATUSES.has(status)) {
      // Terminal bulk disposition: collect ONE rationale (applied to every
      // history row) + show the count — same audit discipline as the
      // single-finding flow, instead of silently applying to up to 200.
      setSummaryText('');
      setTimeout(() => setSummaryPrompt({ kind: 'bulk', status, ids }), 0);
      return;
    }
    // Non-terminal bulk move: confirm the blast radius before applying.
    const ok = await confirm({
      title: `Set ${ids.length} finding${ids.length === 1 ? '' : 's'} to ${STATUS_LABEL[status]}?`,
      severity: 'warning',
      confirmLabel: 'Apply',
    });
    if (ok) await runBulk(ids, status);
  };

  const handleStatusChange = (findingId: number, status: FindingStatus, title: string) => {
    // Terminal dispositions get a "why" prompt (the summary is the audit
    // rationale on the history trail); non-terminal moves apply immediately.
    if (TERMINAL_STATUSES.has(status)) {
      setSummaryText('');
      // Defer the dialog open to the next tick: opening a modal Dialog inside
      // a Radix Select's onValueChange races the Select's dismiss layer, which
      // can leave the body pointer-events:none so the dialog never appears.
      setTimeout(() => setSummaryPrompt({ kind: 'single', findingId, status, title }), 0);
    } else {
      void applyStatus(findingId, status, title);
    }
  };

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md">
        <h1 className="text-page-title font-semibold">Findings</h1>
        <p className="text-metadata text-muted-foreground">
          Promoted notes and triaged results across this project — the record everything rolls up by.
        </p>
      </div>

      <div className="mb-md flex flex-wrap items-end gap-sm">
        <div className="min-w-40">
          <Label htmlFor="findings-status">Status</Label>
          <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as FindingStatus | 'all')}>
            <SelectTrigger id="findings-status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
                <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-40">
          <Label htmlFor="findings-severity">Severity</Label>
          <Select value={severityFilter} onValueChange={(v) => setSeverityFilter(v as FindingSeverity | 'all')}>
            <SelectTrigger id="findings-severity">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All severities</SelectItem>
              {(['critical', 'high', 'medium', 'low', 'info'] as FindingSeverity[]).map((s) => (
                <SelectItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-40">
          <Label htmlFor="findings-source">Source</Label>
          <Select value={sourceFilter} onValueChange={(v) => setSourceFilter(v as FindingSource | 'all')}>
            <SelectTrigger id="findings-source">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All sources</SelectItem>
              {(['note', 'scanner', 'execution', 'manual'] as FindingSource[]).map((s) => (
                <SelectItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="ml-auto text-metadata text-muted-foreground" role="status" aria-live="polite">
          {loading ? 'Loading findings…' : `${total.toLocaleString()} finding${total === 1 ? '' : 's'}`}
        </span>
      </div>

      {/* Severity rollup — "how bad is this scope" at a glance (respects the
          status/source filters, ignores severity + pagination). */}
      {(['critical', 'high', 'medium', 'low', 'info'] as FindingSeverity[]).some((s) => sevCounts[s]) && (
        <div className="mb-md flex flex-wrap items-center gap-xs">
          {(['critical', 'high', 'medium', 'low', 'info'] as FindingSeverity[])
            .filter((s) => sevCounts[s])
            .map((s) => (
              <Badge key={s} variant={SEVERITY_VARIANT[s] as never}>
                {sevCounts[s]} {s}
              </Badge>
            ))}
        </div>
      )}

      {/* Bulk triage bar — appears when rows are selected. Applies one
          disposition to all selected (no per-item summary prompt in bulk). */}
      {selected.size > 0 && (
        <div className="mb-sm flex flex-wrap items-center gap-sm rounded-control border border-border bg-muted/30 p-sm">
          <span className="text-metadata font-medium">{selected.size} selected</span>
          <Select onValueChange={(v) => void applyBulkStatus(v as FindingStatus)} disabled={bulkApplying}>
            <SelectTrigger className="h-8 w-48 text-caption" aria-label="Set status for selected findings">
              <SelectValue placeholder={bulkApplying ? 'Applying…' : 'Set status…'} />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
                <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          {/* overflow-x-auto per the Table primitive's documented usage —
              keeps the fixed-width columns from forcing page-level overflow. */}
          <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  {canManage && (
                    <Checkbox
                      aria-label="Select all findings on this page"
                      checked={findings.length > 0 && findings.every((f) => selected.has(f.id))}
                      onCheckedChange={(v) =>
                        setSelected(v ? new Set(findings.map((f) => f.id)) : new Set())
                      }
                    />
                  )}
                </TableHead>
                <TableHead className="w-28">Severity</TableHead>
                <TableHead>Title</TableHead>
                <TableHead className="w-32">Status</TableHead>
                <TableHead className="w-24">Source</TableHead>
                <TableHead className="w-48">Hosts</TableHead>
                <TableHead className="w-40">Owner</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {/* Only blank the table on the INITIAL load; a filter refetch
                  keeps prior rows visible (no full-table flash). */}
              {loading && findings.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="py-xl text-center text-muted-foreground">
                    <Loader2 className="mx-auto size-5 animate-spin" aria-hidden />
                  </TableCell>
                </TableRow>
              )}
              {!loading && error && (
                <TableRow>
                  <TableCell colSpan={7} className="py-lg text-center text-destructive">
                    <AlertTriangle className="mx-auto mb-xs size-5" aria-hidden />
                    {error}
                  </TableCell>
                </TableRow>
              )}
              {!loading && !error && findings.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="py-xl text-center text-muted-foreground">
                    {hasActiveFilters
                      ? 'No findings match these filters. Clear them to see all.'
                      : 'No findings yet. Promote a note from a host (Notes → Promote to finding) to record one here.'}
                  </TableCell>
                </TableRow>
              )}
              {!loading && !error && findings.map((f) => (
                <TableRow key={f.id} data-state={selected.has(f.id) ? 'selected' : undefined}>
                  <TableCell>
                    {canManage && (
                      <Checkbox
                        aria-label={`Select ${f.title}`}
                        checked={selected.has(f.id)}
                        onCheckedChange={() => toggleSelected(f.id)}
                      />
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={SEVERITY_VARIANT[f.severity] as never}>
                      {f.severity[0].toUpperCase() + f.severity.slice(1)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {/* The note thread is the finding's evidence — link the
                        title to it (deep-links to the thread on the host, with
                        all its replies/discussion) so promote isn't a one-way
                        trip that drops the context. */}
                    <Link
                      to={`/findings/${f.id}`}
                      className="block truncate text-info hover:underline"
                      title={`${f.title} — open finding`}
                    >
                      {f.title}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-xxs">
                      {canManage ? (
                        <Select
                          value={f.status}
                          onValueChange={(v) => handleStatusChange(f.id, v as FindingStatus, f.title)}
                        >
                          <SelectTrigger
                            className="h-7 text-caption"
                            aria-label={`Status for ${f.title}`}
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
                              <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Badge variant="muted">{STATUS_LABEL[f.status]}</Badge>
                      )}
                      <FindingHistoryButton findingId={f.id} />
                    </div>
                  </TableCell>
                  <TableCell className="text-caption text-muted-foreground">{f.source}</TableCell>
                  <TableCell>
                    {f.hosts.length === 0 ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      <span
                        className="block truncate text-caption"
                        title={f.hosts
                          .map((h) => h.ip_address + (h.hostname ? ` (${h.hostname})` : ''))
                          .join(', ')}
                      >
                        <Link
                          to={`/hosts/${f.hosts[0].host_id}`}
                          className="font-mono text-info hover:underline"
                        >
                          {f.hosts[0].ip_address}
                        </Link>
                        {f.host_count > 1 && (
                          <span className="text-muted-foreground"> +{f.host_count - 1}</span>
                        )}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <span className="block truncate">{safeFallback(f.owner_name, 'Unassigned')}</span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          {!error && total > 0 && (
            <div className="border-t border-border p-xs">
              <DataTablePagination
                pageIndex={page}
                pageSize={pageSize}
                totalCount={total}
                onPageChange={setPage}
                onPageSizeChange={(s) => { setPageSize(s); setPage(0); }}
                pageSizeOptions={[25, 50, 100, 200]}
                leftLabel={null}
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Terminal-disposition "why" prompt — the summary lands on the
          finding's history trail as the audit rationale. */}
      <Dialog open={summaryPrompt !== null} onOpenChange={(v) => { if (!v) setSummaryPrompt(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {summaryPrompt
                ? summaryPrompt.kind === 'bulk'
                  ? `Mark ${summaryPrompt.ids.length} finding${summaryPrompt.ids.length === 1 ? '' : 's'} ${STATUS_LABEL[summaryPrompt.status]}`
                  : `Mark ${STATUS_LABEL[summaryPrompt.status]}`
                : ''}
            </DialogTitle>
            <DialogDescription>
              {summaryPrompt?.kind === 'bulk'
                ? 'One reason is recorded on every selected finding’s disposition history.'
                : 'Optionally record why — this is kept on the finding’s disposition history.'}
            </DialogDescription>
          </DialogHeader>
          <Textarea
            rows={3}
            autoFocus
            placeholder="e.g. confirmed false positive — scanner flagged the backport, not the CVE"
            value={summaryText}
            onChange={(e) => setSummaryText(e.target.value)}
            aria-label="Disposition reason"
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                const p = summaryPrompt;
                setSummaryPrompt(null);
                if (!p) return;
                if (p.kind === 'single') void applyStatus(p.findingId, p.status, p.title);
                else void runBulk(p.ids, p.status);
              }}
            >
              Skip
            </Button>
            <Button
              onClick={() => {
                const p = summaryPrompt;
                const summary = summaryText.trim() || undefined;
                setSummaryPrompt(null);
                if (!p) return;
                if (p.kind === 'single') void applyStatus(p.findingId, p.status, p.title, summary);
                else void runBulk(p.ids, p.status, summary);
              }}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {confirmDialog}
    </div>
  );
};

export default Findings;
