/**
 * What one agent session proposed, and what became of it (5.403.0) — the
 * session page's answer to "did my agent's proposal go through?".
 *
 * A proposed new finding is not a finding until a person accepts it, so it
 * was on no Findings list, and an operator who looked for it there read the
 * call as failed.  Each row says where it stands: waiting (decided on the
 * Proposals page), accepted (opens the finding it made or changed), rejected
 * (the reviewer's note), superseded.
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { listProposals, type Proposal } from '../../services/api';
import { useListQuery } from '../../hooks/useListQuery';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import { describeProposal } from '../proposals/ProposalItem';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

const PAGE = 25;
const FAILED = 'Could not load what this session proposed.';

const STATUS_LABEL: Record<Proposal['status'], string> = {
  pending: 'Waiting',
  accepted: 'Accepted',
  rejected: 'Rejected',
  superseded: 'Superseded',
};

/** Where the row leads: the finding it made or is about, else the decision. */
const Outcome: React.FC<{ pr: Proposal; sessionId: number }> = ({ pr, sessionId }) => {
  const findingId = pr.result_finding_id ?? pr.finding_id;
  if (pr.status === 'pending') {
    return (
      <Link
        to={`/proposals?agent_session_id=${sessionId}&scope=all`}
        className="text-primary underline-offset-4 hover:underline"
      >
        Decide on the Proposals page
      </Link>
    );
  }
  if (pr.status === 'rejected') {
    return (
      <span className="line-clamp-2 break-words text-muted-foreground" title={pr.decision_note ?? undefined}>
        {pr.decision_note?.trim() ? pr.decision_note : 'No reason given'}
        {pr.decided_by ? ` — ${pr.decided_by}` : ''}
      </span>
    );
  }
  if (findingId != null) {
    return (
      <Link to={`/findings/${findingId}`} className="text-primary underline-offset-4 hover:underline">
        {pr.kind === 'finding_create' ? 'Open the new finding' : 'Open the finding'}
      </Link>
    );
  }
  return <span className="text-muted-foreground">—</span>;
};

export const SessionProposals: React.FC<{ sessionId: number; ended: boolean }> = ({ sessionId, ended }) => {
  const projectId = useProjectId();
  const list = useListQuery<Proposal>(
    'listProposals',
    ({ offset, limit, signal }) => listProposals(projectId, { agent_session_id: sessionId, status: 'any', limit, offset }, signal),
    [projectId, { agent_session_id: sessionId, status: 'any' }],
    { pageSize: PAGE, errorMessage: FAILED },
  );
  const more = useMutation({ mutationFn: () => list.loadMore() });
  const { total } = list;
  const error = list.error ?? queryErrorText(more.error, FAILED);

  if (!list.rows) {
    if (list.error) {
      return (
        <p role="alert" className="text-metadata text-destructive">
          {list.error}{' '}
          <Button variant="ghost" size="sm" onClick={() => void list.reload()}>Retry</Button>
        </p>
      );
    }
    return <p role="status" className="text-metadata text-muted-foreground">Loading proposals…</p>;
  }
  const rows = list.rows;
  if (total === 0) {
    return (
      <p className="text-metadata text-muted-foreground">
        None — this session has proposed nothing{ended ? '' : ' so far'}.
      </p>
    );
  }
  return (
    <>
      <Table style={{ tableLayout: 'fixed' }} data-testid="session-proposals">
        <TableHeader>
          <TableRow>
            <TableHead>Proposal</TableHead>
            <TableHead className="w-28">Status</TableHead>
            <TableHead className="w-[32%]">Outcome</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((pr) => (
            <TableRow key={pr.id}>
              <TableCell className="truncate" title={describeProposal(pr)}>{describeProposal(pr)}</TableCell>
              <TableCell>
                <Badge
                  variant={pr.status === 'accepted' ? 'success' : pr.status === 'pending' ? 'outline' : 'muted'}
                  className="whitespace-nowrap"
                >
                  {STATUS_LABEL[pr.status]}
                </Badge>
              </TableCell>
              <TableCell className="text-caption"><Outcome pr={pr} sessionId={sessionId} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {error && <p role="alert" className="text-caption text-destructive">{error}</p>}
      {rows.length < total && (
        <div>
          <Button variant="ghost" size="sm" disabled={list.loading || list.loadingMore} onClick={() => more.mutate()}>
            Show more ({(total - rows.length).toLocaleString()} left)
          </Button>
        </div>
      )}
    </>
  );
};

export default SessionProposals;
