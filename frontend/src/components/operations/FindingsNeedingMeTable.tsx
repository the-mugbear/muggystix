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
 * No "Open in Findings" link: the Findings page has no filter that lists
 * exactly these (it filters by owner, not by need), and a tab links only to
 * a list that is exactly its own.
 */
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';

import type { MyFindingItem } from '../../services/api';
import { useListCursor } from '../../hooks/useListCursor';
import { formatRelativeTime } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { ListBody, PagedFooter, WaitingCell, type ListState, type Pager } from './QueueParts';

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

const NO_ROWS: MyFindingItem[] = [];

export const FindingsNeedingMeTable: React.FC<{
  /** The page of the list on screen; null while it loads or when it failed. */
  rows: MyFindingItem[] | null;
  state: ListState;
  pager: Pager;
  /** This list owns the page's j / k / Enter keys (the tab on screen). */
  keysActive?: boolean;
}> = ({ rows: loaded, state, pager, keysActive = true }) => {
  const navigate = useNavigate();
  const rows = loaded ?? NO_ROWS;
  const { cursorRowProps } = useListCursor(
    rows.length,
    (i) => navigate(`/findings/${rows[i].finding_id}`),
    { enabled: keysActive, resetKey: pager.page, getId: (i) => rows[i]?.finding_id },
  );

  return (
    <div className="min-w-0">
      <p className="mb-sm text-caption text-muted-foreground">
        Findings you own that need something from you. A confirmed finding with its report text written is not listed.
      </p>
      <ListBody
        rows={loaded}
        state={state}
        what="the findings that need you"
        empty="Nothing here — a finding you own shows when it is under investigation, a required report section is empty, or a proposal about it is waiting for a decision."
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
                              resolved: its status, its report text and its
                              Proposals section. */}
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
