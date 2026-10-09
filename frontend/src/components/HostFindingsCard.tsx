/**
 * HostFindingsCard — this host's findings, inline in the inspector.
 *
 * Closes the in-context loop: a note promoted on this host shows up here
 * (and on /findings + the host-row badge), so findings live where you
 * triage rather than only on a separate page.  Its read is `listFindings`
 * for this host: whatever makes or changes a finding invalidates that name
 * (a promotion in the inspector, a test's result) and this reads again.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
import { holdProject, invalidateReads, queryErrorText } from '../lib/query';
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
  const queryClient = useQueryClient();
  const adding = useMutation({
    mutationFn: () => createFinding({ title: title.trim(), severity, status, host_ids: [hostId] }),
    onSuccess: (made) => {
      void queryClient.invalidateQueries({ queryKey: ['listFindings'] });
      onAdded(made);
    },
  });
  // Busy until the form goes: a finding that was made is not offered again.
  const busy = adding.isPending || adding.isSuccess;
  const error = queryErrorText(adding.error, 'Could not add the finding.');

  return (
    <form
      className="mb-sm space-y-xs rounded-panel border border-border p-xs"
      onSubmit={(e) => { e.preventDefault(); if (!busy && title.trim()) adding.mutate(); }}
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

const NO_FINDINGS: Finding[] = [];
/** How many of the host's findings one read asks for. */
const HOST_FINDINGS_LIMIT = 100;

/** What the card's read answers: the rows, how many the host has in all, and
 *  how many rows could not be completed (their whole read failed, so this
 *  host's state on them is from a preview that does not hold all of it). */
interface HostFindings {
  findings: Finding[];
  total: number;
  incomplete: number;
}

/** One host's findings.  The read is keyed by the host; the component is too,
 *  so a half-written "Add finding" form does not follow to the next host. */
const HostFindingsCard: React.FC<HostFindingsCardProps> = (props) => (
  <HostFindingsCardBody key={props.hostId} {...props} />
);

/** The key of this card's read.  It starts with `listFindings`, so whatever
 *  makes or changes a finding and invalidates that name re-reads this too. */
const hostFindingsKey = (hostId: number) =>
  ['listFindings', { host_id: hostId, limit: HOST_FINDINGS_LIMIT }, 'this-host-whole'] as const;

