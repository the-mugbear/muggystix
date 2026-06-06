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
import { formatApiError } from '../utils/apiErrors';

export default function HostDetail() {
  const { hostId } = useParams<{ hostId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const numericHostId = hostId ? parseInt(hostId, 10) : null;
  const [navigationLoading, setNavigationLoading] = React.useState(false);
  const toast = useToast();

  const rawNavState = location.state as {
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
  const absoluteIndex = navState?.absoluteIndex ?? currentIndex;
  const totalHostsCount = navState?.totalHosts ?? hostIds?.length ?? 0;
  const hasPrev = absoluteIndex > 0;
  const hasNext =
    totalHostsCount > 0 && absoluteIndex >= 0 && absoluteIndex < totalHostsCount - 1;

  const handleBackToHosts = () => {
    if (navState?.fromHosts) navigate(navState.fromHosts);
    else navigate('/hosts');
  };

  const navigateToHost = async (absoluteTargetIndex: number) => {
    if (
      !navState?.queryContext ||
      absoluteTargetIndex < 0 ||
      absoluteTargetIndex >= totalHostsCount
    ) {
      return;
    }
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
    if (!navState?.fromHosts) return;
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
          {navState?.fromScan ? 'Back to Scan' : 'Back to Hosts'}
        </Button>
        {navState?.fromHosts && totalHostsCount > 1 && (
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
            <span className="text-metadata text-muted-foreground">
              {absoluteIndex + 1} of {totalHostsCount}
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

      <HostInspector hostId={numericHostId} />
    </div>
  );
}
