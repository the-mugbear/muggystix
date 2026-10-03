/**
 * Operations' "Findings" tab (5.331.0): the findings the reader owns that
 * NEED them, one line each, a page at a time.
 *
 * A finding is listed for a reason, and the row says which — it is under
 * investigation, a required report section is empty (named), or proposals are
 * waiting for a decision.  Owning a confirmed, written-up finding is a state,
 * not work, and is not here.  Severity wears the severity ramp
 * (`SeverityBadge` tokens) — nothing else on the page does.
 *
 * The chips narrow it to one kind of work (`?need=`), with the server's
 * counts: a decision (under investigation, or a proposal to decide) or report
 * text alone.  A finding is under one — a decision comes before the writing.
 *
 * No "Open in Findings" link: the Findings page has no filter that lists
 * exactly these (it filters by owner, not by need), and a tab links only to
 * a list that is exactly its own.
 */
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';

import type { FindingNeed, MyFindingItem } from '../../services/api';
import { useListCursor } from '../../hooks/useListCursor';
import { FINDING_NEEDS, FINDING_NEED_LABEL } from '../../utils/operationsTabs';
import { formatRelativeTime } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import {
  FilterChips, ListBody, PagedFooter, WaitingCell, type ListState, type Pager,
} from './QueueParts';

export const FINDINGS_TAB_TITLE = 'Findings that need me';

type BadgeVariant = React.ComponentProps<typeof Badge>['variant'];
/** A finding's severity wears the severity ramp. */
const SEVERITY_VARIANT: Record<string, BadgeVariant> = {
  critical: 'severity-critical',
  high: 'severity-high',
  medium: 'severity-medium',
  low: 'severity-low',
  info: 'severity-info',
};

/** What is owed, in the order to act on. */
export const needsLine = (f: MyFindingItem): string =>
  (f.needs ?? []).map((n) => n.text).join(' · ');

const NEED_TITLE: Record<FindingNeed, string> = {
  decide: 'Findings under investigation, or with a proposal waiting for your decision.',
  write: 'Findings with nothing to decide whose required report text is missing.',
};
const EMPTY_NEED: Record<FindingNeed, string> = {
  decide: 'Nothing here — no finding you own is under investigation or has a proposal waiting for a decision.',
  write: 'Nothing here — no finding you own is only missing required report text.',
};

const NO_ROWS: MyFindingItem[] = [];

export interface FindingsNeedingMeTableProps {
  /** The page of the list on screen; null while it loads or when it failed. */
  rows: MyFindingItem[] | null;
  state: ListState;
  pager: Pager;
  /** The server's count per kind of work; null when not known. */
  needCounts: Record<FindingNeed, number | null>;
  /** The kind the rows are narrowed to (null = both). */
  need: FindingNeed | null;
  onNeed: (need: FindingNeed | null) => void;
  /** This list owns the page's j / k / Enter keys (the tab on screen). */
  keysActive?: boolean;
}

export const FindingsNeedingMeTable: React.FC<FindingsNeedingMeTableProps> = ({
  rows: loaded, state, pager, needCounts, need, onNeed, keysActive = true,
}) => {
  const navigate = useNavigate();
  const rows = loaded ?? NO_ROWS;
  const { cursorRowProps } = useListCursor(
    rows.length,
    (i) => navigate(`/findings/${rows[i].finding_id}`),
    { enabled: keysActive, resetKey: `${need}:${pager.page}`, getId: (i) => rows[i]?.finding_id },
  );
  // The two kinds partition the list; one not known leaves the sum not known.
  const all = needCounts.decide == null || needCounts.write == null
    ? null : needCounts.decide + needCounts.write;

  return (
    <div className="min-w-0">
      <p className="mb-sm text-caption text-muted-foreground">
        Findings you own that need something from you. A confirmed finding with its report text written is not listed.
      </p>
      <FilterChips
        label="Filter by what the finding needs"
        allLabel="All"
        allCount={all}
        chips={FINDING_NEEDS.map((k) => ({
          key: k, label: FINDING_NEED_LABEL[k], count: needCounts[k], title: NEED_TITLE[k],
        }))}
        selected={need}
        onSelect={onNeed}
      />
      <ListBody
        rows={loaded}
        state={state}
        what="the findings that need you"
        empty={need != null
          ? EMPTY_NEED[need]
          : 'Nothing here — a finding you own shows when it is under investigation, a required report section is empty, or a proposal about it is waiting for a decision.'}
      >
        {() => (
          <div>
            <div className="min-w-0 overflow-x-auto">
              <Table aria-label={FINDINGS_TAB_TITLE}>
                <TableHeader>
                  <TableRow className="hover:bg-transparent hover:shadow-none">
                    <TableHead className="w-[6.5rem]">Severity</TableHead>
                    <TableHead className="w-[4.5rem]">#</TableHead>
                    <TableHead>Finding</TableHead>
                    {/* NEEDS is the point of the row: it has the larger share
                        and wraps to two lines before it is cut (it was cut on
                        every row at a 1,126px window). */}
                    <TableHead className="w-[44%]">Needs</TableHead>
                    <TableHead className="w-[5.5rem] text-right"
                      title="How long since the finding last changed.">
                      Waiting
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((f, i) => {
                    const owed = needsLine(f);
                    const hostCount = `${f.host_count.toLocaleString()} host${f.host_count === 1 ? '' : 's'}`;
                    return (
                      <TableRow key={f.finding_id} {...cursorRowProps(i)}>
                        <TableCell className="truncate align-middle">
                          <Badge variant={SEVERITY_VARIANT[f.severity] ?? 'muted'} className="max-w-full whitespace-nowrap">
                            <span className="truncate">{f.severity}</span>
                          </Badge>
                        </TableCell>
                        <TableCell className="truncate align-middle tabular-nums text-muted-foreground">
                          {f.finding_id}
                        </TableCell>
                        <TableCell className="truncate align-middle" title={`${f.title} · ${hostCount}`}>
                          {/* The finding's page is where each of the three is
                              resolved: its status, its report text and the
                              proposals waiting in it. */}
                          <Link
                            to={`/findings/${f.finding_id}`}
                            className="rounded font-medium text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {f.title || `Finding ${f.finding_id}`}
                          </Link>
                          <span className="text-caption text-muted-foreground"> · {hostCount}</span>
                        </TableCell>
                        <TableCell className="align-middle" data-testid="finding-needs">
                          <span className="line-clamp-2 break-words" title={owed || undefined}>
                            {owed || '—'}
                          </span>
                        </TableCell>
                        <TableCell className="truncate text-right align-middle">
                          <WaitingCell
                            waiting={f.updated_at ? formatRelativeTime(f.updated_at, { style: 'compact' }) : ''}
                          />
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            <PagedFooter pager={pager} shown={rows.length} noun="findings" />
          </div>
        )}
      </ListBody>
    </div>
  );
};

export default FindingsNeedingMeTable;
