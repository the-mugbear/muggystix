/**
 * HostDetail — standalone host inspection page at /hosts/:hostId.
 *
 * Thin shell: renders navigation chrome (back / prev / next / counter)
 * above a `<HostInspector hostId>` that owns the data and the body.
 * The same `<HostInspector>` is embedded inside a SideSheet on the
 * Hosts list page (alpha.18) so the host-inspection surface is one
 * component with two consumers.
 */
import React, { useEffect } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { ArrowLeft, ChevronLeft, ChevronRight } from 'lucide-react';
import { getHosts } from '../services/api';
import { Button } from '../components/ui/button';
import HostInspector from '../components/HostInspector';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';

export default function HostDetail() {
  const { hostId } = useParams<{ hostId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const numericHostId = hostId ? parseInt(hostId, 10) : null;
  const [navigationLoading, setNavigationLoading] = React.useState(false);
  const toast = useToast();

  const rawNavState = location.state as {
    /** Opened from the Operations cards: Back returns to the work list. */
    fromOperations?: boolean;
    /** The Operations section it was opened from (utils/operationsQueue). */
    queueLabel?: string;
    queuePartial?: boolean;
    fromHosts?: string;
    fromScan?: { id: number; filename: string };
    hostIds?: number[];
    currentIndex?: number;
    totalHosts?: number;
    absoluteIndex?: number;
    queryContext?: Record<string, string | boolean | number | undefined>;
  } | null;

  // Audit FRX·M3: when arriving via refresh / shared URL, location.state
  // is empty.  Reconstruct the list context from the `?from=hosts&filter=<base64>`
  // query string that Hosts.tsx writes on standalone navigation so prev/next
  // chrome still works on a deep-link.
  const navState = React.useMemo(() => {
    if (rawNavState?.fromHosts) return rawNavState;
    const params = new URLSearchParams(location.search);
    if (params.get('from') !== 'hosts') return rawNavState;
    const encoded = params.get('filter');
    if (!encoded) {
      return { ...(rawNavState ?? {}), fromHosts: '/hosts' };
    }
    try {
      const decoded = JSON.parse(decodeURIComponent(escape(atob(encoded)))) as {
        f?: Record<string, string | boolean | number | undefined>;
        i?: number;
        t?: number;
      };
      return {
        ...(rawNavState ?? {}),
        fromHosts: rawNavState?.fromHosts ?? '/hosts',
        queryContext: rawNavState?.queryContext ?? decoded.f,
        absoluteIndex: rawNavState?.absoluteIndex ?? decoded.i,
        totalHosts: rawNavState?.totalHosts ?? decoded.t,
      };
    } catch {
      return { ...(rawNavState ?? {}), fromHosts: rawNavState?.fromHosts ?? '/hosts' };
    }
  }, [rawNavState, location.search]);

  const hostIds = navState?.hostIds;
  const currentIndex = hostIds && numericHostId !== null ? hostIds.indexOf(numericHostId) : -1;
  // v5.243.0 — a host opened from an Operations section brings that section's
  // host ids (utils/operationsQueue). Prev / Next then step through exactly
  // those: a My-work section is a short list the analyst was looking at, not a
  // Hosts query to re-run by index.
  const opsQueue = !!navState?.fromOperations && !!hostIds && hostIds.length > 1 && currentIndex >= 0;
  const absoluteIndex = opsQueue ? currentIndex : (navState?.absoluteIndex ?? currentIndex);
  const totalHostsCount = opsQueue ? hostIds!.length : (navState?.totalHosts ?? hostIds?.length ?? 0);
  const hasPrev = absoluteIndex > 0;
  const hasNext =
    totalHostsCount > 0 && absoluteIndex >= 0 && absoluteIndex < totalHostsCount - 1;

  // The side sheet on the Hosts page has guarded unsaved inspector work since
  // UX review C1; this page rendered the same inspector with no guard, so
  // Back / Prev / Next / Esc (and a tab close) dropped a draft silently.
  // The app runs under <BrowserRouter>, which has no navigation blocker, so
  // the guard covers this page's own chrome plus the browser's unload; a
  // click on the global nav is not interceptable here.
  const dirtyRef = React.useRef(false);
  const [confirmEl, confirm] = useConfirm();
  const confirmDiscardDraft = async (): Promise<boolean> => {
    if (!dirtyRef.current) return true;
    return confirm({
      title: 'Discard unsaved work?',
      body: 'What you started on this host — a note, pasted screenshots, a reply or a test summary — has not been saved. Leave anyway?',
      severity: 'warning',
      confirmLabel: 'Discard',
    });
  };
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, []);

  const handleBackToHosts = async () => {
    if (!(await confirmDiscardDraft())) return;
    if (navState?.fromOperations) navigate('/operations');
    else if (navState?.fromHosts) navigate(navState.fromHosts);
    else navigate('/hosts');
  };

  const navigateToHost = async (absoluteTargetIndex: number) => {
    if (opsQueue) {
      const targetId = hostIds![absoluteTargetIndex];
      if (targetId == null) return;
      if (!(await confirmDiscardDraft())) return;
      // Same state, next id: the queue travels with the analyst.
      navigate(`/hosts/${targetId}`, { state: navState, replace: true });
      return;
    }
    if (
      !navState?.queryContext ||
      absoluteTargetIndex < 0 ||
      absoluteTargetIndex >= totalHostsCount
    ) {
      return;
    }
    if (!(await confirmDiscardDraft())) return;
    setNavigationLoading(true);
    try {
      const response = await getHosts({
        ...navState.queryContext,
        skip: absoluteTargetIndex,
        limit: 1,
        include_total: false,
      });
      const nextHost = response.items[0];
      if (!nextHost) return;
      navigate(`/hosts/${nextHost.id}`, {
        state: {
          ...navState,
          hostIds: [nextHost.id],
          currentIndex: 0,
          absoluteIndex: absoluteTargetIndex,
        },
        replace: true,
      });
    } catch (err) {
      console.error('Failed to navigate to adjacent host:', err);
      toast.error(formatApiError(err, 'Could not navigate to adjacent host.'));
    } finally {
      setNavigationLoading(false);
    }
  };

  // Keyboard shortcuts: arrow keys / j-k for prev-next, Esc for back.
  useEffect(() => {
    if (!navState?.fromHosts && !opsQueue) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement ||
        el.isContentEditable ||
        el.closest('[role="dialog"]') ||
        el.closest('[role="listbox"]') ||
        el.closest('[role="menu"]')
      )
        return;

      if (e.key === 'ArrowLeft' || e.key === 'k') {
        if (hasPrev) navigateToHost(absoluteIndex - 1);
      } else if (e.key === 'ArrowRight' || e.key === 'j') {
        if (hasNext) navigateToHost(absoluteIndex + 1);
      } else if (e.key === 'Escape') {
        handleBackToHosts();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  });

  if (numericHostId === null) {
    return (
      <div className="space-y-md py-xl text-center">
        <h2 className="text-section-title text-destructive">Host not found</h2>
        <Button onClick={() => navigate('/hosts')}>Back to Hosts</Button>
      </div>
    );
  }

  return (
    <div className="space-y-md">
      {/* Navigation chrome — back + prev/next + position counter.  When
          arriving fresh (no navState.fromHosts), only the back button
          renders.  HostInspector below owns the body and title. */}
      <div className="flex flex-wrap items-center gap-sm">
        <Button variant="ghost" size="sm" onClick={handleBackToHosts}>
          <ArrowLeft className="size-4" aria-hidden />
          {navState?.fromOperations ? 'Back to my work' : navState?.fromScan ? 'Back to Scan' : 'Back to Hosts'}
        </Button>
        {(navState?.fromHosts || opsQueue) && totalHostsCount > 1 && (
          <div className="flex items-center gap-xxs">
            <Button
              variant="outline"
              size="sm"
              disabled={!hasPrev || navigationLoading}
              onClick={() => navigateToHost(absoluteIndex - 1)}
            >
              <ChevronLeft className="size-4" aria-hidden />
              Prev
            </Button>
            <span className="min-w-0 max-w-[20rem] truncate text-metadata text-muted-foreground"
              title={opsQueue && navState?.queueLabel ? `${navState.queueLabel} — the hosts listed on Operations when you opened this one` : undefined}>
              {absoluteIndex + 1} of {totalHostsCount}
              {opsQueue && navState?.queueLabel ? ` in ${navState.queueLabel}` : ''}
              {/* Operations had loaded only the first part of this section. */}
              {opsQueue && navState?.queuePartial ? ' · more on Operations' : ''}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasNext || navigationLoading}
              onClick={() => navigateToHost(absoluteIndex + 1)}
            >
              Next
              <ChevronRight className="size-4" aria-hidden />
            </Button>
          </div>
        )}
      </div>

      <HostInspector
        hostId={numericHostId}
        onDirtyChange={(dirty) => { dirtyRef.current = dirty; }}
      />
      {confirmEl}
    </div>
  );
}
