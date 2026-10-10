/**
 * HostInspector — the data-bearing body of the host detail surface.
 *
 * Renders host overview, tests, vulnerabilities, notes (threaded),
 * data conflicts (when toggled) and port details.  Owns its reads
 * (`getHost` — the host, its notes and the caller's follow state —
 * `getHostConflicts`, `getHostFollowers`) and the per-action mutations
 * (follow, note CRUD, promote / dismiss).  The other sections read their own
 * data; what a write changes elsewhere on the page is invalidated by the
 * read's name, never passed down as a refresh counter.
 *
 * Used in two contexts:
 *  - Standalone page (`pages/HostDetail.tsx`): the page renders
 *    navigation chrome (back / prev / next) above this inspector.
 *  - SideSheet on the Hosts list page: the SideSheet renders its own
 *    header (close + "Open standalone" link) and embeds this
 *    component in the body.
 *
 * The inspector deliberately renders its own h1 with the host IP so
 * that the same shape appears in both contexts.  Page chrome and
 * sheet header therefore stay minimal.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { SEVERITY_RANK, SEVERITY_BADGE_VARIANT, SEVERITY_HSL, type Severity } from '../utils/severity';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Computer,
  ExternalLink,
  Eye,
  Loader2,
  MessageSquare,
  NotebookPen,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
} from 'lucide-react';
import {
  getHost,
  getHostConflicts,
  followHost,
  unfollowHost,
  createAnnotation,
  uploadNoteAttachment,
  updateAnnotation,
  deleteAnnotation,
  promoteVulnerability,
  previewPromoteVulnerability,
  recordHostView,
  getHostFollowers,
  PromotedEvidence,
} from '../services/api';
import type {
  Host,
  HostConflict,
  ConflictHistoryEntry,
  FollowStatus,
  Annotation,
  NoteAttachment,
  NoteType,
  HostFollowerEntry,
  FindingHostStatus,
  FindingStatus,
  HostVulnerability,
  ReviewConclusion,
} from '../services/api';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { buildSameVulnQuery, buildExploitOnPortsQuery } from '../utils/vulnQuery';
import { getHostWebLinks, HostWebLink } from '../utils/webLinks';
import { getConnectionHelpers, ConnectionHelper } from '../utils/connectionHelpers';
import NseScriptsCard from './NseScriptsCard';
import HostFindingsCard from './HostFindingsCard';
import HostNamesCard from './HostNamesCard';
import { TimeAgo } from './TimeAgo';
import { AssigneeControl, TagControl } from './host-inspector/HostWorkControls';
import { stickyBelowChrome } from '../utils/uiStyles';
import { NoteThread } from './host-inspector/NoteThread';
import { NoteComposer } from './host-inspector/NoteComposer';
import { InspectorSection, jumpToInspectorSection, openInspectorSection } from './host-inspector/InspectorSection';
import { previewThreads, rootNoteId } from '../utils/notePreview';
import VulnerabilityGroup from './host-inspector/VulnerabilityGroup';
import ProductObservationGroup from './host-inspector/ProductObservationGroup';
import ProvenanceCard, { provenanceExceedsSummary, attributionIsStale } from './host-inspector/ProvenanceCard';
import HostEvidenceSection, { hostEvidenceKey } from './host-inspector/HostEvidenceSection';
import { HostTestsSection } from './host-inspector/HostTestsSection';
import { HostTestsProvider, useHostTestsController } from './host-inspector/hostTestsController';
import ScopeMembershipCard from './host-inspector/ScopeMembershipCard';
import PortDetailsCard from './host-inspector/PortDetailsCard';
import { changesSinceReview, freshnessFacts } from '../utils/evidenceFreshness';
import DiscoveryTimelineCard from './host-inspector/DiscoveryTimelineCard';
import HostConflictsPanel from './host-inspector/HostConflictsPanel';
import { groupByProduct, groupVulnerabilities } from '../utils/vulnGrouping';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText, useLastSettled } from '../lib/query';
import { useProjectRole } from '../hooks/useProjectRole';
import { promotedResultMessage, testNeedsWork } from '../utils/hostTests';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import { cn } from '../utils/cn';
import { announceMentionOutcome } from '../utils/mentions';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { DetailSkeleton } from './PageSkeleton';
import { useConfirm } from '../hooks/useConfirm';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { InfoTip } from './ui/info-tip';
import { Label } from './ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Textarea } from './ui/textarea';
import {
  ENDPOINT_STATUS_LABEL, STATUS_LABEL as FINDING_STATUS_LABEL,
} from '../utils/findingStatus';

// §9 review-completion outcomes — what "reviewed" actually concluded, recorded
// when a reviewer marks a host done. Order = how they're offered in the dialog.
const REVIEW_CONCLUSION_ORDER: ReviewConclusion[] = [
  'no_issue', 'finding_created', 'needs_evidence', 'out_of_scope', 'duplicate',
];
const REVIEW_CONCLUSION_LABEL: Record<ReviewConclusion, string> = {
  no_issue: 'No actionable issue',
  finding_created: 'Finding created',
  needs_evidence: 'Needs more evidence',
  out_of_scope: 'Out of scope',
  duplicate: 'Duplicate asset',
};

// One line per issue since v5.240.0, so the preview can afford most hosts'
// whole list (the largest host here carries 26).
/** Where a host's display name came from (Host.hostname_source). */
const HOSTNAME_SOURCE_LABEL: Record<string, string> = {
  operator: 'set by an operator',
  ptr: 'from a reverse-DNS (PTR) record',
  scanner: 'reported by a scanner',
  forward: 'from a forward DNS record',
};

const VULNERABILITY_PREVIEW_LIMIT = 25;
const NOTE_THREAD_PREVIEW_LIMIT = 3;

const VULNERABILITY_SEVERITY_ORDER = SEVERITY_RANK;

// Thin null-tolerant wrapper over the canonical severity→badge-variant map.
const severityBadgeVariant = (severity: string | null | undefined): string =>
  SEVERITY_BADGE_VARIANT[(severity ?? '').toLowerCase() as Severity] ?? 'outline';

const FOLLOW_STATUS_META: Record<
  FollowStatus,
  { label: string; description: string; badgeVariant: 'info' | 'warning' | 'success' }
> = {
  watching: {
    label: 'Watching',
    description: 'Track this host for future review or to share with teammates.',
    badgeVariant: 'info',
  },
  in_review: {
    label: 'In Review',
    description: 'You are actively investigating this host and its findings.',
    badgeVariant: 'warning',
  },
  reviewed: {
    label: 'Reviewed',
    description: 'Investigation completed — leave a note with outcomes if relevant.',
    badgeVariant: 'success',
  },
};




// One empty list each, so "nothing loaded" is the same value on every render.
const NO_NOTES: Annotation[] = [];
const NO_CONFLICTS: HostConflict[] = [];
const NO_CONFLICT_HISTORY: ConflictHistoryEntry[] = [];
const NO_FOLLOWERS: HostFollowerEntry[] = [];

interface PendingImage {
  file: File;
  url: string;
  error?: string;
  /** The saved note this file belongs to once its upload failed — retry
   *  targets THIS id, never a later note's (a queue-wide id was wrong when
   *  two notes in a row had failures). */
  noteId?: number;
}

export interface HostInspectorProps {
  hostId: number;
  /**
   * Visual density of the IP heading.  `page` (default) uses the full
   * `text-page-title`; `sheet` drops to `text-section-title` for use
   * inside a SideSheet whose own header is more compact.
   */
  density?: 'page' | 'sheet';
  /**
   * Called with the host when it is loaded, and again whenever what the
   * inspector holds of it changes (a re-read, a note, the follow state).
   * Useful for the parent (e.g. SideSheet header) to show host metadata
   * outside the body.
   */
  onHostLoaded?: (host: Host) => void;
  /**
   * Called when the user changes this host's follow status from inside the
   * inspector, so a parent list (e.g. the /hosts table) can update the row's
   * badge in place instead of waiting for a page reload. Passes the new
   * follow record, or null when the host was unfollowed.
   */
  onFollowChange?: (hostId: number, follow: Host['follow']) => void;
  /**
   * "Find other hosts with this vulnerability" — pivot a vuln row to the
   * filtered /hosts inventory. When the inspector is a SideSheet overlay on
   * the Hosts page the parent passes this to close the sheet AND apply the
   * query; standalone mounts (HostDetail) omit it and the inspector navigates
   * itself.
   */
  onQueryHosts?: (query: string) => void;
  /**
   * Fires whenever the inspector gains or loses unsaved work: note text,
   * pending/failed screenshots, a reply being written, or an edited test
   * summary.  The Hosts queue and the standalone page use it to confirm
   * before navigation discards a draft (UX review C1).
   */
  onDirtyChange?: (dirty: boolean) => void;
  /**
   * When the inspector sits in a queue: step to the next host nobody has
   * started.  Its presence adds "Save and next unreviewed" to the review
   * conclusion dialog (v5.237.0).
   */
  onNextUnreviewed?: () => void;
}

/**
 * One host's inspector.  It is keyed by the host, so stepping to another host
 * (the Hosts queue's Prev / Next) starts a new one: nothing loaded, typed or
 * in flight for the host that was left exists in the one now shown, and a
 * host that cannot be loaded shows its error, never the previous host.
 */
export const HostInspector: React.FC<HostInspectorProps> = (props) => (
  <HostInspectorBody key={props.hostId} {...props} />
);

