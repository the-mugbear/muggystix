/**
 * The reader's unread notifications — theirs, whatever the project — as ONE
 * read and one list, shown in two places: the bell's panel in the top bar
 * (`components/Layout`), which opens over whatever page the reader is on, and
 * the panel at the top of Collaboration.
 *
 * A notification is marked read when it is opened or dismissed, or by "Mark
 * all read" — never because a panel was looked at.
 */
import React, { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';

import {
  getNotifications, markAllNotificationsRead, markNotificationsRead,
  type NotificationItem, type NotificationListResponse,
} from '../services/api';
import { invalidateReads } from '../lib/query';
import { isCrossProjectPath, useProject } from '../contexts/ProjectContext';
import { useToast } from '../contexts/ToastContext';
import { useOpenInProject } from '../hooks/useOpenInProject';
import { useLivePresentations, whoPresents } from '../hooks/useLivePresentations';
import { useAuth } from '../contexts/AuthContext';
import PersonAvatar from './PersonAvatar';
import { cn } from '../utils/cn';
import { notificationHref } from '../utils/notificationLinks';
import { formatRelativeTime } from '../utils/relativeTime';
import { Badge } from './ui/badge';
import { Button } from './ui/button';

/** How many unread notifications a panel asks for. */
export const UNREAD_LIMIT = 50;
/** The reader's unread notifications: theirs, whatever the project. */
export const UNREAD_KEY = ['getNotifications', true, UNREAD_LIMIT] as const;

const NO_NOTIFICATIONS: NotificationItem[] = [];

/** Said when a notification's project is not in the reader's list. */
export const NOT_A_MEMBER =
  'This notification is about a project you can no longer open (you are not a member of it, or it was archived). '
  + 'It stays in your list until you mark it read.';

/** "60 unread notifications" — the server's total, never the preview's
 *  length; and how many are shown when that is fewer. */
export const unreadHeading = (total: number, shown: number): string => {
  const all = Math.max(total, shown);
  const words = `${all.toLocaleString()} unread notification${all === 1 ? '' : 's'}`;
  return shown < all ? `${words} (the latest ${shown.toLocaleString()} shown)` : words;
};

/** The unread notifications and what can be done with them.  `enabled`:
 *  a panel that is closed asks for nothing. */
export const useUnreadNotifications = (enabled = true) => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: UNREAD_KEY,
    queryFn: ({ signal }) => getNotifications(true, UNREAD_LIMIT, signal),
    enabled,
  });

  // A notification leaves the list at once; the server is told, and the
  // bell's count AND the list are read again either way — the list is the
  // first UNREAD_LIMIT, so the one after them comes up only by a re-read
  // (review 2026-10-14, H11).
  const bellChanged = useCallback(() => {
    void invalidateReads(queryClient, 'getUnreadNotificationCount', 'getNotifications');
  }, [queryClient]);
  const setUnread = useCallback((keep: (n: NotificationItem) => boolean) => {
    queryClient.setQueryData<NotificationListResponse>(UNREAD_KEY, (old) => {
      if (!old) return old;
      const notifications = old.notifications.filter(keep);
      const gone = old.notifications.length - notifications.length;
      return { ...old, notifications, total_unread: Math.max(0, old.total_unread - gone) };
    });
  }, [queryClient]);
  const { mutate: dismiss } = useMutation({
    mutationFn: (id: number) => markNotificationsRead([id]),
    onMutate: (id) => setUnread((n) => n.id !== id),
    onSettled: bellChanged,
  });
  const { mutate: markAllRead } = useMutation({
    mutationFn: () => markAllNotificationsRead(),
    onMutate: () => setUnread(() => false),
    onSettled: bellChanged,
  });

  // Open a notification's source: mark it read, then deep-link by kind — in
  // the notification's OWN project (review 2026-10-23, H-FE1: the links name
  // no project, so one from project B opened in A as "Finding not found", or
  // as A's list, and was gone).  A cross-project page (a remediation alert's
  // deadlines list) and the installation's alerts need no switch.  A project
  // the reader cannot open any more is said, and the notification stays.
  const { openInProject } = useOpenInProject();
  const toast = useToast();
  const open = useCallback((n: NotificationItem) => {
    const to = notificationHref(n);
    if (to && n.project_id != null && !isCrossProjectPath(to)) {
      if (!openInProject(n.project_id, to)) {
        toast.warning(NOT_A_MEMBER);
        return;
      }
      dismiss(n.id);
      return;
    }
    dismiss(n.id);
    if (to) navigate(to);
  }, [dismiss, navigate, openInProject, toast]);

  return {
    notifications: query.data?.notifications ?? NO_NOTIFICATIONS,
    /** Every unread notification — the list above is at most UNREAD_LIMIT of them. */
    total: query.data?.total_unread ?? 0,
    failed: query.isError, loaded: query.isSuccess, loading: query.isLoading,
    open, markAllRead: () => markAllRead(),
  };
};

/** The bell's panel: the unread notifications over the page the reader is
 *  on.  Opening one, or going to Collaboration, closes it. */
