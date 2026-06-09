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
  FindingStatus,
  listFindings,
  setFindingStatus,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
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

const SEVERITY_VARIANT: Record<FindingSeverity, string> = {
  critical: 'severity-critical',
  high: 'severity-high',
  medium: 'severity-medium',
  low: 'severity-low',
  info: 'muted',
};

// Terminal dispositions — moving a finding here prompts for a "why" summary
// that lands on the disposition history trail.
const TERMINAL_STATUSES = new Set<FindingStatus>(['false_positive', 'accepted_risk', 'remediated']);

const STATUS_LABEL: Record<FindingStatus, string> = {
  open: 'Open',
  confirmed: 'Confirmed',
  false_positive: 'False positive',
  accepted_risk: 'Accepted risk',
  remediated: 'Remediated',
  retest: 'Retest',
};

const Findings: React.FC = () => {
  const toast = useToast();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<FindingStatus | 'all'>('all');
  const [severityFilter, setSeverityFilter] = useState<FindingSeverity | 'all'>('all');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [summaryPrompt, setSummaryPrompt] = useState<
    { findingId: number; status: FindingStatus; title: string } | null
  >(null);
  const [summaryText, setSummaryText] = useState('');

  const hasActiveFilters = statusFilter !== 'all' || severityFilter !== 'all';

  // A filter change resets to the first page so we never sit on an
  // out-of-range page after the result set shrinks.
  useEffect(() => { setPage(0); }, [statusFilter, severityFilter]);

  const filters = useMemo<FindingFilters>(() => {
    const f: FindingFilters = { limit: pageSize, offset: page * pageSize };
    if (statusFilter !== 'all') f.status = statusFilter;
    if (severityFilter !== 'all') f.severity = severityFilter;
    return f;
  }, [statusFilter, severityFilter, page, pageSize]);

  const fetchFindings = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listFindings(filters);
      setFindings(res.items);
      setTotal(res.total);
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

  const handleStatusChange = (findingId: number, status: FindingStatus, title: string) => {
    // Terminal dispositions get a "why" prompt (the summary is the audit
    // rationale on the history trail); non-terminal moves apply immediately.
    if (TERMINAL_STATUSES.has(status)) {
      setSummaryPrompt({ findingId, status, title });
      setSummaryText('');
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
        <span className="ml-auto text-metadata text-muted-foreground" role="status" aria-live="polite">
          {loading ? 'Loading findings…' : `${total.toLocaleString()} finding${total === 1 ? '' : 's'}`}
        </span>
      </div>

      <Card>
        <CardContent className="p-0">
          {/* overflow-x-auto per the Table primitive's documented usage —
              keeps the fixed-width columns from forcing page-level overflow. */}
          <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
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
                  <TableCell colSpan={6} className="py-xl text-center text-muted-foreground">
                    <Loader2 className="mx-auto size-5 animate-spin" aria-hidden />
                  </TableCell>
                </TableRow>
              )}
              {!loading && error && (
                <TableRow>
                  <TableCell colSpan={6} className="py-lg text-center text-destructive">
                    <AlertTriangle className="mx-auto mb-xs size-5" aria-hidden />
                    {error}
                  </TableCell>
                </TableRow>
              )}
              {!loading && !error && findings.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="py-xl text-center text-muted-foreground">
                    {hasActiveFilters
                      ? 'No findings match these filters. Clear them to see all.'
                      : 'No findings yet. Promote a note from a host (Notes → Promote to finding) to record one here.'}
                  </TableCell>
                </TableRow>
              )}
              {!loading && !error && findings.map((f) => (
                <TableRow key={f.id}>
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
                    {f.source === 'note' && f.evidence_annotation_id && f.hosts.length > 0 ? (
                      <Link
                        to={`/hosts/${f.hosts[0].host_id}#note-${f.evidence_annotation_id}`}
                        className="block truncate text-info hover:underline"
                        title={`${f.title} — view evidence thread`}
                      >
                        {f.title}
                      </Link>
                    ) : (
                      <span className="block truncate" title={f.title}>{f.title}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-xxs">
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
              Mark {summaryPrompt ? STATUS_LABEL[summaryPrompt.status] : ''}
            </DialogTitle>
            <DialogDescription>
              Optionally record why — this is kept on the finding's disposition history.
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
                if (p) void applyStatus(p.findingId, p.status, p.title);
              }}
            >
              Skip
            </Button>
            <Button
              onClick={() => {
                const p = summaryPrompt;
                setSummaryPrompt(null);
                if (p) void applyStatus(p.findingId, p.status, p.title, summaryText.trim() || undefined);
              }}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default Findings;
