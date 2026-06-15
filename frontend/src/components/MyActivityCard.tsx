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
  CheckCircle2, Loader2, MessageSquare, RefreshCw, ShieldAlert,
} from 'lucide-react';

import { getMyActivity, type ActivityEvent, type ActivityEventKind } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Alert, AlertDescription } from './ui/alert';

const KIND_ICON: Record<ActivityEventKind, typeof MessageSquare> = {
  note: MessageSquare,
  finding_created: ShieldAlert,
  finding_status: ShieldAlert,
  host_reviewed: CheckCircle2,
};

const KIND_TONE: Record<ActivityEventKind, string> = {
  note: 'text-info',
  finding_created: 'text-warning',
  finding_status: 'text-warning',
  host_reviewed: 'text-success',
};

function hrefFor(e: ActivityEvent): string | null {
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
  return d.toLocaleDateString();
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export const MyActivityCard: React.FC = () => {
  const navigate = useNavigate();
  const [events, setEvents] = React.useState<ActivityEvent[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(() => {
    setLoading(true);
    getMyActivity(20)
      .then((res) => { setEvents(res.items); setError(null); })
      .catch((err) => setError(formatApiError(err, 'Failed to load your activity.')))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  // Group consecutive events by day (the feed is already newest-first).
  const groups = React.useMemo(() => {
    const out: Array<{ day: string; items: ActivityEvent[] }> = [];
    for (const e of events ?? []) {
      const day = dayBucket(e.at);
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(e);
      else out.push({ day, items: [e] });
    }
    return out;
  }, [events]);

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <p className="text-subheading font-semibold text-foreground">My recent activity</p>
        <p className="mb-sm text-caption text-muted-foreground">
          What you’ve worked on — notes, findings, and reviews. Pick up where you left off.
        </p>

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
            No recent activity yet — add a note, review a host, or promote a finding to see it here.
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
                            onClick={() => navigate(href)}
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
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default MyActivityCard;
