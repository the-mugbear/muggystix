import React, { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Search, MessageSquare, AtSign, ArrowRight } from 'lucide-react';
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
import { CardListSkeleton } from '../components/PageSkeleton';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Card, CardContent } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Separator } from '../components/ui/separator';
import { formatApiError } from '../utils/apiErrors';
import { RefreshCw } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import { cn } from '../utils/cn';
import { InlineLoader } from '../components/ui/inline-loader';
import NoteAttachments from '../components/host-inspector/NoteAttachments';

const STATUS_OPTIONS = [
  { value: '', label: 'All Statuses' },
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In Progress' },
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
};

function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 30) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

const getNoteTimestamp = (note: NoteActivityItem) => note.updated_at || note.created_at;
const getThreadKey = (note: NoteActivityItem) =>
  `${note.host_id}:${note.thread_root_id ?? note.parent_id ?? note.note_id}`;

const Activity: React.FC = () => {
  const navigate = useNavigate();
  // FRX·H6: the notification bell deep-links here with
  // `?mentions=mine`.  When that's set we (a) don't auto-dismiss the
  // mentions panel and (b) scroll it into view on mount so the
  // operator sees what the bell promised instead of the chronological
  // feed.
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
  // Unread notifications surfaced as a banner-style "Your Mentions"
  // section at the top of the feed.  Snapshot pre-mark-read so they
  // stay visible until the user dismisses or navigates away, even
  // after the bell badge has been zeroed out.
  const [unreadNotifications, setUnreadNotifications] = useState<NotificationItem[]>([]);
  const [mentionsDismissed, setMentionsDismissed] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Page through notes rather than capping at one 100-note fetch (which made
  // the thread/host counts and the feed silently miss everything past 100).
  const PAGE_SIZE = 100;
  const fetchActivity = useCallback(async (skip = 0) => {
    const append = skip > 0;
    try {
      if (append) setLoadingMore(true); else setLoading(true);
      setFetchError(null);
      const params: Record<string, string | number> = { limit: PAGE_SIZE, skip };
      if (statusFilter) params.status = statusFilter;
      if (authorFilter) params.author_id = Number(authorFilter);
      if (debouncedSearch) params.search = debouncedSearch;
      const data = await getNoteActivity(params);
      setNotes((prev) => (append ? [...prev, ...data.notes] : data.notes));
      setStatusCounts(data.status_counts);
      setTotalNotes(data.total_notes);
      if (data.authors) setAuthors(data.authors);
    } catch (err) {
      setFetchError(formatApiError(err, 'Failed to load activity.'));
    } finally {
      if (append) setLoadingMore(false); else setLoading(false);
    }
  }, [statusFilter, authorFilter, debouncedSearch]);

  // A filter change refetches from the first page (append=false replaces).
  useEffect(() => {
    fetchActivity(0);
  }, [fetchActivity]);

  // FRX·H6: when the user arrived via the bell (?mentions=mine) and
  // the mentions panel renders, scroll it into view so it isn't lost
  // below the feed.  Effect re-runs once `unreadNotifications` is
  // populated by the mount-only effect below.
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
    // and load the user's unread notifications for the Mentions panel.
    //
    // We deliberately do NOT mark notifications read just because the page
    // opened (§21) — that silently cleared a user's mentions. Read-state is
    // now durable: a mention is marked read only when its thread is opened or
    // via the explicit "Mark all read" control, so it survives across visits.
    let cancelled = false;
    Promise.all([
      markActivitySeen().catch(() => undefined),
      getNotifications(true, 50).catch(() => null),
    ])
      .then(([, res]) => {
        if (cancelled || !res) return;
        setUnreadNotifications(res.notifications);
      })
      .catch((err) => console.error('Activity initial-load handler threw:', err));
    return () => {
      cancelled = true;
    };
  }, []);

  // Mark one mention read (on open) or all of them — durable, per §21. Each
  // dispatches the bell event so Layout refetches the true remaining count.
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

  // Open the source note of a mention: mark it read, then deep-link to the
  // exact note on its host (host_id now rides on the notification).
  const openMention = useCallback((n: NotificationItem) => {
    void dismissMention(n.id);
    if (n.host_id && n.source_id) {
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
          // newest reply — otherwise replying to a resolved thread (replies
          // post as "open") makes it look reopened. Fall back to per-note
          // status only if the backend didn't supply the root status.
          latestStatus: latest.thread_root_status ?? latest.status,
          hostNoteCount: latest.host_note_count,
        };
      })
      .sort((a, b) => new Date(b.latestTimestamp).getTime() - new Date(a.latestTimestamp).getTime());
  }, [notes]);

  const hostCount = useMemo(() => new Set(notes.map((n) => n.host_id)).size, [notes]);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center justify-between gap-sm">
        <div className="flex flex-wrap items-center gap-sm">
          <MessageSquare className="size-7 text-primary" aria-hidden />
          <h1 className="text-page-title">Collaboration</h1>
          <Badge variant="outline">{totalNotes.toLocaleString()} notes</Badge>
          {/* "in view" makes clear these describe the loaded subset, not the
              full set — the thread/host grouping is computed client-side over
              what's loaded so far. */}
          {notes.length < totalNotes ? (
            <span className="text-caption text-muted-foreground">
              showing {notes.length.toLocaleString()} of {totalNotes.toLocaleString()}
              {' · '}{threadGroups.length} thread{threadGroups.length === 1 ? '' : 's'} in view
            </span>
          ) : (
            <Badge variant="outline">{threadGroups.length} threads</Badge>
          )}
        </div>
      </div>

      {/* Your Mentions — rendered above the feed so @-mentions and
          status-change pings don't get buried in the chronological
          thread list.  Only the unread set captured on this visit is
          shown; subsequent visits start fresh.  Click any item to
          deep-link to the source note. */}
      {!mentionsDismissed && unreadNotifications.length > 0 && (
        <div ref={mentionsPanelRef} className="mb-md rounded-panel border border-info/40 bg-info/10 p-md">
          <div className="mb-sm flex items-center justify-between gap-sm">
            <div className="flex items-center gap-xs">
              <AtSign className="size-5 text-info" aria-hidden />
              <h2 className="text-subheading font-semibold">Your Mentions</h2>
              <Badge variant="info">{unreadNotifications.length}</Badge>
            </div>
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
          <ul className="flex flex-col gap-xs">
            {unreadNotifications.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => openMention(n)}
                  className={cn(
                    'flex w-full items-start gap-sm rounded-control border border-info/30 bg-card p-sm text-left',
                    'transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  )}
                >
                  <Badge
                    variant={n.type === 'mention' ? 'info' : 'muted'}
                    className="mt-xxs shrink-0"
                  >
                    {n.type === 'mention' ? 'mention' : n.type.replace('_', ' ')}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-metadata font-medium">{n.title}</p>
                    {n.body && (
                      <p className="line-clamp-2 text-caption text-muted-foreground">
                        {n.body}
                      </p>
                    )}
                    <p className="mt-xxs text-caption text-muted-foreground">
                      {formatRelativeTime(n.created_at)}
                    </p>
                  </div>
                  <ArrowRight className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-md grid grid-cols-2 gap-sm md:grid-cols-4">
        {(['open', 'in_progress', 'resolved'] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setStatusFilter(statusFilter === s ? '' : s)}
            aria-pressed={statusFilter === s}
            className={cn(
              'rounded-panel border border-border bg-card p-md text-center transition-colors hover:bg-accent',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              statusFilter === s && 'border-primary',
            )}
          >
            <p
              className={cn(
                'text-page-title font-semibold',
                s === 'open' && 'text-info',
                s === 'in_progress' && 'text-warning',
                s === 'resolved' && 'text-success',
              )}
            >
              {statusCounts[s]}
            </p>
            <p className="text-caption text-muted-foreground">
              {s === 'open' ? 'Open Notes' : s === 'in_progress' ? 'In Progress' : 'Resolved'}
            </p>
          </button>
        ))}
        <Card>
          <CardContent className="p-md text-center">
            <p className="text-page-title font-semibold text-primary">{hostCount}</p>
            <p className="text-caption text-muted-foreground">Hosts in View</p>
          </CardContent>
        </Card>
      </div>

      <div className="mb-md flex flex-wrap items-end gap-sm">
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
                <SelectItem value="all">All Authors</SelectItem>
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

      {fetchError && (
        <Alert variant="destructive" className="mb-md">
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
        <CardListSkeleton count={5} cardHeight={120} />
      ) : threadGroups.length === 0 ? (
        <Card>
          <CardContent className="py-xxl text-center">
            <MessageSquare className="mx-auto mb-sm size-12 text-muted-foreground" aria-hidden />
            <p className="text-subheading text-muted-foreground">
              {statusFilter || debouncedSearch ? 'No matching activity' : 'No activity yet'}
            </p>
            <p className="mx-auto my-sm max-w-md text-metadata text-muted-foreground">
              {statusFilter || debouncedSearch
                ? 'No notes match your current filters. Try a different status or clear the filters to see everything.'
                : 'Add notes to hosts during your review to track findings and collaboration threads. Activity from your team will appear here.'}
            </p>
            {statusFilter || debouncedSearch ? (
              <Button onClick={() => { setStatusFilter(''); setSearch(''); }}>Clear filters</Button>
            ) : (
              <Button onClick={() => navigate('/hosts')}>Go to Hosts</Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-md">
          {threadGroups.map((thread) => (
            <Card key={thread.key}>
              <CardContent className="p-md">
                <div className="mb-sm flex flex-wrap items-start justify-between gap-sm">
                  <div className="min-w-0">
                    <div className="mb-xxs flex flex-wrap items-center gap-xs">
                      <p className="break-all font-mono text-subheading font-semibold text-foreground">
                        {thread.ipAddress || 'Unknown host'}
                      </p>
                      {thread.hostname && (
                        <p className="text-metadata text-muted-foreground">{thread.hostname}</p>
                      )}
                    </div>
                    {/* Status stays a chip (categorical state — open /
                        resolved / etc), the rest is ordinary metadata
                        that reads better as a single muted subtitle.
                        Four side-by-side badges was a chip-wall that
                        diluted the only one that actually signals state. */}
                    <div className="flex flex-wrap items-center gap-xs">
                      <Badge variant={STATUS_VARIANT[getNoteStatusChipColor(thread.latestStatus)] || 'muted'}>
                        {formatStatusLabel(thread.latestStatus)}
                      </Badge>
                      <span className="text-caption text-muted-foreground">
                        {thread.notes.length} entr{thread.notes.length === 1 ? 'y' : 'ies'} in thread
                        {' · '}
                        {thread.hostNoteCount} total on host
                        {' · '}
                        Updated {formatRelativeTime(thread.latestTimestamp)}
                      </span>
                    </div>
                  </div>
                  <Button onClick={() => navigate(`/hosts/${thread.hostId}#note-${thread.threadRootId}`)}>
                    Open thread
                  </Button>
                </div>
                <p className="mb-sm text-metadata text-muted-foreground">
                  Latest update:{' '}
                  {thread.latestNote.body.length > 220
                    ? `${thread.latestNote.body.slice(0, 220)}…`
                    : thread.latestNote.body}
                </p>
                {/* The participant-name badge row was removed — every
                    note row below already names its author, so the
                    badges just repeated that.  For multi-author threads
                    the per-note author + the "N more in host thread"
                    link cover it. */}
                <Separator className="my-sm" />
                <div className="flex flex-col gap-sm">
                  {thread.notes.slice(0, 3).map((note) => (
                    <div key={note.note_id}>
                      <div className="mb-xxs flex flex-wrap items-center justify-between gap-xs">
                        <div className="flex flex-wrap items-center gap-xs">
                          <p className="text-metadata font-semibold text-foreground">
                            {note.author_name || 'Unknown analyst'}
                          </p>
                          <Badge variant="outline">
                            {formatStatusLabel(note.status)}
                          </Badge>
                        </div>
                        <p className="text-caption text-muted-foreground">
                          {note.updated_at
                            ? `Updated ${formatRelativeTime(note.updated_at)}`
                            : `Created ${formatRelativeTime(note.created_at)}`}
                        </p>
                      </div>
                      <p className="text-metadata text-muted-foreground">
                        {note.body.length > 180 ? `${note.body.slice(0, 180)}…` : note.body}
                      </p>
                      {note.attachments && note.attachments.length > 0 && (
                        <NoteAttachments
                          hostId={note.host_id}
                          noteId={note.note_id}
                          attachments={note.attachments}
                          canManage={false}
                          onChanged={() => {}}
                        />
                      )}
                    </div>
                  ))}
                </div>
                {thread.notes.length > 3 && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-sm"
                    onClick={() => navigate(`/hosts/${thread.hostId}#note-${thread.threadRootId}`)}
                  >
                    View {thread.notes.length - 3} more in host thread
                  </Button>
                )}
              </CardContent>
            </Card>
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

export default Activity;
