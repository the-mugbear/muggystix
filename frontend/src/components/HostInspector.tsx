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
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Bookmark,
  BookmarkPlus,
  ClipboardList,
  Computer,
  Copy,
  ExternalLink,
  Loader2,
  MessageSquare,
  Network,
  NotebookPen,
  RefreshCw,
  Reply,
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
  updateAnnotation,
  deleteAnnotation,
  promoteAnnotation,
  recordHostView,
  getHostTestPlanEntries,
  updateTestPlanEntry,
  getHostFollowers,
} from '../services/api';
import type {
  Host,
  HostConflict,
  ConflictHistoryEntry,
  FollowStatus,
  Annotation,
  NoteStatus,
  HostTestPlanEntry,
  ProposedTestObject,
  HostFollowerEntry,
  FindingSeverity,
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { Textarea } from './ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

const FOLLOW_STATUS_ORDER: FollowStatus[] = ['watching', 'in_review', 'reviewed'];

const VULNERABILITY_PREVIEW_LIMIT = 10;

const VULNERABILITY_SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
  unknown: 5,
};

const severityBadgeVariant = (
  severity: string | null | undefined,
): 'severity-critical' | 'severity-high' | 'severity-medium' | 'severity-low' | 'muted' | 'outline' => {
  const s = (severity ?? 'unknown').toLowerCase();
  if (s === 'critical') return 'severity-critical';
  if (s === 'high') return 'severity-high';
  if (s === 'medium') return 'severity-medium';
  if (s === 'low') return 'severity-low';
  if (s === 'info') return 'muted';
  return 'outline';
};

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
  const { hasPermission } = useAuth();
  const canManageEntries = hasPermission('analyst');
  const [host, setHost] = useState<Host | null>(null);
  const [conflicts, setConflicts] = useState<HostConflict[]>([]);
  const [conflictHistory, setConflictHistory] = useState<ConflictHistoryEntry[]>([]);
  const [showConflicts, setShowConflicts] = useState(false);
  const [loading, setLoading] = useState(true);
  const [followStatus, setFollowStatus] = useState<FollowStatus | ''>('');
  const [followLoading, setFollowLoading] = useState(false);
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
  const [noteStatus, setNoteStatus] = useState<NoteStatus>('open');
  const [noteSubmitting, setNoteSubmitting] = useState(false);
  const [replyTo, setReplyTo] = useState<{ id: number; author: string } | null>(null);
  const [replyBody, setReplyBody] = useState('');
  const [noteError, setNoteError] = useState<string | null>(null);
  // Promote-note-to-finding dialog state (foundation 6b).
  const [promoteNoteId, setPromoteNoteId] = useState<number | null>(null);
  const [promoteSeverity, setPromoteSeverity] = useState<FindingSeverity>('medium');
  const [promoting, setPromoting] = useState(false);
  // Bumped after a promote so the inline HostFindingsCard refetches.
  const [findingsRefresh, setFindingsRefresh] = useState(0);

  const handlePromoteNote = async () => {
    if (promoteNoteId === null) return;
    setPromoting(true);
    try {
      const finding = await promoteAnnotation(promoteNoteId, { severity: promoteSeverity });
      toast.success(`Promoted to finding: ${finding.title}`, { autoHideMs: 3000 });
      setPromoteNoteId(null);
      setFindingsRefresh((n) => n + 1);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to promote note to finding.'));
    } finally {
      setPromoting(false);
    }
  };
  const [noteActionId, setNoteActionId] = useState<number | null>(null);
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
      })
      .catch(() => {
        // getHostConflicts already swallows 404 (older deployments).
        // Anything else here is non-fatal for the host panel itself.
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

  const updateFollow = async (status: FollowStatus | 'none') => {
    setFollowLoading(true);
    try {
      if (status === 'none') {
        await unfollowHost(hostId);
        setFollowStatus('');
        setHost((previous) => (previous ? { ...previous, follow: null } : previous));
        onFollowChange?.(hostId, null);
        toast.info('Removed from your follow list', { autoHideMs: 2000 });
      } else {
        const response = await followHost(hostId, status);
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
      setNotes((previous) => [response, ...previous]);
      setHost((previous) =>
        previous ? { ...previous, notes: [response, ...(previous.notes ?? [])] } : previous,
      );
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

  const handleUpdateNoteStatus = async (noteId: number, status: NoteStatus) => {
    // P3 — resolving a thread requires a summary (the backend rejects a
    // summary-less resolve with 400). Capture it up front so the operator
    // gets a clear prompt instead of an opaque error.
    let resolutionSummary: string | undefined;
    if (status === 'resolved') {
      const entered = window.prompt(
        'Resolution summary (required) — what was the outcome of this thread?',
      );
      if (entered === null || !entered.trim()) {
        if (entered !== null) toast.error('A resolution summary is required to resolve a thread.');
        return;
      }
      resolutionSummary = entered.trim();
    }
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

  const hasConflicts = conflicts.length > 0;
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
  const followInfo = host.follow;
  const followSelectValue = followStatus || 'none';
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
        {hasConflicts && (
          <Button
            variant={showConflicts ? 'default' : 'outline'}
            size="sm"
            onClick={() => setShowConflicts((open) => !open)}
          >
            <AlertTriangle className="size-4" aria-hidden />
            {conflicts.length} conflict{conflicts.length === 1 ? '' : 's'}
          </Button>
        )}
      </div>

      {/* v4.55.0 — triage summary strip (UI/UX phase 2).  Compact
          at-a-glance counts so the operator can size up the host
          before the Host Overview card resolves below.  Each cell
          self-suppresses when its count is 0 so a freshly-discovered
          host doesn't render a row of zeros.  Phase 3 will add a DNS
          evidence cell once the per-host DNS endpoint lands. */}
      <div className="flex flex-wrap items-center gap-xs text-caption">
        <button
          type="button"
          onClick={() => scrollToSection('host-detail-ports')}
          className="inline-flex items-center gap-xxs rounded-control text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          aria-label={`Jump to Port Details (${openPorts.length} open)`}
        >
          <Network className="size-3.5" aria-hidden />
          <strong className="text-foreground">{openPorts.length}</strong>
          {` open port${openPorts.length === 1 ? '' : 's'}`}
        </button>
        {host.vulnerability_summary && host.vulnerability_summary.critical > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-vulnerabilities')}
            className="rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={`Jump to Vulnerabilities (${host.vulnerability_summary.critical} critical)`}
          >
            <Badge variant="severity-critical">
              {host.vulnerability_summary.critical} critical
            </Badge>
          </button>
        )}
        {host.vulnerability_summary && host.vulnerability_summary.high > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-vulnerabilities')}
            className="rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={`Jump to Vulnerabilities (${host.vulnerability_summary.high} high)`}
          >
            <Badge variant="severity-high">{host.vulnerability_summary.high} high</Badge>
          </button>
        )}
        {host.vulnerability_summary && host.vulnerability_summary.medium > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-vulnerabilities')}
            className="rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={`Jump to Vulnerabilities (${host.vulnerability_summary.medium} medium)`}
          >
            <Badge variant="severity-medium">
              {host.vulnerability_summary.medium} medium
            </Badge>
          </button>
        )}
        {testPlanCounts.in_progress > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-proposed-tests')}
            className="inline-flex items-center gap-xxs rounded-control text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <ClipboardList className="size-3.5" aria-hidden />
            <strong className="text-foreground">{testPlanCounts.in_progress}</strong>
            {' in progress'}
          </button>
        )}
        {testPlanCounts.pending > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-proposed-tests')}
            className="inline-flex items-center gap-xxs rounded-control text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <ClipboardList className="size-3.5" aria-hidden />
            <strong className="text-foreground">{testPlanCounts.pending}</strong>
            {' proposed'}
          </button>
        )}
        {testPlanCounts.completed > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-proposed-tests')}
            className="inline-flex items-center gap-xxs rounded-control text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <ClipboardList className="size-3.5" aria-hidden />
            <strong className="text-foreground">{testPlanCounts.completed}</strong>
            {' completed'}
          </button>
        )}
        {(host.web_interface_count ?? 0) > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-web')}
            className="inline-flex items-center gap-xxs rounded-control text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <ExternalLink className="size-3.5" aria-hidden />
            <strong className="text-foreground">{host.web_interface_count}</strong>
            {' web'}
          </button>
        )}
        {notes.length > 0 && (
          <button
            type="button"
            onClick={() => scrollToSection('host-detail-notes')}
            className="inline-flex items-center gap-xxs rounded-control text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <MessageSquare className="size-3.5" aria-hidden />
            <strong className="text-foreground">{notes.length}</strong>
            {` note${notes.length === 1 ? '' : 's'}`}
          </button>
        )}
        {followInfo && (
          <Badge variant={FOLLOW_STATUS_META[followInfo.status].badgeVariant}>
            <Bookmark className="size-3" aria-hidden />
            {FOLLOW_STATUS_META[followInfo.status].label}
          </Badge>
        )}
      </div>

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
            <div className="flex flex-wrap items-center gap-xs">
              <Badge variant={stateBadgeVariant(host.state)}>{host.state || 'unknown'}</Badge>
              {host.hostname && (
                <span className="truncate max-w-full text-metadata text-foreground/90 inline-block">
                  {host.hostname}
                </span>
              )}
              {host.os_name && (
                <Badge variant="outline" className="max-w-[18rem] overflow-hidden">
                  <Computer className="size-3" aria-hidden />
                  <span className="truncate">{host.os_name}</span>
                </Badge>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-xs">
              <Badge variant="success">{openPorts.length} open</Badge>
              {closedPorts.length > 0 && (
                <Badge variant="outline" className="border-destructive/40 text-destructive">
                  {closedPorts.length} closed
                </Badge>
              )}
              {filteredPorts.length > 0 && (
                <Badge variant="outline" className="border-warning/40 text-warning">
                  {filteredPorts.length} filtered
                </Badge>
              )}
              <button
                type="button"
                onClick={() => scrollToSection('host-detail-ports')}
                className="text-caption text-muted-foreground transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                aria-label={`Jump to Port Details (${host.ports.length} total)`}
              >
                {host.ports.length} total port{host.ports.length === 1 ? '' : 's'}
              </button>
            </div>

            {host.vulnerability_summary &&
              host.vulnerability_summary.total_vulnerabilities > 0 && (
                <div className="flex flex-wrap items-center gap-xs">
                  {host.vulnerability_summary.critical > 0 && (
                    <Badge variant="severity-critical">
                      {host.vulnerability_summary.critical} critical
                    </Badge>
                  )}
                  {host.vulnerability_summary.high > 0 && (
                    <Badge variant="severity-high">{host.vulnerability_summary.high} high</Badge>
                  )}
                  {host.vulnerability_summary.medium > 0 && (
                    <Badge variant="severity-medium">{host.vulnerability_summary.medium} medium</Badge>
                  )}
                  {host.vulnerability_summary.low > 0 && (
                    <Badge variant="severity-low">{host.vulnerability_summary.low} low</Badge>
                  )}
                  {host.vulnerability_summary.info > 0 && (
                    <Badge variant="muted">{host.vulnerability_summary.info} info</Badge>
                  )}
                  <button
                    type="button"
                    onClick={() => scrollToSection('host-detail-vulnerabilities')}
                    className="text-caption text-muted-foreground transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    aria-label={`Jump to Vulnerabilities (${host.vulnerability_summary.total_vulnerabilities} total)`}
                  >
                    {host.vulnerability_summary.total_vulnerabilities} total
                  </button>
                </div>
              )}

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
                  {followStatus ? FOLLOW_STATUS_META[followStatus].label : 'Not Following'}
                </Badge>
                {followInfo && (
                  <span className="text-caption text-muted-foreground">
                    Updated{' '}
                    {new Date(
                      followInfo.updated_at ?? followInfo.created_at,
                    ).toLocaleString()}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-xs">
                <Label htmlFor={`host-${hostId}-follow-status`} className="sr-only">
                  Review status
                </Label>
                <Select
                  value={followSelectValue}
                  onValueChange={(value) => updateFollow(value as FollowStatus | 'none')}
                  disabled={followLoading}
                >
                  <SelectTrigger id={`host-${hostId}-follow-status`} className="w-[12rem]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Not Following</SelectItem>
                    {FOLLOW_STATUS_ORDER.map((status) => (
                      <SelectItem key={status} value={status}>
                        {FOLLOW_STATUS_META[status].label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <span className="text-caption text-muted-foreground">{followHelperText}</span>
            </div>

            {otherFollowers.length > 0 && (
              <div className="rounded-control border border-border bg-muted/30 p-sm">
                <p className="mb-xxs text-caption text-muted-foreground">Also tracking this host</p>
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
                </h3>
                <div className="space-y-xs">
                  {discoveryTimeline.slice(0, 5).map((entry) => {
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
              disabled={noteSubmitting}
            />
            <p className="text-caption text-muted-foreground">
              Tip: mention a teammate with <strong>@username</strong> to send them a notification.
              Mentioning yourself doesn&apos;t fire — use it to flag a teammate to look at this host.
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
                setPromoteNoteId(noteId);
                setPromoteSeverity('medium');
              }}
            />
          )}
        </CardContent>
      </Card>

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
          <div className="space-y-xs">
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
                  {/* Severity + exploitability badges, stacked. */}
                  <div className="flex shrink-0 flex-col items-end gap-xxs">
                    <Badge variant={severityBadgeVariant(vuln.severity)}>
                      {(vuln.severity ?? 'unknown').toUpperCase()}
                    </Badge>
                    {vuln.exploitable && (
                      <Badge variant="destructive">Exploit available</Badge>
                    )}
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
                          <TableHead className="w-[10%]">Port</TableHead>
                          <TableHead className="w-[10%]">Protocol</TableHead>
                          <TableHead className="w-[20%]">Service</TableHead>
                          <TableHead className="w-[35%]">Version</TableHead>
                          <TableHead className="w-[12%]">State</TableHead>
                          <TableHead className="w-[13%] text-center">Helpers</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {openPorts.map((port) => {
                          const helpers = connectionHelpersByPort.get(port.id) ?? [];
                          return (
                            <TableRow key={port.id}>
                              <TableCell>{port.port_number}</TableCell>
                              <TableCell>{port.protocol}</TableCell>
                              <TableCell>{port.service_name || 'Unknown'}</TableCell>
                              <TableCell className="truncate">
                                {port.service_product && port.service_version
                                  ? `${port.service_product} ${port.service_version}`
                                  : port.service_product || 'N/A'}
                              </TableCell>
                              <TableCell>
                                <Badge variant={stateBadgeVariant(port.state)}>
                                  {port.state || 'unknown'}
                                </Badge>
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
                          <TableHead className="w-[15%]">Port</TableHead>
                          <TableHead className="w-[15%]">Protocol</TableHead>
                          <TableHead className="w-[45%]">Service</TableHead>
                          <TableHead className="w-[25%]">State</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {closedPorts.map((port) => (
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
                          <TableHead className="w-[15%]">Port</TableHead>
                          <TableHead className="w-[15%]">Protocol</TableHead>
                          <TableHead className="w-[45%]">Service</TableHead>
                          <TableHead className="w-[25%]">State</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {filteredPorts.map((port) => (
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
        <Card>
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
    </div>
  );
};

export default HostInspector;
