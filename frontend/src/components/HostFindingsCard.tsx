/**
 * HostFindingsCard — this host's findings, inline in the inspector.
 *
 * Closes the in-context loop: a note promoted on this host shows up here
 * (and on /findings + the host-row badge), so findings live where you
 * triage rather than only on a separate page.  Refetches when refreshKey
 * changes (the inspector bumps it after a promote).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertHexIcon } from './AppIcons';

import {
  Finding,
  FindingSeverity,
  FindingStatus,
  listFindings,
  setFindingStatus,
} from '../services/api';
import { TERMINAL_STATUSES } from '../utils/findingStatus';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from './ui/badge';
import { FindingHistoryButton } from './FindingHistoryButton';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

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

interface HostFindingsCardProps {
  hostId: number;
  /** Bump to force a refetch (e.g. after promoting a note here). */
  refreshKey?: number;
}

const HostFindingsCard: React.FC<HostFindingsCardProps> = ({ hostId, refreshKey }) => {
  const toast = useToast();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canManage = hasPermission('analyst');
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loaded, setLoaded] = useState(false);

  const fetchFindings = useCallback(async () => {
    try {
      const res = await listFindings({ host_id: hostId, limit: 100 });
      setFindings(res.items);
    } catch {
      // Non-blocking surface — leave empty on error.
    } finally {
      setLoaded(true);
    }
  }, [hostId]);

  useEffect(() => {
    fetchFindings();
  }, [fetchFindings, refreshKey]);

  const handleStatus = async (id: number, status: FindingStatus) => {
    // Terminal dispositions carry an audit rationale — hand off to the canonical
    // finding workspace (which prompts for it) instead of applying silently here.
    if (TERMINAL_STATUSES.has(status)) {
      navigate(`/findings/${id}`);
      return;
    }
    try {
      const updated = await setFindingStatus(id, status);
      setFindings((prev) => prev.map((f) => (f.id === id ? updated : f)));
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update finding status.'));
    }
  };

  // Gate on presence (mirrors WebInterfaces/NetExec cards) — no findings,
  // no card noise.  Appears once a note here is promoted.
  if (!loaded || findings.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-xs">
          <AlertHexIcon className="size-5 text-warning" aria-hidden />
          Findings ({findings.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-xs">
        {findings.map((f) => (
          <div key={f.id} className="flex flex-wrap items-center gap-xs border-b border-border pb-xs last:border-0 last:pb-0">
            <Badge variant={SEVERITY_VARIANT[f.severity] as never}>
              {f.severity[0].toUpperCase() + f.severity.slice(1)}
            </Badge>
            {f.source === 'note' && f.evidence_annotation_id ? (
              <a
                href={`#note-${f.evidence_annotation_id}`}
                className="min-w-0 flex-1 truncate text-info hover:underline"
                title={`${f.title} — jump to evidence thread`}
              >
                {f.title}
              </a>
            ) : (
              <span className="min-w-0 flex-1 truncate" title={f.title}>{f.title}</span>
            )}
            {canManage ? (
              <Select value={f.status} onValueChange={(v) => handleStatus(f.id, v as FindingStatus)}>
                <SelectTrigger className="h-7 w-[9rem] text-caption" aria-label={`Status for ${f.title}`}>
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
        ))}
      </CardContent>
    </Card>
  );
};

export default HostFindingsCard;
