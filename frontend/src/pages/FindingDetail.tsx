/**
 * /findings/:id — the canonical finding workspace.
 *
 * Closes the multi-host dead-end the list view had (host #1 + "+N" in a
 * tooltip): here every affected host is listed with its own disposition and
 * a link, the evidence thread is one click away, the disposition history is
 * inline, and status/owner are editable in place. The place My Work,
 * reports, and notifications link a finding to.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { SEVERITY_BADGE_VARIANT, SEVERITY_LABEL } from '../utils/severity';
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ExternalLink, Loader2, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';

import {
  Finding,
  FindingSeverity,
  FindingStatus,
  Annotation,
  ProjectMember,
  getFinding,
  getFindingHistory,
  setFindingStatus,
  updateFinding,
  deleteFinding,
  FindingHostInfo,
  removeFindingEndpoint,
  addFindingHosts,
  getHostNotes,
  NoteAttachment,
} from '../services/api';
import { useProjectRoster } from '../hooks/useProjectMembers';
import MembersLoadError from '../components/MembersLoadError';
import MessageBubble from '../components/MessageBubble';
import FindingReportTextCard, { missingReportText } from '../components/FindingReportTextCard';
import AgentTaskButton from '../components/agent-sessions/AgentTaskButton';
import { agentInstruction } from '../utils/agentRuns';
import FindingProposalsPanel from '../components/proposals/FindingProposalsPanel';
import FindingEvidence from '../components/FindingEvidence';
import NoteAttachments from '../components/host-inspector/NoteAttachments';
import FindingCommentThread, { useFindingCommentCount } from '../components/FindingCommentThread';
import AddFindingHostsDialog from '../components/AddFindingHostsDialog';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRole } from '../hooks/useProjectRole';
import { useFindingImages } from '../hooks/useFindingImages';
import { useConfirm } from '../hooks/useConfirm';
import { useDiscardGuard } from '../hooks/useDiscardGuard';
import { queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { DetailSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import FindingEndpoints from '../components/findings/FindingEndpoints';
import { findingHistoryKey, findingKey, putFinding } from '../components/findings/findingCache';
import SectionJumpBar, { JumpEntry, jumpTargetStyle } from '../components/SectionJumpBar';
import { useFindingProposals } from '../hooks/useFindingProposals';
import type { OnProposalDecided } from '../hooks/useProposalDecision';
import { formatTimestamp } from '../utils/relativeTime';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';
import { Input } from '../components/ui/input';
import { Textarea } from '../components/ui/textarea';
import { safeFallback } from '../utils/uiStyles';
import { cn } from '../utils/cn';
import { STATUS_LABEL, TERMINAL_STATUSES } from '../utils/findingStatus';
import { RETURN_PARAM, safeFindingsReturn } from '../utils/findingsReturn';

const SEVERITY_VARIANT = SEVERITY_BADGE_VARIANT;
// Severity is an editable attribute (not a lifecycle transition, so it isn't
// in the status history) — surfaced here so a mis-set severity from
// promotion (e.g. medium that should be low) can be reclassified in place.
const histLabel = (s: string | null) => (s ? STATUS_LABEL[s as FindingStatus] ?? s : '—');

/** A finding with no recorded transition got its status when it was made —
 *  say so, rather than "No status changes recorded yet" under "Confirmed". */
export const initialStatusLine = (f: Pick<Finding, 'status' | 'created_at' | 'created_by_name'>): string => {
  const by = f.created_by_name ? ` by ${f.created_by_name}` : '';
  const when = f.created_at ? ` on ${formatTimestamp(f.created_at)}` : '';
  return `${STATUS_LABEL[f.status]} since the finding was created${by}${when} — no changes since.`;
};

