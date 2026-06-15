/**
 * HostInspector — the data-bearing body of the host detail surface.
 *
 * Renders host overview, proposed tests, vulnerabilities, notes
 * (threaded), data conflicts (when toggled), workflow lineage, and
 * port details.  Owns every API call: getHost, getHostConflicts,
 * getHostTestPlanEntries, getHostFollowers, and the per-action
 * mutations (follow, note CRUD, test-plan-entry status / findings).
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
import { SEVERITY_RANK, SEVERITY_BADGE_VARIANT, SEVERITY_HSL, type Severity } from '../utils/severity';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Bookmark,
  BookmarkPlus,
  Ban,
  CheckCircle2,
  ClipboardList,
  Computer,
  Copy,
  ExternalLink,
  Eye,
  Flag,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Network,
  NotebookPen,
  RefreshCw,
  Reply,
  RotateCcw,
  ShieldAlert,
  Terminal,
  Trash2,
  X,
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
  promoteAnnotation,
  promoteVulnerability,
  previewPromoteVulnerability,
  recordHostView,
  getHostTestPlanEntries,
  updateTestPlanEntry,
  getHostFollowers,
  listProjectMembers,
} from '../services/api';
import type {
  Host,
  HostConflict,
  ConflictHistoryEntry,
  FollowStatus,
  Annotation,
  NoteStatus,
  NoteType,
  HostTestPlanEntry,
  ProposedTestObject,
  HostFollowerEntry,
  FindingSeverity,
  PromoteVulnerabilityPreview,
  ProjectMember,
  ReviewConclusion,
} from '../services/api';
import { getHostWebLinks, HostWebLink } from '../utils/webLinks';
import { getConnectionHelpers, ConnectionHelper } from '../utils/connectionHelpers';
import { StructuredTestCard } from './ProposedTestList';
import EntryResultsPanel from './EntryResultsPanel';
import WebInterfacesCard from './WebInterfacesCard';
import NseScriptsCard from './NseScriptsCard';
import NetExecCard from './NetExecCard';
import HostFindingsCard from './HostFindingsCard';
import HostDnsRecordsCard from './HostDnsRecordsCard';
import HostLineagePanel from './HostLineagePanel';
import { NoteThread } from './host-inspector/NoteThread';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import { cn } from '../utils/cn';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { DetailSkeleton } from './PageSkeleton';
import { useConfirm } from '../hooks/useConfirm';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from './ui/accordion';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { Textarea } from './ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

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

const VULNERABILITY_PREVIEW_LIMIT = 10;

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

const NOTE_STATUS_META: Record<
  NoteStatus,
  { label: string; badgeVariant: 'muted' | 'info' | 'warning' | 'success' }
> = {
  open: { label: 'Open', badgeVariant: 'info' },
  in_progress: { label: 'In Progress', badgeVariant: 'warning' },
  resolved: { label: 'Resolved', badgeVariant: 'success' },
};

const stateBadgeVariant = (
  state: string | null,
): 'success' | 'destructive' | 'warning' | 'outline' => {
  switch (state) {
    case 'up':
    case 'open':
      return 'success';
    case 'down':
    case 'closed':
      return 'destructive';
    case 'filtered':
      return 'warning';
    default:
      return 'outline';
  }
};

const confidenceBadgeVariant = (score: number): 'success' | 'warning' | 'destructive' => {
  if (score >= 90) return 'success';
  if (score >= 70) return 'warning';
  return 'destructive';
};

const formatDateTime = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : 'Unknown date';

export interface HostInspectorProps {
  hostId: number;
  /**
   * Visual density of the IP heading.  `page` (default) uses the full
   * `text-page-title`; `sheet` drops to `text-section-title` for use
   * inside a SideSheet whose own header is more compact.
   */
  density?: 'page' | 'sheet';
  /**
   * Called when the inspector loads the host successfully.  Useful
   * for the parent (e.g. SideSheet header) to show host metadata
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
}

export const HostInspector: React.FC<HostInspectorProps> = ({
  hostId,
  density = 'page',
  onHostLoaded,
  onFollowChange,
}) => {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasPermission, user } = useAuth();
  const canManageEntries = hasPermission('analyst');
  const [host, setHost] = useState<Host | null>(null);
  const [conflicts, setConflicts] = useState<HostConflict[]>([]);
  const [conflictHistory, setConflictHistory] = useState<ConflictHistoryEntry[]>([]);
  // Canonical conflict count from the API (same definition as the Hosts-list
  // badge).  The old "N conflicts" derived from `conflicts.length` (per-field
  // confidence records, host + port) — a different number that disagreed with
  // the list badge.
  const [conflictCount, setConflictCount] = useState(0);
  const [showConflicts, setShowConflicts] = useState(false);
  const [conflictsError, setConflictsError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [followStatus, setFollowStatus] = useState<FollowStatus | ''>('');
  const [followLoading, setFollowLoading] = useState(false);
  // §9 review-completion dialog (opened by "Mark reviewed").
  const [reviewCompletionOpen, setReviewCompletionOpen] = useState(false);
  const [reviewConclusion, setReviewConclusion] = useState<ReviewConclusion>('no_issue');
  const [reviewSummaryText, setReviewSummaryText] = useState('');
  const [notes, setNotes] = useState<Annotation[]>([]);
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
  const [pendingImages, setPendingImages] = useState<{ file: File; url: string }[]>([]);
  const [noteStatus, setNoteStatus] = useState<NoteStatus>('open');
  const [noteSubmitting, setNoteSubmitting] = useState(false);
  const [replyTo, setReplyTo] = useState<{ id: number; author: string } | null>(null);
  const [replyBody, setReplyBody] = useState('');
  const [noteError, setNoteError] = useState<string | null>(null);
  // Promote-note-to-finding dialog state (foundation 6b).
  const [promoteNoteId, setPromoteNoteId] = useState<number | null>(null);
  const [promoteSeverity, setPromoteSeverity] = useState<FindingSeverity>('medium');
  // §12 — let the analyst confirm the finding title + owner before promoting
  // (the note's first line / assignee are just defaults).
  const [promoteTitle, setPromoteTitle] = useState('');
  const [promoteOwnerId, setPromoteOwnerId] = useState<number | 'none'>('none');
  const [promoting, setPromoting] = useState(false);
  // Bumped after a promote so the inline HostFindingsCard refetches.
  const [findingsRefresh, setFindingsRefresh] = useState(0);
  // Note-details editor (type/assignee/due/pin) — the write path for the
  // thread work fields the My Work queue groups by.
  const [detailsNote, setDetailsNote] = useState<Annotation | null>(null);
  const [detailsType, setDetailsType] = useState<string>('none');
  const [detailsAssignee, setDetailsAssignee] = useState<string>('none');
  const [detailsDue, setDetailsDue] = useState<string>('');
  const [detailsPinned, setDetailsPinned] = useState(false);
  // Port-table sort by port number (null = scan order). Shared across the
  // open/closed/filtered port tables so they stay consistent.
  const [portSortDir, setPortSortDir] = useState<'asc' | 'desc' | null>(null);
  const [detailsSaving, setDetailsSaving] = useState(false);
  const [members, setMembers] = useState<ProjectMember[]>([]);

  // Lazy-load the project roster the first time the details editor opens
  // (drives the assignee picker); cheap and avoids a fetch on every host open.
  const openNoteDetails = (note: Annotation) => {
    setDetailsNote(note);
    setDetailsType(note.note_type || 'none');
    setDetailsAssignee(note.assignee_id != null ? String(note.assignee_id) : 'none');
    setDetailsDue(note.due_at ? note.due_at.slice(0, 10) : '');
    setDetailsPinned(!!note.pinned);
    if (members.length === 0) {
      listProjectMembers()
        .then(setMembers)
        .catch(() => { /* assignee picker just stays empty */ });
    }
  };

  const handleSaveNoteDetails = async () => {
    if (!detailsNote) return;
    setDetailsSaving(true);
    try {
      const updated = await updateAnnotation(hostId, detailsNote.id, {
        note_type: detailsType === 'none' ? null : (detailsType as NoteType),
        assignee_id: detailsAssignee === 'none' ? null : Number(detailsAssignee),
        // Date input is YYYY-MM-DD; send an ISO timestamp (or null to clear).
        due_at: detailsDue ? new Date(detailsDue).toISOString() : null,
        pinned: detailsPinned,
      });
      setNotes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
      toast.success('Note details updated.');
      setDetailsNote(null);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update note details.'));
    } finally {
      setDetailsSaving(false);
    }
  };

  // Promote / dismiss a scanner vulnerability as a finding (status 'confirmed'
  // promotes; a terminal status dismisses). Idempotent server-side.
  const [vulnActionId, setVulnActionId] = useState<number | null>(null);
  // vulnId → finding id, optimistically set after a promote/dismiss so the
  // row shows "Promoted" immediately (the host's vuln rows only carry the
  // authoritative finding_id on the next host load).
  const [promotedVulns, setPromotedVulns] = useState<Record<number, number>>({});
  // §11 — triage a scanner vuln through a confirm step that previews the
  // cross-host blast radius first (promotion attaches EVERY project host
  // sharing the plugin_id — an icon-click used to do that silently).
  const [triageVuln, setTriageVuln] = useState<
    { id: number; title: string; intent: 'confirmed' | 'false_positive' } | null
  >(null);
  const [triagePreview, setTriagePreview] = useState<PromoteVulnerabilityPreview | null>(null);
  const [triagePreviewLoading, setTriagePreviewLoading] = useState(false);
  const [triageReason, setTriageReason] = useState('');

  // Fetch the blast radius whenever a triage opens.
  useEffect(() => {
    if (!triageVuln) { setTriagePreview(null); return; }
    let cancelled = false;
    setTriagePreviewLoading(true);
    setTriagePreview(null);
    previewPromoteVulnerability(triageVuln.id)
      .then((p) => { if (!cancelled) setTriagePreview(p); })
      .catch(() => { /* dialog still works; just no preview */ })
      .finally(() => { if (!cancelled) setTriagePreviewLoading(false); });
    return () => { cancelled = true; };
  }, [triageVuln]);

  const handlePromoteVuln = async () => {
    if (!triageVuln) return;
    const { id: vulnId, intent } = triageVuln;
    const reason = triageReason.trim();
    setVulnActionId(vulnId);
    try {
      const finding = await promoteVulnerability(vulnId, {
        status: intent,
        summary: reason || undefined,
      });
      // Scanner findings span every host with the same plugin — report it.
      const span = finding.host_count > 1 ? ` across ${finding.host_count} hosts` : '';
      toast.success(
        intent === 'confirmed'
          ? `Promoted to finding${span}: ${finding.title}`
          : `Dismissed as false positive${span}: ${finding.title}`,
        { autoHideMs: 3000 },
      );
      setPromotedVulns((prev) => ({ ...prev, [vulnId]: finding.id }));
      setFindingsRefresh((n) => n + 1);
      setTriageVuln(null);
      setTriageReason('');
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update vulnerability.'));
    } finally {
      setVulnActionId(null);
    }
  };
  const openTriage = (vulnId: number, title: string, intent: 'confirmed' | 'false_positive') => {
    setTriageReason('');
    setTriageVuln({ id: vulnId, title, intent });
  };

  const handlePromoteNote = async () => {
    if (promoteNoteId === null) return;
    setPromoting(true);
    try {
      const finding = await promoteAnnotation(promoteNoteId, {
        severity: promoteSeverity,
        title: promoteTitle.trim() || undefined,
        owner_id: promoteOwnerId === 'none' ? null : promoteOwnerId,
      });
      toast.success(`Promoted to finding: ${finding.title}`, { autoHideMs: 3000 });
      // Optimistically mark the note promoted so its badge appears + the
      // promote affordance hides without waiting for a host reload.
      setNotes((prev) => prev.map((n) => (n.id === promoteNoteId ? { ...n, finding_id: finding.id } : n)));
      setPromoteNoteId(null);
      setFindingsRefresh((n) => n + 1);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to promote note to finding.'));
    } finally {
      setPromoting(false);
    }
  };
  const [noteActionId, setNoteActionId] = useState<number | null>(null);
  // Resolution-summary capture (replaces window.prompt): the note id being
  // resolved + its in-progress summary text.
  const [resolvePrompt, setResolvePrompt] = useState<number | null>(null);
  const [resolveText, setResolveText] = useState('');
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
  const [testPlanEntries, setTestPlanEntries] = useState<HostTestPlanEntry[]>([]);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [testPlanError, setTestPlanError] = useState(false);
  const [findingsDrafts, setFindingsDrafts] = useState<Record<number, string>>({});
  const [findingsOpen, setFindingsOpen] = useState<Record<number, boolean>>({});
  const [savingEntryId, setSavingEntryId] = useState<number | null>(null);
  const [otherFollowers, setOtherFollowers] = useState<HostFollowerEntry[]>([]);
  const [followersError, setFollowersError] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const [confirmEl, confirm] = useConfirm();

  // Monotonic counter to guard against stale responses during rapid navigation.
  const fetchIdRef = React.useRef(0);
  const onHostLoadedRef = React.useRef(onHostLoaded);
  onHostLoadedRef.current = onHostLoaded;

  // Deep-link to an exact note: when the URL carries #note-<id> (from the
  // Activity feed / mentions / a finding's evidence link), scroll that note
  // into view once the thread has rendered and flash a highlight. Runs once
  // per hash so adding a note later doesn't re-trigger it.
  //
  // MUST live above the loading/!host early returns below — a hook placed
  // after them runs only on some renders (React error #310).
  const consumedNoteHashRef = React.useRef<string | null>(null);
  useEffect(() => {
    if (typeof window === 'undefined' || notes.length === 0) return;
    const hash = window.location.hash;
    const match = hash.match(/^#note-(\d+)$/);
    if (!match || consumedNoteHashRef.current === hash) return;
    const el = document.getElementById(`note-${match[1]}`);
    if (!el) return; // target not in this host's thread — leave the hash be
    consumedNoteHashRef.current = hash;
    requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('ring-2', 'ring-info', 'ring-offset-2', 'rounded-control');
      window.setTimeout(
        () => el.classList.remove('ring-2', 'ring-info', 'ring-offset-2', 'rounded-control'),
        2400,
      );
    });
  }, [notes]);

  const refetchTestPlanEntries = React.useCallback(async () => {
    const fetchId = fetchIdRef.current;
    setTestPlanError(false);
    try {
      const tpEntries = await getHostTestPlanEntries(hostId);
      if (fetchId === fetchIdRef.current) setTestPlanEntries(tpEntries);
    } catch (err) {
      if (fetchId === fetchIdRef.current) setTestPlanError(true);
    }
  }, [hostId]);

  useEffect(() => {
    const fetchId = ++fetchIdRef.current;
    setLoading(true);
    setFetchError(null);
    setTestPlanError(false);
    setFollowersError(false);
    setShowAllVulnerabilities(false);
    setShowConflicts(false);
    setNoteBody('');
    setNoteStatus('open');
    setNoteError(null);
    setReplyTo(null);
    setReplyBody('');

    // Audit PRF·H8: previously a single Promise.all blocked the host
    // panel on the slowest of three fetches.  Now the primary host
    // fetch releases the loading skeleton; conflicts and test-plan
    // entries land into their subsections as they resolve.
    const fetchHost = async () => {
      try {
        const hostData = await getHost(hostId);
        if (fetchId !== fetchIdRef.current) return;
        setHost(hostData);
        setFollowStatus(hostData.follow?.status ?? '');
        setNotes(hostData.notes ?? []);
        onHostLoadedRef.current?.(hostData);
      } catch (err: unknown) {
        if (fetchId !== fetchIdRef.current) return;
        console.error('Error fetching host details:', err);
        const status = asAxiosError(err).response?.status;
        if (status === 404) setFetchError('Host not found');
        else if (status === 401 || status === 403)
          setFetchError('You do not have permission to view this host');
        else setFetchError('Failed to load host details. The server may be unavailable.');
      } finally {
        if (fetchId === fetchIdRef.current) setLoading(false);
      }
    };

    fetchHost();

    // Secondary panels — fire in parallel with the primary fetch.
    // Each writes only into its own subsection, so the main host
    // panel renders the instant getHost() resolves.
    getHostConflicts(hostId)
      .then((conflictData) => {
        if (fetchId !== fetchIdRef.current) return;
        setConflicts(conflictData?.confidence || []);
        setConflictHistory(conflictData?.conflict_history || []);
        setConflictCount(conflictData?.conflict_count ?? 0);
        setConflictsError(false);
      })
      .catch(() => {
        // getHostConflicts already swallows 404 (older deployments), so a
        // rejection here is a real failure — surface it instead of letting an
        // empty list read as "no conflicts" (a data-quality false negative).
        if (fetchId === fetchIdRef.current) setConflictsError(true);
      });

    getHostTestPlanEntries(hostId)
      .then((tpEntries) => {
        if (fetchId !== fetchIdRef.current) return;
        setTestPlanEntries(tpEntries);
      })
      .catch(() => {
        if (fetchId === fetchIdRef.current) setTestPlanError(true);
      });

    recordHostView(hostId).catch(() => {});

    getHostFollowers(hostId)
      .then((data) => {
        if (fetchId === fetchIdRef.current) {
          setOtherFollowers(data.followers ?? []);
          setFollowersError(false);
        }
      })
      .catch(() => {
        if (fetchId === fetchIdRef.current) {
          setOtherFollowers([]);
          setFollowersError(true);
        }
      });
  }, [hostId, retryNonce]);

  const updateFollow = async (
    status: FollowStatus | 'none',
    review?: { review_conclusion?: ReviewConclusion; review_summary?: string },
  ) => {
    setFollowLoading(true);
    try {
      if (status === 'none') {
        await unfollowHost(hostId);
        setFollowStatus('');
        setHost((previous) => (previous ? { ...previous, follow: null } : previous));
        onFollowChange?.(hostId, null);
        toast.info('Removed from your follow list', { autoHideMs: 2000 });
      } else {
        const response = await followHost(hostId, status, review);
        setFollowStatus(response.status);
        setHost((previous) => (previous ? { ...previous, follow: response } : previous));
        onFollowChange?.(hostId, response);
        toast.success(`Marked as ${FOLLOW_STATUS_META[status].label}`, { autoHideMs: 2000 });
      }
    } catch (err) {
      console.error('Failed to update follow status:', err);
      toast.error('Failed to update follow status. Please try again.');
    } finally {
      setFollowLoading(false);
    }
  };

  const openReviewCompletion = () => {
    setReviewConclusion('no_issue');
    setReviewSummaryText('');
    setReviewCompletionOpen(true);
  };
  const submitReviewCompletion = () => {
    setReviewCompletionOpen(false);
    void updateFollow('reviewed', {
      review_conclusion: reviewConclusion,
      review_summary: reviewSummaryText.trim() || undefined,
    });
  };

  const handleEntryStatusChange = async (entry: HostTestPlanEntry, newStatus: string) => {
    if (newStatus === entry.status) return;
    setSavingEntryId(entry.id);
    try {
      const updated = await updateTestPlanEntry(entry.test_plan_id, entry.id, {
        status: newStatus,
        expected_updated_at: entry.updated_at,
      });
      setTestPlanEntries((prev) =>
        prev.map((e) =>
          e.id === entry.id
            ? { ...e, status: updated.status as string, updated_at: updated.updated_at as string }
            : e,
        ),
      );
      toast.success(`Marked ${newStatus.replace('_', ' ')}`, {
        id: `host-entry-status-${entry.id}`,
        autoHideMs: 2000,
      });
    } catch (err: unknown) {
      console.error('Failed to update test plan entry status:', err);
      toast.error(formatApiError(err, 'Failed to update entry status.'), {
        id: `host-entry-status-${entry.id}-err`,
      });
    } finally {
      setSavingEntryId(null);
    }
  };

  const handleSaveFindings = async (entry: HostTestPlanEntry) => {
    const draft = (findingsDrafts[entry.id] ?? '').trim();
    setSavingEntryId(entry.id);
    try {
      const updated = await updateTestPlanEntry(entry.test_plan_id, entry.id, {
        findings: draft || undefined,
        expected_updated_at: entry.updated_at,
      });
      setTestPlanEntries((prev) =>
        prev.map((e) =>
          e.id === entry.id
            ? { ...e, findings: draft || undefined, updated_at: updated.updated_at as string }
            : e,
        ),
      );
      setFindingsOpen((prev) => ({ ...prev, [entry.id]: false }));
      toast.success('Findings saved', { autoHideMs: 2000 });
    } catch (err: unknown) {
      console.error('Failed to save findings:', err);
      toast.error(formatApiError(err, 'Failed to save findings.'));
    } finally {
      setSavingEntryId(null);
    }
  };

  const toggleFindings = (entry: HostTestPlanEntry) => {
    setFindingsOpen((prev) => ({ ...prev, [entry.id]: !prev[entry.id] }));
    setFindingsDrafts((prev) => {
      if (entry.id in prev) return prev;
      return { ...prev, [entry.id]: entry.findings ?? '' };
    });
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

  const handleCreateNote = async () => {
    if (!noteBody.trim()) {
      setNoteError('Add a short note before saving.');
      return;
    }
    setNoteSubmitting(true);
    try {
      const response = await createAnnotation(hostId, {
        body: noteBody.trim(),
        status: noteStatus,
      });
      // Upload any pasted/attached images to the new note, then attach the
      // resulting metadata so the thumbnails show without a reload.
      const uploaded = [];
      for (const { file } of pendingImages) {
        try {
          uploaded.push(await uploadNoteAttachment(hostId, response.id, file));
        } catch (e) {
          console.error('Failed to upload pasted image:', e);
        }
      }
      const noteWithImages = uploaded.length ? { ...response, attachments: uploaded } : response;
      setNotes((previous) => [noteWithImages, ...previous]);
      setHost((previous) =>
        previous ? { ...previous, notes: [noteWithImages, ...(previous.notes ?? [])] } : previous,
      );
      pendingImages.forEach((p) => URL.revokeObjectURL(p.url));
      setPendingImages([]);
      setNoteBody('');
      setNoteStatus('open');
      setNoteError(null);
    } catch (err) {
      console.error('Failed to save note:', err);
      setNoteError('Unable to save note right now. Please try again.');
    } finally {
      setNoteSubmitting(false);
    }
  };

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
    setNoteActionId(noteId);
    try {
      await deleteAnnotation(hostId, noteId);
      setNotes((previous) => previous.filter((note) => note.id !== noteId));
      setHost((previous) =>
        previous
          ? { ...previous, notes: (previous.notes ?? []).filter((note) => note.id !== noteId) }
          : previous,
      );
      toast.success('Note deleted.');
    } catch (err) {
      // Pre-audit (C8): console.error only — user clicked Trash and
      // the note stayed in the list with no signal whether the click
      // did anything.
      toast.error(formatApiError(err, 'Failed to delete note.'));
    } finally {
      setNoteActionId(null);
    }
  };

  const handleReply = async () => {
    if (!replyTo || !replyBody.trim()) return;
    setNoteSubmitting(true);
    try {
      const newNote = await createAnnotation(hostId, {
        body: replyBody.trim(),
        status: 'open',
        parent_id: replyTo.id,
      });
      setNotes((prev) => [newNote, ...prev]);
      setReplyTo(null);
      setReplyBody('');
      toast.success('Reply posted.');
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to post reply.'));
    } finally {
      setNoteSubmitting(false);
    }
  };

  const doUpdateNoteStatus = async (
    noteId: number, status: NoteStatus, resolutionSummary?: string,
  ) => {
    setNoteActionId(noteId);
    try {
      const response = await updateAnnotation(hostId, noteId, {
        status,
        ...(resolutionSummary ? { resolution_summary: resolutionSummary } : {}),
      });
      setNotes((previous) => previous.map((note) => (note.id === noteId ? response : note)));
      setHost((previous) =>
        previous
          ? {
              ...previous,
              notes: (previous.notes ?? []).map((note) => (note.id === noteId ? response : note)),
            }
          : previous,
      );
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update note status.'));
    } finally {
      setNoteActionId(null);
    }
  };

  const handleUpdateNoteStatus = (noteId: number, status: NoteStatus) => {
    // Resolving a thread requires a summary (the backend rejects a
    // summary-less resolve with 400).  Capture it in an accessible dialog
    // (consistent styling/validation) instead of a native window.prompt.
    if (status === 'resolved') {
      setResolveText('');
      setResolvePrompt(noteId);
      return;
    }
    void doUpdateNoteStatus(noteId, status);
  };

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
          <Button onClick={() => setRetryNonce((n) => n + 1)}>
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
  const conflictsByField = conflicts.reduce(
    (acc, conflict) => {
      if (!acc[conflict.field_name]) acc[conflict.field_name] = [];
      acc[conflict.field_name].push(conflict);
      return acc;
    },
    {} as Record<string, HostConflict[]>,
  );

  // (noteThreadGroups useMemo hoisted to the top of the component body —
  // see line ~221.  Pre-fix it sat below the loading/!host early returns
  // and triggered React error #310 "Rendered more hooks than during the
  // previous render" on the first post-load render.)

  const webLinks: HostWebLink[] = getHostWebLinks(host);
  const primaryWebLink = webLinks[0] ?? null;
  const openPorts = host.ports.filter((port) => port.state === 'open');
  // connectionHelpersByPort is computed once per host above the early
  // returns (see note near noteThreadGroups) to keep the hook count stable.
  const closedPorts = host.ports.filter((port) => port.state === 'closed');
  const filteredPorts = host.ports.filter((port) => port.state === 'filtered');
  const sortPorts = <T extends { port_number: number | null }>(arr: T[]): T[] => {
    if (!portSortDir) return arr;
    const s = [...arr].sort((a, b) => (a.port_number ?? 0) - (b.port_number ?? 0));
    return portSortDir === 'desc' ? s.reverse() : s;
  };
  const PortSortHead: React.FC<{ className?: string }> = ({ className }) => (
    <TableHead className={className}
      aria-sort={portSortDir ? (portSortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button"
        onClick={() => setPortSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
        className="inline-flex items-center gap-xxs rounded text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        Port
        {portSortDir
          ? (portSortDir === 'asc' ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />)
          : <ArrowUpDown className="size-3 opacity-40" aria-hidden />}
      </button>
    </TableHead>
  );
  const followInfo = host.follow;
  const followHelperText = followStatus
    ? FOLLOW_STATUS_META[followStatus].description
    : 'Select a review status to keep track of this host.';
  const discoveryTimeline = host.discoveries ?? [];

  const toTimestamp = (value: string | null | undefined) =>
    value ? new Date(value).getTime() : 0;
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
  const totalVulnerabilities =
    host.vulnerability_summary?.total_vulnerabilities ?? sortedVulnerabilities.length;
  const vulnSummaryError = host.vulnerability_summary?.error === true;
  const displayedVulnerabilities = showAllVulnerabilities
    ? sortedVulnerabilities
    : sortedVulnerabilities.slice(0, VULNERABILITY_PREVIEW_LIMIT);
  const hasVulnerabilities = sortedVulnerabilities.length > 0;

  const TESTER_STATUSES = [
    { value: 'in_progress', label: 'In Progress' },
    { value: 'completed', label: 'Completed' },
    { value: 'rejected', label: 'Rejected' },
  ];
  const STATUS_LABEL: Record<string, string> = {
    proposed: 'Proposed',
    approved: 'Approved',
    in_progress: 'In Progress',
    completed: 'Completed',
    rejected: 'Rejected',
  };

  const entriesWithTests = testPlanEntries.filter(
    (entry) => (entry.proposed_tests?.length ?? 0) > 0,
  );
  const totalProposedTests = entriesWithTests.reduce(
    (sum, e) => sum + (e.proposed_tests?.length ?? 0),
    0,
  );

  // v4.55.0 — triage summary strip counts.  Derived from already-
  // loaded state so the strip never blocks first paint; the test-plan
  // cell appears once the async fetch resolves (which is the same
  // shape as the Proposed Tests card below).
  const testPlanCounts = {
    in_progress: testPlanEntries.filter((e) => e.status === 'in_progress').length,
    completed: testPlanEntries.filter((e) => e.status === 'completed').length,
    pending: testPlanEntries.filter(
      (e) => e.status === 'proposed' || e.status === 'approved',
    ).length,
  };

  // v4.55.0 — intra-page jump helper.  Each card below carries
  // ``id="host-detail-{section}"`` so the triage strip cells and
  // Host Overview counts can scroll to them.  Smooth-scroll with
  // a soft offset so the section header lands just under the
  // top chrome instead of flush with the viewport edge.
  const scrollToSection = (id: string) => {
    if (typeof document === 'undefined') return;
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const titleClasses = density === 'sheet' ? 'text-section-title' : 'text-page-title';
  const titleIconClass = density === 'sheet' ? 'size-5' : 'size-6';

  return (
    <div className="space-y-md">
      {confirmEl}
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
        {hasConflicts ? (
          <Button
            variant={showConflicts ? 'default' : 'outline'}
            size="sm"
            aria-expanded={showConflicts}
            aria-controls="host-detail-conflicts"
            onClick={() => {
              const opening = !showConflicts;
              setShowConflicts(opening);
              // The detail panel renders near the bottom of the inspector and
              // is conditionally mounted, so on open it appears off-screen and
              // the click reads as a no-op ("where is this info?").  Scroll to
              // it once it has mounted (two rAFs = after the commit + layout).
              if (opening) {
                requestAnimationFrame(() =>
                  requestAnimationFrame(() => scrollToSection('host-detail-conflicts')),
                );
              }
            }}
          >
            <AlertTriangle className="size-4" aria-hidden />
            {conflictCount} conflict{conflictCount === 1 ? '' : 's'}
          </Button>
        ) : conflictsError ? (
          <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground" title="The data-conflict check failed to load — this is not a confirmation that the host has none">
            <AlertTriangle className="size-3.5" aria-hidden />
            Couldn&apos;t check conflicts
          </span>
        ) : null}
      </div>

      {host.hostname && (
        <p className="-mt-sm truncate text-metadata text-muted-foreground" title={host.hostname}>
          {host.hostname}
        </p>
      )}

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
        <CardContent className="grid gap-md pt-md md:grid-cols-12">
          <div className="space-y-sm md:col-span-7">
            {/* Identity — labelled key/value, not a badge soup. Colour is
                reserved for genuine alerts (SMB disabled, unassigned owner). */}
            <dl className="grid gap-x-lg gap-y-sm sm:grid-cols-2">
              <div className="flex gap-sm">
                <dt className="w-14 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">State</dt>
                <dd className="min-w-0 text-metadata">
                  <span className="capitalize text-foreground">{host.state || 'unknown'}</span>
                  {host.state_reason && (
                    <span className="text-caption text-muted-foreground" title={`State reason: ${host.state_reason}`}> · {host.state_reason}</span>
                  )}
                </dd>
              </div>
              <div className="flex gap-sm">
                <dt className="w-14 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">OS</dt>
                <dd className="min-w-0 truncate text-metadata text-foreground"
                  title={[
                    [host.os_family, host.os_type, host.os_generation].filter(Boolean).join(' · '),
                    host.os_accuracy != null && host.os_accuracy !== '' && Number(host.os_accuracy) < 70
                      ? `Low-confidence OS guess (${Number(host.os_accuracy)}% match)`
                      : '',
                  ].filter(Boolean).join(' — ') || undefined}>
                  {host.os_name ? (() => {
                    // De-weight a low-confidence guess so a 60% match doesn't read
                    // as authoritatively as a 98% one.
                    const acc = host.os_accuracy != null && host.os_accuracy !== ''
                      ? Number(host.os_accuracy) : null;
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
              <div className="flex gap-sm">
                <dt className="w-14 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">SMB</dt>
                <dd className="min-w-0 text-metadata">
                  {host.smb_signing === 'disabled' ? (
                    <span className="inline-flex items-center gap-xxs font-medium text-destructive" title="SMB message signing disabled — NTLM relay-vulnerable">
                      <AlertTriangle className="size-3.5" aria-hidden /> Signing disabled
                    </span>
                  ) : host.smb_signing === 'enabled' ? (
                    <span className="text-warning" title="SMB signing enabled but not required">Signing enabled (not required)</span>
                  ) : host.smb_signing === 'required' ? (
                    <span className="text-foreground">Signing required</span>
                  ) : <span className="text-muted-foreground">—</span>}
                </dd>
              </div>
              <div className="flex gap-sm">
                <dt className="w-14 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">Owner</dt>
                <dd className="min-w-0 truncate text-metadata">
                  {host.assignees && host.assignees.length > 0 ? (
                    <span className="text-foreground" title={host.assignees.map((a) => a.name).join(', ')}>
                      {host.assignees.map((a) => a.name).join(', ')}
                    </span>
                  ) : <span className="text-warning">unassigned</span>}
                </dd>
              </div>
              {host.tags && host.tags.length > 0 && (
                <div className="flex gap-sm sm:col-span-2">
                  <dt className="w-14 shrink-0 text-caption uppercase tracking-wide text-muted-foreground">Tags</dt>
                  <dd className="flex min-w-0 flex-wrap gap-xxs">
                    {host.tags.map((tag) => (
                      <span key={tag.id} className="rounded-chip border border-border px-xs text-caption text-foreground"
                        style={tag.color ? { borderColor: tag.color, color: tag.color } : undefined} title={tag.name}>
                        {tag.name}
                      </span>
                    ))}
                  </dd>
                </div>
              )}
            </dl>

            {/* At a glance — actionable counts as quiet linked stats. Severity
                numbers carry colour (genuine alerts); the rest stay muted. */}
            <div className="flex flex-wrap items-center gap-x-md gap-y-xs border-t border-border pt-sm text-caption text-muted-foreground">
              <button type="button" onClick={() => scrollToSection('host-detail-ports')}
                className="rounded hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <strong className="text-foreground">{openPorts.length}</strong> open
                <span className="opacity-70"> / {host.ports.length} ports</span>
              </button>
              {host.vulnerability_summary && host.vulnerability_summary.total_vulnerabilities > 0 && (
                <button type="button" onClick={() => scrollToSection('host-detail-vulnerabilities')}
                  className="inline-flex items-center gap-sm rounded hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  {(['critical', 'high', 'medium', 'low', 'info'] as const)
                    .filter((k) => (host.vulnerability_summary?.[k] ?? 0) > 0)
                    .map((k) => (
                      <span key={k}>
                        <strong style={{ color: SEVERITY_HSL[k] }}>{host.vulnerability_summary?.[k]}</strong>{' '}{k}
                      </span>
                    ))}
                </button>
              )}
              {(host.web_interface_count ?? 0) > 0 && (
                <button type="button" onClick={() => scrollToSection('host-detail-web')}
                  className="rounded hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <strong className="text-foreground">{host.web_interface_count}</strong> web
                </button>
              )}
              {notes.length > 0 && (
                <button type="button" onClick={() => scrollToSection('host-detail-notes')}
                  className="rounded hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <strong className="text-foreground">{notes.length}</strong> note{notes.length === 1 ? '' : 's'}
                </button>
              )}
              {(testPlanCounts.in_progress + testPlanCounts.pending + testPlanCounts.completed) > 0 && (
                <button type="button" onClick={() => scrollToSection('host-detail-proposed-tests')}
                  className="inline-flex items-center gap-xxs rounded hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <ClipboardList className="size-3.5" aria-hidden />
                  <strong className="text-foreground">{testPlanCounts.in_progress}</strong> in progress
                  {testPlanCounts.pending > 0 && <> · <strong className="text-foreground">{testPlanCounts.pending}</strong> proposed</>}
                </button>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-sm pt-xxs">
              <div className="flex items-center gap-xs">
                {followStatus ? (
                  <Bookmark
                    className={cn(
                      'size-4',
                      followStatus === 'reviewed'
                        ? 'text-success'
                        : followStatus === 'in_review'
                          ? 'text-warning'
                          : 'text-info',
                    )}
                    aria-hidden
                  />
                ) : (
                  <BookmarkPlus className="size-4 text-muted-foreground" aria-hidden />
                )}
                <Badge
                  variant={
                    followStatus ? FOLLOW_STATUS_META[followStatus].badgeVariant : 'outline'
                  }
                >
                  {followStatus ? FOLLOW_STATUS_META[followStatus].label : 'Not reviewed'}
                </Badge>
                {followStatus === 'reviewed' && followInfo?.review_conclusion && (
                  <span className="text-caption font-medium text-foreground"
                    title={followInfo.review_summary ?? undefined}>
                    {REVIEW_CONCLUSION_LABEL[followInfo.review_conclusion]
                      ?? followInfo.review_conclusion}
                  </span>
                )}
                {followInfo && (
                  <span className="text-caption text-muted-foreground">
                    Updated{' '}
                    {new Date(
                      followInfo.updated_at ?? followInfo.created_at,
                    ).toLocaleString()}
                  </span>
                )}
              </div>
              {/* §6/§9 — one state-aware review control (replaces the status
                  dropdown): primary action for the common path + an overflow
                  for the off-path transitions, so nothing the dropdown did is
                  lost (mark-reviewed-direct, clear status). */}
              <div className="flex items-center gap-xs">
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
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="icon" variant="ghost" disabled={followLoading}
                      aria-label="More review actions">
                      <MoreHorizontal className="size-4" aria-hidden />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {followStatus !== 'in_review' && followStatus !== 'reviewed' && (
                      <DropdownMenuItem onClick={openReviewCompletion}>
                        Mark reviewed…
                      </DropdownMenuItem>
                    )}
                    {followStatus && (
                      <DropdownMenuItem onClick={() => updateFollow('none')}>
                        Clear review status
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              <span className="text-caption text-muted-foreground">{followHelperText}</span>
            </div>

            {otherFollowers.length > 0 && (
              <div className="rounded-control border border-border bg-muted/30 p-sm">
                <p className="mb-xxs text-caption text-muted-foreground">Also reviewing this host</p>
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

          <div className="md:col-span-5">
            {discoveryTimeline.length > 0 && (
              <div className="rounded-control border border-border bg-muted/30 p-sm">
                <h3 className="mb-xs text-subheading">
                  Discovered in {discoveryTimeline.length} scan
                  {discoveryTimeline.length === 1 ? '' : 's'}
                  {discoveryTimeline.length > 3 && (
                    <span className="ml-xs text-caption font-normal text-muted-foreground">
                      (most recent 3)
                    </span>
                  )}
                </h3>
                <div className="space-y-xs">
                  {[...discoveryTimeline]
                    .sort((a, b) => {
                      const t = (e: typeof a) =>
                        new Date(e.scan_end || e.scan_start || e.discovered_at || 0).getTime();
                      return t(b) - t(a);
                    })
                    .slice(0, 3)
                    .map((entry) => {
                    // SOC alert correlation needs the scan window (when the
                    // tool was probing the network), not the ingest time.
                    // Fall back to discovered_at only when the parser
                    // couldn't extract start/end — common for masscan list
                    // output and some gnmap files.
                    const hasWindow = entry.scan_start || entry.scan_end;
                    return (
                      <div
                        key={`disc-${entry.scan_id}-${entry.discovered_at ?? ''}`}
                        className="rounded-control border border-border/60 bg-background/40 px-xs py-xxs"
                      >
                        <div className="flex items-center gap-xs">
                          <Badge variant="outline">
                            {entry.scan_type || entry.tool_name || 'Scan'}
                          </Badge>
                          <span
                            className="min-w-0 flex-1 truncate text-caption"
                            title={entry.scan_filename || `Scan #${entry.scan_id}`}
                          >
                            {entry.scan_filename || `Scan #${entry.scan_id}`}
                          </span>
                        </div>
                        <dl className="mt-xxs grid grid-cols-[auto_1fr] gap-x-xs gap-y-0 text-metadata text-muted-foreground">
                          {hasWindow ? (
                            <>
                              <dt className="font-medium">Scan start:</dt>
                              <dd className="tabular-nums">
                                {entry.scan_start ? formatDateTime(entry.scan_start) : '—'}
                              </dd>
                              <dt className="font-medium">Scan end:</dt>
                              <dd className="tabular-nums">
                                {entry.scan_end ? formatDateTime(entry.scan_end) : '—'}
                              </dd>
                            </>
                          ) : (
                            <>
                              <dt className="font-medium" title="Scan tool did not record start/end; this is when the file was ingested.">
                                Ingested:
                              </dt>
                              <dd className="tabular-nums">{formatDateTime(entry.discovered_at)}</dd>
                            </>
                          )}
                          {entry.command_line && (
                            <>
                              <dt className="font-medium">Command:</dt>
                              <dd className="flex min-w-0 items-center gap-1">
                                <span className="min-w-0 truncate font-mono" title={entry.command_line}>
                                  {entry.command_line}
                                </span>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="size-6 shrink-0 text-muted-foreground hover:text-foreground"
                                  aria-label="Copy scan command to clipboard"
                                  title="Copy command"
                                  onClick={() => {
                                    navigator.clipboard
                                      .writeText(entry.command_line as string)
                                      .then(
                                        () => toast.info('Command copied', { autoHideMs: 1500 }),
                                        () => {
                                          /* clipboard denied */
                                        },
                                      );
                                  }}
                                >
                                  <Copy className="size-3.5" aria-hidden />
                                </Button>
                              </dd>
                            </>
                          )}
                        </dl>
                      </div>
                    );
                  })}
                  {discoveryTimeline.length > 5 && (
                    <p className="text-caption text-muted-foreground">
                      + {discoveryTimeline.length - 5} more
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

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
            2. Where do I record my next observation? (Add Note + Team
               Notes promoted right under Overview)
            3. How risky is it? (Vulnerabilities lifted ahead of the
               agent + tool evidence stacks)
            4. What's being done about it? (Proposed Tests)
            5. What evidence supports it? (Web, NSE, NetExec, Ports)
            6. History / audit (Conflicts, Lineage tail)
        */}

      {/* Add Note */}
      <Card id="host-detail-notes">
        <CardHeader>
          <div className="flex items-center gap-xs">
            <NotebookPen className="size-5 text-primary" aria-hidden />
            <CardTitle>Add Investigation Note</CardTitle>
          </div>
          <p className="text-caption text-muted-foreground">
            Capture observations, remediation actions, or handoff context for teammates.
          </p>
        </CardHeader>
        <CardContent className="space-y-sm">
          {noteError && (
            <Alert variant="destructive">
              <AlertDescription className="flex items-center justify-between gap-sm">
                <span>{noteError}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setNoteError(null)}
                  aria-label="Dismiss note error"
                >
                  <X className="size-3.5" aria-hidden />
                </Button>
              </AlertDescription>
            </Alert>
          )}
          <div className="space-y-xxs">
            <Label htmlFor={`host-${hostId}-note-body`}>Note</Label>
            <Textarea
              id={`host-${hostId}-note-body`}
              rows={3}
              placeholder="Example: Confirmed port 445 is exposed; scheduling remediation."
              value={noteBody}
              onChange={(event) => {
                if (noteError) setNoteError(null);
                setNoteBody(event.target.value);
              }}
              onPaste={handleComposerPaste}
              disabled={noteSubmitting}
            />
            {pendingImages.length > 0 && (
              <div className="flex flex-wrap gap-xs">
                {pendingImages.map((img, idx) => (
                  <div key={img.url} className="group relative">
                    <img
                      src={img.url}
                      alt={`Pasted image ${idx + 1}`}
                      className="size-16 rounded-control border border-border object-cover"
                    />
                    <button
                      type="button"
                      onClick={() => removePendingImage(idx)}
                      aria-label={`Remove pasted image ${idx + 1}`}
                      className="absolute -right-1 -top-1 rounded-full bg-destructive p-0.5 text-white shadow"
                      disabled={noteSubmitting}
                    >
                      <X className="size-3" aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <p className="text-caption text-muted-foreground">
              Tip: mention a teammate with <strong>@username</strong> to notify them — and you can
              <strong> paste a screenshot</strong> (Ctrl/Cmd+V) to attach it as evidence.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-sm">
            <div className="flex items-center gap-xs">
              <Label htmlFor={`host-${hostId}-note-status`}>Status</Label>
              <Select
                value={noteStatus}
                onValueChange={(value) => setNoteStatus(value as NoteStatus)}
                disabled={noteSubmitting}
              >
                <SelectTrigger id={`host-${hostId}-note-status`} className="w-[10rem]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(NOTE_STATUS_META).map(([value, meta]) => (
                    <SelectItem key={value} value={value}>
                      {meta.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grow" />
            <Button onClick={handleCreateNote} disabled={noteSubmitting}>
              {noteSubmitting ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  Saving…
                </>
              ) : (
                <>
                  <NotebookPen className="size-4" aria-hidden />
                  Save Note
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Team Notes */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-xs">
            <MessageSquare className="size-5 text-primary" aria-hidden />
            <CardTitle>Team Notes</CardTitle>
            <Badge variant="outline">{notes.length}</Badge>
          </div>
        </CardHeader>
        <CardContent>
          {notes.length === 0 ? (
            <p className="text-metadata text-muted-foreground">
              No notes yet — add your first observation to start a review trail.
            </p>
          ) : (
            // v2.43.0 — MONO-2: notes rendering is now <NoteThread> (see
            // ./host-inspector/NoteThread.tsx).  Pre-extraction this was
            // a 120-line closure capturing 11 pieces of parent state.
            <NoteThread
              topLevel={noteThreadGroups.topLevel}
              repliesByParent={noteThreadGroups.repliesByParent}
              noteStatusMeta={NOTE_STATUS_META}
              replyTo={replyTo}
              replyBody={replyBody}
              onReplyToChange={setReplyTo}
              onReplyBodyChange={setReplyBody}
              onSubmitReply={handleReply}
              noteSubmitting={noteSubmitting}
              noteActionId={noteActionId}
              onUpdateNoteStatus={handleUpdateNoteStatus}
              onDeleteNote={handleDeleteNote}
              onPromoteNote={(noteId) => {
                const note = notes.find((n) => n.id === noteId);
                const firstLine = (note?.body ?? '')
                  .split('\n').map((l) => l.trim()).find(Boolean) ?? '';
                setPromoteTitle(firstLine.slice(0, 200));
                setPromoteOwnerId(note?.assignee_id ?? user?.id ?? 'none');
                setPromoteSeverity('medium');
                // Need the roster for the owner picker (lazy — only fetched once).
                if (members.length === 0) {
                  listProjectMembers().then(setMembers).catch(() => { /* picker degrades */ });
                }
                setPromoteNoteId(noteId);
              }}
              onEditDetails={openNoteDetails}
              hostId={hostId}
              canManageNotes={canManageEntries}
              onAttachmentsChanged={() => setRetryNonce((n) => n + 1)}
            />
          )}
        </CardContent>
      </Card>

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
            <Button disabled={followLoading} onClick={submitReviewCompletion}>
              <CheckCircle2 className="size-3.5" aria-hidden /> Mark reviewed
            </Button>
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
            {/* Blast-radius preview */}
            <div className="rounded-control border border-border bg-muted/30 p-sm text-caption">
              {triagePreviewLoading ? (
                <span className="flex items-center gap-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" aria-hidden /> Checking affected hosts…
                </span>
              ) : triagePreview ? (
                triagePreview.already_promoted ? (
                  <span className="text-foreground">
                    Already promoted — this will re-disposition the existing finding
                    {triagePreview.finding_id != null ? ` (#${triagePreview.finding_id})` : ''}.
                  </span>
                ) : (
                  <>
                    <span className="text-foreground">
                      {triageVuln?.intent === 'confirmed' ? 'Creates one finding across ' : 'Records a finding across '}
                      <strong>{triagePreview.affected_host_count}</strong>{' '}
                      host{triagePreview.affected_host_count === 1 ? '' : 's'}
                      {triagePreview.plugin_id ? ' sharing this plugin.' : ' (this host only).'}
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
                <span className="text-muted-foreground">Couldn't preview affected hosts — you can still proceed.</span>
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
                || (triageVuln?.intent === 'false_positive' && !triageReason.trim())}
              onClick={() => void handlePromoteVuln()}
            >
              {triageVuln?.intent === 'confirmed' ? 'Promote' : 'Dismiss'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Promote-to-finding dialog (foundation 6b) — the bridge from a note
          thread (which stays as the finding's evidence) to a durable,
          roll-up-able record.  Severity is the one required input. */}
      <Dialog open={promoteNoteId !== null} onOpenChange={(v) => { if (!v) setPromoteNoteId(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Promote to finding</DialogTitle>
            <DialogDescription>
              Creates a finding from this note thread. The thread stays attached as the
              finding's evidence; the finding rolls up on the Findings page and can span
              multiple hosts.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-sm">
            <div className="space-y-xxs">
              <Label htmlFor="promote-title">Title</Label>
              <Input
                id="promote-title"
                value={promoteTitle}
                onChange={(e) => setPromoteTitle(e.target.value)}
                placeholder="Finding title (defaults to the note's first line)"
                maxLength={200}
              />
            </div>
            <div className="grid grid-cols-2 gap-sm">
              <div className="space-y-xxs">
                <Label htmlFor="promote-severity">Severity</Label>
                <Select
                  value={promoteSeverity}
                  onValueChange={(v) => setPromoteSeverity(v as FindingSeverity)}
                >
                  <SelectTrigger id="promote-severity">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(['critical', 'high', 'medium', 'low', 'info'] as FindingSeverity[]).map((s) => (
                      <SelectItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-xxs">
                <Label htmlFor="promote-owner">Owner</Label>
                <Select
                  value={promoteOwnerId === 'none' ? 'none' : String(promoteOwnerId)}
                  onValueChange={(v) => setPromoteOwnerId(v === 'none' ? 'none' : Number(v))}
                >
                  <SelectTrigger id="promote-owner">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Unassigned</SelectItem>
                    {members.map((m) => (
                      <SelectItem key={m.user_id} value={String(m.user_id)}>
                        {m.full_name || m.username || `User ${m.user_id}`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <p className="text-caption text-muted-foreground">
              The note thread stays attached as the finding's evidence.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPromoteNoteId(null)} disabled={promoting}>
              Cancel
            </Button>
            <Button onClick={handlePromoteNote} disabled={promoting}>
              {promoting ? 'Promoting…' : 'Promote'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Note-details editor — the write path for the thread work fields
          (type/assignee/due/pin) that the My Work queue groups by. */}
      <Dialog open={detailsNote !== null} onOpenChange={(v) => { if (!v) setDetailsNote(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Note details</DialogTitle>
            <DialogDescription>
              Set the note's type, owner, and due date so it surfaces in the assignee's
              My Work queue (handoffs, assigned, and overdue groups).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-sm">
            <div className="space-y-xxs">
              <Label htmlFor="note-type">Type</Label>
              <Select value={detailsType} onValueChange={setDetailsType}>
                <SelectTrigger id="note-type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— none —</SelectItem>
                  {(['observation', 'finding', 'question', 'decision', 'action', 'handoff'] as const).map((t) => (
                    <SelectItem key={t} value={t} className="capitalize">{t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-xxs">
              <Label htmlFor="note-assignee">Assignee</Label>
              <Select value={detailsAssignee} onValueChange={setDetailsAssignee}>
                <SelectTrigger id="note-assignee"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Unassigned</SelectItem>
                  {members.map((m) => (
                    <SelectItem key={m.user_id} value={String(m.user_id)}>
                      {m.full_name || m.username || `User ${m.user_id}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-xxs">
              <Label htmlFor="note-due">Due date</Label>
              <Input
                id="note-due"
                type="date"
                value={detailsDue}
                onChange={(e) => setDetailsDue(e.target.value)}
              />
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

      {/* This host's findings, inline — appears once a note here is promoted. */}
      <HostFindingsCard hostId={host.id} refreshKey={findingsRefresh} />

      {/* Vulnerabilities */}
      {hasVulnerabilities && (
        <Card id="host-detail-vulnerabilities">
          <CardHeader>
            <div className="flex items-center gap-xs">
              <ShieldAlert className="size-5 text-destructive" aria-hidden />
              <CardTitle className="text-destructive">Vulnerabilities</CardTitle>
              <Badge variant="destructive">{totalVulnerabilities}</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-sm">
            {displayedVulnerabilities.map((vuln) => {
              const title = vuln.title || vuln.plugin_id || 'Unnamed finding';
              const cveLink = vuln.cve_id
                ? `https://cve.mitre.org/cgi-bin/cvename.cgi?name=${vuln.cve_id}`
                : null;
              const descExpanded = expandedVulnIds.has(vuln.id);
              const references = vuln.references ?? [];
              return (
                <div
                  key={`${vuln.id}-${vuln.plugin_id}-${vuln.port_number ?? 'host'}`}
                  className="flex items-start gap-sm border-b border-border pb-sm last:border-b-0 last:pb-0"
                >
                  <div className="min-w-0 flex-1 space-y-xxs">
                    <h4 className="text-subheading">{title}</h4>
                    <p className="text-caption text-muted-foreground">
                      {vuln.source ? vuln.source.toUpperCase() : 'Unknown source'}
                      {vuln.cvss_score !== null && vuln.cvss_score !== undefined && (
                        <> · CVSS {vuln.cvss_score}</>
                      )}
                      {cveLink && (
                        <>
                          {' · '}
                          <a
                            href={cveLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary underline-offset-4 hover:underline"
                          >
                            {vuln.cve_id}
                          </a>
                        </>
                      )}
                    </p>
                    {/* CVSS vector string — the breakdown behind the score. */}
                    {vuln.cvss_vector && (
                      <p className="break-all font-mono text-caption text-muted-foreground">
                        {vuln.cvss_vector}
                      </p>
                    )}
                    {/* Originating plugin/check name, when it differs from
                        the title we're already showing. */}
                    {vuln.source_plugin_name &&
                      vuln.source_plugin_name !== title && (
                        <p className="truncate text-caption text-muted-foreground">
                          Plugin: {vuln.source_plugin_name}
                        </p>
                      )}
                    {vuln.port_number && (
                      <p className="text-caption text-muted-foreground">
                        Port {vuln.port_number}/{(vuln.protocol ?? '').toUpperCase() || 'TCP'}
                        {vuln.service_name && ` • ${vuln.service_name}`}
                      </p>
                    )}
                    {/* Description — the writeup.  Clamped to 4 lines with
                        a per-row expand so a long Nessus paragraph doesn't
                        dominate the card. */}
                    {vuln.description && (
                      <div className="pt-xxs">
                        <p className="text-caption font-semibold text-foreground">Description</p>
                        <p
                          className={`whitespace-pre-wrap break-words text-caption text-muted-foreground${
                            descExpanded ? '' : ' line-clamp-4'
                          }`}
                        >
                          {vuln.description}
                        </p>
                        <button
                          type="button"
                          onClick={() => toggleVulnDescription(vuln.id)}
                          className="text-caption text-primary underline-offset-4 hover:underline"
                        >
                          {descExpanded ? 'Show less' : 'Show more'}
                        </button>
                      </div>
                    )}
                    {vuln.solution && (
                      <div className="pt-xxs">
                        <p className="text-caption font-semibold text-foreground">Remediation</p>
                        <p className="whitespace-pre-wrap break-words text-caption text-muted-foreground">
                          {vuln.solution}
                        </p>
                      </div>
                    )}
                    {/* References — CVE / advisory / exploit links. */}
                    {references.length > 0 && (
                      <div className="pt-xxs">
                        <p className="text-caption font-semibold text-foreground">
                          References ({references.length})
                        </p>
                        <ul className="space-y-0">
                          {references.map((ref, i) => {
                            const isUrl = /^https?:\/\//i.test(ref);
                            // Only surface the native tooltip when the
                            // value is likely to truncate — otherwise
                            // every short ref shows a redundant hover.
                            // 60 ≈ the column at its narrowest before
                            // an ellipsis appears.
                            const titleAttr = ref.length > 60 ? ref : undefined;
                            return (
                              <li key={`${vuln.id}-ref-${i}`} className="min-w-0">
                                {isUrl ? (
                                  <a
                                    href={ref}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    title={titleAttr}
                                    className="block truncate text-caption text-primary underline-offset-4 hover:underline"
                                  >
                                    {ref}
                                  </a>
                                ) : (
                                  <span
                                    title={titleAttr}
                                    className="block truncate text-caption text-muted-foreground"
                                  >
                                    {ref}
                                  </span>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}
                    {vuln.last_seen && (
                      <p className="pt-xxs text-caption text-muted-foreground">
                        Last seen {new Date(vuln.last_seen).toLocaleString()}
                      </p>
                    )}
                  </div>
                  {/* Severity + exploitability badges, stacked, with
                      promote/dismiss-to-finding actions (triage through the
                      spine rather than annotating the raw vuln). */}
                  <div className="flex shrink-0 flex-col items-end gap-xxs">
                    <Badge variant={severityBadgeVariant(vuln.severity) as never}>
                      {(vuln.severity ?? 'unknown').toUpperCase()}
                    </Badge>
                    {vuln.exploitable && (
                      <Badge variant="destructive">Exploit available</Badge>
                    )}
                    {(() => {
                      const promotedFindingId = vuln.finding_id ?? promotedVulns[vuln.id];
                      return promotedFindingId ? (
                        <Link to={`/findings/${promotedFindingId}`} aria-label={`View the finding for ${title}`}>
                          <Badge variant="info" className="hover:underline">Promoted → finding</Badge>
                        </Link>
                      ) : (
                        <div className="flex items-center gap-xxs">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost" size="icon"
                                disabled={vulnActionId === vuln.id}
                                onClick={() => openTriage(vuln.id, title, 'confirmed')}
                                aria-label={`Promote ${title} to a finding`}
                              >
                                <Flag className="size-3.5" aria-hidden />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Promote to finding</TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost" size="icon"
                                disabled={vulnActionId === vuln.id}
                                onClick={() => openTriage(vuln.id, title, 'false_positive')}
                                aria-label={`Dismiss ${title} as a false positive`}
                              >
                                <Ban className="size-3.5" aria-hidden />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Dismiss (false positive)</TooltipContent>
                          </Tooltip>
                        </div>
                      );
                    })()}
                  </div>
                </div>
              );
            })}
            {sortedVulnerabilities.length > VULNERABILITY_PREVIEW_LIMIT && (
              <div className="flex justify-end">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowAllVulnerabilities((prev) => !prev)}
                >
                  {showAllVulnerabilities
                    ? 'Show fewer findings'
                    : `Show all findings (${totalVulnerabilities})`}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Proposed Tests */}
      {testPlanError && testPlanEntries.length === 0 && (
        <Alert variant="warning">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
            <span>Test plan data could not be loaded for this host.</span>
            <Button size="sm" variant="outline" onClick={() => refetchTestPlanEntries()}>
              <RefreshCw className="size-4" aria-hidden />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {entriesWithTests.length > 0 && (
        <Card id="host-detail-proposed-tests">
          <CardHeader>
            <div className="flex items-center gap-xs">
              <ClipboardList className="size-5 text-primary" aria-hidden />
              <CardTitle>Proposed Tests</CardTitle>
              <Badge variant="default">{totalProposedTests}</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-md">
            {entriesWithTests.map((entry) => {
              const structured = entry.proposed_tests.filter(
                (t): t is ProposedTestObject =>
                  typeof t === 'object' && t !== null && 'tool' in t,
              );
              const legacy = entry.proposed_tests.filter(
                (t): t is string => typeof t === 'string',
              );
              const isFindingsOpen = !!findingsOpen[entry.id];
              const isTerminal = entry.status === 'completed' || entry.status === 'rejected';
              const isSaving = savingEntryId === entry.id;

              return (
                <div
                  key={entry.id}
                  className={cn(
                    'rounded-control border border-border p-sm',
                    isTerminal && 'opacity-80',
                  )}
                >
                  <div className="mb-sm flex flex-wrap items-center justify-between gap-xs">
                    <div className="flex flex-wrap items-center gap-xs">
                      <button
                        type="button"
                        onClick={() => navigate(`/test-plans/${entry.test_plan_id}`)}
                        className="inline-flex max-w-[18rem] items-center rounded-chip border border-border px-sm py-px text-micro font-semibold uppercase tracking-wider text-foreground hover:bg-accent"
                      >
                        <span className="truncate">{entry.plan_title}</span>
                      </button>
                      <Badge
                        variant={
                          entry.priority === 'critical'
                            ? 'severity-critical'
                            : entry.priority === 'high'
                              ? 'severity-high'
                              : entry.priority === 'medium'
                                ? 'severity-medium'
                                : entry.priority === 'low'
                                  ? 'severity-low'
                                  : 'outline'
                        }
                      >
                        {entry.priority}
                      </Badge>
                      <Badge variant="outline">{entry.test_phase.replace('_', ' ')}</Badge>
                    </div>

                    {canManageEntries ? (
                      <Select
                        value={
                          TESTER_STATUSES.some((s) => s.value === entry.status)
                            ? entry.status
                            : ''
                        }
                        disabled={isSaving}
                        onValueChange={(value) => handleEntryStatusChange(entry, value)}
                      >
                        <SelectTrigger className="w-[12rem]">
                          <SelectValue
                            placeholder={`${STATUS_LABEL[entry.status] ?? entry.status} — set status`}
                          />
                        </SelectTrigger>
                        <SelectContent>
                          {TESTER_STATUSES.map((s) => (
                            <SelectItem key={s.value} value={s.value}>
                              {s.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Badge
                        variant={
                          entry.status === 'completed'
                            ? 'success'
                            : entry.status === 'rejected'
                              ? 'muted'
                              : entry.status === 'in_progress'
                                ? 'warning'
                                : 'info'
                        }
                      >
                        {STATUS_LABEL[entry.status] ?? entry.status}
                      </Badge>
                    )}
                  </div>

                  <div className="space-y-xs">
                    {structured.map((test, i) => (
                      <StructuredTestCard
                        key={`s-${entry.id}-${i}`}
                        test={test}
                        hostIp={host?.ip_address}
                      />
                    ))}
                    {legacy.map((label, i) => (
                      <Badge key={`l-${entry.id}-${i}`} variant="outline">
                        {label}
                      </Badge>
                    ))}
                  </div>

                  {/* v4.42.0 — per-test execution results (which tool ran, what
                      command, the recorded finding/severity/raw output).
                      Pre-fix the host detail showed only `entry.findings` (the
                      tester's overall summary) as a lumped paragraph below,
                      making it impossible to attribute "which tool produced
                      which finding" or see what actually ran. Mount only when
                      the entry has been executed so we don't fetch for every
                      proposed entry. The same panel is used on the test-plan
                      detail page (PlanTab); proposed_tests is passed in so
                      each result row shows the originating tool. */}
                  {(entry.status === 'in_progress' || entry.status === 'completed') && (
                    <div className="mt-sm">
                      <EntryResultsPanel
                        planId={entry.test_plan_id}
                        entryId={entry.id}
                        proposedTests={entry.proposed_tests}
                        showSessionPicker
                      />
                    </div>
                  )}

                  {canManageEntries && (
                    <div className="mt-sm">
                      {!isFindingsOpen ? (
                        <Button size="sm" variant="ghost" onClick={() => toggleFindings(entry)}>
                          {entry.findings ? 'Edit tester summary' : 'Add tester summary'}
                        </Button>
                      ) : (
                        <div className="space-y-xs">
                          <Textarea
                            rows={3}
                            aria-label={`Tester summary for ${entry.plan_title}`}
                            placeholder="Overall summary for this entry — per-test details (tool, command, severity, output) are recorded above."
                            value={findingsDrafts[entry.id] ?? ''}
                            onChange={(e) =>
                              setFindingsDrafts((prev) => ({
                                ...prev,
                                [entry.id]: e.target.value,
                              }))
                            }
                            disabled={isSaving}
                          />
                          <div className="flex gap-xs">
                            <Button
                              size="sm"
                              onClick={() => handleSaveFindings(entry)}
                              disabled={isSaving}
                            >
                              Save summary
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => toggleFindings(entry)}
                              disabled={isSaving}
                            >
                              Cancel
                            </Button>
                          </div>
                        </div>
                      )}
                      {!isFindingsOpen && entry.findings && (
                        <div className="mt-xxs">
                          <p className="text-caption font-semibold text-muted-foreground">
                            Tester summary
                          </p>
                          <p className="whitespace-pre-wrap text-metadata text-muted-foreground">
                            {entry.findings}
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      <div id="host-detail-web">
        <WebInterfacesCard hostId={host.id} count={host.web_interface_count ?? 0} />
      </div>

      {/* v4.55.0 — DNS evidence card (UX phase 3 + #44.1 frontend
          surface).  Self-suppresses when the host has no DNS records,
          so it doesn't add visual clutter on freshly-discovered hosts. */}
      <HostDnsRecordsCard hostId={host.id} />

      {/* NSE script output — port + host scripts.  Renders nothing
          when the host was scanned without -sC/--script. */}
      <NseScriptsCard host={host} />

      {/* NetExec credentialed enumeration — renders nothing when the
          host was never probed with NetExec. */}
      <NetExecCard hostId={host.id} count={host.netexec_result_count ?? 0} />

      {/* Port Details */}
      <Card id="host-detail-ports">
        <CardHeader>
          <div className="flex items-center gap-xs">
            <Network className="size-5 text-primary" aria-hidden />
            <CardTitle>Port Details</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <Accordion type="multiple" defaultValue={openPorts.length > 0 ? ['open'] : []}>
            {openPorts.length > 0 && (
              <AccordionItem value="open">
                <AccordionTrigger>Open Ports ({openPorts.length})</AccordionTrigger>
                <AccordionContent>
                  <div className="overflow-x-auto">
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow>
                          <PortSortHead className="w-[10%]" />
                          <TableHead className="w-[10%]">Proto</TableHead>
                          <TableHead className="w-[20%]">Service</TableHead>
                          <TableHead className="w-[35%]">Version</TableHead>
                          <TableHead className="w-[12%]">State</TableHead>
                          <TableHead className="w-[13%] text-center">Helpers</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {sortPorts(openPorts).map((port) => {
                          const helpers = connectionHelpersByPort.get(port.id) ?? [];
                          return (
                            <TableRow key={port.id}>
                              <TableCell>{port.port_number}</TableCell>
                              <TableCell>{port.protocol}</TableCell>
                              <TableCell className="truncate" title={port.service_name || undefined}>
                                <div className="truncate">{port.service_name || 'Unknown'}</div>
                                {(port.service_method || (port.service_conf != null && port.service_conf !== '')) && (
                                  <div className="truncate text-caption text-muted-foreground" title="How the service was detected (and nmap confidence 0–10)">
                                    {[
                                      port.service_method,
                                      port.service_conf != null && port.service_conf !== ''
                                        ? `conf ${port.service_conf}`
                                        : null,
                                    ]
                                      .filter(Boolean)
                                      .join(' · ')}
                                  </div>
                                )}
                              </TableCell>
                              <TableCell className="max-w-[16rem] truncate" title={port.service_extrainfo || undefined}>
                                {port.service_product && port.service_version
                                  ? `${port.service_product} ${port.service_version}`
                                  : port.service_product || 'N/A'}
                                {port.service_extrainfo && (
                                  <span className="ml-xxs text-caption text-muted-foreground">
                                    ({port.service_extrainfo})
                                  </span>
                                )}
                              </TableCell>
                              <TableCell>
                                <Badge variant={stateBadgeVariant(port.state)}>
                                  {port.state || 'unknown'}
                                </Badge>
                                {port.reason && (
                                  <div className="truncate text-caption text-muted-foreground" title={`Why this port is ${port.state || 'in this state'}: ${port.reason}`}>
                                    {port.reason}
                                  </div>
                                )}
                              </TableCell>
                              <TableCell className="text-center">
                                <Popover>
                                  <PopoverTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      aria-label={`Connection helpers for port ${port.port_number}`}
                                    >
                                      <Terminal className="size-4" aria-hidden />
                                    </Button>
                                  </PopoverTrigger>
                                  <PopoverContent className="w-[34rem] max-w-[90vw]" align="start">
                                    <div className="max-h-[24rem] overflow-y-auto p-xs">
                                      <h4 className="mb-xs text-subheading">
                                        Commands for {host?.ip_address}:{port.port_number}
                                      </h4>
                                      <div className="space-y-xs">
                                        {helpers.map((helper, idx) => (
                                          <div
                                            key={idx}
                                            className="flex items-start gap-xs rounded-control bg-muted/30 p-xs"
                                          >
                                            <div className="min-w-0 flex-1">
                                              <p className="text-caption text-muted-foreground">
                                                {helper.tool} — {helper.description}
                                              </p>
                                              <div className="mt-xxs max-h-[8rem] overflow-y-auto rounded-control bg-muted/30 p-xs">
                                                <code className="block whitespace-pre-wrap break-words font-mono text-caption">
                                                  {helper.command}
                                                </code>
                                              </div>
                                            </div>
                                            <Tooltip>
                                              <TooltipTrigger asChild>
                                                <Button
                                                  variant="ghost"
                                                  size="icon"
                                                  // Audit RSP·H5 — keep the copy button
                                                  // pinned at full size next to the
                                                  // (truncating) command text.
                                                  className="shrink-0"
                                                  aria-label="Copy command to clipboard"
                                                  onClick={() => {
                                                    navigator.clipboard
                                                      .writeText(helper.command)
                                                      .then(
                                                        () =>
                                                          toast.info('Copied to clipboard', {
                                                            autoHideMs: 1500,
                                                          }),
                                                        () => {
                                                          /* clipboard denied */
                                                        },
                                                      );
                                                  }}
                                                >
                                                  <Copy className="size-4" aria-hidden />
                                                </Button>
                                              </TooltipTrigger>
                                              <TooltipContent>Copy to clipboard</TooltipContent>
                                            </Tooltip>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  </PopoverContent>
                                </Popover>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                </AccordionContent>
              </AccordionItem>
            )}

            {closedPorts.length > 0 && (
              <AccordionItem value="closed">
                <AccordionTrigger>Closed Ports ({closedPorts.length})</AccordionTrigger>
                <AccordionContent>
                  <div className="overflow-x-auto">
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow>
                          <PortSortHead className="w-[15%]" />
                          <TableHead className="w-[15%]">Proto</TableHead>
                          <TableHead className="w-[45%]">Service</TableHead>
                          <TableHead className="w-[25%]">State</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {sortPorts(closedPorts).map((port) => (
                          <TableRow key={port.id}>
                            <TableCell>{port.port_number}</TableCell>
                            <TableCell>{port.protocol}</TableCell>
                            <TableCell>{port.service_name || 'Unknown'}</TableCell>
                            <TableCell>
                              <Badge variant={stateBadgeVariant(port.state)}>
                                {port.state || 'unknown'}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </AccordionContent>
              </AccordionItem>
            )}

            {filteredPorts.length > 0 && (
              <AccordionItem value="filtered">
                <AccordionTrigger>Filtered Ports ({filteredPorts.length})</AccordionTrigger>
                <AccordionContent>
                  <div className="overflow-x-auto">
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow>
                          <PortSortHead className="w-[15%]" />
                          <TableHead className="w-[15%]">Proto</TableHead>
                          <TableHead className="w-[45%]">Service</TableHead>
                          <TableHead className="w-[25%]">State</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {sortPorts(filteredPorts).map((port) => (
                          <TableRow key={port.id}>
                            <TableCell>{port.port_number}</TableCell>
                            <TableCell>{port.protocol}</TableCell>
                            <TableCell>{port.service_name || 'Unknown'}</TableCell>
                            <TableCell>
                              <Badge variant={stateBadgeVariant(port.state)}>
                                {port.state || 'unknown'}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </AccordionContent>
              </AccordionItem>
            )}
          </Accordion>
        </CardContent>
      </Card>

      {/* Data conflicts */}
      {showConflicts && hasConflicts && (
        <Card id="host-detail-conflicts" className="scroll-mt-20">
          <CardHeader>
            <div className="flex items-center gap-xs">
              <AlertTriangle className="size-5 text-warning" aria-hidden />
              <CardTitle>Data Conflicts &amp; Confidence</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-md">
            <Alert variant="info">
              <AlertDescription>
                When the same host is scanned by multiple tools, each tool reports its own findings
                with a confidence score. BlueStick automatically selects the highest-confidence
                value for each field. Scores are based on the tool's detection method — for example,
                Nmap's <code>-sV</code> version probe (95%) is weighted higher than Masscan's basic
                port check (60%).
              </AlertDescription>
            </Alert>
            {Object.entries(conflictsByField).map(([fieldName, fieldConflicts]) => {
              const sorted = [...fieldConflicts].sort(
                (a, b) => b.confidence_score - a.confidence_score,
              );
              const winner = sorted[0];
              const alternatives = sorted.slice(1);
              const relatedHistory = conflictHistory.filter((h) => h.field_name === fieldName);

              return (
                <div key={fieldName} className="space-y-xs">
                  <h3 className="text-subheading capitalize">{fieldName.replace(/_/g, ' ')}</h3>

                  <div className="rounded-control border border-border bg-muted/30 p-sm">
                    <div className="mb-xxs flex items-center gap-xs">
                      <Badge variant={confidenceBadgeVariant(winner.confidence_score)}>
                        {winner.confidence_score}%
                      </Badge>
                      <span className="text-metadata font-semibold">
                        Selected value — {winner.scan_type}
                      </span>
                    </div>
                    <p className="text-caption text-muted-foreground">
                      Source: {winner.data_source || 'unknown'} | Method:{' '}
                      {winner.method || 'default'} | Scan #{winner.scan_id}
                    </p>
                    {winner.additional_factors &&
                      Object.keys(winner.additional_factors).length > 0 && (
                        <p className="text-caption text-muted-foreground">
                          Factors:{' '}
                          {Object.entries(winner.additional_factors)
                            .map(([k, v]) => `${k}: ${v}`)
                            .join(', ')}
                        </p>
                      )}
                  </div>

                  {alternatives.length > 0 && (
                    <div className="ml-md border-l-2 border-border pl-sm">
                      <p className="mb-xxs text-caption text-muted-foreground">
                        Alternative values not chosen:
                      </p>
                      {alternatives.map((alt, idx) => (
                        <div key={idx} className="flex items-center gap-xs">
                          <Badge variant={confidenceBadgeVariant(alt.confidence_score)}>
                            {alt.confidence_score}%
                          </Badge>
                          <span className="text-caption">
                            {alt.scan_type} — {alt.data_source || 'unknown'} via{' '}
                            {alt.method || 'default'} (Scan #{alt.scan_id})
                          </span>
                        </div>
                      ))}
                    </div>
                  )}

                  {relatedHistory.length > 0 && (
                    <div className="ml-md border-l-2 border-warning pl-sm">
                      <p className="mb-xxs text-caption font-semibold text-muted-foreground">
                        Resolution history:
                      </p>
                      {relatedHistory.map((entry) => (
                        <div key={entry.id} className="mb-xxs">
                          <p className="text-caption">
                            <strong>{entry.previous_value || '(empty)'}</strong> (
                            {entry.previous_confidence}% via {entry.previous_method || '?'})
                            {' → '}
                            <strong>{entry.new_value || '(empty)'}</strong> (
                            {entry.new_confidence}% via {entry.new_method || '?'})
                          </p>
                          {entry.resolved_at && (
                            <p className="text-caption text-muted-foreground">
                              Resolved {new Date(entry.resolved_at).toLocaleString()}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {/* Workflow lineage */}
      <HostLineagePanel hostId={host.id} />

      {/* Resolution-summary capture (replaces window.prompt) — required to
          resolve a note thread; the backend 400s without it. */}
      <Dialog open={resolvePrompt !== null} onOpenChange={(v) => { if (!v) setResolvePrompt(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Resolve thread</DialogTitle>
            <DialogDescription>
              A resolution summary is required — record the outcome on the thread's history.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            rows={3}
            autoFocus
            value={resolveText}
            onChange={(e) => setResolveText(e.target.value)}
            placeholder="e.g. patched on all affected hosts; retest passed"
            aria-label="Resolution summary"
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setResolvePrompt(null)}>Cancel</Button>
            <Button
              disabled={!resolveText.trim()}
              onClick={() => {
                const noteId = resolvePrompt;
                const summary = resolveText.trim();
                setResolvePrompt(null);
                if (noteId !== null && summary) void doUpdateNoteStatus(noteId, 'resolved', summary);
              }}
            >
              Resolve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default HostInspector;
