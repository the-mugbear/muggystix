/**
 * HostInspector — the data-bearing body of the host detail surface.
 *
 * Renders host overview, tests, vulnerabilities, notes (threaded),
 * data conflicts (when toggled) and port details.  Owns its reads
 * (`getHost` — the host, its notes and the caller's follow state —
 * `getHostConflicts`, `getHostFollowers`); each section with writes of its
 * own is its own component under `host-inspector/` (the review controls, the
 * scanner observations with their promote / dismiss, the discussion), and the
 * other sections read their own data.  What a write changes elsewhere on the
 * page is invalidated by the read's name, never passed down as a refresh
 * counter.
 *
 * Two components, so that no hook is ever below an early return:
 * `HostInspectorBody` holds the reads and decides between the skeleton, the
 * error and the host; `HostInspectorView` renders a host that is loaded and
 * returns nothing early.
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
import { SEVERITY_HSL } from '../utils/severity';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Computer,
  ExternalLink,
  NotebookPen,
  RefreshCw,
} from 'lucide-react';
import {
  getHost,
  getHostConflicts,
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
  HostFollowerEntry,
} from '../services/api';
import { storedReviewConclusionLabel } from '../utils/reviewConclusion';
import { reviewerName } from '../utils/people';
import { ReviewNote } from './host-inspector/ReviewNote';
import { getHostWebLinks, HostWebLink } from '../utils/webLinks';
import { getConnectionHelpers, ConnectionHelper } from '../utils/connectionHelpers';
import NseScriptsCard from './NseScriptsCard';
import HostFindingsCard from './HostFindingsCard';
import HostNamesCard from './HostNamesCard';
import { TimeAgo } from './TimeAgo';
import { AssigneeControl, TagControl } from './host-inspector/HostWorkControls';
import { stickyBelowChrome } from '../utils/uiStyles';
import { jumpToInspectorSection } from './host-inspector/InspectorSection';
import ProvenanceCard, { provenanceExceedsSummary, attributionIsStale } from './host-inspector/ProvenanceCard';
import HostEvidenceSection from './host-inspector/HostEvidenceSection';
import { HostTestsSection } from './host-inspector/HostTestsSection';
import {
  HostTestsProvider, useHostTestsController, type HostTestsController,
} from './host-inspector/hostTestsController';
import ScopeMembershipCard from './host-inspector/ScopeMembershipCard';
import PortDetailsCard from './host-inspector/PortDetailsCard';
import { HostReviewControls } from './host-inspector/HostReviewControls';
import { ScannerObservationsSection, type InformationalRows } from './host-inspector/ScannerObservationsSection';
import { HostDiscussionSection } from './host-inspector/HostDiscussionSection';
import { changesSinceReview, freshnessFacts } from '../utils/evidenceFreshness';
import DiscoveryTimelineCard from './host-inspector/DiscoveryTimelineCard';
import HostConflictsPanel from './host-inspector/HostConflictsPanel';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { usePageSubject } from '../hooks/usePageSubject';
import { useProjectId } from '../hooks/useProjectId';
import { useLastSettled } from '../lib/query';
import { useProjectRole } from '../hooks/useProjectRole';
import { promotedResultMessage, testNeedsWork } from '../utils/hostTests';
import { asAxiosError } from '../utils/apiErrors';
import { cn } from '../utils/cn';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { DetailSkeleton } from './PageSkeleton';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';

/** Where a host's display name came from (Host.hostname_source). */
const HOSTNAME_SOURCE_LABEL: Record<string, string> = {
  operator: 'set by an operator',
  ptr: 'from a reverse-DNS (PTR) record',
  scanner: 'reported by a scanner',
  forward: 'from a forward DNS record',
};

// One empty list each, so "nothing loaded" is the same value on every render.
const NO_NOTES: Annotation[] = [];
const NO_CONFLICTS: HostConflict[] = [];
const NO_CONFLICT_HISTORY: ConflictHistoryEntry[] = [];
const NO_FOLLOWERS: HostFollowerEntry[] = [];

