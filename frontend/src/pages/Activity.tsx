/**
 * Collaboration → Activity — the project's host-note threads, latest first.
 *
 * The Posture layout (UI_STYLE_GUIDE §7): a one-line header, ONE filter row
 * closed by a rule (the status breakdown is a line of clickable counts under
 * it, not four stat cards), then the threads as ONE ROW each, grouped by day.
 * A row is the host (IP + hostname), the thread's status as its one chip, the
 * latest message once — with its author and time — and the entry count; the
 * whole row opens the thread on the host.  It used to be a card per thread
 * that printed the same note twice ("Latest update: X", then X again as the
 * first entry) under a bright "Open thread" button on every card.
 *
 * Behaviour kept: search (debounced), status and author filters, paging via
 * Load more, the since-last-visit cursor (markActivitySeen on mount), and the
 * unread notifications panel with the ?mentions=mine deep link.
 */
import React, { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Search, MessageSquare, Bell, ArrowRight, Paperclip, RefreshCw, Loader2 } from 'lucide-react';
import {
  getNoteActivity,
  NoteActivityItem,
  NoteActivityAuthor,
  markActivitySeen,
  getNotifications,
  markNotificationsRead,
  markAllNotificationsRead,
  NotificationItem,
} from '../services/api';
import { formatStatusLabel, getNoteStatusChipColor } from '../utils/statusMeta';
import { AgentAuthorBadge } from '../components/AgentAuthorBadge';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { formatApiError } from '../utils/apiErrors';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import { cn } from '../utils/cn';
import { InlineLoader } from '../components/ui/inline-loader';
import { formatRelativeTime as relativeTime } from '../utils/relativeTime';

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'resolved', label: 'Resolved' },
];

const STATUS_VARIANT: Record<string, 'info' | 'warning' | 'success' | 'muted' | 'default'> = {
  info: 'info',
  warning: 'warning',
  success: 'success',
  default: 'muted',
  primary: 'default',
};

type NoteThreadGroup = {
  key: string;
  hostId: number;
  ipAddress: string | null;
  hostname: string | null;
  threadRootId: number;
  notes: NoteActivityItem[];
  latestNote: NoteActivityItem;
  latestTimestamp: string;
  participantNames: string[];
  latestStatus: string;
  hostNoteCount: number;
  // The whole thread's size from the server — `notes` holds only the
  // entries on the loaded pages that match the filters.
  threadNoteCount: number;
  imageCount: number;
};

/** Short age, switching to a date past a month — "412d ago" tells a reader
 *  less than the date does. */
function formatRelativeTime(dateStr: string | null | undefined): string {
  return relativeTime(dateStr, { absoluteAfterDays: 30 });
}

const getNoteTimestamp = (note: NoteActivityItem) => note.updated_at || note.created_at;
const getThreadKey = (note: NoteActivityItem) =>
  `${note.host_id}:${note.thread_root_id ?? note.parent_id ?? note.note_id}`;

/** The local calendar day a thread was last active on, as a heading. */
function dayLabel(ts: string, now = new Date()): string {
  const d = new Date(ts);
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
}

const dayKey = (ts: string) => {
  const d = new Date(ts);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
};

const excerpt = (text: string, max = 200) => {
  const one = (text || '').replace(/\s+/g, ' ').trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
};