export const BellPanel: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const navigate = useNavigate();
  const { notifications, total, failed, loading, open, markAllRead } = useUnreadNotifications();
  return (
    <div className="min-w-0">
      <PresentingNow onJoined={onClose} />
      <div className="mb-xs flex flex-wrap items-center justify-between gap-sm">
        <h2 className="text-metadata font-semibold text-foreground">
          {loading ? 'Notifications' : failed ? 'Notifications'
            : notifications.length === 0 ? 'No unread notifications'
            : unreadHeading(total, notifications.length)}
        </h2>
        {notifications.length > 0 && (
          <Button variant="ghost" size="sm" className="h-7" onClick={markAllRead}>Mark all read</Button>
        )}
      </div>
      {loading && <p className="text-caption text-muted-foreground">Loading…</p>}
      {/* A failed load is said, not shown as "no notifications". */}
      {failed && (
        <p role="alert" className="text-caption text-destructive">
          Your notifications could not be loaded. Close this and open it again to retry.
        </p>
      )}
      {notifications.length > 0 && (
        <div className="max-h-[60vh] overflow-y-auto">
          <NotificationRows wrap notifications={notifications} onOpen={(n) => { open(n); onClose(); }} />
        </div>
      )}
      <div className="mt-xs border-t border-border pt-xs">
        <button type="button" className="text-caption text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => { onClose(); navigate('/activity?mentions=mine'); }}>
          Open Collaboration
        </button>
      </div>
    </div>
  );
};

/** The bell's first section while a presentation is live in one of the
 *  reader's projects (5.426.0): who presents which report, where, and Join —
 *  the presentation's Controller, in its own project.  Read from what is live
 *  (`useLivePresentations`), so it is gone when the presentation ends. */
export const PresentingNow: React.FC<{ onJoined: () => void }> = ({ onJoined }) => {
  const { live } = useLivePresentations();
  const { user } = useAuth();
  const { openInProject } = useOpenInProject();
  const toast = useToast();
  if (live.length === 0) return null;
  return (
    <section aria-label="Presenting now" className="mb-sm border-l-4 border-info bg-info/10 px-sm py-xs" data-testid="presenting-now">
      <h2 className="text-metadata font-semibold text-foreground">Presenting now</h2>
      <ul className="mt-xxs space-y-xxs">
        {live.map((p) => (
          <li key={p.presentation_id} className="flex min-w-0 items-center gap-sm">
            <PersonAvatar person={p.driver} size={20} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-metadata" title={p.report_title}>
                <span className="font-medium">{whoPresents(p.driver, user?.id)}</span>
                {p.report_number ? ` report #${p.report_number}` : ''} — {p.report_title}
              </span>
              <span className="block truncate text-caption text-muted-foreground" title={p.project_name}>In {p.project_name}</span>
            </span>
            <Button size="sm" className="h-7 shrink-0" onClick={() => {
              if (!openInProject(p.project_id, `/reports/${p.report_id}/present`)) {
                toast.warning(NOT_A_MEMBER);
                return;
              }
              onJoined();
            }}>
              Join
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
};

/** The notifications as rows; a row opens its source. */
export const NotificationRows: React.FC<{
  notifications: NotificationItem[];
  onOpen: (n: NotificationItem) => void;
  /** A narrow place (the bell's panel): a title runs to a second line
   *  instead of being cut before it has said what it is about. */
  wrap?: boolean;
}> = ({ notifications, onOpen, wrap = false }) => {
  const { projects, currentProject } = useProject();
  /** The project a row is about, named when it is not the selected one. */
  const otherProject = (n: NotificationItem): string | null => {
    if (n.project_id == null || n.project_id === currentProject?.id) return null;
    return projects.find((p) => p.id === n.project_id)?.name ?? 'another project';
  };
  return (
    <ul className="divide-y divide-border/60">
      {notifications.map((n) => {
        const elsewhere = otherProject(n);
        return (<li key={n.id}>
          <button
            type="button"
            onClick={() => onOpen(n)}
            className={cn(
              'flex w-full min-w-0 items-start gap-sm rounded-control py-xs text-left',
              'transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            )}
          >
            <Badge variant={n.type === 'mention' ? 'info' : 'muted'} className="mt-xxs shrink-0">
              {n.type === 'mention' ? 'mention' : n.type.replace('_', ' ')}
            </Badge>
            <span className="min-w-0 flex-1">
              <span className={cn('text-metadata font-medium', wrap ? 'line-clamp-2 break-words' : 'block truncate')}
                title={n.title}>
                {n.title}
              </span>
              {n.body && (
                <span className={cn('break-words text-caption text-muted-foreground', wrap ? 'line-clamp-2' : 'line-clamp-1')}>
                  {n.body}
                </span>
              )}
              {elsewhere && (
                <span className="block truncate text-caption text-muted-foreground" title={elsewhere}>
                  In {elsewhere}
                </span>
              )}
            </span>
            {/* Short age, a date past a month ("412d ago" says less than the date). */}
            <span className="shrink-0 text-caption text-muted-foreground">
              {formatRelativeTime(n.created_at, { absoluteAfterDays: 30 })}
            </span>
            <ArrowRight className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
          </button>
        </li>);
      })}
    </ul>
  );
};
