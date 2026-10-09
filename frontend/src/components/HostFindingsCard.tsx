/**
 * HostFindingsCard — this host's findings, inline in the inspector.
 *
 * Closes the in-context loop: a note promoted on this host shows up here
 * (and on /findings + the host-row badge), so findings live where you
 * triage rather than only on a separate page.  Refetches when refreshKey
 * changes (the inspector bumps it after a promote).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { SEVERITY_BADGE_VARIANT, SEVERITY_LABEL, SEVERITY_ORDER } from '../utils/severity';
import { useNavigate } from 'react-router-dom';
import { Loader2, Plus } from 'lucide-react';
import { AlertHexIcon } from './AppIcons';

import {
  createFinding,
  Finding,
  FindingHostStatus,
  FindingSeverity,
  FindingStatus,
  getFinding,
  listFindings,
  setFindingEndpointStatus,
  setFindingStatus,
} from '../services/api';
import { ENDPOINT_STATUS_LABEL, STATUS_LABEL, TERMINAL_STATUSES } from '../utils/findingStatus';
import { endpointPreviewIsCut } from '../utils/findingEndpoints';
import { runLimited } from '../utils/runLimited';
import { useToast } from '../contexts/ToastContext';
import { useProjectRole } from '../hooks/useProjectRole';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { FindingHistoryButton } from './FindingHistoryButton';
import { InspectorSection, openInspectorSection } from './host-inspector/InspectorSection';
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

/** What a finding written by hand may start as: the two a proposed finding
 *  may (`proposal_service.NEW_FINDING_STATUSES`). */
const NEW_STATUSES: Array<{ value: FindingStatus; label: string }> = [
  { value: 'open', label: 'Under investigation' },
  { value: 'confirmed', label: 'Confirmed' },
];

/** Write a finding on this host: a title, a severity and whether it is
 *  confirmed.  Its report text is written on the finding's own page. */
const AddFindingForm: React.FC<{
  hostId: number;
  onAdded: (made: Finding) => void;
  onCancel: () => void;
}> = ({ hostId, onAdded, onCancel }) => {
  const [title, setTitle] = useState('');
  const [severity, setSeverity] = useState<FindingSeverity>('medium');
  const [status, setStatus] = useState<FindingStatus>('open');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      onAdded(await createFinding({ title: title.trim(), severity, status, host_ids: [hostId] }));
    } catch (err) {
      setError(formatApiError(err, 'Could not add the finding.'));
      setBusy(false);
    }
  };

  return (
    <form
      className="mb-sm space-y-xs rounded-panel border border-border p-xs"
      onSubmit={(e) => { e.preventDefault(); if (!busy && title.trim()) void create(); }}
    >
      <div className="flex min-w-0 flex-wrap items-end gap-xs">
        <div className="min-w-0 flex-1 basis-64">
          <Label htmlFor="add-finding-title">Finding title</Label>
          <Input id="add-finding-title" value={title} maxLength={500} autoFocus onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="add-finding-severity">Severity</Label>
          <Select value={severity} onValueChange={(v) => setSeverity(v as FindingSeverity)}>
            <SelectTrigger id="add-finding-severity" className="h-9 w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SEVERITY_ORDER.map((sev) => <SelectItem key={sev} value={sev}>{SEVERITY_LABEL[sev]}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="add-finding-status">State</Label>
          <Select value={status} onValueChange={(v) => setStatus(v as FindingStatus)}>
            <SelectTrigger id="add-finding-status" className="h-9 w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              {NEW_STATUSES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      <p className="text-caption text-muted-foreground">
        For an issue no scanner row or test result on this host stands for. Write its report text, and add further hosts, on the finding.
      </p>
      <div className="flex flex-wrap gap-xs">
        <Button type="submit" size="sm" disabled={busy || title.trim().length === 0}>
          {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Add finding
        </Button>
        <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCancel}>Cancel</Button>
      </div>
      {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
    </form>
  );
};

/** One host's findings.  Keyed by the host, so another host starts empty and
 *  an answer for the host that was left has no list to land in. */
const HostFindingsCard: React.FC<HostFindingsCardProps> = (props) => (
  <HostFindingsCardBody key={props.hostId} {...props} />
);

const HostFindingsCardBody: React.FC<HostFindingsCardProps> = ({ hostId, refreshKey }) => {
  const toast = useToast();
  const navigate = useNavigate();
  const { canWrite: canManage } = useProjectRole();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [adding, setAdding] = useState(false);

  // A refresh supersedes the read before it: only the latest may write.
  const generation = useRef(0);

  const fetchFindings = useCallback(async () => {
    generation.current += 1;
    const mine = generation.current;
    const current = () => generation.current === mine;
    try {
      const res = await listFindings({ host_id: hostId, limit: 100 });
      if (!current()) return;
      // A list row's `hosts` is a preview of at most five endpoints (C2), and
      // with `host_id` the server puts THIS host's endpoint rows first
      // (`endpoint_summaries(first_host_id=)`), so the preview is enough: no
      // read per finding.  The one case it cannot answer is a cut preview
      // that is ALL this host's rows — a host with more named endpoints on
      // the finding than the preview holds — where the rows beyond it would
      // be left out of this host's state and of a change to it.  Only then is
      // the finding read whole; a failed read keeps the list row.
      const cut = res.items.filter((f) => {
        const shown = f.hosts ?? [];
        return endpointPreviewIsCut(f) && shown.length > 0 && shown.every((h) => h.host_id === hostId);
      });
      const whole = await runLimited<Finding, Finding>(cut, 4, (f) => getFinding(f.id));
      const byId = new Map<number, Finding>();
      whole.forEach((r) => { if (r.status === 'fulfilled') byId.set(r.value.id, r.value); });
      if (!current()) return;
      setFindings(res.items.map((f) => byId.get(f.id) ?? f));
    } catch {
      // Non-blocking surface — leave empty on error.
    } finally {
      if (current()) setLoaded(true);
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
      // A partial multi-row update must not be left looking whole.
      void fetchFindings();
    }
  };

  // No findings and nothing to do here: no section.  Someone who can write
  // always has it, because "Add finding" is how a finding that is neither a
  // scanner observation nor a test's result gets onto this host (5.346.0).
  if (!loaded || (findings.length === 0 && !canManage)) return null;

  const onAdded = (made: Finding) => {
    setAdding(false);
    toast.success(`Finding added: ${made.title}`, {
      autoHideMs: 8000,
      action: { label: 'Write it up', onClick: () => navigate(`/findings/${made.id}?edit=report-text`) },
    });
    void fetchFindings();
  };

  return (
    <InspectorSection
      id="host-detail-findings"
      title="Findings"
      icon={<AlertHexIcon className="size-4 shrink-0 text-warning" aria-hidden />}
      count={findings.length}
      actions={canManage && !adding ? (
        <Button variant="ghost" size="sm" className="h-7" onClick={() => { openInspectorSection('host-detail-findings'); setAdding(true); }}>
          <Plus className="size-3.5" aria-hidden /> Add finding
        </Button>
      ) : undefined}
    >
      {adding && <AddFindingForm hostId={hostId} onAdded={onAdded} onCancel={() => setAdding(false)} />}
      {findings.length === 0 && !adding && (
        <p className="text-metadata text-muted-foreground">No finding is recorded on this host.</p>
      )}
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