const HostFindingsCardBody: React.FC<HostFindingsCardProps> = ({ hostId }) => {
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { canWrite: canManage } = useProjectRole();
  const [adding, setAdding] = useState(false);

  const query = useQuery({
    queryKey: hostFindingsKey(hostId),
    queryFn: async ({ signal }): Promise<HostFindings> => {
      const res = await listFindings({ host_id: hostId, limit: HOST_FINDINGS_LIMIT }, signal);
      // A list row's `hosts` is a preview of at most five endpoints (C2), and
      // with `host_id` the server puts THIS host's endpoint rows first
      // (`endpoint_summaries(first_host_id=)`), so the preview is enough: no
      // read per finding.  The one case it cannot answer is a cut preview
      // that is ALL this host's rows — a host with more named endpoints on
      // the finding than the preview holds — where the rows beyond it would
      // be left out of this host's state and of a change to it.  Only then is
      // the finding read whole; a failed read keeps the list row, and is
      // counted (`incomplete`) so the section says so.
      const cut = res.items.filter((f) => {
        const shown = f.hosts ?? [];
        return endpointPreviewIsCut(f) && shown.length > 0 && shown.every((h) => h.host_id === hostId);
      });
      const whole = await runLimited<Finding, Finding>(cut, 4, (f) => getFinding(f.id, signal));
      const byId = new Map<number, Finding>();
      whole.forEach((r) => { if (r.status === 'fulfilled') byId.set(r.value.id, r.value); });
      return {
        findings: res.items.map((f) => byId.get(f.id) ?? f),
        total: res.total ?? res.items.length,
        incomplete: cut.length - byId.size,
      };
    },
  });
  const findings = query.data?.findings ?? NO_FINDINGS;
  const total = Math.max(query.data?.total ?? 0, findings.length);
  const incomplete = query.data?.incomplete ?? 0;
  const loaded = !query.isPending;
  // A read that failed with nothing to show is NOT "no findings" (code review
  // 2026-10-09): the section says so, to everyone, with Retry.
  const loadError = query.data == null ? queryErrorText(query.error, 'Could not load this host’s findings.') : null;

  // A status or endpoint change answers with the finding (put in the list)
  // and appended to its history: the trail behind the history button is out
  // of date, and is read again when it is next opened.  So is the host: its
  // scanner rows carry the state of the finding that covers them
  // (`finding_status`), which the inspector shows beside each.
  const findingChanged = () => {
    void invalidateReads(queryClient, 'getFindingHistory');
    void queryClient.invalidateQueries({ queryKey: ['getHost', hostId] });
  };
  const put = (updated: Finding) => {
    queryClient.setQueryData<HostFindings>(
      hostFindingsKey(hostId),
      (prev) => prev && { ...prev, findings: prev.findings.map((f) => (f.id === updated.id ? updated : f)) },
    );
    findingChanged();
  };

  const statusChange = useMutation({
    mutationFn: ({ id, status }: { id: number; status: FindingStatus }) => setFindingStatus(id, status),
    onSuccess: put,
    onError: (err) => toast.error(formatApiError(err, 'Failed to update finding status.')),
  });
  const handleStatus = (id: number, status: FindingStatus) => {
    // Terminal dispositions carry an audit rationale — hand off to the canonical
    // finding workspace (which prompts for it) instead of applying silently here.
    if (TERMINAL_STATUSES.has(status)) {
      navigate(`/findings/${id}`);
      return;
    }
    statusChange.mutate({ id, status });
  };

  // v5.238.1 — a finding that spans several hosts is not this host's to
  // re-judge: the control here sets THIS host's endpoint state, on every
  // endpoint row the host has on the finding (one per named endpoint).  The
  // selector used to set the ISSUE's status for every host from inside one
  // host's inspector — the same reach the false-positive dismissal had.
  const endpointChange = useMutation({
    mutationFn: async ({ f, rowIds, hostStatus }: { f: Finding; rowIds: number[]; hostStatus: FindingHostStatus }) => {
      let updated: Finding = f;
      const stillHere = holdProject();
      for (const rowId of rowIds) {
        stillHere();
        updated = await setFindingEndpointStatus(f.id, rowId, hostStatus);
      }
      return updated;
    },
    onSuccess: put,
    onError: (err) => {
      toast.error(formatApiError(err, 'Failed to update this host’s state on the finding.'));
      // A partial multi-row update must not be left looking whole — nor its
      // history and the host, which the rows that did change were written to.
      void queryClient.invalidateQueries({ queryKey: hostFindingsKey(hostId) });
      findingChanged();
    },
  });
  const handleEndpointStatus = (f: Finding, hostStatus: FindingHostStatus) => {
    const rows = (f.hosts ?? []).filter((h) => h.host_id === hostId && h.host_status !== hostStatus);
    if (rows.length === 0) return;
    endpointChange.mutate({ f, rowIds: rows.map((row) => row.id), hostStatus });
  };

  // No findings and nothing to do here: no section.  Someone who can write
  // always has it, because "Add finding" is how a finding that is neither a
  // scanner observation nor a test's result gets onto this host (5.346.0).
  if (!loaded || (findings.length === 0 && !canManage && !loadError)) return null;

  const onAdded = (made: Finding) => {
    setAdding(false);
    toast.success(`Finding added: ${made.title}`, {
      autoHideMs: 8000,
      action: { label: 'Write it up', onClick: () => navigate(`/findings/${made.id}?edit=report-text`) },
    });
  };

  return (
    <InspectorSection
      id="host-detail-findings"
      title="Findings"
      icon={<AlertHexIcon className="size-4 shrink-0 text-warning" aria-hidden />}
      count={loadError ? undefined : total}
      actions={canManage && !adding ? (
        <Button variant="ghost" size="sm" className="h-7" onClick={() => { openInspectorSection('host-detail-findings'); setAdding(true); }}>
          <Plus className="size-3.5" aria-hidden /> Add finding
        </Button>
      ) : undefined}
    >
      {adding && <AddFindingForm hostId={hostId} onAdded={onAdded} onCancel={() => setAdding(false)} />}
      {loadError && (
        <p role="alert" className="break-words text-metadata text-destructive">
          {loadError}{' '}
          <button type="button" className="text-info hover:underline" onClick={() => { void query.refetch(); }}>Retry</button>
        </p>
      )}
      {!loadError && findings.length === 0 && !adding && (
        <p className="text-metadata text-muted-foreground">No finding is recorded on this host.</p>
      )}
      {total > findings.length && (
        <p role="status" className="text-caption text-warning">
          Showing the first {findings.length.toLocaleString()} of {total.toLocaleString()} findings on this host.
        </p>
      )}
      {incomplete > 0 && (
        <p role="status" className="break-words text-caption text-warning">
          {incomplete === 1
            ? 'This host’s state on 1 finding could not be read in full; what is shown for it may leave out some of its endpoints.'
            : `This host’s state on ${incomplete.toLocaleString()} findings could not be read in full; what is shown for them may leave out some of their endpoints.`}{' '}
          <button type="button" className="text-info hover:underline" onClick={() => { void query.refetch(); }}>Retry</button>
        </p>
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
                    <Select value={state} onValueChange={(v) => handleEndpointStatus(f, v as FindingHostStatus)}>
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