const toTimestamp = (value: string | null | undefined) =>
  value ? new Date(value).getTime() : 0;

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
   * started.  Its presence adds "Reviewed, next unreviewed" to the review
   * controls (a review is one click since 2026-10-10).
   */
  onNextUnreviewed?: () => void;
  /**
   * This inspector IS the page (the standalone host page): the host's address
   * leads the browser tab's title.  Not set inside the Hosts page's sheet,
   * where the page is still the list.
   */
  namesThePage?: boolean;
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

/**
 * The reads, and the choice between the skeleton, the error and the host.
 * Its early returns are its last statements: everything with hooks of its own
 * that needs a loaded host is `HostInspectorView` or one of its sections.
 */
const HostInspectorBody: React.FC<HostInspectorProps> = ({
  hostId,
  density = 'page',
  onHostLoaded,
  onFollowChange,
  onQueryHosts,
  onDirtyChange,
  onNextUnreviewed,
  namesThePage = false,
}) => {
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  // The PROJECT role (analyst+), not the account role every member has (R32).
  const { canWrite: canManageEntries } = useProjectRole();
  const projectId = useProjectId();
  const queryClient = useQueryClient();
  // THE read of this host.  Its notes and the caller's follow state come with
  // it, so they are read from it — never copied out; a write puts the server's
  // answer back in and reads the host again (`putHost`), or says the host is
  // out of date (invalidating
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
  usePageSubject(namesThePage ? host?.ip_address : null);
  const informationalPending = showInformational && hostQuery.data === undefined;
  const informational: InformationalRows = {
    loading: informationalPending && hostQuery.isFetching,
    failed: informationalPending && hostQuery.isError && !hostQuery.isFetching,
    error: hostQuery.error,
    onShow: () => setShowInformational(true),
    onRetry: () => void hostQuery.refetch(),
  };
  // A write's answer goes onto EVERY cached reading of this host (with and
  // without the informational rows: the one on screen may be the other key's
  // while the wider read loads), and the host is read again.  A read already
  // on its way may have been answered before the write, so it is cancelled
  // first — `invalidateQueries` alone joins a FIRST read in flight rather than
  // re-sending it, and the cancel's revert must not undo the answer just put
  // on (review 2026-10-23b, FEC-1).
  const putHost = useCallback((update: (previous: Host) => Host) => {
    const record = ['getHost', projectId, hostId] as const;
    void queryClient.cancelQueries({ queryKey: record }).then(() => {
      queryClient.setQueriesData<Host>({ queryKey: record }, (previous) => (previous ? update(previous) : previous));
      return queryClient.invalidateQueries({ queryKey: record });
    });
  }, [queryClient, projectId, hostId]);

  const conflictsQuery = useQuery({
    queryKey: ['getHostConflicts', projectId, hostId],
    queryFn: ({ signal }) => getHostConflicts(projectId, hostId, signal),
  });

  const followersQuery = useQuery({
    queryKey: ['getHostFollowers', projectId, hostId],
    queryFn: ({ signal }) => getHostFollowers(projectId, hostId, signal),
  });

  // Opening a host is recorded (the "viewed" fact), and again on Retry.
  const { mutate: recordView } = useMutation({ mutationFn: (id: number) => recordHostView(projectId, id) });
  useEffect(() => { recordView(hostId); }, [recordView, hostId]);

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

  // This inspector is one host's for its whole life (it is keyed by the host).
  // What a write that answers after the reader stepped on must not do is
  // speak about "this host" over the next one: those toasts are given to
  // `mutate()` itself, which answers only while the inspector is on screen.

  // Unsaved work: the discussion's (note, screenshots, reply) and the tests'
  // (an edited summary), each said by the section that holds it.
  const onDirtyChangeRef = React.useRef(onDirtyChange);
  useEffect(() => {
    onDirtyChangeRef.current = onDirtyChange;
  }, [onDirtyChange]);
  const [discussionDirty, setDiscussionDirty] = useState(false);
  const [testsDraftDirty, setTestsDraftDirty] = useState(false);
  const composerDirty = discussionDirty || testsDraftDirty;
  useEffect(() => {
    onDirtyChangeRef.current?.(composerDirty);
  }, [composerDirty]);
  const onHostLoadedRef = React.useRef(onHostLoaded);
  onHostLoadedRef.current = onHostLoaded;
  useEffect(() => {
    if (host) onHostLoadedRef.current?.(host);
  }, [host]);

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

  const followersError = followersQuery.isError;
  return (
    <HostInspectorView
      host={host}
      hostId={hostId}
      density={density}
      putHost={putHost}
      onFollowChange={onFollowChange}
      onQueryHosts={onQueryHosts}
      onNextUnreviewed={onNextUnreviewed}
      informational={informational}
      conflicts={conflictsQuery.data?.confidence || NO_CONFLICTS}
      conflictHistory={conflictsQuery.data?.conflict_history || NO_CONFLICT_HISTORY}
      // Canonical conflict count from the API (same definition as the
      // Hosts-list badge).  The old "N conflicts" derived from
      // `conflicts.length` (per-field confidence records, host + port) — a
      // different number that disagreed with the list badge.
      conflictCount={conflictsQuery.data?.conflict_count ?? 0}
      // getHostConflicts already swallows 404 (older deployments), so a
      // failure here is a real one — said, instead of letting an empty list
      // read as "no conflicts" (a data-quality false negative).
      conflictsError={conflictsQuery.isError}
      otherFollowers={(!followersError && followersQuery.data?.followers) || NO_FOLLOWERS}
      followersError={followersError}
      hostTests={hostTests}
      hostTestsElement={hostTestsElement}
      onDiscussionDirtyChange={setDiscussionDirty}
      onTestsDirtyChange={setTestsDraftDirty}
    />
  );
};