const Activity: React.FC = () => {
  const navigate = useNavigate();
  // FRX·H6: the notification bell deep-links here with `?mentions=mine`.
  // When that's set we scroll the notifications panel into view on mount so
  // the operator sees what the bell promised instead of the feed.
  const [searchParams] = useSearchParams();
  const mentionsFilter = searchParams.get('mentions');
  const mentionsPanelRef = useRef<HTMLDivElement | null>(null);
  const [notes, setNotes] = useState<NoteActivityItem[]>([]);
  const [statusCounts, setStatusCounts] = useState({ open: 0, in_progress: 0, resolved: 0 });
  const [totalNotes, setTotalNotes] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [authorFilter, setAuthorFilter] = useState<string>('');
  const [authors, setAuthors] = useState<NoteActivityAuthor[]>([]);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  // Unread notifications, snapshot pre-mark-read so they stay visible until
  // dismissed or opened, even after the bell badge has been zeroed out.
  const [unreadNotifications, setUnreadNotifications] = useState<NotificationItem[]>([]);
  const [notificationsFailed, setNotificationsFailed] = useState(false);
  const [mentionsDismissed, setMentionsDismissed] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Page through notes rather than capping at one 100-note fetch (which made
  // the thread/host counts and the feed silently miss everything past 100).
  const PAGE_SIZE = 100;
  // Only the latest request applies: a slower response for an older filter
  // used to overwrite the newer one, and a "Load more" page that landed after
  // a filter change was appended to the new list (review 2026-09-23 R11).
  const fetchGenRef = useRef(0);
  const fetchActivity = useCallback(async (skip = 0) => {
    const append = skip > 0;
    const gen = append ? fetchGenRef.current : ++fetchGenRef.current;
    const current = () => gen === fetchGenRef.current;
    try {
      if (append) setLoadingMore(true); else setLoading(true);
      setFetchError(null);
      const params: Record<string, string | number> = { limit: PAGE_SIZE, skip };
      if (statusFilter) params.status = statusFilter;
      if (authorFilter) params.author_id = Number(authorFilter);
      if (debouncedSearch) params.search = debouncedSearch;
      const data = await getNoteActivity(params);
      if (!current()) return;
      setNotes((prev) => (append ? [...prev, ...data.notes] : data.notes));
      setStatusCounts(data.status_counts);
      setTotalNotes(data.total_notes);
      if (data.authors) setAuthors(data.authors);
    } catch (err) {
      if (!current()) return;
      setFetchError(formatApiError(err, 'Failed to load activity.'));
    } finally {
      if (current()) {
        if (append) setLoadingMore(false); else setLoading(false);
      }
    }
  }, [statusFilter, authorFilter, debouncedSearch]);

  // A filter change refetches from the first page (append=false replaces).
  useEffect(() => {
    fetchActivity(0);
  }, [fetchActivity]);

  // FRX·H6: scroll the notifications panel into view when arriving from the bell.
  useEffect(() => {
    if (mentionsFilter !== 'mine') return;
    if (unreadNotifications.length === 0) return;
    if (mentionsDismissed) return;
    const node = mentionsPanelRef.current;
    if (node) {
      node.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
  }, [mentionsFilter, unreadNotifications.length, mentionsDismissed]);

  useEffect(() => {
    // Mount-only: mark the activity FEED seen (the "since last visit" cursor)
    // and load the user's unread notifications for the panel.  Notifications
    // are NOT marked read just because the page opened (§21): a notification
    // is marked read only when opened or via "Mark all read".
    let cancelled = false;
    Promise.all([
      markActivitySeen().catch(() => undefined),
      getNotifications(true, 50).catch(() => null),
    ])
      .then(([, res]) => {
        if (cancelled) return;
        if (!res) {
          setNotificationsFailed(true);
          return;
        }
        setUnreadNotifications(res.notifications);
      })
      .catch((err) => console.error('Activity initial-load handler threw:', err));
    return () => {
      cancelled = true;
    };
  }, []);

  const dismissMention = useCallback(async (id: number) => {
    setUnreadNotifications((prev) => prev.filter((n) => n.id !== id));
    await markNotificationsRead([id]).catch(() => undefined);
    window.dispatchEvent(new CustomEvent('nm:notifications-marked-read'));
  }, []);

  const markAllMentionsRead = useCallback(async () => {
    setUnreadNotifications([]);
    await markAllNotificationsRead().catch(() => undefined);
    window.dispatchEvent(new CustomEvent('nm:notifications-marked-read'));
  }, []);

  // Open a notification's source: mark it read, then deep-link by kind.
  const openMention = useCallback((n: NotificationItem) => {
    void dismissMention(n.id);
    if (n.source_type === 'scan' && n.source_id) {
      navigate(`/hosts?scan_ids=${n.source_id}`);
    } else if (n.source_type === 'report_job' && n.source_id) {
      navigate(`/hosts?reports=1&job=${n.source_id}`);
    } else if (n.source_type === 'note' && n.host_id && n.source_id) {
      navigate(`/hosts/${n.host_id}#note-${n.source_id}`);
    } else if (n.host_id) {
      navigate(`/hosts/${n.host_id}`);
    }
  }, [dismissMention, navigate]);

  const threadGroups = useMemo<NoteThreadGroup[]>(() => {
    const grouped = new Map<string, NoteActivityItem[]>();
    notes.forEach((n) => {
      const key = getThreadKey(n);
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key)!.push(n);
    });
    return Array.from(grouped.entries())
      .map(([key, threadNotes]) => {
        const sorted = [...threadNotes].sort(
          (a, b) => new Date(getNoteTimestamp(b)).getTime() - new Date(getNoteTimestamp(a)).getTime(),
        );
        const latest = sorted[0];
        const participants = Array.from(
          new Set(sorted.map((n) => n.author_name).filter(Boolean) as string[]),
        );
        return {
          key,
          hostId: latest.host_id,
          ipAddress: latest.ip_address,
          hostname: latest.hostname,
          threadRootId: latest.thread_root_id ?? latest.parent_id ?? latest.note_id,
          notes: sorted,
          latestNote: latest,
          latestTimestamp: getNoteTimestamp(latest),
          participantNames: participants,
          // Thread status comes from the ROOT note (server-supplied), not the
          // newest reply — replies post as "open", which would make a resolved
          // thread look reopened.
          latestStatus: latest.thread_root_status ?? latest.status,
          hostNoteCount: latest.host_note_count,
          threadNoteCount: Math.max(sorted.length, ...sorted.map((n) => n.thread_note_count ?? 0)),
          imageCount: sorted.reduce((sum, n) => sum + (n.attachments?.length ?? 0), 0),
        };
      })
      .sort((a, b) => new Date(b.latestTimestamp).getTime() - new Date(a.latestTimestamp).getTime());
  }, [notes]);

  // Threads grouped by the day they were last active, latest day first.
  const days = useMemo(() => {
    const out: Array<{ key: string; label: string; threads: NoteThreadGroup[] }> = [];
    for (const t of threadGroups) {
      const k = dayKey(t.latestTimestamp);
      const last = out[out.length - 1];
      if (last && last.key === k) last.threads.push(t);
      else out.push({ key: k, label: dayLabel(t.latestTimestamp), threads: [t] });
    }
    return out;
  }, [threadGroups]);

  const hostCount = useMemo(() => new Set(notes.map((n) => n.host_id)).size, [notes]);
  const filtered = Boolean(statusFilter || authorFilter || debouncedSearch);

  return (
    <div className="space-y-md p-md md:p-lg">
      <header className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Collaboration</h1>
          <p className="mt-xxs max-w-3xl text-metadata text-muted-foreground">
            Note threads on this project&apos;s hosts, latest first. Open one to read it and reply on the host.
          </p>
        </div>
        {/* "in view": the thread and host figures are computed over what is
            loaded so far, not the full set. */}
        <p className="text-caption text-muted-foreground" aria-live="polite">
          {totalNotes.toLocaleString()} note{totalNotes === 1 ? '' : 's'}
          {notes.length < totalNotes && <> · showing {notes.length.toLocaleString()}</>}
          {' · '}{threadGroups.length} thread{threadGroups.length === 1 ? '' : 's'} on {hostCount} host{hostCount === 1 ? '' : 's'} in view
        </p>
      </header>

      {/* A failed load is said, not shown as "no notifications". */}
      {notificationsFailed && (
        <p role="status" className="text-metadata text-muted-foreground">
          Your notifications could not be loaded; the bell in the top bar still lists them.
        </p>
      )}

      {/* Unread notifications — above the feed so mentions and status pings
          are not buried in it.  A left rule, not a filled panel. */}
      {!mentionsDismissed && unreadNotifications.length > 0 && (
        <section ref={mentionsPanelRef} aria-label="Notifications" className="border-l-4 border-l-info py-xs pl-md">
          <div className="mb-xs flex flex-wrap items-center justify-between gap-sm">
            <h2 className="flex items-center gap-xs text-metadata font-semibold text-foreground">
              <Bell className="size-4 text-info" aria-hidden />
              {unreadNotifications.length} unread notification{unreadNotifications.length === 1 ? '' : 's'}
            </h2>
            <div className="flex items-center gap-xs">
              <Button variant="ghost" size="sm" onClick={() => void markAllMentionsRead()}>
                Mark all read
              </Button>
              {/* Hide for this visit without marking read (read-state is durable). */}
              <Button variant="ghost" size="sm" onClick={() => setMentionsDismissed(true)}>
                Hide
              </Button>
            </div>
          </div>
          <ul className="divide-y divide-border/60">
            {unreadNotifications.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => openMention(n)}
                  className={cn(
                    'flex w-full min-w-0 items-start gap-sm rounded-control py-xs text-left',
                    'transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  )}
                >
                  <Badge variant={n.type === 'mention' ? 'info' : 'muted'} className="mt-xxs shrink-0">
                    {n.type === 'mention' ? 'mention' : n.type.replace('_', ' ')}
                  </Badge>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-metadata font-medium">{n.title}</span>
                    {n.body && <span className="line-clamp-1 text-caption text-muted-foreground">{n.body}</span>}
                  </span>
                  <span className="shrink-0 text-caption text-muted-foreground">{formatRelativeTime(n.created_at)}</span>
                  <ArrowRight className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* One filter row, closed by a rule; the status breakdown is a line of
          counts under it that act as the status filter (was four stat cards). */}
      <div className="border-b border-border pb-sm">
        <div className="flex flex-wrap items-end gap-sm">
          <div className="min-w-72 flex-1">
            <Label htmlFor="act-search">Search</Label>
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                id="act-search"
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by IP, hostname, or note content…"
                className="pl-xl"
              />
            </div>
          </div>
          <div className="w-40">
            <Label htmlFor="act-status">Status</Label>
            <Select
              value={statusFilter || 'all'}
              onValueChange={(v) => setStatusFilter(v === 'all' ? '' : v)}
            >
              <SelectTrigger id="act-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value || 'all'} value={opt.value || 'all'}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {authors.length > 0 && (
            <div className="w-48">
              <Label htmlFor="act-author">Author</Label>
              <Select
                value={authorFilter || 'all'}
                onValueChange={(v) => setAuthorFilter(v === 'all' ? '' : v)}
              >
                <SelectTrigger id="act-author">
                  <SelectValue placeholder="All authors" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All authors</SelectItem>
                  {authors.map((a) => (
                    <SelectItem key={a.id} value={String(a.id)}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
        <p className="mt-xs flex flex-wrap items-center gap-x-xs text-caption text-muted-foreground" aria-label="Notes by status">
          {(['open', 'in_progress', 'resolved'] as const).map((s, i) => (
            <React.Fragment key={s}>
              {i > 0 && <span aria-hidden>·</span>}
              <button
                type="button"
                onClick={() => setStatusFilter(statusFilter === s ? '' : s)}
                aria-pressed={statusFilter === s}
                className={cn(
                  'rounded tabular-nums hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  statusFilter === s && 'font-semibold text-foreground underline',
                )}
              >
                {statusCounts[s].toLocaleString()} {s === 'in_progress' ? 'in progress' : s}
              </button>
            </React.Fragment>
          ))}
        </p>
      </div>

      {fetchError && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
            <span>{fetchError}</span>
            <Button size="sm" variant="outline" onClick={() => fetchActivity()}>
              <RefreshCw className="size-4" aria-hidden />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {loading ? (
        <p className="inline-flex items-center gap-xs text-metadata text-muted-foreground" role="status">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading activity…
        </p>
      ) : threadGroups.length === 0 ? (
        <div className="flex max-w-2xl items-start gap-sm border-l-4 border-border py-xs pl-md">
          <MessageSquare className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0">
            <p className="text-subheading font-semibold text-foreground">
              {filtered ? 'No matching activity' : 'No activity yet'}
            </p>
            <p className="mt-xxs text-metadata text-muted-foreground">
              {filtered
                ? 'No notes match these filters. Try a different status or clear the filters to see everything.'
                : 'Notes added to hosts during review appear here as threads, with your team’s replies.'}
            </p>
            <div className="mt-sm">
              {filtered ? (
                <Button size="sm" variant="outline" onClick={() => { setStatusFilter(''); setAuthorFilter(''); setSearch(''); }}>
                  Clear filters
                </Button>
              ) : (
                <Button size="sm" variant="outline" onClick={() => navigate('/hosts')}>Go to Hosts</Button>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-md">
          {days.map((day) => (
            <section key={day.key} aria-label={day.label}>
              <h2 className="border-b border-border pb-xxs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
                {day.label}
              </h2>
              <ul className="divide-y divide-border/60">
                {day.threads.map((thread) => (
                  <li key={thread.key}>
                    <ThreadRow thread={thread} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {notes.length < totalNotes && (
            <div className="flex justify-center pt-sm">
              <Button
                variant="outline"
                onClick={() => fetchActivity(notes.length)}
                disabled={loadingMore}
              >
                {loadingMore
                  ? <InlineLoader label="Loading…" />
                  : `Load more (${(totalNotes - notes.length).toLocaleString()} more)`}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/** One thread, one row: host, status, the latest message once, the count —
 *  and the whole row opens the thread on the host. */
const ThreadRow: React.FC<{ thread: NoteThreadGroup }> = ({ thread }) => {
  const latest = thread.latestNote;
  const others = thread.participantNames.filter((n) => n !== latest.author_name);
  const host = thread.ipAddress || 'Unknown host';
  return (
    <Link
      to={`/hosts/${thread.hostId}#note-${thread.threadRootId}`}
      data-thread={thread.key}
      aria-label={`Open the thread on ${host}${thread.hostname ? ` (${thread.hostname})` : ''}`}
      className="group grid min-w-0 grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto] items-start gap-x-md py-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="min-w-0">
        <span className="block truncate font-mono text-metadata font-semibold text-foreground group-hover:text-info" title={host}>
          {host}
        </span>
        {thread.hostname && (
          <span className="block truncate text-caption text-muted-foreground" title={thread.hostname}>{thread.hostname}</span>
        )}
      </span>
      <span className="min-w-0">
        <span className="flex min-w-0 flex-wrap items-center gap-xs">
          <Badge variant={STATUS_VARIANT[getNoteStatusChipColor(thread.latestStatus)] || 'muted'}>
            {formatStatusLabel(thread.latestStatus)}
          </Badge>
          <span className="text-caption font-medium text-foreground">{latest.author_name || 'Unknown analyst'}</span>
          <AgentAuthorBadge actorType={latest.actor_type} />
          {others.length > 0 && (
            <span className="truncate text-caption text-muted-foreground">with {others.join(', ')}</span>
          )}
        </span>
        <span className="mt-xxs line-clamp-2 break-words text-metadata text-muted-foreground group-hover:text-foreground">
          {excerpt(latest.body)}
        </span>
      </span>
      <span className="flex shrink-0 flex-col items-end text-right text-caption text-muted-foreground">
        <span>{formatRelativeTime(thread.latestTimestamp)}</span>
        <span>
          {thread.threadNoteCount} entr{thread.threadNoteCount === 1 ? 'y' : 'ies'}
          {thread.hostNoteCount > thread.threadNoteCount ? ` · ${thread.hostNoteCount} on host` : ''}
        </span>
        {thread.imageCount > 0 && (
          <span className="inline-flex items-center gap-xxs">
            <Paperclip className="size-3" aria-hidden /> {thread.imageCount} image{thread.imageCount === 1 ? '' : 's'}
          </span>
        )}
      </span>
    </Link>
  );
};

export default Activity;
