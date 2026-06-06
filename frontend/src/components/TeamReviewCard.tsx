/**
 * Team Review card — the project-wide review roster, grouped by
 * reviewer.  Companion to MyQueueCard: that one is the caller's own
 * In-Review queue, this one is the whole team's, so operators can see
 * who is working what and plan coverage (v4.9.0).
 *
 * Self-contained: fetches its own data, renders its own states.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, RefreshCw, Users } from 'lucide-react';

import { getTeamReview } from '../services/api';
import type { TeamReviewResponse, TeamReviewerGroup } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';

// How many reviewer groups to show before collapsing behind a toggle.
const REVIEWER_PREVIEW = 6;

const ReviewerRow: React.FC<{ group: TeamReviewerGroup }> = ({ group }) => {
  const navigate = useNavigate();
  const name = group.full_name || group.username;
  return (
    <div className="border-b border-border pb-sm last:border-b-0 last:pb-0">
      <div className="mb-xxs flex flex-wrap items-baseline gap-xs">
        <span className="min-w-0 truncate text-metadata font-semibold text-foreground">
          {name}
        </span>
        <Badge variant="outline">
          {group.host_count} host{group.host_count === 1 ? '' : 's'}
        </Badge>
      </div>
      <div className="flex flex-wrap gap-xxs">
        {group.hosts.map((h) => (
          <button
            key={h.host_id}
            type="button"
            onClick={() => navigate(`/hosts/${h.host_id}`)}
            title={h.hostname ? `${h.ip_address} · ${h.hostname}` : h.ip_address}
            className="max-w-[12rem] truncate rounded-control border border-border bg-card px-xs py-xxs font-mono text-caption text-primary transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {h.ip_address}
          </button>
        ))}
      </div>
    </div>
  );
};

export const TeamReviewCard: React.FC = () => {
  const [data, setData] = useState<TeamReviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const fetchRoster = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTeamReview()
      .then((resp) => { if (!cancelled) setData(resp); })
      .catch((err) => {
        if (cancelled) return;
        setData(null);
        setError(formatApiError(err, 'Could not load the team review roster.'));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => fetchRoster(), [fetchRoster]);

  const reviewers = data?.reviewers ?? [];
  const shown = expanded ? reviewers : reviewers.slice(0, REVIEWER_PREVIEW);

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex items-start justify-between gap-sm">
          <div className="flex items-start gap-xs">
            <Users className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
            <div>
              <p className="text-subheading font-semibold text-foreground">Team Review</p>
              <p className="text-caption text-muted-foreground">
                Who has which hosts marked <strong>In Review</strong>
                {data && data.total_hosts_in_review > 0 && (
                  <> · {data.total_hosts_in_review} host
                    {data.total_hosts_in_review === 1 ? '' : 's'} across{' '}
                    {reviewers.length} reviewer{reviewers.length === 1 ? '' : 's'}</>
                )}
              </p>
            </div>
          </div>
          {!loading && (
            <Button size="sm" variant="ghost" onClick={fetchRoster} aria-label="Refresh">
              <RefreshCw className="size-3.5" aria-hidden />
            </Button>
          )}
        </div>

        {loading ? (
          <div className="flex items-center gap-xs">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading roster…</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTitle>Couldn't load the roster</AlertTitle>
            <AlertDescription>
              <p className="break-words">{error}</p>
              <Button size="sm" variant="outline" className="mt-xs" onClick={fetchRoster}>
                <RefreshCw className="size-3.5" aria-hidden />
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : reviewers.length === 0 ? (
          <p className="text-metadata text-muted-foreground">
            No hosts are marked In Review yet. Mark hosts from the host detail panel to
            build a review queue your team can see here.
          </p>
        ) : (
          <div className="flex flex-col gap-sm">
            {shown.map((group) => (
              <ReviewerRow key={group.user_id} group={group} />
            ))}
            {reviewers.length > REVIEWER_PREVIEW && (
              <Button
                size="sm"
                variant="ghost"
                className="self-start"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded
                  ? 'Show fewer reviewers'
                  : `Show all ${reviewers.length} reviewers`}
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default TeamReviewCard;
