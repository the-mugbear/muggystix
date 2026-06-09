/**
 * /findings — the unified findings view (foundation phase 6).
 *
 * The project-wide roll-up of the finding spine: promoted notes (and, as
 * later phases reference them, scanner vulns + execution findings) in one
 * filterable list by status / severity. The destination that "matriculate
 * up" lands on.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, AlertTriangle } from 'lucide-react';

import {
  Finding,
  FindingFilters,
  FindingSeverity,
  FindingStatus,
  listFindings,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
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

const STATUS_LABEL: Record<FindingStatus, string> = {
  open: 'Open',
  confirmed: 'Confirmed',
  false_positive: 'False positive',
  accepted_risk: 'Accepted risk',
  remediated: 'Remediated',
  retest: 'Retest',
};

const STATUS_VARIANT: Record<FindingStatus, string> = {
  open: 'warning',
  confirmed: 'destructive',
  false_positive: 'muted',
  accepted_risk: 'muted',
  remediated: 'success',
  retest: 'info',
};

const Findings: React.FC = () => {
  const toast = useToast();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<FindingStatus | 'all'>('all');
  const [severityFilter, setSeverityFilter] = useState<FindingSeverity | 'all'>('all');

  const filters = useMemo<FindingFilters>(() => {
    const f: FindingFilters = { limit: 200 };
    if (statusFilter !== 'all') f.status = statusFilter;
    if (severityFilter !== 'all') f.severity = severityFilter;
    return f;
  }, [statusFilter, severityFilter]);

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
        <span className="ml-auto text-metadata text-muted-foreground">
          {total.toLocaleString()} finding{total === 1 ? '' : 's'}
        </span>
      </div>

      <Card>
        <CardContent className="p-0">
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
              {loading && (
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
                    No findings yet. Promote a note from a host (Notes → Promote to finding) to
                    record one here.
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
                    <span className="block truncate" title={f.title}>{f.title}</span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[f.status] as never}>{STATUS_LABEL[f.status]}</Badge>
                  </TableCell>
                  <TableCell className="text-caption text-muted-foreground">{f.source}</TableCell>
                  <TableCell>
                    {f.hosts.length === 0 ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      <span
                        className="block truncate font-mono text-caption"
                        title={f.hosts
                          .map((h) => h.ip_address + (h.hostname ? ` (${h.hostname})` : ''))
                          .join(', ')}
                      >
                        {f.hosts[0].ip_address}
                        {f.host_count > 1 && (
                          <span className="font-sans text-muted-foreground"> +{f.host_count - 1}</span>
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
        </CardContent>
      </Card>
    </div>
  );
};

export default Findings;
