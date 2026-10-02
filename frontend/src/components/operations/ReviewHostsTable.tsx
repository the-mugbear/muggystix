/**
 * Operations' "Hosts" tab (5.331.0): the hosts the reader has In Review, one
 * line each, a page at a time — most recently taken first.
 *
 * Read-only here, as the group was on "My work": a review is concluded on the
 * host's own page.  "Open all N in Hosts" opens `follow:mine` — exactly these
 * hosts, counted by the same predicate.
 */
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';

import type { MyAttentionHost } from '../../services/api';
import { useListCursor } from '../../hooks/useListCursor';
import { cn } from '../../utils/cn';
import { buildHostsUrl } from '../../utils/drilldownLinks';
import { MY_REVIEW_QUERY, fromOperationsQueue } from '../../utils/operationsQueue';
import { formatRelativeTime } from '../../utils/relativeTime';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { ListBody, PagedFooter, WaitingCell, type ListState, type Pager } from './QueueParts';

export const HOSTS_TAB_TITLE = 'Hosts I am reviewing';

const NO_ROWS: MyAttentionHost[] = [];

/** A count of scanner observations: its severity's colour when there is one,
 *  a quiet zero otherwise. */
const SeverityCount: React.FC<{ n: number; tone: string; label: string }> = ({ n, tone, label }) => (
  <span
    className={cn('tabular-nums', n > 0 ? cn('font-medium', tone) : 'text-muted-foreground')}
    aria-label={`${n.toLocaleString()} ${label}`}
  >
    {n.toLocaleString()}
  </span>
);

export const ReviewHostsTable: React.FC<{
  /** The page of the list on screen; null while it loads or when it failed. */
  rows: MyAttentionHost[] | null;
  state: ListState;
  pager: Pager;
  /** The reader's role allows writes — only the empty line's wording depends on it. */
  canWrite?: boolean;
  keysActive?: boolean;
}> = ({ rows: loaded, state, pager, canWrite = true, keysActive = true }) => {
  const navigate = useNavigate();
  const rows = loaded ?? NO_ROWS;
  // The page's hosts travel with the navigation, so the host page's Next
  // walks the list the reader was looking at.
  const navState = fromOperationsQueue(rows.map((r) => r.host_id), HOSTS_TAB_TITLE,
    { partial: pager.total > rows.length, tab: 'hosts' }).state;
  const { cursorRowProps } = useListCursor(
    rows.length,
    (i) => navigate(`/hosts/${rows[i].host_id}`, { state: navState }),
    { enabled: keysActive, resetKey: pager.page, getId: (i) => rows[i]?.host_id },
  );

  return (
    <div className="min-w-0">
      <p className="mb-sm text-caption text-muted-foreground">
        Hosts you have In Review, most recently taken first. Critical and high count scanner observations on the host.
      </p>
      <ListBody
        rows={loaded}
        state={state}
        what="the hosts you are reviewing"
        empty={canWrite
          ? 'Nothing here — a host shows when you take it into review (Review on the Pick up tab, or In Review on the host’s page).'
          : 'Nothing here — a host shows when you have it In Review.'}
      >
        {() => (
          <div>
            <div className="min-w-0 overflow-x-auto">
              <Table aria-label={HOSTS_TAB_TITLE}>
                <TableHeader>
                  <TableRow className="hover:bg-transparent hover:shadow-none">
                    <TableHead className="w-[8.5rem]">Host</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead className="w-[7rem] text-right">Open ports</TableHead>
                    <TableHead className="w-[6rem] text-right">Critical</TableHead>
                    <TableHead className="w-[5rem] text-right">High</TableHead>
                    <TableHead className="w-[5.5rem] text-right"
                      title="How long since you took the host into review, or last changed your review of it.">
                      Waiting
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((h, i) => (
                    <TableRow key={h.host_id} {...cursorRowProps(i)}>
                      <TableCell className="truncate align-middle font-mono">
                        <Link
                          to={`/hosts/${h.host_id}`}
                          state={navState}
                          title={h.hostname ? `${h.ip_address} · ${h.hostname}` : h.ip_address}
                          className="rounded text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {h.ip_address}
                        </Link>
                      </TableCell>
                      <TableCell className="truncate align-middle text-muted-foreground" title={h.hostname ?? undefined}>
                        {h.hostname || '—'}
                      </TableCell>
                      <TableCell className="truncate text-right align-middle tabular-nums">
                        {h.open_port_count.toLocaleString()}
                      </TableCell>
                      <TableCell className="truncate text-right align-middle">
                        <SeverityCount n={h.critical_vulns} tone="text-sev-critical" label="critical scanner observations" />
                      </TableCell>
                      <TableCell className="truncate text-right align-middle">
                        <SeverityCount n={h.high_vulns} tone="text-sev-high" label="high scanner observations" />
                      </TableCell>
                      <TableCell className="truncate text-right align-middle">
                        <WaitingCell
                          waiting={h.follow_updated_at ? formatRelativeTime(h.follow_updated_at, { style: 'compact' }) : ''}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <PagedFooter
              pager={pager}
              shown={rows.length}
              noun="hosts"
              openAll={{
                to: buildHostsUrl({ q: MY_REVIEW_QUERY }),
                label: `Open all ${pager.total.toLocaleString()} in Hosts`,
              }}
            />
          </div>
        )}
      </ListBody>
    </div>
  );
};

export default ReviewHostsTable;
