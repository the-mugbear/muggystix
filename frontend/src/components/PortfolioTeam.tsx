/**
 * Cross-project team roster (SOC-P4) — who is on which projects and how much
 * each person has on their plate, busiest first.
 *
 * The Posture layout (UI_STYLE_GUIDE §7): one strip of measures, then ONE
 * section with a table, a row per member.  It was a card per member with an
 * avatar and two meters, under a summary card.
 *
 * The two workload figures, as the server counts them (`/portfolio/team`,
 * across the projects the reader can see, archived ones left out):
 *   - `open_tasks` — host tests ASSIGNED to the person that are still to do
 *     (proposed or in progress).  Work is host tests; there are no "tasks",
 *     so the page says "Tests assigned".  `open_tasks` is the server's field
 *     name, kept until `/portfolio/team` renames it.
 *   - `hosts_in_review` — distinct hosts the person has In Review.
 * Neither opens a list: no page lists another person's tests or reviews
 * across projects.
 */
import React, { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RefreshCw, Users } from 'lucide-react';

import { TeamMember, getPortfolioTeam } from '../services/api';
import { GLOBAL, queryErrorText } from '../lib/query';
import { projectRoleLabel } from '../utils/projectMembers';
import PostureEmpty from './posture/PostureEmpty';
import PostureMeasure from './posture/PostureMeasure';
import PostureSection from './posture/PostureSection';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { InfoTip } from './ui/info-tip';

type Tone = 'default' | 'destructive' | 'success' | 'info' | 'muted' | 'warning' | 'outline';
const roleTone = (role: string): Tone =>
  role === 'admin' ? 'destructive' : role === 'analyst' ? 'success' : role === 'auditor' ? 'info' : 'muted';

/** Project chips shown in a row before "+N". */
const PROJECTS_SHOWN = 5;

const TESTS_INFO = 'Host tests assigned to the person that are still to do — proposed or in progress — across the projects you can see. Archived projects are left out.';
const REVIEW_INFO = 'Hosts the person has In Review, each host once, across the projects you can see. Archived projects are left out.';

const count = (n: number) => n.toLocaleString();
const plural = (n: number, word: string) => `${count(n)} ${word}${n === 1 ? '' : 's'}`;

const MemberRow: React.FC<{ m: TeamMember }> = ({ m }) => {
  const name = m.full_name || m.username;
  const shown = m.projects.slice(0, PROJECTS_SHOWN);
  const rest = m.projects.slice(PROJECTS_SHOWN);
  return (
    <tr className="border-t border-border/60 align-top">
      <td className="py-xs pr-md">
        <span className="block truncate font-medium text-foreground" title={name}>{name}</span>
        <span className="block truncate text-caption text-muted-foreground">
          {m.full_name ? `@${m.username} · ` : ''}{plural(m.project_count, 'project')}
        </span>
      </td>
      <td className="py-xs pr-md">
        {m.projects.length === 0 ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <span className="flex min-w-0 flex-wrap gap-xxs">
            {shown.map((pr) => (
              <Badge key={pr.project_id} variant={roleTone(pr.role)} className="max-w-full"
                title={`${projectRoleLabel(pr.role)} on ${pr.project_name}`}>
                <span className="max-w-[10rem] truncate">{pr.project_name}</span>
                <span className="ml-xxs shrink-0 opacity-80">· {projectRoleLabel(pr.role)}</span>
              </Badge>
            ))}
            {rest.length > 0 && (
              <Badge variant="outline"
                title={rest.map((pr) => `${pr.project_name} · ${projectRoleLabel(pr.role)}`).join(', ')}>
                +{rest.length}
              </Badge>
            )}
          </span>
        )}
      </td>
      <td className="py-xs pr-md text-right tabular-nums text-foreground">{count(m.open_tasks)}</td>
      <td className="py-xs text-right tabular-nums text-foreground">{count(m.hosts_in_review)}</td>
    </tr>
  );
};

export const PortfolioTeam: React.FC = () => {
  const query = useQuery({
    queryKey: [GLOBAL, 'getPortfolioTeam'],
    queryFn: ({ signal }) => getPortfolioTeam(signal),
  });
  const members = query.data?.members;
  const loading = query.isFetching;
  const error = queryErrorText(query.error, 'Failed to load the team roster.');

  const sorted = useMemo(
    () => [...(members ?? [])].sort((a, b) =>
      (b.open_tasks + b.hosts_in_review) - (a.open_tasks + a.hosts_in_review)
      || a.username.localeCompare(b.username)),
    [members],
  );
  const totals = useMemo(() => sorted.reduce(
    (acc, m) => ({ tests: acc.tests + m.open_tasks, review: acc.review + m.hosts_in_review }),
    { tests: 0, review: 0 },
  ), [sorted]);

  if (loading) {
    return <p role="status" aria-live="polite" className="text-metadata text-muted-foreground">Loading team roster…</p>;
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
          <span className="min-w-0 break-words">{error}</span>
          <Button size="sm" variant="outline" onClick={() => { void query.refetch(); }}>
            <RefreshCw className="size-4" aria-hidden /> Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (sorted.length === 0) {
    return (
      <PostureEmpty Icon={Users} title="No team members">
        Nobody is a member of any of your projects yet.
      </PostureEmpty>
    );
  }

  return (
    <div className="space-y-md">
      <div data-testid="team-measures"
        className="grid gap-md border-b border-border pb-md sm:grid-cols-3 sm:divide-x sm:divide-border">
        <PostureMeasure label="Members" value={count(sorted.length)}
          info="People who are a member of at least one project you can see. Archived projects are left out." />
        <PostureMeasure label="Tests assigned" value={count(totals.tests)} info={TESTS_INFO} />
        <PostureMeasure label="Hosts in review" value={count(totals.review)}
          info={`${REVIEW_INFO} A host two people have in review counts for each of them.`} />
      </div>

      <PostureSection title="Workload by member"
        description="Busiest first: tests assigned plus hosts in review.">
        {/* Fixed widths never sum to the content width: the table has a
            minimum, inside its own scroller, and Projects takes what is left. */}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[44rem] border-collapse text-metadata" style={{ tableLayout: 'fixed' }}
            aria-label="Team workload">
            <thead>
              <tr className="text-left text-caption text-muted-foreground">
                <th className="w-[28%] pb-xxs pr-md font-medium">Member</th>
                <th className="pb-xxs pr-md font-medium">Projects</th>
                <th className="w-36 pb-xxs pr-md text-right font-medium">
                  <span className="inline-flex items-center gap-xxs">Tests assigned<InfoTip text={TESTS_INFO} label="What Tests assigned counts" /></span>
                </th>
                <th className="w-36 pb-xxs text-right font-medium">
                  <span className="inline-flex items-center gap-xxs">Hosts in review<InfoTip text={REVIEW_INFO} label="What Hosts in review counts" /></span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((m) => <MemberRow key={m.user_id} m={m} />)}
            </tbody>
          </table>
        </div>
      </PostureSection>
    </div>
  );
};

export default PortfolioTeam;
