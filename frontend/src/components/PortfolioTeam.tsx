/**
 * Cross-project team roster (SOC-P4) — who's on which projects and their
 * current workload (assigned open tasks + hosts In Review), busiest-first.
 *
 * Visual overhaul: a team summary band + a card per member (initials avatar,
 * the two workload metrics shown as relative bars so an overloaded teammate is
 * obvious at a glance, and their project/role chips). Replaces the bare table.
 */
import React, { useMemo } from 'react';
import { Loader2, RefreshCw, Users } from 'lucide-react';

import { TeamMember, getPortfolioTeam } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';

type Tone = 'default' | 'destructive' | 'success' | 'info' | 'muted' | 'warning' | 'outline';
const roleTone = (role: string): Tone =>
  role === 'admin' ? 'destructive' : role === 'analyst' ? 'success' : role === 'auditor' ? 'info' : 'muted';

// Deterministic, non-alarming avatar tint per member (never red).
const AVATAR_HSL = ['var(--primary)', 'var(--info)', 'var(--success)', 'var(--warning)'];
const avatarColor = (id: number) => `hsl(${AVATAR_HSL[id % AVATAR_HSL.length]})`;
const initials = (name: string) =>
  name.trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase() || '?';

// A workload metric with a bar relative to the team max — so "who's slammed"
// reads instantly without comparing raw numbers across cards.
const WorkloadStat: React.FC<{
  label: string; value: number; max: number; color: string; warn?: boolean;
}> = ({ label, value, max, color, warn }) => (
  <div className="rounded-control border border-border p-sm">
    <div className="flex items-baseline justify-between gap-xs">
      <span className="text-caption text-muted-foreground">{label}</span>
      <span className={`text-body font-bold tabular-nums ${warn ? 'text-warning' : 'text-foreground'}`}>{value}</span>
    </div>
    <div className="mt-xs h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
      <div className="h-full rounded-full"
        style={{ width: `${max > 0 ? (value / max) * 100 : 0}%`, background: color }} />
    </div>
  </div>
);

const MemberCard: React.FC<{ m: TeamMember; maxTasks: number; maxReview: number }> = ({ m, maxTasks, maxReview }) => {
  const name = m.full_name || m.username;
  const color = avatarColor(m.user_id);
  return (
    <Card>
      <CardContent className="flex flex-col gap-sm p-md">
        <div className="flex items-center gap-sm">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full text-metadata font-bold text-white"
            style={{ background: color }} aria-hidden>
            {initials(name)}
          </span>
          <div className="min-w-0">
            <p className="truncate font-semibold text-foreground">{name}</p>
            <p className="truncate text-caption text-muted-foreground">
              {m.full_name ? `@${m.username} · ` : ''}{m.project_count} project{m.project_count === 1 ? '' : 's'}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-sm">
          <WorkloadStat label="Open tasks" value={m.open_tasks} max={maxTasks}
            color="hsl(var(--info))" warn={m.open_tasks >= 10} />
          <WorkloadStat label="Hosts in review" value={m.hosts_in_review} max={maxReview}
            color="hsl(var(--success))" />
        </div>

        {m.projects.length > 0 && (
          <div className="flex flex-wrap gap-xxs">
            {m.projects.slice(0, 5).map((pr) => (
              <Badge key={pr.project_id} variant={roleTone(pr.role)} title={`${pr.role} on ${pr.project_name}`}>
                <span className="max-w-[10rem] truncate">{pr.project_name}</span>
                <span className="ml-xxs opacity-80">· {pr.role}</span>
              </Badge>
            ))}
            {m.projects.length > 5 && <Badge variant="outline">+{m.projects.length - 5}</Badge>}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export const PortfolioTeam: React.FC = () => {
  const [members, setMembers] = React.useState<TeamMember[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [nonce, setNonce] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getPortfolioTeam()
      .then((r) => { if (!cancelled) setMembers(r.members); })
      .catch((err) => { if (!cancelled) setError(formatApiError(err, 'Failed to load the team roster.')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [nonce]);

  const sorted = useMemo(
    () => [...(members ?? [])].sort((a, b) =>
      (b.open_tasks + b.hosts_in_review) - (a.open_tasks + a.hosts_in_review)
      || a.username.localeCompare(b.username)),
    [members],
  );
  const maxTasks = Math.max(1, ...sorted.map((m) => m.open_tasks));
  const maxReview = Math.max(1, ...sorted.map((m) => m.hosts_in_review));
  const totals = useMemo(() => sorted.reduce(
    (acc, m) => ({ tasks: acc.tasks + m.open_tasks, review: acc.review + m.hosts_in_review }),
    { tasks: 0, review: 0 },
  ), [sorted]);

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-xs p-md text-metadata text-muted-foreground" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading team roster…
        </CardContent>
      </Card>
    );
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
          <span>{error}</span>
          <Button size="sm" variant="outline" onClick={() => setNonce((n) => n + 1)}>
            <RefreshCw className="size-4" aria-hidden /> Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (sorted.length === 0) {
    return (
      <Card>
        <CardContent className="p-xl text-center">
          <Users className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">No team members across your projects yet.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-md">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-x-lg gap-y-sm p-md">
          {[
            { label: 'Members', value: sorted.length },
            { label: 'Open tasks (team)', value: totals.tasks },
            { label: 'Hosts in review (team)', value: totals.review },
          ].map((s) => (
            <div key={s.label}>
              <p className="text-subheading font-bold tabular-nums text-foreground">{s.value.toLocaleString()}</p>
              <p className="text-caption text-muted-foreground">{s.label}</p>
            </div>
          ))}
          <p className="ml-auto max-w-xs text-caption text-muted-foreground">
            Who's across your projects and their current load — assigned open tasks and hosts in review, busiest-first.
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-md sm:grid-cols-2 xl:grid-cols-3">
        {sorted.map((m) => (
          <MemberCard key={m.user_id} m={m} maxTasks={maxTasks} maxReview={maxReview} />
        ))}
      </div>
    </div>
  );
};

export default PortfolioTeam;
