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
  // bell's count is read again either way.
  const bellChanged = useCallback(() => {
    void invalidateReads(queryClient, 'getUnreadNotificationCount');
  }, [queryClient]);
  const setUnread = useCallback((keep: (n: NotificationItem) => boolean) => {
    queryClient.setQueryData<NotificationListResponse>(UNREAD_KEY, (old) => (
      old ? { ...old, notifications: old.notifications.filter(keep) } : old
    ));
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

  // Open a notification's source: mark it read, then deep-link by kind.
  const open = useCallback((n: NotificationItem) => {
    dismiss(n.id);
    const to = notificationHref(n);
    if (to) navigate(to);
  }, [dismiss, navigate]);

  return {
    notifications: query.data?.notifications ?? NO_NOTIFICATIONS,
    failed: query.isError, loaded: query.isSuccess, loading: query.isLoading,
    open, markAllRead: () => markAllRead(),
  };
};

/** The bell's panel: the unread notifications over the page the reader is
 *  on.  Opening one, or going to Collaboration, closes it. */
export const BellPanel: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const navigate = useNavigate();
  const { notifications, failed, loading, open, markAllRead } = useUnreadNotifications();
  return (
    <div className="min-w-0">
      <div className="mb-xs flex flex-wrap items-center justify-between gap-sm">
        <h2 className="text-metadata font-semibold text-foreground">
          {loading ? 'Notifications' : failed ? 'Notifications'
            : notifications.length === 0 ? 'No unread notifications'
            : `${notifications.length.toLocaleString()} unread notification${notifications.length === 1 ? '' : 's'}`}
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

/** The notifications as rows; a row opens its source. */
export const NotificationRows: React.FC<{
  notifications: NotificationItem[];
  onOpen: (n: NotificationItem) => void;
  /** A narrow place (the bell's panel): a title runs to a second line
   *  instead of being cut before it has said what it is about. */
  wrap?: boolean;
}> = ({ notifications, onOpen, wrap = false }) => (
  <ul className="divide-y divide-border/60">
    {notifications.map((n) => (
      <li key={n.id}>
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
          </span>
          {/* Short age, a date past a month ("412d ago" says less than the date). */}
          <span className="shrink-0 text-caption text-muted-foreground">
            {formatRelativeTime(n.created_at, { absoluteAfterDays: 30 })}
          </span>
          <ArrowRight className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
        </button>
      </li>
    ))}
  </ul>
);
