/**
 * HostFindingsCard — this host's findings, inline in the inspector.
 *
 * Closes the in-context loop: a note promoted on this host shows up here
 * (and on /findings + the host-row badge), so findings live where you
 * triage rather than only on a separate page.  Refetches when refreshKey
 * changes (the inspector bumps it after a promote).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { SEVERITY_BADGE_VARIANT } from '../utils/severity';
import { useNavigate } from 'react-router-dom';
import { AlertHexIcon } from './AppIcons';

import {
  Finding,
  FindingHostStatus,
  FindingStatus,
  listFindings,
  setFindingEndpointStatus,
  setFindingStatus,
} from '../services/api';
import { ENDPOINT_STATUS_LABEL, STATUS_LABEL, TERMINAL_STATUSES } from '../utils/findingStatus';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from './ui/badge';
import { FindingHistoryButton } from './FindingHistoryButton';
import { InspectorSection } from './host-inspector/InspectorSection';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

const SEVERITY_VARIANT = SEVERITY_BADGE_VARIANT;

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

  // v5.238.1 — a finding that spans several hosts is not this host's to
  // re-judge: the control here sets THIS host's endpoint state, on every
  // endpoint row the host has on the finding (one per named endpoint).  The
  // selector used to set the ISSUE's status for every host from inside one
  // host's inspector — the same reach the false-positive dismissal had.
  const handleEndpointStatus = async (f: Finding, hostStatus: FindingHostStatus) => {
    const rows = (f.hosts ?? []).filter((h) => h.host_id === hostId && h.host_status !== hostStatus);
    if (rows.length === 0) return;
    try {
      let updated: Finding = f;
      for (const row of rows) {
        updated = await setFindingEndpointStatus(f.id, row.id, hostStatus);
      }
      setFindings((prev) => prev.map((x) => (x.id === f.id ? updated : x)));
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update this host’s state on the finding.'));
      void fetchFindings(); // a partial multi-row update must not be left looking whole
    }
  };

  // Gate on presence (mirrors WebInterfaces/NetExec cards) — no findings,
  // no card noise.  Appears once a note here is promoted.
  if (!loaded || findings.length === 0) return null;

  return (
    <InspectorSection
      id="host-detail-findings"
      title="Findings"
      icon={<AlertHexIcon className="size-4 shrink-0 text-warning" aria-hidden />}
      count={findings.length}
    >
      <div className="flex flex-col gap-xs">
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
            {(() => {
              const here = (f.hosts ?? []).filter((h) => h.host_id === hostId).map((h) => h.host_status);
              // Several named endpoints of this host on one finding: "false
              // positive here" only when all are; otherwise the live state.
              const state: FindingHostStatus = here.length === 0
                ? 'open'
                : here.every((s) => s === 'false_positive')
                  ? 'false_positive'
                  : here.find((s) => s !== 'open' && s !== 'false_positive') ?? 'open';
              const shared = (f.host_count ?? f.hosts?.length ?? 1) > 1;

              if (!shared) {
                // This host is the finding's only one: the issue's status IS
                // this host's, so it is set here as before.
                return canManage ? (
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
                );
              }

              return (
                <>
                  {/* The ISSUE's status, across all its hosts: read here,
                      changed on the finding's own page. */}
                  <button
                    type="button"
                    onClick={() => navigate(`/findings/${f.id}`)}
                    className="shrink-0 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    title={`The issue's status across its ${f.host_count} hosts. Open the finding to change it.`}
                    aria-label={`${f.title}: ${STATUS_LABEL[f.status]} across ${f.host_count} hosts — open the finding`}
                  >
                    <Badge variant="muted" className="hover:underline">
                      {STATUS_LABEL[f.status]} · {f.host_count} hosts
                    </Badge>
                  </button>
                  {canManage && here.length > 0 ? (
                    <Select value={state} onValueChange={(v) => void handleEndpointStatus(f, v as FindingHostStatus)}>
                      <SelectTrigger className="h-7 w-[11rem] text-caption" aria-label={`State of ${f.title} on this host`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(Object.keys(ENDPOINT_STATUS_LABEL) as FindingHostStatus[]).map((s) => (
                          <SelectItem key={s} value={s}>{ENDPOINT_STATUS_LABEL[s]}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Badge variant={state === 'open' ? 'warning' : state === 'remediated' ? 'success' : state === 'false_positive' ? 'outline' : 'info'}>
                      {ENDPOINT_STATUS_LABEL[state]}
                    </Badge>
                  )}
                </>
              );
            })()}
            <FindingHistoryButton findingId={f.id} />
          </div>
        ))}
      </div>
    </InspectorSection>
  );
};

export default HostFindingsCard;
