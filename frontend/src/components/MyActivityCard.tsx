/**
 * "My recent activity" (§27) — a unified personal work history that answers
 * "what did I do?" across entities, replacing the authored-notes-only Recent
 * Notes card.  Self-fetches from GET /workbench/my-activity (a feed of notes
 * authored, findings created/promoted/dispositioned, and hosts reviewed),
 * groups by day, and deep-links each event to its exact artifact.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CheckCircle2, Loader2, MessageSquare, Play, RefreshCw, ShieldAlert,
} from 'lucide-react';

import { getMyActivity, type ActivityEvent, type ActivityEventKind } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { formatDate } from '../utils/relativeTime';
import { PostureSection } from './posture/PostureSection';
import UpdatedAt from './UpdatedAt';
import { Button } from './ui/button';
import { Alert, AlertDescription } from './ui/alert';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from './ui/select';

// Coarse type filter → the backend kind set.
type TypeFilter = 'all' | 'notes' | 'findings' | 'reviews' | 'runs';
const TYPE_KINDS: Record<TypeFilter, string | undefined> = {
  all: undefined,
  notes: 'note',
  findings: 'finding_created,finding_status',
  reviews: 'host_reviewed',
  runs: 'session',
};

const KIND_ICON: Record<ActivityEventKind, typeof MessageSquare> = {
  note: MessageSquare,
  finding_created: ShieldAlert,
  finding_status: ShieldAlert,
  host_reviewed: CheckCircle2,
  session: Play,
};

const KIND_TONE: Record<ActivityEventKind, string> = {
  note: 'text-info',
  finding_created: 'text-warning',
  finding_status: 'text-warning',
  host_reviewed: 'text-success',
  session: 'text-muted-foreground',
};

function hrefFor(e: ActivityEvent): string | null {
  if (e.link) return e.link;
  if (e.finding_id != null) return `/findings/${e.finding_id}`;
  if (e.host_id != null) return e.note_id != null
    ? `/hosts/${e.host_id}#note-${e.note_id}`
    : `/hosts/${e.host_id}`;
  return null;
}

function dayBucket(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Earlier';
  const today = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(today) - startOf(d)) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days}d ago`;
  return formatDate(d);
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export const MyActivityCard: React.FC<{
  /** Bumped by the page-level Refresh; the card otherwise fetches for itself. */
  refreshKey?: number;
}> = ({ refreshKey = 0 }) => {
  const navigate = useNavigate();
  const [events, setEvents] = React.useState<ActivityEvent[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  // Self-fetching, so it keeps its own load time (v5.243.0); a failed refetch
  // keeps the previous events under its error.
  const [loadedAt, setLoadedAt] = React.useState<Date | null>(null);
  // v5.294.0 (UX review) — one filter, the type. A free-text box and a time
  // range as well made a side rail read like a search page; the feed is the
  // last twenty events, already grouped by day, so recency is on screen and
  // finding something older is the full lists' job.
  const [typeFilter, setTypeFilter] = React.useState<TypeFilter>('all');

  const load = React.useCallback(() => {
    setLoading(true);
    getMyActivity({ limit: 20, kinds: TYPE_KINDS[typeFilter] })
      .then((res) => { setEvents(res.items); setError(null); setLoadedAt(new Date()); })
      .catch((err) => setError(formatApiError(err, 'Failed to load your activity.')))
      .finally(() => setLoading(false));
  }, [typeFilter]);

  React.useEffect(() => { load(); }, [load, refreshKey]);
  // Reset the preview when the filtered result set changes.
  const [expanded, setExpanded] = React.useState(false);
  React.useEffect(() => { setExpanded(false); }, [typeFilter]);

  const hasFilters = typeFilter !== 'all';

  // Preview a few rows so this column stays near the height of "My work"
  // beside it (three rows per category); "Show more" reveals the rest of the
  // loaded feed.
  const PREVIEW = 8;
  const all = events ?? [];
  const shown = expanded ? all : all.slice(0, PREVIEW);

  // Group consecutive shown events by day (the feed is already newest-first).
  const groups = React.useMemo(() => {
    const out: Array<{ day: string; items: ActivityEvent[] }> = [];
    for (const e of shown) {
      const day = dayBucket(e.at);
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(e);
      else out.push({ day, items: [e] });
    }
    return out;
  }, [shown]);

  return (
    // v5.267.0 — a section, not a card (UI_STYLE_GUIDE §7).
    <PostureSection
      title={<span>My recent activity</span>}
      description="What you’ve worked on — notes, findings, and reviews."
      actions={<>
        <UpdatedAt at={loadedAt} stale={!!error} hideWhenFresh />
        <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as TypeFilter)}>
          <SelectTrigger className="h-7 w-28 text-caption" aria-label="Activity type"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            <SelectItem value="notes">Notes</SelectItem>
            <SelectItem value="findings">Findings</SelectItem>
            <SelectItem value="reviews">Reviews</SelectItem>
            <SelectItem value="runs">Runs</SelectItem>
          </SelectContent>
        </Select>
      </>}
    >

        {loading && !events ? (
          <div className="flex items-center gap-xs" role="status" aria-live="polite">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading…</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
              <span className="break-words">{error}</span>
              <Button size="sm" variant="outline" onClick={load}>
                <RefreshCw className="size-3.5" aria-hidden /> Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : (events?.length ?? 0) === 0 ? (
          <p className="text-metadata text-muted-foreground">
            {hasFilters
              ? 'No activity matches these filters.'
              : 'No recent activity yet — add a note, review a host, or promote a finding to see it here.'}
          </p>
        ) : (
          <div className="flex flex-col gap-sm">
            {groups.map((g) => (
              <div key={g.day}>
                <p className="mb-xxs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
                  {g.day}
                </p>
                <ul className="flex flex-col">
                  {g.items.map((e, i) => {
                    const Icon = KIND_ICON[e.kind] ?? MessageSquare;
                    const href = hrefFor(e);
                    const inner = (
                      <>
                        <span className="shrink-0 text-caption tabular-nums text-muted-foreground">
                          {timeOf(e.at)}
                        </span>
                        <Icon className={`size-3.5 shrink-0 ${KIND_TONE[e.kind] ?? 'text-muted-foreground'}`} aria-hidden />
                        <span className="min-w-0 flex-1 truncate text-metadata text-foreground">
                          {e.summary}
                        </span>
                      </>
                    );
                    return (
                      <li key={`${e.kind}-${e.at}-${i}`}>
                        {href ? (
                          <button
                            type="button"
                            // Same contract as MyWorkCard.FROM_OPERATIONS: the host
                            // page then offers "Back to my work".
                            onClick={() => navigate(href, href.startsWith('/hosts/') ? { state: { fromOperations: true } } : undefined)}
                            className="flex w-full items-center gap-xs rounded-control px-xs py-xxs text-left hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {inner}
                          </button>
                        ) : (
                          <div className="flex items-center gap-xs px-xs py-xxs">{inner}</div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
            {all.length > PREVIEW && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="mt-xxs self-start rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {expanded ? 'Show fewer' : `Show ${all.length - PREVIEW} more`}
              </button>
            )}
          </div>
        )}
    </PostureSection>
  );
};

export default MyActivityCard;