interface HostInspectorViewProps
  extends Pick<HostInspectorProps, 'onFollowChange' | 'onQueryHosts' | 'onNextUnreviewed'> {
  host: Host;
  hostId: number;
  density: 'page' | 'sheet';
  putHost: (update: (previous: Host) => Host) => void;
  informational: InformationalRows;
  conflicts: HostConflict[];
  conflictHistory: ConflictHistoryEntry[];
  conflictCount: number;
  conflictsError: boolean;
  otherFollowers: HostFollowerEntry[];
  followersError: boolean;
  hostTests: HostTestsController;
  hostTestsElement: React.ReactNode;
  onDiscussionDirtyChange: (dirty: boolean) => void;
  onTestsDirtyChange: (dirty: boolean) => void;
}

/** A loaded host.  It returns nothing early, so any hook may go anywhere in it. */
const HostInspectorView: React.FC<HostInspectorViewProps> = ({
  host,
  hostId,
  density,
  putHost,
  onFollowChange,
  onQueryHosts,
  onNextUnreviewed,
  informational,
  conflicts,
  conflictHistory,
  conflictCount,
  conflictsError,
  otherFollowers,
  followersError,
  hostTests,
  hostTestsElement,
  onDiscussionDirtyChange,
  onTestsDirtyChange,
}) => {
  const { user } = useAuth();
  // The PROJECT role (analyst+), not the account role every member has (R32).
  const { canWrite: canManageEntries, isProjectAdmin } = useProjectRole();
  const [showConflicts, setShowConflicts] = useState(false);

  const notes = host.notes ?? NO_NOTES;
  const followStatus: FollowStatus | '' = host.follow?.status ?? '';
  const testsToDo = (hostTests.tests ?? []).filter(testNeedsWork).length;

  // The server's answer about the reader's review, onto the cached host; the
  // list's row is told too (the callback names the host).
  const putFollow = (response: Host['follow']) => {
    onFollowChange?.(hostId, response ?? null);
    putHost((previous) => ({ ...previous, follow: response ?? null }));
  };

  const connectionHelpersByPort = useMemo(() => {
    const map = new Map<number, ConnectionHelper[]>();
    host.ports
      .filter((port) => port.state === 'open')
      .forEach((port) => {
        map.set(port.id, getConnectionHelpers(host.ip_address, port, host.hostname));
      });
    return map;
  }, [host]);

  const hasConflicts = conflictCount > 0;
  const vulnSummaryError = host.vulnerability_summary?.error === true;

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
  const closedPorts = host.ports.filter((port) => port.state === 'closed');
  // v5.299.0 — every other state too (open|filtered, closed|filtered,
  // unfiltered): a UDP port nmap could not settle was on no list at all.
  const filteredPorts = host.ports.filter((port) => port.state !== 'open' && port.state !== 'closed');
  const followInfo = host.follow;
  const storedConclusion = storedReviewConclusionLabel(followInfo?.review_conclusion);
  const discoveryTimeline = host.discoveries ?? [];

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

        <HostReviewControls
          hostId={hostId}
          followStatus={followStatus}
          canEdit={canManageEntries}
          onSaved={putFollow}
          onNextUnreviewed={onNextUnreviewed}
        />
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

          {/* Review detail — only what exists: the reviewer's note (optional;
              writers can add or edit it), a conclusion an older review
              recorded (displayed, never asked for), what changed since, when,
              and who else is on this host. The status and its action are in
              the title row; an unreviewed host that nobody else follows
              renders nothing here. */}
          {/* v5.246.0 — a bare "Updated <time>" no longer holds this row open:
              for a host merely In review it was a divided row carrying one
              timestamp. The time still shows whenever the row has a reason. */}
          {((followStatus === 'reviewed'
              && (storedConclusion || followInfo?.review_summary || canManageEntries))
            || newEvidenceSinceReview || otherFollowers.length > 0 || followersError) && (
          <div className="space-y-xs border-t border-border pt-xs">
            <div className="flex min-w-0 flex-wrap items-center gap-sm">
              <div className="flex min-w-0 flex-wrap items-center gap-xs">
                {followStatus === 'reviewed' && storedConclusion && (
                  <span className="min-w-0 break-words text-caption font-medium text-foreground">
                    {storedConclusion}
                  </span>
                )}
                {followStatus === 'reviewed' && (
                  <ReviewNote
                    hostId={hostId}
                    note={followInfo?.review_summary ?? null}
                    canEdit={canManageEntries}
                    onSaved={putFollow}
                  />
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
                  {otherFollowers.map((f, index) => {
                    const label = reviewerName(f.full_name, f.username);
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
                      // A former member has no id, and two of them can mark one host.
                      <Badge key={f.user_id ?? `former-${index}`} variant={variant}>
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
      <ScannerObservationsSection
        host={host}
        putHost={putHost}
        onQueryHosts={onQueryHosts}
        informational={informational}
      />

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
      <NseScriptsCard host={host} />

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
      <HostTestsSection key={host.id} hostId={host.id} canEdit={canManageEntries} userId={user?.id} onDirtyChange={onTestsDirtyChange} />

      <HostEvidenceSection hostId={host.id} />

      {/* Discussion — one section: the composer (a single line until used)
          over the thread, after the evidence it is written about. */}
      <HostDiscussionSection
        hostId={hostId}
        notes={notes}
        putHost={putHost}
        canEdit={canManageEntries}
        isProjectAdmin={isProjectAdmin}
        currentUserId={user?.id ?? null}
        onDirtyChange={onDiscussionDirtyChange}
      />

      {/* Scan discovery timeline — audit evidence, relevant occasionally, so it
          lives at the bottom with a show-all expander (was pinned in the header,
          capped at 3). */}
      <DiscoveryTimelineCard discoveries={discoveryTimeline} />

    </div>
    </HostTestsProvider>
  );
};

export default HostInspector;
