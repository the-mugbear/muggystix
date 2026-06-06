/**
 * Expanded per-project detail for /portfolio (focus-on-remediation view).
 * Surfaces, for one project, the most damning signals + next actions in one
 * place: attention reasons, findings, workflow exceptions, a member
 * preview, and quick actions.  Row actions switch the active project BEFORE
 * navigating.  Members are managed in the existing ProjectMembersSheet
 * (opened via "Manage").
 */
import React from 'react';
import { Loader2, SquareArrowOutUpRight } from 'lucide-react';

import { ProjectCard, ProjectMember, getProjectMembers } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from './ui/badge';
import { Button } from './ui/button';

type Tone = 'default' | 'destructive' | 'success' | 'info' | 'muted' | 'warning' | 'outline' | 'secondary';

const ATTENTION_LABEL: Record<string, { label: string; tone: Tone }> = {
  critical_findings: { label: 'Critical findings', tone: 'destructive' },
  high_findings: { label: 'High findings', tone: 'warning' },
  no_admin: { label: 'No admin', tone: 'destructive' },
  blocked_session: { label: 'Blocked run', tone: 'destructive' },
  pending_review: { label: 'Pending review', tone: 'warning' },
  stale: { label: 'Stale', tone: 'muted' },
  unreviewed: { label: 'Under-reviewed', tone: 'info' },
  no_data: { label: 'No data', tone: 'muted' },
};
const roleTone = (role: string): Tone =>
  role === 'admin' ? 'destructive' : role === 'analyst' ? 'success' : role === 'auditor' ? 'info' : 'muted';

export interface PortfolioProjectDetailProps {
  card: ProjectCard;
  onOpen: (card: ProjectCard, to: string) => void;
  onManageMembers: (card: ProjectCard) => void;
}

const Stat: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="min-w-0">
    <p className="text-caption uppercase tracking-wide text-muted-foreground">{label}</p>
    <div className="mt-xxs">{children}</div>
  </div>
);

export const PortfolioProjectDetail: React.FC<PortfolioProjectDetailProps> = ({
  card, onOpen, onManageMembers,
}) => {
  const [members, setMembers] = React.useState<ProjectMember[] | null>(null);
  const [memberError, setMemberError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    getProjectMembers(card.id)
      .then((m) => { if (!cancelled) setMembers(m); })
      .catch((err) => { if (!cancelled) setMemberError(formatApiError(err, 'Failed to load members.')); });
    return () => { cancelled = true; };
  }, [card.id]);

  const v = card.vuln_summary;
  return (
    <div className="grid grid-cols-1 gap-md bg-muted/20 p-md md:grid-cols-2 lg:grid-cols-4">
      {/* Why this project needs attention */}
      <Stat label="Attention">
        {card.attention_reasons.length === 0 ? (
          <span className="text-metadata text-success">Healthy — no signals</span>
        ) : (
          <div className="flex flex-wrap gap-xxs">
            {card.attention_reasons.map((r) => {
              const m = ATTENTION_LABEL[r] ?? { label: r, tone: 'muted' as Tone };
              return <Badge key={r} variant={m.tone}>{m.label}</Badge>;
            })}
          </div>
        )}
      </Stat>

      {/* Findings — the most damning detail + a jump to the hosts */}
      <Stat label="Findings">
        <div className="flex flex-wrap items-center gap-xs">
          {v.critical + v.high + v.medium + v.low === 0 ? (
            <span className="text-metadata text-muted-foreground">None recorded</span>
          ) : (
            <>
              {v.critical > 0 && <Badge variant="severity-critical">{v.critical} critical</Badge>}
              {v.high > 0 && <Badge variant="severity-high">{v.high} high</Badge>}
              {v.medium > 0 && <span className="text-caption text-muted-foreground">{v.medium} med</span>}
            </>
          )}
          {card.host_count > 0 && (
            <button
              type="button"
              onClick={() => onOpen(card, '/hosts?has=critical')}
              className="inline-flex items-center gap-xxs rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Open hosts <SquareArrowOutUpRight className="size-3" aria-hidden />
            </button>
          )}
        </div>
      </Stat>

      {/* Workflow exceptions */}
      <Stat label="Workflow">
        <div className="flex flex-wrap items-center gap-xs text-metadata">
          {card.pending_plan_reviews > 0 ? (
            <button
              type="button"
              onClick={() => onOpen(card, '/test-plans')}
              className="inline-flex items-center gap-xxs rounded text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Review {card.pending_plan_reviews} <SquareArrowOutUpRight className="size-3" aria-hidden />
            </button>
          ) : (
            <span className="text-muted-foreground">No pending reviews</span>
          )}
          {card.blocked_sessions > 0 && <Badge variant="destructive">{card.blocked_sessions} blocked run</Badge>}
          {card.active_sessions > 0 && <Badge variant="success">{card.active_sessions} active</Badge>}
          {card.open_tasks > 0 && <span className="text-muted-foreground">· {card.open_tasks} open tasks</span>}
        </div>
      </Stat>

      {/* Members preview + manage */}
      <Stat label={`Team (${card.member_count})`}>
        {memberError ? (
          <span className="text-caption text-destructive">{memberError}</span>
        ) : members === null ? (
          <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
            <Loader2 className="size-3 animate-spin" aria-hidden /> loading…
          </span>
        ) : (
          <div className="flex flex-wrap items-center gap-xxs">
            {members.length === 0 && <span className="text-caption text-muted-foreground">No members</span>}
            {members.slice(0, 5).map((m) => (
              <Badge key={m.id} variant={roleTone(m.role)} title={`${m.full_name || m.username} · ${m.role}`}>
                <span className="max-w-[8rem] truncate">{m.full_name || m.username}</span>
              </Badge>
            ))}
            {members.length > 5 && <span className="text-caption text-muted-foreground">+{members.length - 5}</span>}
            <button
              type="button"
              onClick={() => onManageMembers(card)}
              className="rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Manage
            </button>
          </div>
        )}
      </Stat>

      <div className="md:col-span-2 lg:col-span-4">
        <Button size="sm" onClick={() => onOpen(card, '/operations')}>
          Open Operations
          <SquareArrowOutUpRight className="size-3" aria-hidden />
        </Button>
      </div>
    </div>
  );
};

export default PortfolioProjectDetail;