const HostInspectorBody: React.FC<HostInspectorProps> = ({
  hostId,
  density = 'page',
  onHostLoaded,
  onFollowChange,
  onQueryHosts,
  onDirtyChange,
  onNextUnreviewed,
}) => {
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  // The PROJECT role (analyst+), not the account role every member has (R32).
  const { canWrite: canManageEntries } = useProjectRole();

  // Pivot a vuln to the host inventory filtered to every host with the same
  // vulnerability. When the parent supplies onQueryHosts (the Hosts SideSheet)
  // it closes the sheet + applies the query; otherwise (standalone HostDetail)
  // we navigate to the filtered /hosts ourselves.
  const handleQueryHosts = useCallback(
    (vuln: HostVulnerability) => {
      const q = buildSameVulnQuery(vuln);
      if (!q) return;
      if (onQueryHosts) onQueryHosts(q);
      else navigate(buildHostsUrl({ q }));
    },
    [onQueryHosts, navigate],
  );

  // Pivot a vuln GROUP to the host inventory filtered to every host with an
  // exploitable finding on ANY of the group's exploitable ports (same-row
  // correlation, not "port open anywhere AND exploit anywhere"). Takes all the
  // group's exploit ports, not one representative row's — a plugin exploitable
  // on 80/443/8080 pivots on all three. Same close-sheet-or-navigate contract
  // as handleQueryHosts.
  const handleQueryExploitPort = useCallback(
    (ports: number[]) => {
      const q = buildExploitOnPortsQuery(ports);
      if (!q) return;
      if (onQueryHosts) onQueryHosts(q);
      else navigate(buildHostsUrl({ q }));
    },
    [onQueryHosts, navigate],
  );
  const projectId = useProjectId();
  const queryClient = useQueryClient();
  // THE read of this host.  Its notes and the caller's follow state come with
  // it, so they are read from it — never copied out; a write puts the server's
  // answer back in (`putHost`) or says the host is out of date (invalidating
  // `['getHost', projectId, hostId]`, which the assignee / tag controls, a
  // note's images and a test's result do from where they are written).  A
  // re-read that fails keeps what is on screen.
  //
  // v5.215.0 — informational rows are left out of the detail payload until
  // asked ("N informational hidden · show").  5.364.0 — asking for them is
  // part of WHAT IS READ (`includeInfo`, in the key), so every later re-read
  // of the host brings them again.  They used to be laid over the cached host
  // by a one-off request, and the next re-read came back without them.
  const [showInformational, setShowInformational] = useState(false);
  const hostKey = useMemo(
    () => ['getHost', projectId, hostId, { includeInfo: showInformational }] as const,
    [projectId, hostId, showInformational],
  );
  const hostQuery = useQuery({
    queryKey: hostKey,
    queryFn: ({ signal }) => getHost(projectId, hostId, showInformational ? { includeInfo: true, signal } : { signal }),
  });
  // The host stays on screen while the wider read is made, and when it fails
  // (this inspector is one host's for its whole life: it is keyed by the host).
  const host = useLastSettled(hostQuery.data) ?? null;
  const informationalPending = showInformational && hostQuery.data === undefined;
  const loadingInformational = informationalPending && hostQuery.isFetching;
  const informationalFailed = informationalPending && hostQuery.isError && !hostQuery.isFetching;
  const putHost = useCallback((update: (previous: Host) => Host) => {
    queryClient.setQueryData<Host>(hostKey, (previous) => (previous ? update(previous) : previous));
  }, [queryClient, hostKey]);
  const notes = host?.notes ?? NO_NOTES;
  const followStatus: FollowStatus | '' = host?.follow?.status ?? '';

  const conflictsQuery = useQuery({
    queryKey: ['getHostConflicts', projectId, hostId],
    queryFn: ({ signal }) => getHostConflicts(projectId, hostId, signal),
  });
  const conflicts = conflictsQuery.data?.confidence || NO_CONFLICTS;
  const conflictHistory = conflictsQuery.data?.conflict_history || NO_CONFLICT_HISTORY;
  // Canonical conflict count from the API (same definition as the Hosts-list
  // badge).  The old "N conflicts" derived from `conflicts.length` (per-field
  // confidence records, host + port) — a different number that disagreed with
  // the list badge.
  const conflictCount = conflictsQuery.data?.conflict_count ?? 0;
  // getHostConflicts already swallows 404 (older deployments), so a failure
  // here is a real one — said, instead of letting an empty list read as "no
  // conflicts" (a data-quality false negative).
  const conflictsError = conflictsQuery.isError;
  const [showConflicts, setShowConflicts] = useState(false);

  const followersQuery = useQuery({
    queryKey: ['getHostFollowers', projectId, hostId],
    queryFn: ({ signal }) => getHostFollowers(projectId, hostId, signal),
  });
  const followersError = followersQuery.isError;
  const otherFollowers = (!followersError && followersQuery.data?.followers) || NO_FOLLOWERS;

  // Opening a host is recorded (the "viewed" fact), and again on Retry.
  const { mutate: recordView } = useMutation({ mutationFn: (id: number) => recordHostView(projectId, id) });
  useEffect(() => { recordView(hostId); }, [recordView, hostId]);

  // The skeleton stands for "nothing to show yet": the first read, and a
  // Retry after it failed.  A re-read behind a host that is on screen does
  // not bring it back.
  const loading = !host && (hostQuery.isPending || hostQuery.isFetching);
  const fetchError = !hostQuery.isError
    ? null
    : (() => {
      const status = asAxiosError(hostQuery.error).response?.status;
      if (status === 404) return 'Host not found';
      if (status === 401 || status === 403) return 'You do not have permission to view this host';
      return 'Failed to load host details. The server may be unavailable.';
    })();
  const retry = () => {
    void hostQuery.refetch();
    void conflictsQuery.refetch();
    void followersQuery.refetch();
    recordView(hostId);
  };

  // §9 review-completion dialog (opened by "Mark reviewed").
  const [reviewCompletionOpen, setReviewCompletionOpen] = useState(false);
  const [reviewConclusion, setReviewConclusion] = useState<ReviewConclusion>('no_issue');
  const [reviewSummaryText, setReviewSummaryText] = useState('');
  // v2.43.0 — MONO-2: thread grouping for <NoteThread>.  MUST live above
  // the conditional early returns (loading / !host) so the hook count is
  // stable across the first-paint-with-skeleton → data-loaded transition.
  // Pre-v2.43.3 it sat below the early returns and triggered React error
  // #310 ("Rendered more hooks than during the previous render") the
  // moment the loading skeleton flipped to real content.
  const noteThreadGroups = React.useMemo(() => {
    const topLevel = notes.filter((n) => !n.parent_id);
    const repliesByParent: Record<number, Annotation[]> = {};
    notes.filter((n) => n.parent_id).forEach((n) => {
      const pid = n.parent_id!;
      if (!repliesByParent[pid]) repliesByParent[pid] = [];
      repliesByParent[pid].push(n);
    });
    Object.values(repliesByParent).forEach((arr) =>
      arr.sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      ),
    );
    return { topLevel, repliesByParent };
  }, [notes]);
  // Hoisted above the loading / !host early returns for the same reason as
  // noteThreadGroups: a useMemo below them is skipped on the first
  // (skeleton) render and runs once data lands, changing the hook count and
  // throwing React #310. Null-guarded so it's safe before `host` resolves.
  const connectionHelpersByPort = React.useMemo(() => {
    const map = new Map<number, ConnectionHelper[]>();
    if (!host) return map;
    host.ports
      .filter((port) => port.state === 'open')
      .forEach((port) => {
        map.set(port.id, getConnectionHelpers(host.ip_address, port, host.hostname));
      });
    return map;
  }, [host]);
  const [noteBody, setNoteBody] = useState('');
  // Images pasted/attached into the composer, uploaded to the note on save.
  // `error` marks a file whose upload failed after the note itself was
  // created; it stays here (bound to its own `noteId`) until the user
  // retries or removes it — never silently dropped (UX review C3).
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [replyTo, setReplyTo] = useState<{ id: number; author: string } | null>(null);
  const [replyBody, setReplyBody] = useState('');
  const [noteError, setNoteError] = useState<string | null>(null);
  // The thread shows its newest threads until asked for the rest (v5.240.0).
  const [showAllNotes, setShowAllNotes] = useState(false);
  // The host's tests, held once: the Weaknesses rows and the Tests section
  // both read them, and the result panel is rendered once (5.322.0).  What a
  // result or a finding from one changes elsewhere on this page (the findings,
  // the other evidence, the host's own row) the controller says itself.
  const onTestFindingCreated = useCallback((findingId: number, made?: PromotedEvidence) => {
    // The test leaves the to-do list with its finding made, so the way to the
    // write-up is offered here rather than on a row that is no longer shown.
    // From the response: a result that joined a concluded finding did not
    // re-status it, and the toast must not say it did.
    toast.success(promotedResultMessage(made ?? { finding_id: findingId }), {
      autoHideMs: 8000,
      action: { label: 'Write it up', onClick: () => navigate(`/findings/${findingId}?edit=report-text`) },
    });
  }, [toast, navigate]);
  const { controller: hostTests, element: hostTestsElement } = useHostTestsController({
    hostId,
    canEdit: canManageEntries,
    userId: user?.id,
    onFindingCreated: onTestFindingCreated,
  });
  const testsToDo = (hostTests.tests ?? []).filter(testNeedsWork).length;
  // Note-details editor: a thread's type and pin.
  const [detailsNote, setDetailsNote] = useState<Annotation | null>(null);
  const [detailsType, setDetailsType] = useState<string>('none');
  const NOTE_TYPES = ['observation', 'question', 'decision', 'handoff'] as const;
  const [detailsPinned, setDetailsPinned] = useState(false);

  const openNoteDetails = (note: Annotation) => {
    setDetailsNote(note);
    setDetailsType(note.note_type || 'none');
    setDetailsPinned(!!note.pinned);
  };

  const noteDetails = useMutation({
    mutationFn: ({ note, type, pinned }: { note: Annotation; type: string; pinned: boolean }) => {
      // The type goes only when it changed: a thread labelled before 5.326.0
      // may carry "finding" or "action", which can be kept but not chosen.
      const typeChanged = type !== (note.note_type || 'none');
      return updateAnnotation(projectId, hostId, note.id, {
        ...(typeChanged ? { note_type: type === 'none' ? null : (type as NoteType) } : {}),
        pinned,
      });
    },
    onSuccess: (updated) => {
      putHost((previous) => ({
        ...previous, notes: (previous.notes ?? []).map((n) => (n.id === updated.id ? updated : n)),
      }));
      toast.success('Note details updated.');
      setDetailsNote(null);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update note details.')),
  });
  const detailsSaving = noteDetails.isPending;
  const handleSaveNoteDetails = () => {
    if (!detailsNote) return;
    noteDetails.mutate({ note: detailsNote, type: detailsType, pinned: detailsPinned });
  };

  // Promote / dismiss a scanner vulnerability as a finding (status 'confirmed'
  // promotes; a terminal status dismisses). Idempotent server-side.
  // §11 — triage a scanner vuln through a confirm step that previews the
  // cross-host blast radius first (promotion attaches EVERY project host
  // sharing the plugin_id — an icon-click used to do that silently).
  const [triageVuln, setTriageVuln] = useState<
    { id: number; title: string; intent: 'confirmed' | 'false_positive' } | null
  >(null);
  const [triageReason, setTriageReason] = useState('');
  // v5.238.0 — how far a false-positive dismissal reaches.  It is made in ONE
  // host's inspector about that host's observation, so it defaults to this
  // host; marking the issue a false positive everywhere is an explicit choice.
  const [triageScope, setTriageScope] = useState<'host' | 'issue'>('host');

  // The blast radius, read whenever a triage opens (and again on Retry).
  // The action reaches hosts other than this one, so without the preview the
  // dialog does not offer it: confirm stays disabled until the set is known.
  // A failed read leaves it unknown: the dialog offers Retry, not proceed.
  const triageVulnId = triageVuln?.id;
  const triagePreviewQuery = useQuery({
    queryKey: ['previewPromoteVulnerability', projectId, triageVulnId],
    queryFn: ({ signal }) => previewPromoteVulnerability(projectId, triageVulnId as number, signal),
    enabled: triageVulnId != null,
  });
  const triagePreview = (triageVulnId != null && triagePreviewQuery.data) || null;
  const triagePreviewLoading = triageVulnId != null && triagePreviewQuery.isFetching;

  type Triage = { vulnId: number; intent: 'confirmed' | 'false_positive'; scope: 'host' | 'issue'; reason: string };
  const triage = useMutation({
    mutationFn: ({ vulnId, intent, scope, reason }: Triage) => promoteVulnerability(projectId, vulnId, {
      status: intent,
      summary: reason || undefined,
      // ALWAYS sent (v5.245.0): what the dialog showed is what the server
      // does, whatever its default — the API's default for a promotion is
      // still the whole issue, the dialog's is this host.
      scope,
    }),
    // The toast names the finding and is true wherever the reader is by then.
    onSuccess: (finding, { vulnId, intent, scope }) => {
      const hostOnly = intent === 'false_positive' && scope === 'host';
      // Scanner findings span every host with the same plugin — report it.
      const span = finding.host_count > 1 ? ` across ${finding.host_count} hosts` : '';
      toast.success(
        intent === 'confirmed'
          ? (scope === 'host'
            ? `Promoted to finding for this host: ${finding.title}`
            : `Promoted to finding${span}: ${finding.title}`)
          : hostOnly
            ? `Dismissed as false positive on this host only: ${finding.title}`
            : `Dismissed as false positive${span}: ${finding.title}`,
        {
          autoHideMs: 8000,
          // A promotion leads to the write-up; a dismissal has none to write.
          action: intent === 'confirmed'
            ? { label: 'Write it up', onClick: () => navigate(`/findings/${finding.id}?edit=report-text`) }
            : { label: 'Open finding', onClick: () => navigate(`/findings/${finding.id}`) },
        },
      );
      // The row says it at once, in the server's own words: the finding it
      // answered with, and this host's row on it.  (5.364.0 — it was two maps
      // of "done in this session" that every row was checked against, which
      // knew nothing of the finding's status: an issue-wide false positive
      // read "Promoted → finding" until the page was reloaded.)
      const here = (finding.hosts ?? []).find((h) => h.host_id === hostId);
      putHost((previous) => ({
        ...previous,
        vulnerabilities: previous.vulnerabilities?.map((row) => (row.id !== vulnId ? row : {
          ...row,
          finding_id: finding.id,
          finding_status: finding.status,
          finding_on_this_host: true,
          finding_endpoint_status: here?.host_status ?? (hostOnly ? 'false_positive' : row.finding_endpoint_status),
        })),
      }));
      // …then the host is read again: the issue's OTHER rows on this host
      // are covered by the same finding now.
      void queryClient.invalidateQueries({ queryKey: ['getHost', projectId, hostId] });
      // This host has a finding it did not (or one changed); the promotion
      // took the issue's test results onto the finding, so the tests and the
      // other evidence are out of date too.
      void queryClient.invalidateQueries({ queryKey: ['listFindings'] });
      void queryClient.invalidateQueries({ queryKey: ['listHostTests'] });
      void queryClient.invalidateQueries({ queryKey: hostEvidenceKey(projectId, hostId) });
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update vulnerability.')),
  });
  const vulnActionId = triage.isPending ? triage.variables.vulnId : null;
  const handlePromoteVuln = () => {
    if (!triageVuln) return;
    const { id: vulnId, intent } = triageVuln;
    const scope = triageScope;
    // The rest is this panel's own state: it is set only while the reader is
    // still on this host.
    triage.mutate({ vulnId, intent, scope, reason: triageReason.trim() }, {
      onSuccess: () => {
        setTriageVuln(null);
        setTriageReason('');
      },
    });
  };
  const openTriage = (vulnId: number, title: string, intent: 'confirmed' | 'false_positive') => {
    setTriageReason('');
    setTriageScope('host');
    setTriageVuln({ id: vulnId, title, intent });
  };

  const [showAllVulnerabilities, setShowAllVulnerabilities] = useState(false);
  // Per-vuln expand state for the (often long) description writeup.
  const [expandedVulnIds, setExpandedVulnIds] = useState<Set<number>>(new Set());
  const toggleVulnDescription = (id: number) =>
    setExpandedVulnIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const [confirmEl, confirm] = useConfirm();

  // This inspector is one host's for its whole life (it is keyed by the host).
  // What a write that answers after the reader stepped on must not do is
  // speak about "this host" over the next one: those toasts are given to
  // `mutate()` itself, which answers only while the inspector is on screen.

  const onDirtyChangeRef = React.useRef(onDirtyChange);
  useEffect(() => {
    onDirtyChangeRef.current = onDirtyChange;
  }, [onDirtyChange]);
  const [findingsDraftDirty, setFindingsDraftDirty] = useState(false);
  const composerDirty = noteBody.trim().length > 0
    || pendingImages.length > 0
    || replyBody.trim().length > 0
    || findingsDraftDirty;
  useEffect(() => {
    onDirtyChangeRef.current?.(composerDirty);
  }, [composerDirty]);
  const onHostLoadedRef = React.useRef(onHostLoaded);
  onHostLoadedRef.current = onHostLoaded;
  useEffect(() => {
    if (host) onHostLoadedRef.current?.(host);
  }, [host]);

  // Deep-link to an exact note: when the URL carries #note-<id> (from the
  // Activity feed / mentions / a finding's evidence link), scroll that note
  // into view once the thread has rendered and flash a highlight. Runs once
  // per hash so adding a note later doesn't re-trigger it.
  //
  // MUST live above the loading/!host early returns below — a hook placed
  // after them runs only on some renders (React error #310).
  //
  // v5.244.0 (code review finding 19) — REVEAL, then scroll. This effect used
  // to look for an element that was already mounted and give up otherwise. The
  // density pass then made two things unmount a note: the thread preview (only
  // some roots render) and a collapsed Notes section (its children unmount).
  // A link to evidence that silently lands nowhere is worse than no link, so:
  // resolve the hash against the LOADED notes, keep its root thread in the
  // preview (`linkedRootId`, read by previewThreads below), open the section,
  // and only then scroll — retrying across a few frames while the reveal
  // commits. A hash naming a note this host does not have is left alone.
  const consumedNoteHashRef = React.useRef<string | null>(null);
  const [linkedNoteId, setLinkedNoteId] = useState<number | null>(null);
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const read = () => {
      const match = window.location.hash.match(/^#note-(\d+)$/);
      setLinkedNoteId(match ? Number(match[1]) : null);
    };
    read();
    window.addEventListener('hashchange', read);
    return () => window.removeEventListener('hashchange', read);
  }, [hostId]);
  const linkedRootId = React.useMemo(
    () => (linkedNoteId != null ? rootNoteId(linkedNoteId, notes) : null),
    [linkedNoteId, notes],
  );
  useEffect(() => {
    if (typeof window === 'undefined' || linkedNoteId == null || linkedRootId == null) return undefined;
    const key = `${hostId}#note-${linkedNoteId}`;
    if (consumedNoteHashRef.current === key) return undefined;
    openInspectorSection('host-detail-notes');
    let frame = 0;
    let tries = 0;
    let timer = 0;
    const attempt = () => {
      const el = document.getElementById(`note-${linkedNoteId}`);
      if (!el) {
        // The reveal (section open + preview membership) lands on a later commit.
        if (tries++ < 20) frame = requestAnimationFrame(attempt);
        return;
      }
      consumedNoteHashRef.current = key;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('ring-2', 'ring-info', 'ring-offset-2', 'rounded-control');
      timer = window.setTimeout(
        () => el.classList.remove('ring-2', 'ring-info', 'ring-offset-2', 'rounded-control'),
        2400,
      );
    };
    frame = requestAnimationFrame(attempt);
    return () => {
      cancelAnimationFrame(frame);
      // Leave the highlight timer running: it only removes classes.
      void timer;
    };
  }, [hostId, linkedNoteId, linkedRootId]);

  // The composer's screenshots are this host's draft: their previews are
  // released when the inspector goes (a host change included).
  const pendingImagesRef = React.useRef(pendingImages);
  pendingImagesRef.current = pendingImages;
  useEffect(() => () => {
    pendingImagesRef.current.forEach((p) => URL.revokeObjectURL(p.url));
  }, []);

  type FollowChange = {
    status: FollowStatus | 'none';
    review?: { review_conclusion?: ReviewConclusion; review_summary?: string };
  };
  const follow = useMutation({
    mutationFn: ({ status, review }: FollowChange): Promise<Host['follow']> => (
      status === 'none' ? unfollowHost(projectId, hostId).then(() => null) : followHost(projectId, hostId, status, review)
    ),
    // The list's row is told either way (the callback names the host).
    onSuccess: (response) => {
      onFollowChange?.(hostId, response ?? null);
      putHost((previous) => ({ ...previous, follow: response ?? null }));
    },
  });
  const followLoading = follow.isPending;
  const updateFollow = (status: FollowChange['status'], review?: FollowChange['review'], onSaved?: () => void) => {
    // The toasts say "this host", so only while the reader is still on it.
    follow.mutate({ status, review }, {
      onSuccess: () => {
        if (status === 'none') toast.info('Removed from your follow list', { autoHideMs: 2000 });
        else toast.success(`Marked as ${FOLLOW_STATUS_META[status].label}`, { autoHideMs: 2000 });
        onSaved?.();
      },
      onError: () => toast.error('Failed to update follow status. Please try again.'),
    });
  };

  const openReviewCompletion = () => {
    setReviewConclusion('no_issue');
    setReviewSummaryText('');
    setReviewCompletionOpen(true);
  };
  // `advance`: save, then move to the next host nobody has started — the
  // conclusion and the next task were two separate trips through the queue
  // chrome.  Only after the save succeeded: a failed save must not carry the
  // operator away from the host whose conclusion was lost.
  const submitReviewCompletion = (advance = false) => {
    setReviewCompletionOpen(false);
    updateFollow('reviewed', {
      review_conclusion: reviewConclusion,
      review_summary: reviewSummaryText.trim() || undefined,
    }, advance ? onNextUnreviewed : undefined);
  };

  const addPendingImages = useCallback((files: File[]) => {
    const imgs = files
      .filter((f) => f.type.startsWith('image/'))
      .map((file) => ({ file, url: URL.createObjectURL(file) }));
    if (imgs.length) setPendingImages((prev) => [...prev, ...imgs]);
  }, []);

  // Paste an image straight into the note composer (QoL) — captured here so it
  // attaches to the note on save instead of pasting a garbage data URL into
  // the text. Non-image clipboard content (plain text) pastes normally.
  const handleComposerPaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const it of items) {
      if (it.kind === 'file' && it.type.startsWith('image/')) {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      addPendingImages(files);
    }
  }, [addPendingImages]);

  const removePendingImage = useCallback((idx: number) => {
    setPendingImages((prev) => {
      const target = prev[idx];
      if (target) URL.revokeObjectURL(target.url);
      return prev.filter((_, i) => i !== idx);
    });
  }, []);

  // A new thread: the note, then its screenshots one after another — one
  // write as far as the composer is concerned.  A screenshot that fails does
  // not fail the note: it comes back bound to the note that now exists.
  const postNote = useMutation({
    mutationFn: async ({ body, images }: { body: string; images: PendingImage[] }) => {
      // The note is this project's, and so is each screenshot: every request
      // carries the project this inspector was rendered in.
      const note = await createAnnotation(projectId, hostId, { body });
      const uploaded: NoteAttachment[] = [];
      const failed: PendingImage[] = [];
      for (const img of images) {
        try {
          uploaded.push(await uploadNoteAttachment(projectId, hostId, note.id, img.file));
          URL.revokeObjectURL(img.url);
        } catch (e) {
          failed.push({ ...img, error: formatApiError(e, 'Upload failed.'), noteId: note.id });
        }
      }
      return { note, uploaded, failed };
    },
    onSuccess: ({ note, uploaded, failed }) => {
      if (note.mention_warning) toast.warning(note.mention_warning);
      else announceMentionOutcome(toast, note);
      const noteWithImages = uploaded.length ? { ...note, attachments: uploaded } : note;
      putHost((previous) => ({ ...previous, notes: [noteWithImages, ...(previous.notes ?? [])] }));
      // Keep the failed files (with their previews) so they can be retried
      // against the note that now exists — the clipboard is gone, this is
      // the only copy (UX review C3).
      setPendingImages((prev) => [...prev.filter((p) => p.error), ...failed]);
      setNoteBody('');
      setNoteError(null);
    },
    onError: () => setNoteError('Unable to save note right now. Please try again.'),
  });
  const handleCreateNote = () => {
    if (!noteBody.trim()) {
      setNoteError('Add a short note before saving.');
      return;
    }
    // Files still bound to an earlier note's retry queue stay with that note;
    // only fresh files go on the new one.
    postNote.mutate({ body: noteBody.trim(), images: pendingImages.filter((p) => !p.error) });
  };

  // Retry one failed attachment against the note it was meant for.  Never
  // creates a second note.
  const retryImage = useMutation({
    mutationFn: ({ target, noteId }: { target: PendingImage; noteId: number }) =>
      uploadNoteAttachment(projectId, hostId, noteId, target.file),
    onMutate: ({ target }) => setPendingImages(
      (prev) => prev.map((p) => (p.url === target.url ? { ...p, error: 'Uploading…' } : p)),
    ),
    onSuccess: (attachment, { target, noteId }) => {
      URL.revokeObjectURL(target.url);
      setPendingImages((prev) => prev.filter((p) => p.url !== target.url));
      // On the note at once, so the thumbnail shows without a re-read.
      putHost((previous) => ({
        ...previous,
        notes: (previous.notes ?? []).map((n) => (
          n.id === noteId ? { ...n, attachments: [...(n.attachments ?? []), attachment] } : n
        )),
      }));
    },
    onError: (e, { target }) => setPendingImages(
      (prev) => prev.map((p) => (p.url === target.url ? { ...p, error: formatApiError(e, 'Upload failed.') } : p)),
    ),
  });
  const retryPendingImage = (idx: number) => {
    const target = pendingImages[idx];
    if (!target || target.noteId == null) return;
    retryImage.mutate({ target, noteId: target.noteId });
  };

  const failedAttachmentCount = pendingImages.filter((p) => p.error && p.error !== 'Uploading…').length;

  const deleteNote = useMutation({
    mutationFn: (noteId: number) => deleteAnnotation(projectId, hostId, noteId),
    onSuccess: (_none, noteId) => {
      putHost((previous) => ({
        ...previous, notes: (previous.notes ?? []).filter((note) => note.id !== noteId),
      }));
      toast.success('Note deleted.');
    },
    // Pre-audit (C8): console.error only — user clicked Trash and
    // the note stayed in the list with no signal whether the click
    // did anything.
    onError: (err) => toast.error(formatApiError(err, 'Failed to delete note.')),
  });
  const noteActionId = deleteNote.isPending ? deleteNote.variables : null;
  const handleDeleteNote = async (noteId: number) => {
    const note = notes.find((n) => n.id === noteId);
    const preview = note?.body ? note.body.slice(0, 140) : 'This note';
    const ok = await confirm({
      title: 'Delete note',
      body: `Delete this note? "${preview}${note?.body && note.body.length > 140 ? '…' : ''}"`,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    deleteNote.mutate(noteId);
  };

  const postReply = useMutation({
    mutationFn: ({ body, parentId }: { body: string; parentId: number }) =>
      createAnnotation(projectId, hostId, { body, parent_id: parentId }),
    onSuccess: (newNote) => {
      if (newNote.mention_warning) toast.warning(newNote.mention_warning);
      else if (!announceMentionOutcome(toast, newNote)) toast.success('Reply posted.');
      putHost((previous) => ({ ...previous, notes: [newNote, ...(previous.notes ?? [])] }));
      setReplyTo(null);
      setReplyBody('');
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to post reply.')),
  });
  const handleReply = () => {
    if (!replyTo || !replyBody.trim()) return;
    postReply.mutate({ body: replyBody.trim(), parentId: replyTo.id });
  };
  const noteSubmitting = postNote.isPending || postReply.isPending;

  if (loading) {
    return <DetailSkeleton />;
  }

  if (!host) {
    return (
      <div className="space-y-md py-xl">
        <Alert variant="destructive">
          <AlertTitle>{fetchError === 'Host not found' ? 'Host not found' : 'Unable to load host'}</AlertTitle>
          <AlertDescription>{fetchError || 'Host not found'}</AlertDescription>
        </Alert>
        <div className="flex flex-wrap justify-center gap-xs">
          <Button onClick={retry}>
            <RefreshCw className="size-4" aria-hidden />
            Retry
          </Button>
          <Button variant="outline" onClick={() => navigate('/hosts')}>
            Back to Hosts
          </Button>
        </div>
      </div>
    );
  }

  const hasConflicts = conflictCount > 0;

  // (noteThreadGroups useMemo hoisted to the top of the component body —
  // see line ~221.  Pre-fix it sat below the loading/!host early returns
  // and triggered React error #310 "Rendered more hooks than during the
  // previous render" on the first post-load render.)

  const webLinks: HostWebLink[] = getHostWebLinks(host);
  const primaryWebLink = webLinks[0] ?? null;
  // RDAP attribution surfaced at identity level. The most-specific block is
  // returned first, so [0] is the one to headline; the full Provenance card
  // renders below only when it holds more than this one line.
  const attributions = host.attributions ?? [];
  const primaryAttribution = attributions[0] ?? null;
  const attributionStale = attributionIsStale(primaryAttribution?.looked_up_at);
  const registeredTooltip = primaryAttribution
    ? [
        primaryAttribution.org_name?.trim(),
        primaryAttribution.asn != null
          ? `AS${primaryAttribution.asn}${primaryAttribution.as_name ? ` (${primaryAttribution.as_name})` : ''}`
          : null,
        primaryAttribution.country,
        primaryAttribution.registry,
        primaryAttribution.handle,
        primaryAttribution.cloud_provider
          ? `${primaryAttribution.cloud_provider}${primaryAttribution.cloud_region ? ` · ${primaryAttribution.cloud_region}` : ''}`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : undefined;
  const showProvenanceCard = provenanceExceedsSummary(
    host.attributions, host.cert_orgs, host.cert_status,
  );
  const openPorts = host.ports.filter((port) => port.state === 'open');
  // connectionHelpersByPort is computed once per host above the early
  // returns (see note near noteThreadGroups) to keep the hook count stable.
  const closedPorts = host.ports.filter((port) => port.state === 'closed');
  // v5.299.0 — every other state too (open|filtered, closed|filtered,
  // unfiltered): a UDP port nmap could not settle was on no list at all.
  const filteredPorts = host.ports.filter((port) => port.state !== 'open' && port.state !== 'closed');
  const followInfo = host.follow;
  const followHelperText = followStatus
    ? FOLLOW_STATUS_META[followStatus].description
    : 'Select a review status to keep track of this host.';
  const discoveryTimeline = host.discoveries ?? [];

  const toTimestamp = (value: string | null | undefined) =>
    value ? new Date(value).getTime() : 0;

  // §9 — a Reviewed host has gone stale if a scan re-observed it AFTER the
  // review (last_seen = newest observation; followInfo.updated_at = when the
  // caller set Reviewed). Prompt a re-check rather than letting it silently age.
  const reviewedAtTs = followStatus === 'reviewed' ? toTimestamp(followInfo?.updated_at) : 0;
  // v5.224.0 — material changes (ports or vulnerabilities first observed
  // after the review) are told apart from a mere re-observation, so the
  // badge says which it is instead of "new evidence" for both.
  const sinceReview = changesSinceReview(
    followStatus === 'reviewed' ? followInfo?.updated_at : null,
    host.last_seen,
    host.ports,
    host.vulnerabilities ?? [],
  );
  const newEvidenceSinceReview = reviewedAtTs > 0 && sinceReview != null;
  const daysSinceReview = newEvidenceSinceReview
    ? Math.max(1, Math.round((toTimestamp(host.last_seen) - reviewedAtTs) / 86400000))
    : 0;
  const freshness = host.assessment ? freshnessFacts(host.assessment) : [];

  const sortedVulnerabilities = (host.vulnerabilities ?? []).slice().sort((a, b) => {
    const severityA = (a.severity ?? 'unknown').toLowerCase();
    const severityB = (b.severity ?? 'unknown').toLowerCase();
    const rankA =
      VULNERABILITY_SEVERITY_ORDER[severityA] ?? VULNERABILITY_SEVERITY_ORDER['unknown'];
    const rankB =
      VULNERABILITY_SEVERITY_ORDER[severityB] ?? VULNERABILITY_SEVERITY_ORDER['unknown'];
    if (rankA !== rankB) return rankA - rankB;
    const timeA = toTimestamp(a.last_seen ?? a.first_seen);
    const timeB = toTimestamp(b.last_seen ?? b.first_seen);
    if (timeA !== timeB) return timeB - timeA;
    return b.id - a.id;
  });
  // Group by the ISSUE, not the scanner — Nessus and GreenBone report the same
  // problem in their own words, and a flat list made the operator correlate by
  // eye on every host. See utils/vulnGrouping.ts for the keying rules and why
  // there is deliberately no fuzzy matching.
  // Plain computation, NOT useMemo: this sits after the component's early
  // returns, and a hook here changes the hook count between the loading and
  // loaded renders ("Rendered more hooks than during the previous render").
  // HostInspector.smoke.test.tsx guards exactly that. It's an O(n) pass over
  // one host's findings, so memoising buys nothing anyway.
  const vulnGroups = groupVulnerabilities(sortedVulnerabilities);
  // The header count stays the raw finding total (what the scanners reported);
  // the preview limit now counts ISSUES, since that's what a row is.
  const totalVulnerabilities =
    host.vulnerability_summary?.total_vulnerabilities ?? sortedVulnerabilities.length;
  const vulnSummaryError = host.vulnerability_summary?.error === true;
  // v5.292.0 — issues about one product on the same ports (one CPE) fold into
  // a product line, so an outdated Tomcat's dozen advisory checks no longer
  // bury the host. The preview limit counts LINES.
  // v5.298.0 — misconfigurations (catalog checks, issue key `check:…`) are
  // listed apart from vulnerabilities, misconfigurations first; each list
  // keeps its severity order.
  const isMisconfig = (item: (typeof groupedItems)[number]) =>
    item.kind !== 'product' && item.group.key.startsWith('check:');
  const groupedItems = groupByProduct(vulnGroups);
  const observationItems = [
    ...groupedItems.filter(isMisconfig),
    ...groupedItems.filter((i) => !isMisconfig(i)),
  ];
  const displayedVulnerabilities = showAllVulnerabilities
    ? observationItems
    : observationItems.slice(0, VULNERABILITY_PREVIEW_LIMIT);
  const misconfigCount = groupedItems.filter(isMisconfig).length;
  const splitWeaknesses = misconfigCount > 0 && misconfigCount < groupedItems.length;
  // v5.215.0 — a host whose only findings are informational still gets the
  // card, so the "N informational hidden · show" affordance has somewhere to
  // live; otherwise the hidden rows would be invisible exactly when they are
  // all there is.
  const hiddenInformational =
    host.informational_included === false ? (host.informational_count ?? 0) : 0;
  const hasVulnerabilities = vulnGroups.length > 0 || hiddenInformational > 0;

  // v4.55.0 — intra-page jump helper.  Each card below carries
  // ``id="host-detail-{section}"`` so the triage strip cells and
  // Host Overview counts can scroll to them.  Smooth-scroll with
  // a soft offset so the section header lands just under the
  // top chrome instead of flush with the viewport edge.
  const scrollToSection = (id: string) => {
    // Re-opens the target first: a collapsed section would make the jump look
    // like a dead link.
    jumpToInspectorSection(id);
  };

  // Newest threads first in line for the space; the API's order is kept, and a
  // thread being replied to is never hidden.
  // v5.244.0 — membership is decided in utils/notePreview: pinned threads, the
  // one being replied to and the one a #note- link points into are always
  // visible; the rest compete on latest ACTIVITY (a reply today keeps an old
  // thread up), not on when the root was written.
  const notePreview = previewThreads(noteThreadGroups.topLevel, noteThreadGroups.repliesByParent, {
    limit: NOTE_THREAD_PREVIEW_LIMIT,
    // A reply target can itself be a reply; what must stay visible is its ROOT.
    keepIds: [replyTo ? rootNoteId(replyTo.id, notes) : null, linkedRootId],
    showAll: showAllNotes,
  });
  const visibleTopLevelNotes = notePreview.visible;
  const hiddenNoteCount = notePreview.hidden;

  // Scanner observations — what scanners reported on this host, grouped by
  // issue, not yet judged (v5.225.0 vocabulary). One line per issue.
  const observationsSection = hasVulnerabilities ? (
    <InspectorSection
      id="host-detail-vulnerabilities"
      title="Weaknesses"
      titleHint="Scanner observations on this host, grouped by issue — one row per weakness whichever tool reported it (nmap, NetExec, Nessus…). Not yet judged: promote an issue to make it a finding under investigation."
      icon={<ShieldAlert className="size-4 shrink-0 text-destructive" aria-hidden />}
      // Rows are issues, so the count is issues. When scanners overlapped,
      // say so rather than showing a number that doesn't match the rows.
      count={vulnGroups.length}
      actions={(
        <>
          {/* v5.289.0 — "5 … from 6 scanner observations" read as a
              contradiction; name both units and say why they differ. */}
          {totalVulnerabilities > vulnGroups.length && (
            <span className="inline-flex min-w-0 items-center gap-xxs text-caption text-muted-foreground" data-testid="observation-grouping">
              <span className="min-w-0 truncate">
                {vulnGroups.length} {vulnGroups.length === 1 ? 'issue' : 'issues'} · from {totalVulnerabilities} scanner rows
              </span>
              <InfoTip
                label="About issues and scanner rows"
                text="Scanners can report the same issue more than once on a host (on several ports, or from more than one scanner). Rows of the same issue are grouped into one line, so there are fewer issues than scanner rows."
              />
            </span>
          )}
          {/* v5.215.0 — informational rows are hidden until asked; say how
              many there are so nothing looks lost. */}
          {(host.informational_count ?? 0) > 0 && host.informational_included === false && !informationalFailed && (
            <Button
              size="sm"
              variant="ghost"
              className="text-caption text-muted-foreground"
              onClick={() => setShowInformational(true)}
              disabled={loadingInformational}
              aria-label={`Show ${host.informational_count} informational findings`}
            >
              {loadingInformational
                ? 'Loading…'
                : `${host.informational_count} informational hidden · show`}
            </Button>
          )}
          {/* Said where the rows would have been, with Retry (it was a toast
              over a link that had stopped spinning). */}
          {informationalFailed && (
            <p role="alert" className="break-words text-caption text-destructive">
              {queryErrorText(hostQuery.error, 'The informational observations could not be loaded.')}{' '}
              <button type="button" className="text-info hover:underline" onClick={() => void hostQuery.refetch()}>Retry</button>
            </p>
          )}
        </>
      )}
    >
      <div className="space-y-xs">
        {displayedVulnerabilities.map((item, index) => {
          const prev = displayedVulnerabilities[index - 1];
          // A heading where the list changes kind — only when it has both.
          const heading = splitWeaknesses && (index === 0 || isMisconfig(prev) !== isMisconfig(item))
            ? (isMisconfig(item)
              ? `Misconfigurations (${misconfigCount})`
              : `Vulnerabilities (${groupedItems.length - misconfigCount})`)
            : null;
          const rowProps = {
            severityBadgeVariant,
            expandedVulnIds,
            onToggleDescription: toggleVulnDescription,
            vulnActionId,
            onTriage: openTriage,
            onQueryHosts: handleQueryHosts,
            onQueryExploitPort: handleQueryExploitPort,
          };
          const row = item.kind === 'product' ? (
            <ProductObservationGroup
              key={`${host.id}:${item.product.key}`}
              product={item.product}
              hostId={host.id}
              {...rowProps}
            />
          ) : (
            <VulnerabilityGroup
              // Host-qualified: the inspector stays mounted across prev/next, and
              // an issue shared by two hosts must not carry its open state over.
              key={`${host.id}:${item.group.key}`}
              group={item.group}
              {...rowProps}
            />
          );
          return heading ? (
            <React.Fragment key={`h-${heading}`}>
              <h3 className="pt-xxs text-caption font-semibold uppercase tracking-wide text-muted-foreground">{heading}</h3>
              {row}
            </React.Fragment>
          ) : row;
        })}
        {observationItems.length > VULNERABILITY_PREVIEW_LIMIT && (
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowAllVulnerabilities((prev) => !prev)}
            >
              {showAllVulnerabilities
                ? 'Show fewer issues'
                : `Show all issues (${vulnGroups.length})`}
            </Button>
          </div>
        )}
      </div>
    </InspectorSection>
  ) : null;

  const glanceLinkClass =
    'rounded hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
  // Severity numbers carry colour (genuine alerts); the rest stay muted.
  const glanceLinks = (
    <>
      <button type="button" onClick={() => scrollToSection('host-detail-ports')} className={glanceLinkClass}>
        <strong className="text-foreground">{openPorts.length}</strong> open
        {/* The total only when it says more ("5 open / 5 ports"). */}
        {host.ports.length !== openPorts.length && (
          <span className="opacity-70"> / {host.ports.length} ports</span>
        )}
        {host.ports.length === openPorts.length && ` port${openPorts.length === 1 ? '' : 's'}`}
      </button>
      {host.vulnerability_summary && host.vulnerability_summary.total_vulnerabilities > 0 && (() => {
        // Informational is excluded from the at-a-glance line — it dwarfs
        // real severities. If a host has only info vulns, show a quiet count.
        const sevs = (['critical', 'high', 'medium', 'low'] as const)
          .filter((k) => (host.vulnerability_summary?.[k] ?? 0) > 0);
        return (
          <button type="button" onClick={() => scrollToSection('host-detail-vulnerabilities')}
            className={cn('inline-flex items-center gap-sm', glanceLinkClass)}>
            {sevs.length === 0 ? (
              <span className="text-muted-foreground">
                <strong className="text-muted-foreground">{host.vulnerability_summary?.info ?? 0}</strong> informational
              </span>
            ) : sevs.map((k) => (
              <span key={k}>
                <strong style={{ color: SEVERITY_HSL[k] }}>{host.vulnerability_summary?.[k]}</strong>{' '}{k}
              </span>
            ))}
          </button>
        );
      })()}
      {(host.web_interface_count ?? 0) > 0 && (
        <button type="button" onClick={() => scrollToSection('host-detail-web')} className={glanceLinkClass}>
          <strong className="text-foreground">{host.web_interface_count}</strong> web
        </button>
      )}
      {/* Always present: quick capture is one jump from anywhere on the host,
          and lands in the note field rather than beside it. */}
      <button type="button"
        onClick={() => {
          scrollToSection('host-detail-notes');
          requestAnimationFrame(() => document.getElementById(`host-${hostId}-note-body`)?.focus({ preventScroll: true }));
        }}
        className={cn('inline-flex items-center gap-xxs', glanceLinkClass)}>
        <NotebookPen className="size-3.5" aria-hidden />
        {notes.length > 0
          ? <><strong className="text-foreground">{notes.length}</strong> note{notes.length === 1 ? '' : 's'} · add</>
          : 'Add note'}
      </button>
      <button type="button" onClick={() => scrollToSection('host-detail-proposed-tests')} className={glanceLinkClass}>
        {testsToDo > 0
          ? <><strong className="text-foreground">{testsToDo}</strong> test{testsToDo === 1 ? '' : 's'} to do</>
          : 'Tests'}
      </button>
    </>
  );

  const titleClasses = density === 'sheet' ? 'text-section-title' : 'text-page-title';
  const titleIconClass = density === 'sheet' ? 'size-5' : 'size-6';

  return (
    <HostTestsProvider value={hostTests}>
    <div className="space-y-md">
      {confirmEl}
      {hostTestsElement}
      {vulnSummaryError && (
        <Alert variant="warning">
          <AlertDescription>
            Vulnerability data could not be loaded for this host. The counts below may be incomplete
            or missing — this is a server-side fetch error, not an absence of findings.
          </AlertDescription>
        </Alert>
      )}

      {/* Inspector title row — IP + primary web link + conflicts affordance.
          Density flag lets a SideSheet caller drop to a slightly smaller
          title so its own header doesn't compete with this h1. */}
      <div className="flex flex-wrap items-center gap-sm">
        <Computer className={cn(titleIconClass, 'text-primary')} aria-hidden />
        <h1 className={titleClasses}>
          {primaryWebLink ? (
            <a
              href={primaryWebLink.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-xs text-primary underline-offset-4 hover:underline"
            >
              {host.ip_address}
              <ExternalLink className={density === 'sheet' ? 'size-4' : 'size-5'} aria-hidden />
            </a>
          ) : (
            host.ip_address
          )}
        </h1>
        {host.hostname && (
          <span className="min-w-0 max-w-[24rem] truncate text-metadata text-muted-foreground"
            // v5.276.0 — where the display name came from (served since
            // v2.322.0, never shown): the names inventory lists the others.
            title={`${host.hostname}${HOSTNAME_SOURCE_LABEL[host.hostname_source ?? ''] ? ` — ${HOSTNAME_SOURCE_LABEL[host.hostname_source ?? '']}` : ''}`}>
            {host.hostname}
          </span>
        )}
        {hasConflicts ? (
          <Button
            variant={showConflicts ? 'default' : 'outline'}
            size="sm"
            aria-expanded={showConflicts}
            aria-controls="host-detail-conflicts"
            // The panel mounts directly under the overview (v5.246.0), so it
            // opens in view; it used to mount near the bottom and need a scroll.
            onClick={() => setShowConflicts((v) => !v)}
            title="Two scans reported different values for this host — open to see which, and from which scans"
          >
            <AlertTriangle className="size-4 text-warning" aria-hidden />
            {conflictCount} conflict{conflictCount === 1 ? '' : 's'}
            {showConflicts
              ? <ChevronDown className="size-3.5" aria-hidden />
              : <ChevronRight className="size-3.5" aria-hidden />}
          </Button>
        ) : conflictsError ? (
          <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground" title="The data-conflict check failed to load — this is not a confirmation that the host has none">
            <AlertTriangle className="size-3.5" aria-hidden />
            Couldn&apos;t check conflicts
          </span>
        ) : null}

        {/* v5.240.0 — the review control lives in the title row. It used to
            own 5/12 of the overview card, which for an unreviewed host was one
            button over ~200px of nothing while the OS beside it truncated. */}
        <div className="ml-auto flex flex-wrap items-center gap-xs">
          {/* 5.303.0 — the bookmark icon that stood here looked like a button
              and did nothing; the badge says the state. */}
          <Badge
            variant={followStatus ? FOLLOW_STATUS_META[followStatus].badgeVariant : 'outline'}
            title={followHelperText}
          >
            {followStatus ? FOLLOW_STATUS_META[followStatus].label : 'Not reviewed'}
          </Badge>
          {/* §6/§9 — one state-aware review control: primary action for the
              common path + an overflow for the off-path transitions
              (mark-reviewed-direct, clear status). */}
          {followStatus === 'reviewed' ? (
            <Button size="sm" variant="outline" disabled={followLoading}
              onClick={() => updateFollow('in_review')}>
              <RotateCcw className="size-3.5" aria-hidden /> Re-open review
            </Button>
          ) : followStatus === 'in_review' ? (
            <Button size="sm" disabled={followLoading} onClick={openReviewCompletion}>
              <CheckCircle2 className="size-3.5" aria-hidden /> Mark reviewed
            </Button>
          ) : (
            <Button size="sm" disabled={followLoading}
              onClick={() => updateFollow('in_review')}>
              <Eye className="size-3.5" aria-hidden /> Start review
            </Button>
          )}
          {/* 5.303.0 — the off-path transitions as quiet buttons: the "⋯"
              menu they lived in held one item on almost every host. */}
          {followStatus !== 'in_review' && followStatus !== 'reviewed' && (
            <Button size="sm" variant="ghost" disabled={followLoading} onClick={openReviewCompletion}>
              Mark reviewed…
            </Button>
          )}
          {followStatus && (
            <Button size="sm" variant="ghost" disabled={followLoading} onClick={() => updateFollow('none')}
              title="Clear this host's review status">
              Clear status
            </Button>
          )}
        </div>
      </div>

      {/* Scope is part of what this host IS to the engagement, so it sits with
          the identity — not in a card of its own further down. */}
      <ScopeMembershipCard membership={host.scope_membership} />

      {webLinks.length > 1 && (
        <div className="flex flex-wrap gap-xs">
          {webLinks.map((link) => (
            <a
              key={link.url}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-xxs rounded-chip border border-border px-sm py-px text-micro font-semibold uppercase tracking-wider text-foreground hover:bg-accent"
            >
              <ExternalLink className="size-3" aria-hidden />
              {link.protocol.toUpperCase()} {link.port}
            </a>
          ))}
        </div>
      )}

      {/* Host Overview */}
      <Card>
        <CardContent className="space-y-sm p-sm">
            {/* Identity — labelled key/value, not a badge soup. Colour is
                reserved for genuine alerts (SMB disabled, unassigned owner). */}
            <dl className="grid gap-x-lg gap-y-xs sm:grid-cols-2">
              <div className="flex gap-sm">
                <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">State</dt>
                <dd className="min-w-0 text-metadata">
                  <span className="capitalize text-foreground">{host.state || 'unknown'}</span>
                  {host.state_reason && (
                    <span className="text-caption text-muted-foreground" title={`State reason: ${host.state_reason}`}> · {host.state_reason}</span>
                  )}
                </dd>
              </div>
              <div className="flex gap-sm">
                <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">OS</dt>
                <dd className="min-w-0 truncate text-metadata text-foreground"
                  title={[
                    [host.os_family, host.os_type, host.os_generation].filter(Boolean).join(' · '),
                    Number(host.os_accuracy) > 0 && Number(host.os_accuracy) < 70
                      ? `Low-confidence OS guess (${Number(host.os_accuracy)}% match)`
                      : '',
                  ].filter(Boolean).join(' — ') || undefined}>
                  {host.os_name ? (() => {
                    // De-weight a low-confidence guess so a 60% match doesn't read
                    // as authoritatively as a 98% one.
                    // 0 is "the source gave no figure" (a Nessus / NetExec OS),
                    // not a 0% match: it rendered "~Ubuntu 22.04 · 0%" in amber.
                    const acc = Number(host.os_accuracy) > 0 ? Number(host.os_accuracy) : null;
                    const tentative = acc != null && acc < 70;
                    const label = host.os_vendor && !host.os_name.toLowerCase().includes(host.os_vendor.toLowerCase())
                      ? `${host.os_vendor} ${host.os_name}`
                      : host.os_name;
                    return (
                      <>
                        <span className={tentative ? 'italic text-muted-foreground' : undefined}>
                          {tentative ? `~${label}` : label}
                        </span>
                        {acc != null && (
                          <span className={tentative ? 'text-caption text-amber-600' : 'text-caption text-muted-foreground'}>
                            {' · '}{acc}%
                          </span>
                        )}
                      </>
                    );
                  })() : <span className="text-muted-foreground">—</span>}
                </dd>
              </div>
              {/* v5.297.0 — only on a host that speaks SMB: an FTP server read
                  "SMB —", a question nobody asked. */}
              {(host.smb_signing || openPorts.some((p) => p.port_number === 445 || p.port_number === 139)) && (
              <div className="flex gap-sm">
                <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">SMB</dt>
                <dd className="min-w-0 text-metadata">
                  {host.smb_signing === 'disabled' ? (
                    <span className="inline-flex items-center gap-xxs font-medium text-destructive" title="SMB message signing disabled — NTLM relay-vulnerable">
                      <AlertTriangle className="size-3.5" aria-hidden /> Signing disabled
                    </span>
                  ) : host.smb_signing === 'not_required' || host.smb_signing === 'enabled' ? (
                    // v5.274.0 — 'not_required' is the stored state for "enabled
                    // but not required" from nmap and (signing:False) from
                    // NetExec; still relay-exposable.  v5.298.0 — the weakness
                    // is listed under Weaknesses; the header states the fact.
                    // 5.303.0 — the tooltip said "listed under Weaknesses",
                    // which holds only when a scanner reported it as a check.
                    <span className="text-foreground" title="SMB signing is not required — NTLM relay is still possible">
                      Signing not required
                    </span>
                  ) : host.smb_signing === 'required' ? (
                    <span className="text-foreground">Signing required</span>
                  ) : (
                    // "—" read as "nothing wrong".
                    <span className="text-muted-foreground" title="No import recorded this host's SMB signing (nmap smb2-security-mode, NetExec)">
                      signing not checked
                    </span>
                  )}
                </dd>
              </div>
              )}
              {/* v5.276.0 — identity scanners report that was dropped:
                  NetBIOS name (Nessus) and MAC + vendor (nmap, Nessus). */}
              {(host.netbios_name || host.mac_address) && (
                <div className="flex gap-sm">
                  <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">Identity</dt>
                  <dd className="min-w-0 truncate text-metadata text-foreground"
                    title={[host.netbios_name && `NetBIOS ${host.netbios_name}`, host.mac_address && `MAC ${host.mac_address}${host.mac_vendor ? ` (${host.mac_vendor})` : ''}`].filter(Boolean).join(' · ')}>
                    {host.netbios_name && <span>NetBIOS <span className="font-mono">{host.netbios_name}</span></span>}
                    {host.netbios_name && host.mac_address && <span className="text-muted-foreground"> · </span>}
                    {host.mac_address && (
                      <span>
                        <span className="font-mono">{host.mac_address}</span>
                        {host.mac_vendor && <span className="text-caption text-muted-foreground"> {host.mac_vendor}</span>}
                      </span>
                    )}
                  </dd>
                </div>
              )}
              <div className="flex gap-sm">
                <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">Assignee</dt>
                <dd className="min-w-0 text-metadata">
                  <AssigneeControl
                    hostId={host.id}
                    assignees={host.assignees ?? []}
                    canEdit={canManageEntries}
                  />
                </dd>
              </div>
              {/* v2.423.0 — the weakness / access flags a Hosts filter can
                  match (an end-of-life OS read as a plain OS name, and a host
                  opened from "SMB signing not required" said nothing of it
                  here). */}
              {(host.weakness_flags?.length ?? 0) > 0 && (
                <div className="flex gap-sm sm:col-span-2">
                  <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground"
                    title="Weakness and access flags — the Hosts filter “Weakness or access”">Weakness</dt>
                  <dd className="flex min-w-0 flex-wrap gap-xxs text-metadata">
                    {host.weakness_flags!.map((flag) => (
                      <span key={flag}
                        className="rounded-chip border border-warning/40 bg-warning/10 px-xs text-caption text-warning">
                        {host.weakness_labels?.[flag] ?? flag.replace(/_/g, ' ')}
                      </span>
                    ))}
                  </dd>
                </div>
              )}
              {/* Registered owner of the host's netblock (RDAP). Distinct from
                  "Assignee" above (a person); this is the outside world's answer
                  to "whose is this?" — the scope-validation signal, surfaced at
                  identity level. The full Provenance card renders below only
                  when it holds more than this one line (see
                  provenanceExceedsSummary). */}
              {primaryAttribution && (
                <div className="flex gap-sm sm:col-span-2">
                  <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">Registered</dt>
                  <dd className="flex min-w-0 flex-wrap items-center gap-xs text-metadata"
                    title={registeredTooltip}>
                    <span className="min-w-0 truncate text-foreground">
                      {primaryAttribution.org_name?.trim() || 'Registrant not published'}
                    </span>
                    {primaryAttribution.country && (
                      <span className="shrink-0 rounded-chip border border-border px-xs text-caption text-muted-foreground">
                        {primaryAttribution.country}
                      </span>
                    )}
                    {primaryAttribution.asn != null && (
                      <span className="shrink-0 text-caption text-muted-foreground">
                        AS{primaryAttribution.asn}
                      </span>
                    )}
                    {primaryAttribution.cloud_provider && (
                      <span className="shrink-0 rounded-chip border border-info/40 px-xs text-caption text-info">
                        {primaryAttribution.cloud_provider.toUpperCase()}
                      </span>
                    )}
                    {attributionStale && (
                      <span className="shrink-0 text-caption text-warning"
                        title="This registration lookup is over 180 days old — re-check before citing.">
                        · stale
                      </span>
                    )}
                  </dd>
                </div>
              )}
              {((host.tags?.length ?? 0) > 0 || canManageEntries) && (
                <div className="flex gap-sm sm:col-span-2">
                  <dt className="w-20 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">Tags</dt>
                  <dd className="min-w-0">
                    <TagControl hostId={host.id} tags={host.tags ?? []} canEdit={canManageEntries} />
                  </dd>
                </div>
              )}
            </dl>

            {/* v5.224.0 — evidence freshness beside each kind of evidence
                (design review item 4): observed / vulnerabilities / web /
                SMB / tested, each with its own date, "not assessed" or "n/a",
                so the newest scan does not make every fact look current. */}
            {freshness.length > 0 && (
              <dl className="flex flex-wrap items-center gap-x-md gap-y-xxs text-caption" aria-label="Evidence freshness">
                {freshness.map((f) => (
                  <div key={f.key} className="inline-flex items-baseline gap-xxs" title={f.title}>
                    <dt className="text-muted-foreground">{f.label}</dt>
                    <dd
                      className={cn(
                        'font-medium',
                        f.tone === 'ok' && 'text-foreground',
                        f.tone === 'gap' && 'text-warning',
                        f.tone === 'warn' && 'text-warning',
                        f.tone === 'na' && 'text-muted-foreground',
                      )}
                    >
                      {f.value}
                      {/* Plain words beside the value (whether the scan
                          authenticated) — text, not a badge. */}
                      {f.note && (
                        <span className={cn('font-normal', f.noteTone === 'warn' ? 'text-warning' : 'text-muted-foreground')}>
                          {' · '}{f.note}
                        </span>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            )}

          {/* Review detail — only what exists: the conclusion, what changed
              since, when, and who else is on this host. The status and its
              action are in the title row; an unreviewed host that nobody else
              follows renders nothing here. */}
          {/* v5.246.0 — a bare "Updated <time>" no longer holds this row open:
              for a host merely In review it was a divided row carrying one
              timestamp. The time still shows whenever the row has a reason. */}
          {((followStatus === 'reviewed' && followInfo?.review_conclusion)
            || newEvidenceSinceReview || otherFollowers.length > 0 || followersError) && (
          <div className="space-y-xs border-t border-border pt-xs">
            <div className="flex flex-wrap items-center gap-sm">
              <div className="flex flex-wrap items-center gap-xs">
                {followStatus === 'reviewed' && followInfo?.review_conclusion && (
                  <span className="text-caption font-medium text-foreground"
                    title={followInfo.review_summary ?? undefined}>
                    {REVIEW_CONCLUSION_LABEL[followInfo.review_conclusion]
                      ?? followInfo.review_conclusion}
                  </span>
                )}
                {newEvidenceSinceReview && sinceReview && (
                  sinceReview.reobservedOnly ? (
                    <Badge variant="outline"
                      title={`A scan re-observed this host ${daysSinceReview}d after you marked it Reviewed, and recorded nothing new — no new port or vulnerability.`}>
                      Re-observed since review, nothing new
                    </Badge>
                  ) : (
                    <Badge variant="warning"
                      title={[
                        sinceReview.newPorts.length
                          ? `${sinceReview.newPorts.length} port${sinceReview.newPorts.length === 1 ? '' : 's'} first seen since your review: ${sinceReview.newPorts.map((p) => `${p.port_number}/${p.protocol}`).join(', ')}`
                          : null,
                        sinceReview.newVulns.length
                          ? `${sinceReview.newVulns.length} vulnerabilit${sinceReview.newVulns.length === 1 ? 'y' : 'ies'} first seen since your review`
                          : null,
                        'Re-open to re-check.',
                      ].filter(Boolean).join(' · ')}>
                      Changed since review
                      {sinceReview.newPorts.length > 0 && ` · ${sinceReview.newPorts.length} new port${sinceReview.newPorts.length === 1 ? '' : 's'}`}
                      {sinceReview.newVulns.length > 0 && ` · ${sinceReview.newVulns.length} new vuln${sinceReview.newVulns.length === 1 ? '' : 's'}`}
                    </Badge>
                  )
                )}
                {followInfo && (
                  <span className="text-caption text-muted-foreground">
                    Updated{' '}
                    <TimeAgo value={followInfo.updated_at ?? followInfo.created_at} absoluteAfterDays={30} />
                  </span>
                )}
              </div>
            </div>

            {otherFollowers.length > 0 && (
              <div className="flex flex-wrap items-center gap-xs">
                <p className="text-caption text-muted-foreground">Also reviewing</p>
                <div className="flex flex-wrap gap-xs">
                  {otherFollowers.map((f) => {
                    const label = f.full_name || f.username;
                    const statusLabel =
                      f.status === 'in_review'
                        ? 'In Review'
                        : f.status === 'watching'
                          ? 'Watching'
                          : 'Reviewed';
                    const variant =
                      f.status === 'in_review'
                        ? 'warning'
                        : f.status === 'watching'
                          ? 'info'
                          : 'success';
                    return (
                      <Badge key={f.user_id} variant={variant}>
                        {label} · {statusLabel}
                      </Badge>
                    );
                  })}
                </div>
              </div>
            )}
            {followersError && otherFollowers.length === 0 && (
              <p className="text-caption text-muted-foreground">Follower list unavailable</p>
            )}
          </div>
          )}

        </CardContent>
      </Card>

      {/* Data conflicts — directly under the overview (v5.246.0). It used to
          mount near the bottom of the inspector, so the title-row button had
          to scroll the page to it; and it stays a bordered panel because it is
          an exception the analyst asked to see, not routine evidence. */}
      {showConflicts && hasConflicts && (
        <HostConflictsPanel
          id="host-detail-conflicts"
          conflictCount={conflictCount}
          history={conflictHistory}
          confidence={conflicts}
          ports={host.ports ?? []}
        />
      )}

      {/* At a glance — actionable counts as quiet linked stats, and the
          inspector's navigation. Sticky (v5.240.0): the links used to scroll
          away with the overview, which is exactly when they are needed. */}
      <div
        className={cn(
          'sticky z-10 flex flex-wrap items-center gap-x-md gap-y-xs rounded-control border border-border px-sm py-xs text-caption text-muted-foreground shadow-raised',
          // The sheet's scroll body has `py-md`, and a sticky offset is measured
          // from INSIDE that padding: `top-0` parked the strip 16px down with
          // the content scrolling visibly above it. `-top-md` cancels it.
          density === 'sheet' ? '-top-md bg-card' : 'bg-background',
        )}
        style={density === 'sheet' ? undefined : stickyBelowChrome}
      >
        {glanceLinks}
      </div>

      {/* v4.54.0 — host detail section order rebalanced (UI/UX phase 1).
          Pre-fix the order was:
            Host Overview → Proposed Tests → Web → NSE → NetExec
            → Vulnerabilities → Add Note → Team Notes → Conflicts
            → Lineage → Port Details
          which pushed the note composer below the fold on any host
          with real data, and rendered Port Details (foundational scan
          data) twelfth — after Workflow Lineage.

          New order answers the operator's questions in priority:
            1. What is this host? (Overview, unchanged)
            2. Is it in scope, and what is listening? (Scope + Port
               Details — v5.236.0, see below)
            3. Where do I record my next observation? (Add Note + Team
               Notes)
            4. How risky is it? (Vulnerabilities lifted ahead of the
               agent + tool evidence stacks)
            5. What's being done about it? (Proposed Tests)
            6. What evidence supports it? (Web, NSE, NetExec)
            7. History / audit (Conflicts, Lineage tail)

          v5.236.0 (design review 2026-09-19) — Scope and Port Details moved
          from below the whole discussion to directly under the overview: an
          analyst should not scroll through a conversation to find out what is
          listening, and a note is written ABOUT a service. The composer stays
          one jump away: the overview's "Add note" link scrolls to it.

          v5.240.0 (density pass) — the median host here has 3 open ports and
          3 scanner observations, and needed three screens: every data source
          had its own Card, the composer was a permanently open ~250px form,
          and each observation printed its whole write-up. Now: scope is a
          header line, sections are heading + divider (InspectorSection), the
          composer is one line until used, observations are one line each, and
          the discussion follows the evidence instead of separating the ports
          from the observations.
        */}

      {/* v5.297.0 — the host's weaknesses first (scanner observations, one
          row per issue, whichever tool reported it), then this host's
          findings, then each service with everything known about it. */}
      {observationsSection}

      {/* This host's findings, inline. */}
      <HostFindingsCard hostId={host.id} />

      {/* Services — one row per open port; a row opens to its weaknesses,
          access, web pages and paths, and the tools' output. */}
      <PortDetailsCard
        hostId={host.id}
        hostIp={host.ip_address}
        hostname={host.hostname}
        hostLastSeen={host.last_seen ?? null}
        openPorts={openPorts}
        closedPorts={closedPorts}
        filteredPorts={filteredPorts}
        connectionHelpersByPort={connectionHelpersByPort}
        vulnerabilities={host.vulnerabilities ?? []}
        netexecCount={host.netexec_result_count ?? 0}
        webPathCount={host.web_path_count ?? 0}
      />

      {/* Host-level evidence: what is about the host, not one service. */}
      {/* v5.193.0 — every name bound to this address (the host row shows
          one display name; a load balancer carries many). v5.241.0 — the DNS
          records behind those names are a disclosure INSIDE this section
          (HostDnsRecordsCard, embedded); it stands alone only when the host
          has no names at all. */}
      <HostNamesCard hostId={host.id} />

      {/* Nmap's host scripts (port scripts are in each service's panel). */}
      <NseScriptsCard host={host} hostOnly />

      {/* Where this host is registered and hosted — the outside world's answer
          to "is this the client's?", vs the scope's own CIDR list. A single
          fresh attribution is already shown as the "Registered" line in the
          identity block above, so the card renders only when it adds more:
          cert data, a second block, or a stale lookup to re-verify. */}
      {showProvenanceCard && (
        <ProvenanceCard
          attributions={host.attributions}
          certOrgs={host.cert_orgs}
          certStatus={host.cert_status}
        />
      )}

      {/* v5.316.0 — what agents ran against this host (renders nothing when none). */}
      {/* Tests proposed for this host, then every command an agent recorded
          against it (the evidence a test's result is). */}
      <HostTestsSection key={host.id} hostId={host.id} canEdit={canManageEntries} userId={user?.id} onDirtyChange={setFindingsDraftDirty} />

      <HostEvidenceSection hostId={host.id} />

      {/* Discussion — one section: the composer (a single line until used)
          over the thread, after the evidence it is written about. Notes are
          talk about the host; work is a test and its result (5.325.0). */}
      <InspectorSection
        id="host-detail-notes"
        title="Discussion"
        titleHint="Notes and replies about this host, for the team. Record work as a test and its result; a finding comes from a weakness or a test result."
        icon={<MessageSquare className="size-4 shrink-0 text-primary" aria-hidden />}
        count={notes.length}
      >
        <div className="space-y-sm">
          {/* Writing a note is a project analyst's (R32); a reader sees the
              discussion without a composer whose Post the server refuses. */}
          {!canManageEntries && notes.length === 0 && (
            <p className="text-caption text-muted-foreground">No notes on this host.</p>
          )}
          {canManageEntries && (
          <NoteComposer
            hostId={hostId}
            body={noteBody}
            onBodyChange={setNoteBody}
            submitting={noteSubmitting}
            onSubmit={handleCreateNote}
            error={noteError}
            onDismissError={() => setNoteError(null)}
            onPaste={handleComposerPaste}
            images={pendingImages}
            failedAttachmentCount={failedAttachmentCount}
            onRemoveImage={removePendingImage}
            onRetryImage={retryPendingImage}
          />
          )}
          {notes.length > 0 && (
            // v2.43.0 — MONO-2: notes rendering is now <NoteThread> (see
            // ./host-inspector/NoteThread.tsx).  Pre-extraction this was
            // a 120-line closure capturing 11 pieces of parent state.
            <NoteThread
              topLevel={visibleTopLevelNotes}
              repliesByParent={noteThreadGroups.repliesByParent}
              replyTo={replyTo}
              replyBody={replyBody}
              onReplyToChange={setReplyTo}
              onReplyBodyChange={setReplyBody}
              onSubmitReply={handleReply}
              noteSubmitting={noteSubmitting}
              noteActionId={noteActionId}
              onDeleteNote={handleDeleteNote}
              onEditDetails={openNoteDetails}
              currentUserId={user?.id ?? null}
              hostId={hostId}
              canManageNotes={canManageEntries}
            />
          )}
          {hiddenNoteCount > 0 && (
            <Button size="sm" variant="ghost" className="text-caption" onClick={() => setShowAllNotes(true)}>
              Show {hiddenNoteCount} earlier thread{hiddenNoteCount === 1 ? '' : 's'}
            </Button>
          )}
        </div>
      </InspectorSection>

      {/* §9 — review-completion dialog. Marking a host Reviewed records WHAT
          the reviewer concluded so "reviewed" is an auditable outcome. */}
      <Dialog open={reviewCompletionOpen} onOpenChange={(v) => { if (!v) setReviewCompletionOpen(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Complete review</DialogTitle>
            <DialogDescription>
              Record what this review concluded. It's kept on the host's review state and shown to
              the team.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-sm">
            <div>
              <Label htmlFor="review-conclusion" className="text-caption">Conclusion</Label>
              <Select value={reviewConclusion} onValueChange={(v) => setReviewConclusion(v as ReviewConclusion)}>
                <SelectTrigger id="review-conclusion"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {REVIEW_CONCLUSION_ORDER.map((c) => (
                    <SelectItem key={c} value={c}>{REVIEW_CONCLUSION_LABEL[c]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="review-summary" className="text-caption">Summary (optional)</Label>
              <Textarea
                id="review-summary"
                rows={3}
                placeholder="What you checked and why you concluded this…"
                value={reviewSummaryText}
                onChange={(e) => setReviewSummaryText(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setReviewCompletionOpen(false)}>Cancel</Button>
            <Button
              variant={onNextUnreviewed ? 'outline' : 'default'}
              disabled={followLoading}
              onClick={() => submitReviewCompletion(false)}
            >
              <CheckCircle2 className="size-3.5" aria-hidden /> Mark reviewed
            </Button>
            {/* Only inside a queue (the Hosts side sheet passes the step). */}
            {onNextUnreviewed && (
              <Button disabled={followLoading} onClick={() => submitReviewCompletion(true)}>
                <CheckCircle2 className="size-3.5" aria-hidden /> Save and next unreviewed
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* §11 — vuln triage confirm. Promotion fans out across every project
          host sharing the plugin_id, so show that blast radius (and capture
          a rationale, esp. for a false-positive dismissal) before committing. */}
      <Dialog open={triageVuln !== null} onOpenChange={(v) => { if (!v) { setTriageVuln(null); setTriageReason(''); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {triageVuln?.intent === 'confirmed' ? 'Promote to finding' : 'Dismiss as false positive'}
            </DialogTitle>
            <DialogDescription className="break-words">
              {triageVuln?.title}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-sm">
            {/* v5.238.0 — a dismissal is made in one host's inspector about
                that host's observation: it defaults to THIS host.  Marking the
                issue a false positive on every host is the explicit choice.
                v5.245.0 — the same rule for a PROMOTION: it could only be
                issue-wide, so confirming what was verified on one host recorded
                "confirmed" for every host carrying the issue, including hosts
                nobody had looked at.  What is done in one host's inspector is
                about that host unless it is widened. */}
            {triagePreview && (
              <fieldset className="space-y-xxs">
                <legend className="text-caption font-semibold text-foreground">Applies to</legend>
                <label className="flex items-start gap-xs text-caption">
                  <input
                    type="radio" name="triage-scope" className="mt-[3px]"
                    checked={triageScope === 'host'} onChange={() => setTriageScope('host')}
                  />
                  <span className="min-w-0 break-words">
                    <strong>This host only</strong>
                    {triagePreview.host_ip ? ` (${triagePreview.host_ip})` : ''} — the other hosts carrying
                    this issue are left as they are.
                  </span>
                </label>
                <label className="flex items-start gap-xs text-caption">
                  <input
                    type="radio" name="triage-scope" className="mt-[3px]"
                    checked={triageScope === 'issue'} onChange={() => setTriageScope('issue')}
                    disabled={triagePreview.affected_host_count <= 1}
                  />
                  <span className="min-w-0 break-words">
                    <strong>
                      All {triagePreview.affected_host_count} host{triagePreview.affected_host_count === 1 ? '' : 's'} carrying this issue
                    </strong>
                    {triagePreview.affected_host_count <= 1
                      ? ' — no other host carries it.'
                      : triageVuln?.intent === 'confirmed'
                        ? ' — record the finding on every host that reports it, verified here or not.'
                        : ' — the issue itself is a false positive (a scanner misfire, not something about this host).'}
                  </span>
                </label>
              </fieldset>
            )}
            {/* Blast-radius preview */}
            <div className="rounded-control border border-border bg-muted/30 p-sm text-caption">
              {triagePreviewLoading ? (
                <span className="flex items-center gap-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" aria-hidden /> Checking affected hosts…
                </span>
              ) : triagePreview && triageVuln?.intent === 'false_positive' && triageScope === 'host' ? (
                <span className="text-foreground">
                  {triagePreview.already_promoted && triagePreview.host_endpoint_status === 'false_positive' ? (
                    <>
                      <strong>{triagePreview.host_ip ?? 'This host'}</strong> is already a false positive on
                      <Link to={`/findings/${triagePreview.finding_id}`} className="text-primary underline-offset-2 hover:underline">finding #{triagePreview.finding_id}</Link>; nothing changes.
                    </>
                  ) : triagePreview.already_promoted ? (
                    <>
                      <Link to={`/findings/${triagePreview.finding_id}`} className="text-primary underline-offset-2 hover:underline">Finding #{triagePreview.finding_id}</Link> already covers this issue
                      {triagePreview.host_endpoint_status
                        ? <> ({ENDPOINT_STATUS_LABEL[triagePreview.host_endpoint_status as FindingHostStatus] ?? triagePreview.host_endpoint_status})</>
                        : ' on other hosts'}
                      . This marks <strong>{triagePreview.host_ip ?? 'this host'}</strong> a false positive on it; the
                      finding stays{' '}
                      <strong>{FINDING_STATUS_LABEL[(triagePreview.finding_status ?? 'open') as FindingStatus] ?? triagePreview.finding_status}</strong>{' '}
                      for its other hosts.
                    </>
                  ) : (
                    <>
                      Records a false positive for <strong>{triagePreview.host_ip ?? 'this host'}</strong> only.
                      {triagePreview.affected_host_count > 1 && (
                        <> The other {triagePreview.affected_host_count - 1} host{triagePreview.affected_host_count - 1 === 1 ? '' : 's'} carrying
                          this issue stay untriaged.</>
                      )}
                    </>
                  )}
                </span>
              ) : triagePreview && triageVuln?.intent === 'confirmed' && triageScope === 'host' ? (
                <span className="text-foreground">
                  {triagePreview.already_promoted && triagePreview.host_endpoint_status ? (
                    <>
                      <strong>{triagePreview.host_ip ?? 'This host'}</strong> is already on <Link to={`/findings/${triagePreview.finding_id}`} className="text-primary underline-offset-2 hover:underline">finding #{triagePreview.finding_id}</Link>{' '}
                      ({ENDPOINT_STATUS_LABEL[triagePreview.host_endpoint_status as FindingHostStatus] ?? triagePreview.host_endpoint_status});
                      this records this scanner&rsquo;s evidence on it.
                    </>
                  ) : triagePreview.already_promoted ? (
                    <>
                      <Link to={`/findings/${triagePreview.finding_id}`} className="text-primary underline-offset-2 hover:underline">Finding #{triagePreview.finding_id}</Link> already covers this issue. This adds{' '}
                      <strong>{triagePreview.host_ip ?? 'this host'}</strong> to it and records this
                      scanner&rsquo;s evidence; no other host is attached.
                    </>
                  ) : (
                    <>
                      Creates a finding for <strong>{triagePreview.host_ip ?? 'this host'}</strong> only.
                      {triagePreview.affected_host_count > 1 && (
                        <> The other {triagePreview.affected_host_count - 1} host{triagePreview.affected_host_count - 1 === 1 ? '' : 's'} reporting
                          this issue stay untriaged; promoting it from one of them later joins this same finding.</>
                      )}
                    </>
                  )}
                </span>
              ) : triagePreview ? (
                triagePreview.already_promoted ? (
                  <>
                    <span className="text-foreground">
                      A finding for this issue already exists
                      {triagePreview.finding_id != null && <> (<Link to={`/findings/${triagePreview.finding_id}`} className="text-primary underline-offset-2 hover:underline">#{triagePreview.finding_id}</Link>)</>}
                      {triagePreview.new_host_count > 0
                        ? ' — this attaches '
                        : ' — this re-dispositions it; no new hosts are attached.'}
                      {triagePreview.new_host_count > 0 && (
                        <>
                          <strong>{triagePreview.new_host_count}</strong>{' '}
                          more host{triagePreview.new_host_count === 1 ? '' : 's'} and records this
                          scanner&rsquo;s evidence.
                        </>
                      )}
                    </span>
                    {/* The issue can already be promoted from a different host or
                        a different scanner's wording, which is not obvious from
                        the row the operator clicked — name the total so the
                        dialog doesn't read as "nothing happens". */}
                    <span className="mt-xxs block text-muted-foreground">
                      Covers <strong>{triagePreview.affected_host_count}</strong>{' '}
                      host{triagePreview.affected_host_count === 1 ? '' : 's'} in total.
                    </span>
                  </>
                ) : (
                  <>
                    <span className="text-foreground">
                      {triageVuln?.intent === 'confirmed' ? 'Creates one finding across ' : 'Records a finding across '}
                      <strong>{triagePreview.affected_host_count}</strong>{' '}
                      host{triagePreview.affected_host_count === 1 ? '' : 's'}
                      {triagePreview.affected_host_count > 1
                        ? ' carrying this same issue — including rows reported by other scanners under different wording.'
                        : ' (this host only).'}
                    </span>
                    {triagePreview.affected_host_sample.length > 0 && (
                      <span className="mt-xxs block break-words text-muted-foreground">
                        {triagePreview.affected_host_sample.join(', ')}
                        {triagePreview.affected_host_count > triagePreview.affected_host_sample.length
                          ? `, +${triagePreview.affected_host_count - triagePreview.affected_host_sample.length} more`
                          : ''}
                      </span>
                    )}
                  </>
                )
              ) : (
                <span role="alert" className="flex flex-wrap items-center gap-xs text-warning">
                  <span className="min-w-0 flex-1">
                    Couldn&rsquo;t determine which hosts this affects. It can reach hosts other
                    than this one, so it isn&rsquo;t offered until that is known.
                  </span>
                  <Button size="sm" variant="outline" onClick={() => void triagePreviewQuery.refetch()}>
                    Retry
                  </Button>
                </span>
              )}
            </div>

            <div>
              <Label htmlFor="vuln-triage-reason" className="text-caption">
                Rationale{triageVuln?.intent === 'false_positive' ? '' : ' (optional)'}
              </Label>
              <Textarea
                id="vuln-triage-reason"
                rows={3}
                placeholder={triageVuln?.intent === 'false_positive'
                  ? 'e.g. scanner flagged the backported package, not the CVE'
                  : 'Optional context recorded on the finding history'}
                value={triageReason}
                onChange={(e) => setTriageReason(e.target.value)}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setTriageVuln(null); setTriageReason(''); }}>
              Cancel
            </Button>
            <Button
              variant={triageVuln?.intent === 'false_positive' ? 'destructive' : 'default'}
              disabled={vulnActionId === triageVuln?.id
                || !triagePreview
                || (triageVuln?.intent === 'false_positive' && !triageReason.trim())}
              onClick={handlePromoteVuln}
            >
              {triageVuln?.intent === 'confirmed' ? 'Promote' : 'Dismiss'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Note-details editor — a thread's type and pin. */}
      <Dialog open={detailsNote !== null} onOpenChange={(v) => { if (!v) setDetailsNote(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Note details</DialogTitle>
            <DialogDescription>
              Label what kind of thread this is, and pin it to keep it at the top of the discussion.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-sm">
            <div className="space-y-xxs">
              <Label htmlFor="note-type">Type</Label>
              <Select value={detailsType} onValueChange={setDetailsType}>
                <SelectTrigger id="note-type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— none —</SelectItem>
                  {NOTE_TYPES.map((t) => (
                    <SelectItem key={t} value={t} className="capitalize">{t}</SelectItem>
                  ))}
                  {detailsNote?.note_type && !(NOTE_TYPES as readonly string[]).includes(detailsNote.note_type) && (
                    <SelectItem value={detailsNote.note_type} className="capitalize">
                      {detailsNote.note_type} (older label)
                    </SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
            <Button
              type="button"
              variant={detailsPinned ? 'default' : 'outline'}
              size="sm"
              onClick={() => setDetailsPinned((v) => !v)}
              aria-pressed={detailsPinned}
            >
              {detailsPinned ? 'Pinned' : 'Pin to top'}
            </Button>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDetailsNote(null)} disabled={detailsSaving}>
              Cancel
            </Button>
            <Button onClick={handleSaveNoteDetails} disabled={detailsSaving}>
              {detailsSaving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Scan discovery timeline — audit evidence, relevant occasionally, so it
          lives at the bottom with a show-all expander (was pinned in the header,
          capped at 3). */}
      <DiscoveryTimelineCard discoveries={discoveryTimeline} />

    </div>
    </HostTestsProvider>
  );
};

export default HostInspector;
