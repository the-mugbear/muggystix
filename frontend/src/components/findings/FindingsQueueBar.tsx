/**
 * Previous / Next through the Findings list a finding was opened from
 * (owner, 2026-10-23): the LIST decides what is walked — an analyst on
 * Owner: me steps through their own findings, an admin on an unfiltered list
 * through all of them.  The list is the `from` address the row link carries
 * (`utils/findingsReturn`), read with the list's own reading
 * (`findingQueueFilters`); without one it is the default list.
 *
 * The server says where this finding stands and what is either side
 * (`GET …/findings/{id}/neighbours`, the list's own selection and order).
 * The finding's `revision` is in the key, so a write that changes it — a
 * status that takes it out of the list, an owner, a severity that moves it —
 * reads its place again.  A finding no longer in the list has no position;
 * its neighbours are where it sat.  `j` / `k` step, as on the list.
 *
 * The report draft's bar (`ReportDraftBar`) takes this place when the page
 * was opened from a draft's checklist.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { getFindingNeighbours, type FindingFilters, type FindingQueueStep } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts';
import { queryErrorText, useLastSettled } from '../../lib/query';
import { FINDINGS_ROUTE, findingDetailHref } from '../../utils/findingsReturn';

interface Props {
  findingId: number;
  /** The cached finding's revision: a write that lands on it reads the place again. */
  revision: number;
  /** What the list asks for (`findingQueueFilters`). */
  filters: FindingFilters;
  /** The list's address, `/findings?…` (validated). */
  returnTo: string;
  /** Leaving goes through the page's unsaved-text guard. */
  onNavigate: (to: string) => void;
}

const FindingsQueueBar: React.FC<Props> = ({ findingId, revision, filters, returnTo, onNavigate }) => {
  const projectId = useProjectId();
  const query = useQuery({
    queryKey: ['getFindingNeighbours', projectId, findingId, filters, revision],
    queryFn: ({ signal }) => getFindingNeighbours(projectId, findingId, filters, signal),
  });
  // The last answer for THIS finding stays while a write's re-read is out.
  const answer = useLastSettled(query.data, { resetKey: findingId });

  const listSearch = returnTo.slice(FINDINGS_ROUTE.length);
  const hrefOf = (step: FindingQueueStep) => findingDetailHref(step.id, listSearch);
  const go = (step: FindingQueueStep | null | undefined) => { if (step) onNavigate(hrefOf(step)); };
  useKeyboardShortcuts({ j: () => go(answer?.next), k: () => go(answer?.previous) });

  const link = (step: FindingQueueStep, children: React.ReactNode, label: string) => (
    <Link to={hrefOf(step)} aria-label={`${label}: ${step.title}`} title={step.title}
      className="inline-flex shrink-0 items-center gap-xxs text-info hover:underline"
      onClick={(e) => { e.preventDefault(); onNavigate(hrefOf(step)); }}>
      {children}
    </Link>
  );

  let where: React.ReactNode = null;
  if (answer) {
    const n = answer.total.toLocaleString();
    where = answer.position != null
      ? `Finding ${answer.position.toLocaleString()} of ${n} in the list`
      : `No longer in the list (${n} remain${answer.total === 1 ? 's' : ''})`;
  } else if (query.isError) {
    where = <span className="text-destructive">{queryErrorText(query.error, 'Could not read the list.')}</span>;
  }
  if (!where) return null;

  return (
    <nav aria-label="Findings list" data-testid="findings-queue-bar"
      className="mb-sm flex min-w-0 flex-wrap items-center gap-x-md gap-y-xxs border-b border-border pb-xs text-caption">
      <span className="min-w-0 break-words text-muted-foreground" data-testid="findings-queue-position">{where}</span>
      {answer?.previous && link(answer.previous, <><ChevronLeft className="size-3.5" aria-hidden /> Previous</>, 'Previous')}
      {answer?.next && link(answer.next, <>Next <ChevronRight className="size-3.5" aria-hidden /></>, 'Next')}
    </nav>
  );
};

export default FindingsQueueBar;