const FindingDetail: React.FC = () => {
  const { findingId } = useParams<{ findingId: string }>();
  const id = Number(findingId);
  const projectId = useProjectId();
  const toast = useToast();
  const navigate = useNavigate();
  // M1 — the list hands its queue URL over as `from`; validated to an
  // internal /findings path so a crafted link can't send the operator off-site.
  const [searchParams] = useSearchParams();
  const returnTo = safeFindingsReturn(searchParams.get(RETURN_PARAM));
  // `?endpoint=<finding_host id>` — a proposal's link to the row it is about.
  const focusEndpointId = Number(searchParams.get('endpoint')) || null;
  const { user } = useAuth();
  // Findings routes admit viewers (read-only); analyst+ may dispose/detach.
  // Gate the write affordances so viewers see history without 403-bait controls.
  // Triage is a project analyst's (R32): a viewer or auditor reads the page.
  const { canWrite: canManage } = useProjectRole();
  const [confirmDialog, confirm] = useConfirm();
  // Unsaved report text: the page's Back asks first (the card guards reload
  // and its own Cancel).
  const reportTextDirty = useRef(false);
  const noteReportTextDirty = useCallback((dirty: boolean) => { reportTextDirty.current = dirty; }, []);
  const { confirmLeave, confirmEl: leaveDialog } = useDiscardGuard(
    () => reportTextDirty.current,
    'The report text you changed has not been saved. Leave anyway?',
  );
  const backToFindings = () => {
    if (!reportTextDirty.current) { navigate(returnTo); return; }
    void confirmLeave().then((ok) => { if (ok) navigate(returnTo); });
  };

  const queryClient = useQueryClient();
  // The finding is ONE query.  Every write route answers with the finding,
  // and whatever sent the write — this page, the endpoint table, the report
  // text, the "Add hosts" dialog — puts that answer on this record itself
  // (`putFinding`): no second read of thousands of endpoints.
  // The skeleton shows only while there is no finding yet.
  const findingQuery = useQuery({ queryKey: findingKey(projectId, id), queryFn: ({ signal }) => getFinding(projectId, id, signal) });
  const finding = findingQuery.data ?? null;
  // M2 — history is ancillary: its own query, it never gates the finding.
  const historyQuery = useQuery({ queryKey: findingHistoryKey(projectId, id), queryFn: ({ signal }) => getFindingHistory(projectId, id, signal) });
  const history = historyQuery.data ?? [];
  const historyLoading = historyQuery.isFetching;
  const historyError = queryErrorText(historyQuery.error, 'History unavailable.');
  // Terminal-disposition "why" prompt — mirrors the /findings list so a
  // status change on this page also captures the audit rationale.
  const [summaryPrompt, setSummaryPrompt] = useState<{ status: FindingStatus } | null>(null);
  const [summaryText, setSummaryText] = useState('');
  const [addHostsOpen, setAddHostsOpen] = useState(false);

  // A write of this page's own: the answer goes on the record (C2); the
  // history is read again only when the write appended to it.
  const replaceFinding = (updated: Finding, appendsHistory = true) =>
    putFinding(queryClient, projectId, updated, { history: appendsHistory });

  // An accepted proposal changed the finding (text, an endpoint).  The
  // decision asks for the finding, its history, its images and the pending
  // proposals again itself (`useProposalDecision`); the page's part is to say
  // so when the finding could not be read again.
  const proposalDecided = useCallback<OnProposalDecided>((updated, settled) => {
    if (updated.status !== 'accepted') return;
    void settled.then(() => {
      const failed = [findingKey(projectId, id), findingHistoryKey(projectId, id)]
        .some((key) => queryClient.getQueryState(key)?.status === 'error');
      if (failed) toast.warning('Accepted, but the page could not refresh — reload to see the change.');
    });
  }, [queryClient, projectId, id, toast]);

  // Roster for the owner picker (analyst+ only — viewers can't reassign).
  const roster = useProjectRoster({ enabled: canManage });
  const members = roster.members;

  const memberLabel = (m: ProjectMember) => m.full_name || m.username || `User ${m.user_id}`;

  const evidenceHref = useMemo(() => {
    if (!finding || finding.source !== 'note' || !finding.evidence_annotation_id) return null;
    const host = finding.hosts[0];
    return host ? `/hosts/${host.host_id}#note-${finding.evidence_annotation_id}` : null;
  }, [finding]);

  const evidenceHostId = finding?.hosts[0]?.host_id ?? null;

  // The note thread this finding was promoted from (root + replies, with
  // image attachments), shown inline — not just a link out.  H1-style split:
  // a failed read is shown as unavailable, not as "no evidence".
  const evidenceRootId = finding?.source === 'note' ? finding.evidence_annotation_id ?? null : null;
  const hostNotesQuery = useQuery({
    queryKey: ['getHostNotes', projectId, evidenceHostId],
    queryFn: ({ signal }) => getHostNotes(projectId, evidenceHostId as number, signal),
    enabled: !!evidenceRootId && !!evidenceHostId,
  });
  const evidenceError = queryErrorText(hostNotesQuery.error, 'Evidence note unavailable.');
  const hostNotes = hostNotesQuery.isError ? undefined : hostNotesQuery.data;
  const evidenceThread = useMemo<Annotation[]>(() => {
    if (!hostNotes || !evidenceRootId) return [];
    // Walk the subtree from the root note via parent_id links.
    const inThread = new Set<number>([evidenceRootId]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const n of hostNotes) {
        if (!inThread.has(n.id) && n.parent_id != null && inThread.has(n.parent_id)) {
          inThread.add(n.id);
          changed = true;
        }
      }
    }
    return hostNotes.filter((n) => inThread.has(n.id)).sort((a, b) => a.id - b.id);
  }, [hostNotes, evidenceRootId]);

  // C2 — a status or severity edit shows at once from the route's answer: the
  // page stays mounted (so the comment composer keeps its draft), the finding
  // is not read a second time, and only the edited control shows it is busy —
  // for a status until its history trail is back.
  //
  // One case reads the finding again instead.  The answer is the finding as
  // it stood when THIS change committed; if the record was written or is
  // being read while the request was out (a proposal accepted meanwhile),
  // which of the two is newer is not known, and laying the answer over it
  // could put the page back to an older finding.  `sentOver` is the record
  // the request was sent over.
  const refetchFinding = findingQuery.refetch;
  const refetchHistory = historyQuery.refetch;
  const warnNotRefreshed = (error: unknown) =>
    toast.warning(formatApiError(error, 'Saved, but the page could not refresh — reload to see the change.'));
  const showAnswer = async (updated: Finding, sentOver: Finding | undefined, appendsHistory: boolean) => {
    const record = queryClient.getQueryState<Finding>(findingKey(projectId, id));
    if (record?.fetchStatus === 'idle' && record.data === sentOver) {
      replaceFinding(updated, false);
      if (!appendsHistory) return;
      const h = await refetchHistory();
      if (h.isError) warnNotRefreshed(h.error);
      return;
    }
    const [f, h] = await Promise.all([refetchFinding(), refetchHistory()]);
    if (f.isError) warnNotRefreshed(f.error);
    else if (h.isError) warnNotRefreshed(h.error);
  };
  const recordAsSent = () => ({ sentOver: queryClient.getQueryData<Finding>(findingKey(projectId, id)) });
  const statusChange = useMutation({
    mutationFn: (v: { status: FindingStatus; summary?: string }) => setFindingStatus(projectId, id, v.status, v.summary),
    onMutate: recordAsSent,
    onSuccess: (updated, _v, sent) => showAnswer(updated, sent.sentOver, true), // status + history trail together
    onError: (err) => toast.error(formatApiError(err, 'Failed to update status.')),
  });
  const severityChange = useMutation({
    mutationFn: (severity: FindingSeverity) => updateFinding(projectId, id, { severity }),
    onMutate: recordAsSent,
    onSuccess: async (updated, severity, sent) => {
      await showAnswer(updated, sent.sentOver, false); // headline badge; severity is not on the history trail
      toast.success(`Severity reclassified to ${SEVERITY_LABEL[severity]}.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update severity.')),
  });
  const refreshing: 'status' | 'severity' | null = statusChange.isPending
    ? 'status' : severityChange.isPending ? 'severity' : null;

  const applyStatus = (status: FindingStatus, summary?: string) => {
    if (finding) statusChange.mutate({ status, summary });
  };

  const handleStatus = (status: FindingStatus) => {
    if (!finding || status === finding.status) return;
    // Terminal dispositions get the same "why" prompt the /findings list
    // shows — the summary is the audit rationale on the history trail.
    // Defer the open a tick so the modal doesn't race the Radix Select's
    // dismiss layer (which can otherwise leave body pointer-events:none).
    if (TERMINAL_STATUSES.has(status)) {
      setSummaryText('');
      setTimeout(() => setSummaryPrompt({ status }), 0);
    } else {
      void applyStatus(status);
    }
  };

  const handleSeverity = (severity: FindingSeverity) => {
    if (!finding || severity === finding.severity) return;
    severityChange.mutate(severity);
  };

  const ownerChange = useMutation({
    mutationFn: (ownerId: number | null) => updateFinding(projectId, id, { owner_id: ownerId }),
    onSuccess: (updated, ownerId) => {
      replaceFinding(updated, false);
      toast.success(ownerId == null ? 'Owner cleared.' : `Owner set to ${updated.owner_name ?? 'user'}.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update owner.')),
  });
  const handleOwner = (ownerId: number | null) => {
    if (!finding || ownerId === finding.owner_id) return;
    ownerChange.mutate(ownerId);
  };

  // v5.256.0 — authored content: the finding's author (or a project admin)
  // renames or deletes it; the server decides and says so in `can_modify`.
  // Severity, owner and status above stay open to any analyst (triage).
  const canModify = canManage && !!finding?.can_modify;
  // v5.260.0 — images are opt-in for the client report: the uploader marks
  // theirs, a project admin any (the server enforces the same rule).
  // The finding's images, loaded once for the page: each image's caption and
  // where the report text places it (the comment thread's rows), the editor's
  // "Insert image" and the images shown in the text.
  const findingImages = useFindingImages(finding?.id ?? null);
  // 5.334.0 — one read of the pending proposals; each is shown where it
  // applies (the summary at the top, report text, the endpoint rows).
  const proposals = useFindingProposals(finding?.id ?? null);
  const endpointIds = useMemo(() => new Set((finding?.hosts ?? []).map((h) => h.id)), [finding?.hosts]);
  const reloadImages = findingImages.reload;
  const imagesById = useMemo(
    () => new Map(findingImages.images.map((img) => [img.id, img])), [findingImages.images],
  );
  const reportMarking = useMemo(() => ({
    canMark: (att: NoteAttachment) =>
      canManage && (!!finding?.viewer_is_project_admin || (user?.id != null && att.uploaded_by_id === user.id)),
    placement: (att: NoteAttachment) => imagesById.get(att.id),
    captionMax: findingImages.captionMax,
    // The thread's thumbnails come from the page's one cache of image bytes.
    thumbnails: findingImages.thumbnails,
  }), [
    canManage, finding?.viewer_is_project_admin, user?.id, imagesById, findingImages.captionMax,
    findingImages.thumbnails,
  ]);
  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const rename = useMutation({
    mutationFn: (title: string) => updateFinding(projectId, id, { title }),
    onSuccess: (updated) => {
      replaceFinding(updated, false);
      setTitleDraft(null);
      toast.success('Finding renamed.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to rename the finding.')),
  });
  const titleSaving = rename.isPending;

  const saveTitle = () => {
    if (!finding || titleDraft === null) return;
    const title = titleDraft.trim();
    if (!title) return;
    if (title === finding.title) { setTitleDraft(null); return; }
    rename.mutate(title);
  };

  const remove = useMutation({
    mutationFn: () => deleteFinding(projectId, id),
    onSuccess: () => {
      toast.success('Finding deleted.');
      navigate(returnTo);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to delete the finding.')),
  });

  // Undo of a removal: the row goes back with its name and its own state.
  const restoreEndpoint = useMutation({
    mutationFn: (v: { row: FindingHostInfo; label: string }) => addFindingHosts(projectId, id, [], [
      { host_id: v.row.host_id, name_id: v.row.name_id ?? null, host_status: v.row.host_status },
    ]),
    onSuccess: (reverted, v) => {
      replaceFinding(reverted);
      toast.success(`Put ${v.label} back on the finding.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to undo the removal.')),
  });
  const removeEndpoint = useMutation({
    mutationFn: (v: { row: FindingHostInfo; label: string }) => removeFindingEndpoint(projectId, id, v.row.id),
    onSuccess: (updated, v) => {
      replaceFinding(updated, false);
      toast.success(`Removed ${v.label} from the finding.`, {
        action: { label: 'Undo', onClick: () => restoreEndpoint.mutate(v) },
      });
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to remove the endpoint.')),
  });

  const handleDelete = async () => {
    if (!finding) return;
    const survives =
      finding.source === 'note'
        ? 'The host note it was promoted from stays.'
        : finding.source === 'scanner'
          ? 'The scanner observations stay, untriaged again.'
          : 'What it was recorded from stays.';
    const ok = await confirm({
      title: 'Delete finding?',
      body: (
        <>
          <p>
            &quot;{finding.title}&quot; is removed with its comments, screenshots and status history,
            and leaves every report. {survives}
          </p>
          <p className="mt-xs">
            If the issue does not apply, set the status to False positive instead — that keeps the record.
          </p>
        </>
      ),
      severity: 'danger',
      confirmLabel: 'Delete finding',
    });
    if (!ok) return;
    remove.mutate();
  };

  // A host may carry several affected-endpoint rows (one per named endpoint).
  // Removal addresses the ROW, so a vhost's siblings survive, and Undo
  // restores that row's name and per-endpoint status, not a bare host.
  const handleRemoveEndpoint = async (row: FindingHostInfo) => {
    if (!finding) return;
    const label = row.fqdn
      ? `${row.fqdn} (${row.ip_address || `Host ${row.host_id}`})`
      : row.ip_address || row.hostname || `Host ${row.host_id}`;
    const ok = await confirm({
      title: row.fqdn ? 'Remove endpoint from finding?' : 'Remove host from finding?',
      body: `"${label}" will be removed from this finding.`,
      resourceName: label,
      severity: 'danger',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    removeEndpoint.mutate({ row, label });
  };

  // The jump bar: one entry per section, in page order.  The bar leaves out
  // any whose section renders nothing, so each is named here unconditionally.
  const commentCount = useFindingCommentCount(id);
  const pendingProposals = proposals.items?.length ?? 0;
  const emptyReportSections = finding ? missingReportText(finding.report_text).length : 0;
  const hostCount = finding?.host_count ?? 0;
  const jumpEntries = useMemo<JumpEntry[]>(() => [
    { id: 'section-proposals', label: 'Proposals', count: pendingProposals > 0 ? pendingProposals.toLocaleString() : null },
    { id: 'section-hosts', label: 'Affected hosts', count: hostCount.toLocaleString() },
    { id: 'section-test-evidence', label: 'Test evidence' },
    { id: 'section-report-text', label: 'Report text', count: emptyReportSections > 0 ? `${emptyReportSections} empty` : null },
    { id: 'section-evidence-note', label: 'Evidence note' },
    { id: 'section-comments', label: 'Comments & evidence', count: commentCount ? commentCount.toLocaleString() : null },
    { id: 'section-history', label: 'Disposition history' },
  ], [pendingProposals, hostCount, emptyReportSections, commentCount]);

  if (!finding && findingQuery.isFetching) return <DetailSkeleton />;
  if (!finding) {
    return (
      <div className="p-md md:p-lg">
        <Button variant="ghost" size="sm" onClick={() => navigate(returnTo)}>
          <ArrowLeft className="size-4" aria-hidden /> Findings
        </Button>
        <p className="mt-md text-destructive">
          {queryErrorText(findingQuery.error, 'Failed to load finding.') || 'Finding not found.'}
        </p>
        <Button variant="outline" size="sm" className="mt-sm" onClick={() => void findingQuery.refetch()}>
          <RefreshCw className="size-4" aria-hidden /> Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="p-md md:p-lg">
      {leaveDialog}
      <Button variant="ghost" size="sm" onClick={backToFindings} className="mb-sm">
        <ArrowLeft className="size-4" aria-hidden /> Findings
      </Button>

      <div className="mb-md flex flex-wrap items-start gap-sm">
        <Badge variant={SEVERITY_VARIANT[finding.severity] as never}>
          {finding.severity[0].toUpperCase() + finding.severity.slice(1)}
        </Badge>
        {titleDraft !== null ? (
          <form
            className="flex min-w-0 flex-1 flex-wrap items-center gap-xs"
            onSubmit={(e) => { e.preventDefault(); void saveTitle(); }}
          >
            <Input
              autoFocus
              value={titleDraft}
              maxLength={500}
              onChange={(e) => setTitleDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Escape') setTitleDraft(null); }}
              aria-label="Finding title"
              className="min-w-0 flex-1"
              disabled={titleSaving}
            />
            <Button type="submit" size="sm" disabled={titleSaving || !titleDraft.trim()}>
              {titleSaving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setTitleDraft(null)} disabled={titleSaving}>
              Cancel
            </Button>
          </form>
        ) : (
          <>
            <h1 className="min-w-0 flex-1 break-words text-page-title font-semibold">{finding.title}</h1>
            {canModify && (
              <div className="flex shrink-0 items-center gap-xs">
                <Button variant="ghost" size="sm" onClick={() => setTitleDraft(finding.title)}>
                  <Pencil className="size-4" aria-hidden /> Rename
                </Button>
                <Button variant="ghost" size="sm" className="text-destructive" onClick={() => void handleDelete()}>
                  <Trash2 className="size-4" aria-hidden /> Delete
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      <div className="mb-md flex flex-wrap items-center gap-md text-metadata">
        <div className="flex items-center gap-xs">
          <span className="text-muted-foreground">Status</span>
          {canManage ? (
            <Select value={finding.status} onValueChange={(v) => handleStatus(v as FindingStatus)}
              disabled={refreshing !== null}>
              <SelectTrigger className="h-7 w-[10rem] text-caption" aria-label="Finding status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Badge variant="muted">{STATUS_LABEL[finding.status]}</Badge>
          )}
          {refreshing === 'status' && <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Saving status" />}
        </div>
        <div className="flex items-center gap-xs">
          <span className="text-muted-foreground">Severity</span>
          {canManage ? (
            <Select value={finding.severity} onValueChange={(v) => handleSeverity(v as FindingSeverity)}
              disabled={refreshing !== null}>
              <SelectTrigger className="h-7 w-[8rem] text-caption" aria-label="Finding severity">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(SEVERITY_LABEL) as FindingSeverity[]).map((s) => (
                  <SelectItem key={s} value={s}>{SEVERITY_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Badge variant="muted">{SEVERITY_LABEL[finding.severity]}</Badge>
          )}
          {refreshing === 'severity' && <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Saving severity" />}
        </div>
        <div className="flex items-center gap-xs">
          <span className="text-muted-foreground">Owner</span>
          {canManage ? (
            <>
              <Select
                value={finding.owner_id != null ? String(finding.owner_id) : 'none'}
                onValueChange={(v) => void handleOwner(v === 'none' ? null : Number(v))}
              >
                <SelectTrigger className="h-7 w-[11rem] text-caption" aria-label="Finding owner">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Unassigned</SelectItem>
                  {/* Keep the current owner selectable even if they've since
                      left the roster, so the control reflects reality. */}
                  {finding.owner_id != null && !members.some((m) => m.user_id === finding.owner_id) && (
                    <SelectItem value={String(finding.owner_id)}>
                      {safeFallback(finding.owner_name, `User ${finding.owner_id}`)}
                    </SelectItem>
                  )}
                  {members.map((m) => (
                    <SelectItem key={m.user_id} value={String(m.user_id)}>{memberLabel(m)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {roster.status === 'error' && <MembersLoadError onRetry={roster.retry} />}
              {user?.id != null && finding.owner_id !== user.id && (
                <Button variant="ghost" size="sm" className="h-7 px-xs text-caption"
                  onClick={() => void handleOwner(user.id)}>
                  Assign to me
                </Button>
              )}
            </>
          ) : (
            <span>{safeFallback(finding.owner_name, 'Unassigned')}</span>
          )}
        </div>
        <span><span className="text-muted-foreground">Source</span> {finding.source}</span>
        {finding.created_by_name && (
          <span className="min-w-0 max-w-[16rem] truncate" title={finding.created_by_name}>
            <span className="text-muted-foreground">Recorded by</span> {finding.created_by_name}
          </span>
        )}
        {evidenceHref && (
          <Link to={evidenceHref} className="inline-flex items-center gap-xxs text-info hover:underline">
            Evidence thread <ExternalLink className="size-3" aria-hidden />
          </Link>
        )}
      </div>

      {/* Where the page's sections are, pinned under the chrome: the hosts
          panel is bounded, so every section is one click away.  No `#id` in
          the address here: `?endpoint=` is this page's deep link, and two
          things must not both scroll the page on load. */}
      <SectionJumpBar entries={jumpEntries} label="Sections of this finding" hash={false} />

      {/* What is waiting for a decision comes first, as a summary that goes
          to each proposal where it applies. */}
      <div id="section-proposals" style={jumpTargetStyle}>
        <FindingProposalsPanel
          proposals={proposals} endpointIds={endpointIds} canDecide={canManage} onDecided={proposalDecided}
        />
      </div>

      {/* Sections over thin rules, and the hosts first: triage starts from
          where the issue is, so the affected hosts sit directly under the
          status row, before the test evidence, the report text and the
          discussion.  The list is a bounded panel (FindingEndpoints), which
          also says how the endpoints stand. */}
      <div id="section-hosts" style={jumpTargetStyle}>
        <PostureSection
          className="mb-md"
          title={<>
            <span>Affected hosts</span>
            <SectionCount>{finding.host_count.toLocaleString()}</SectionCount>
          </>}
          actions={canManage ? (
            <Button variant="outline" size="sm" onClick={() => setAddHostsOpen(true)}>
              <Plus className="size-4" aria-hidden /> Add hosts
            </Button>
          ) : undefined}
        >
          <FindingEndpoints
            key={finding.id}
            finding={finding}
            canManage={canManage}
            onRemove={(row) => void handleRemoveEndpoint(row)}
            focusEndpointId={focusEndpointId}
            proposals={proposals.byEndpoint}
            canDecide={canManage}
            onProposalDecided={proposalDecided}
          />
        </PostureSection>
      </div>

      {/* The proof before the prose: what was run and what came back sits
          directly under the hosts, above the five report-text editors.
          Order: hosts → test evidence → report text → comments → history. */}
      <div id="section-test-evidence" style={jumpTargetStyle}>
        <FindingEvidence findingId={finding.id} />
      </div>

      <div id="section-report-text" style={jumpTargetStyle}>
      {findingImages.error && (
        // The images' list feeds the report text (placed images), the editor's
        // picker and the comment thread's rows: a failed read is said here,
        // once, with a way to read it again — not left as images that look
        // like they do not exist (S4).
        <p role="alert" className="flex min-w-0 flex-wrap items-center gap-xs text-caption text-destructive"
          data-testid="finding-images-error">
          <span className="min-w-0 break-words">
            {findingImages.error}{' '}
            {findingImages.listStatus === 'failed'
              ? 'Images placed in the report text cannot be shown, and captions and “In report” placement are not listed.'
              : 'What is shown is from the last successful read.'}
          </span>
          <Button variant="outline" size="sm" className="h-6 px-xs" onClick={reloadImages} disabled={findingImages.loading}>
            {findingImages.loading && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Retry
          </Button>
        </p>
      )}

      <FindingReportTextCard
        finding={finding} canEdit={canModify} canPropose={canManage}
        images={findingImages}
        drafts={proposals.textByField}
        canDecide={canManage}
        onProposalDecided={proposalDecided}
        onDirtyChange={noteReportTextDirty}
        agentAction={(
          <AgentTaskButton
            variant="ghost"
            label="Work on this with your agent"
            title="Give your agent session this finding to review and complete — its changes arrive as proposals, shown in the sections they change"
            instruction={agentInstruction.reviewFinding(finding.id, missingReportText(finding.report_text))}
          />
        )}
        startEditing={searchParams.get('edit') === 'report-text'}
      />
      </div>

      <div id="section-evidence-note" style={jumpTargetStyle}>
      {evidenceError && (
        <PostureSection className="mb-md" title={<span>Evidence note</span>}>
          <p className="text-caption text-destructive">{evidenceError}</p>
          {evidenceHref && (
            <Link to={evidenceHref} className="text-caption text-info hover:underline">Open on the host</Link>
          )}
        </PostureSection>
      )}
      {evidenceThread.length > 0 && (
        // v5.264.0 — the source note's thread as a conversation, not a card.
        <section className="mb-md min-w-0" aria-label="Evidence note">
          <div className="border-b border-border pb-xs">
            <h2 className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">Evidence note</h2>
          </div>
          <div className="space-y-md pt-sm">
            {evidenceThread.map((note) => {
              const parent = note.parent_id != null && note.parent_id !== evidenceThread[0]?.id
                ? evidenceThread.find((n) => n.id === note.parent_id) : undefined;
              return (
                <MessageBubble
                  key={note.id}
                  mine={user?.id != null && note.author_id === user.id}
                  author={note.author_name || 'Unknown analyst'}
                  actorType={note.actor_type}
                  createdAt={note.created_at}
                  replyingTo={parent ? { author: parent.author_name || 'Unknown analyst', excerpt: parent.body ?? '' } : null}
                >
                  <p className="whitespace-pre-wrap break-words text-body">{note.body}</p>
                  {note.attachments && note.attachments.length > 0 && evidenceHostId && (
                    <NoteAttachments
                      hostId={evidenceHostId}
                      noteId={note.id}
                      attachments={note.attachments}
                      canManage={false}
                      reportMarking={reportMarking}
                    />
                  )}
                </MessageBubble>
              );
            })}
          </div>
        </section>
      )}
      </div>

      <div id="section-comments" style={jumpTargetStyle}>
        <FindingCommentThread
          findingId={finding.id} canManage={canManage} reportMarking={reportMarking}
        />
      </div>

      <div id="section-history" style={jumpTargetStyle}>
      <PostureSection title={<span>Disposition history</span>}>
          {historyLoading && history.length === 0 ? (
            <div className="flex items-center gap-xs text-caption text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Loading history…
            </div>
          ) : historyError ? (
            <div className="flex flex-wrap items-center gap-sm">
              <p className="text-caption text-destructive">History unavailable — {historyError}</p>
              <Button variant="outline" size="sm" onClick={() => void historyQuery.refetch()} disabled={historyLoading}>
                <RefreshCw className="size-4" aria-hidden /> Retry
              </Button>
            </div>
          ) : history.length === 0 ? (
            // UX review 2026-09-24: a Confirmed finding read "No status
            // changes recorded yet" — say how it got its status instead.
            <p className="text-caption text-muted-foreground">{initialStatusLine(finding)}</p>
          ) : (
            <ul className="flex flex-col gap-sm">
              {history.map((r) => (
                <li key={r.id} className="border-l-2 border-border pl-sm">
                  {/* An endpoint change is recorded on the finding's history
                      with the finding's status unchanged: "Confirmed →
                      Confirmed" said nothing — the summary names the change. */}
                  {r.from_status !== r.to_status || !r.summary ? (
                    <div className="text-metadata">
                      <span className="text-muted-foreground">{histLabel(r.from_status)}</span>
                      {' → '}<span className="font-medium">{histLabel(r.to_status)}</span>
                    </div>
                  ) : null}
                  <div className="text-caption text-muted-foreground">
                    {safeFallback(r.changed_by_name, 'Unknown')} · {formatTimestamp(r.created_at)}
                  </div>
                  {r.summary && (
                    <p className={cn('mt-xxs whitespace-pre-wrap', r.from_status === r.to_status ? 'text-metadata' : 'text-caption')}>
                      {r.summary}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
      </PostureSection>
      </div>

      {/* Terminal-disposition "why" prompt — same policy as the /findings
          list; the summary lands on the finding's disposition history. */}
      <Dialog open={summaryPrompt !== null} onOpenChange={(v) => { if (!v) setSummaryPrompt(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark {summaryPrompt ? STATUS_LABEL[summaryPrompt.status] : ''}</DialogTitle>
            <DialogDescription>
              Why? The reason is kept on the finding's history and carried into the
              report. You can save without one.
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
            <Button variant="outline" onClick={() => setSummaryPrompt(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                const p = summaryPrompt;
                setSummaryPrompt(null);
                if (p) void applyStatus(p.status, summaryText.trim() || undefined);
              }}
            >
              {summaryText.trim() ? 'Save' : 'Save without a reason'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {confirmDialog}
      {canManage && (
        <AddFindingHostsDialog
          open={addHostsOpen}
          onOpenChange={setAddHostsOpen}
          finding={finding}
        />
      )}
    </div>
  );
};

export default FindingDetail;
