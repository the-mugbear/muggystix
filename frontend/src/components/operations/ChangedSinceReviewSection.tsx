/**
 * "Changed since review" (5.329.0; it was "Needs another look", v5.237.0).
 *
 * The READER'S OWN reviewed hosts that are not done (5.330.0 — Operations is
 * the reader's page; it listed every teammate's reviews, marked "by you"):
 * the host gained open ports or critical / high scanner observations after
 * the reader's review, or the reader concluded "needs more evidence".  The
 * team-wide lists are on Hosts (`has:changed_since_review`,
 * `conclusion:needs_evidence`).  Two answers:
 *
 *  - **Still reviewed** — "I saw the change; my review stands": the review
 *    date moves to now, the conclusion stays.  Never on a "needs more
 *    evidence" conclusion (an open question is not answered by looking again).
 *  - **Re-open review** — back In Review, with a confirming second click: it
 *    clears the conclusion.
 *
 * One line per host, a selection column and both actions in bulk.  Readers (a
 * role that cannot write) get the rows and the links, no checkboxes and no
 * actions.  "Open all N in Hosts" opens `follow:revisit` — the same rows,
 * counted by the same predicate.
 *
 * 5.331.0 — the content of Operations' "Changed since review" tab: the tab
 * is its heading and carries its count; this is one page of the list (the
 * caller pages it — `Pager`), with the panel's states from `ListBody`.
 *
 * 5.351.0 — an action says which reads are out of date
 * (`QueueParts.useOperationsChanged`): the list and the page's counts are
 * read again in place.  There is no `onChanged` for the parent to wire.
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';

import type { ReviewFollowupRow } from '../../services/api';
import { followHost, markStillReviewed } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useListCursor } from '../../hooks/useListCursor';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import { buildHostsUrl } from '../../utils/drilldownLinks';
import { isPageShortcutEvent } from '../../utils/keyboard';
import { CHANGED_SINCE_REVIEW_QUERY, fromOperationsQueue } from '../../utils/operationsQueue';
import { formatRelativeTime, formatTimestamp } from '../../utils/relativeTime';
import { runLimited } from '../../utils/runLimited';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import {
  BulkBar, ListBody, PagedFooter, useOperationsChanged, useRowSelection, type ListState, type Pager,
} from './QueueParts';

export const CHANGED_SINCE_REVIEW_TITLE = 'Changed since review';
/** Requests at once when a bulk action is one call per host. */
const BULK_CONCURRENCY = 6;

/** One row per host: every row is the reader's own review. */
const rowKey = (row: ReviewFollowupRow) => String(row.host_id);
/** "Still reviewed" is not an answer to an open question. */
export const canConfirmReview = (row: ReviewFollowupRow) =>
  row.review_conclusion !== 'needs_evidence';

const ago = (iso: string | null) => (iso ? formatRelativeTime(iso, { style: 'compact' }) : '');
const hosts = (n: number) => `${n.toLocaleString()} host${n === 1 ? '' : 's'}`;

const NO_ROWS: ReviewFollowupRow[] = [];

export const ChangedSinceReviewSection: React.FC<{
  /** The page of the list on screen; null while it loads or when it failed. */
  rows: ReviewFollowupRow[] | null;
  state: ListState;
  pager: Pager;
  /** The reader's project role allows writes (hooks/useProjectRole). */
  canWrite: boolean;
  /** This list owns the page's j / k / Enter / x keys (the tab on screen). */
  keysActive?: boolean;
}> = ({ rows: loaded, state, pager, canWrite, keysActive = true }) => {
  const toast = useToast();
  const navigate = useNavigate();
  // After an action: the list and the counts are read again, in place.
  const changed = useOperationsChanged();
  const rows = loaded ?? NO_ROWS;
  const keys = React.useMemo(() => rows.map(rowKey), [rows]);
  const selection = useRowSelection(keys);
  const [outcome, setOutcome] = React.useState<string | null>(null);
  // Re-opening a finished review clears its conclusion, which no undo puts
  // back exactly: it asks for a second click (one row, or the bulk button).
  const [armed, setArmed] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (armed == null) return undefined;
    const t = setTimeout(() => setArmed(null), 5000);
    return () => clearTimeout(t);
  }, [armed]);

  const hostTotal = pager.total;
  const navState = fromOperationsQueue(rows.map((r) => r.host_id), CHANGED_SINCE_REVIEW_TITLE,
    { partial: pager.total > rows.length, tab: 'changed' }).state;

  const { cursorRowProps, cursorId } = useListCursor(
    rows.length,
    (i) => navigate(`/hosts/${rows[i].host_id}`, { state: navState }),
    { enabled: keysActive, getId: (i) => keys[i] },
  );
  // x — tick the row under the cursor (the Hosts page's key).  The cursor is
  // anchored by id, so the key acts on the row it is ON after a reload.
  React.useEffect(() => {
    if (!keysActive || !canWrite) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'x' || !isPageShortcutEvent(e)) return;
      if (typeof cursorId !== 'string') return;
      e.preventDefault();
      selection.toggle(cursorId);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // "Still reviewed" — one request for one row or for the selection (all or
  // nothing on the server).  `row` names the row whose own button asked.
  const confirm = useMutation({
    mutationFn: ({ targets }: { targets: ReviewFollowupRow[]; row: string | null }) =>
      markStillReviewed([...new Set(targets.map((r) => r.host_id))]),
    onSuccess: (_saved, { targets }) => {
      const count = new Set(targets.map((r) => r.host_id)).size;
      toast.success(
        count === 1
          ? `${targets[0].ip_address}: your review stands as of now`
          : `Your review of ${hosts(count)} stands as of now`,
        { autoHideMs: 3000 },
      );
      selection.clear();
      changed();
    },
    // All or nothing on the server: nothing was changed.
    onError: (err) => toast.error(formatApiError(err, 'Could not confirm the review. Nothing was changed.')),
  });
  const stillReviewed = (targets: ReviewFollowupRow[], row: string | null) => {
    if (targets.length === 0) return;
    setOutcome(null);
    confirm.mutate({ targets, row });
  };

  const reopen = useMutation({
    mutationFn: (row: ReviewFollowupRow) => followHost(row.host_id, 'in_review'),
    onSuccess: (_follow, row) => {
      toast.success(`${row.ip_address} is back in your review queue`, { autoHideMs: 2500 });
      changed();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not re-open the review.')),
  });
  const reopenOne = (row: ReviewFollowupRow) => {
    const key = rowKey(row);
    if (armed !== key) {
      setArmed(key);
      return;
    }
    setArmed(null);
    reopen.mutate(row);
  };

  const picked = rows.filter((r) => selection.isSelected(rowKey(r)));
  const confirmable = picked.filter(canConfirmReview);
  const pickedHostIds = picked.map((r) => r.host_id);

  // One call per host, a few at a time: the batch is one action, and it
  // settles with every host's own outcome.
  const reopenBulk = useMutation({
    mutationFn: (hostIds: number[]) =>
      runLimited(hostIds, BULK_CONCURRENCY, (id) => followHost(id, 'in_review')),
    onSuccess: (results) => {
      const failed = results.filter((r) => r.status === 'rejected') as PromiseRejectedResult[];
      const done = results.length - failed.length;
      if (failed.length === 0) {
        toast.success(`${hosts(done)} back in your review queue`, { autoHideMs: 3000 });
      } else {
        // Partial failure, said as it is: what moved, what did not, and why.
        setOutcome(
          `Re-opened ${done} of ${hosts(results.length)}; ${failed.length} could not be re-opened `
          + `(${formatApiError(failed[0].reason, 'the request failed')}). They are still listed below.`,
        );
      }
      selection.clear();
      if (done > 0) changed();
    },
  });
  const reopenMany = () => {
    if (armed !== 'bulk') {
      setArmed('bulk');
      return;
    }
    setArmed(null);
    setOutcome(null);
    reopenBulk.mutate(pickedHostIds);
  };

  // The row whose own action is in flight, and whether a bulk one is.
  const busyKey = confirm.isPending && confirm.variables.row != null
    ? confirm.variables.row
    : reopen.isPending ? rowKey(reopen.variables) : null;
  const bulkBusy = reopenBulk.isPending || (confirm.isPending && confirm.variables.row == null);

  return (
    <div className="min-w-0">
      <p className="mb-sm text-caption text-muted-foreground">
        Hosts you reviewed that are not done: the host gained open ports or critical / high scanner
        observations after your review, or you concluded{' '}
        <span className="font-medium text-foreground">“Needs more evidence”</span>.
        A teammate’s reviews are not listed here.
      </p>
      <ListBody
        rows={loaded}
        state={state}
        what="the hosts you reviewed"
        empty="Nothing here — a host you reviewed shows when it gains open ports or critical / high scanner observations after your review, or when you conclude “needs more evidence”."
      >
        {() => (
        <div>
          {canWrite && (
            <BulkBar count={picked.length} noun="review" onClear={selection.clear} outcome={outcome}>
              <Button
                size="sm" variant="outline" className="h-7"
                disabled={bulkBusy || confirmable.length === 0}
                onClick={() => stillReviewed(confirmable, null)}
                title={confirmable.length < picked.length
                  ? `${picked.length - confirmable.length} of the selected reviews cannot be confirmed here: concluded “needs more evidence”.`
                  : 'You looked at what changed and your review stands: the review date moves to now, the conclusion stays.'}
              >
                Still reviewed ({confirmable.length})
              </Button>
              <Button size="sm" variant="outline" className="h-7" disabled={bulkBusy} onClick={reopenMany}>
                {armed === 'bulk'
                  ? `Click to confirm — clears ${picked.length} conclusion${picked.length === 1 ? '' : 's'}`
                  : `Re-open review (${pickedHostIds.length})`}
              </Button>
            </BulkBar>
          )}
          {/* The table never widens the page: its columns fit, and if a
              browser disagrees the scroll stays inside this box. */}
          <div className="min-w-0 overflow-x-auto">
          <Table aria-label={CHANGED_SINCE_REVIEW_TITLE}>
            <TableHeader>
              <TableRow className="hover:bg-transparent hover:shadow-none">
                {canWrite && (
                  <TableHead className="w-8">
                    <Checkbox
                      checked={selection.allState}
                      onCheckedChange={(v) => selection.toggleAll(v === true)}
                      aria-label="Select all rows shown"
                    />
                  </TableHead>
                )}
                {/* Fixed widths that fit a narrowed window: the name and the
                    review's age give way first (both stay on the row's
                    tooltips), the reason keeps what is left. */}
                <TableHead className="w-[8.5rem]">Host</TableHead>
                <TableHead className="hidden w-[18%] lg:table-cell">Name</TableHead>
                <TableHead>What changed</TableHead>
                <TableHead className="hidden w-[6rem] md:table-cell">Reviewed</TableHead>
                {canWrite && <TableHead className="w-[14.5rem] text-right">Action</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, i) => {
                const key = rowKey(row);
                const isSelected = selection.isSelected(key);
                const reason = row.reasons.map((r) => r.text).join(' · ');
                const when = ago(row.reviewed_at);
                return (
                  <TableRow
                    key={key}
                    {...cursorRowProps(i)}
                    data-state={isSelected ? 'selected' : undefined}
                    {...(canWrite ? { 'aria-selected': isSelected } : {})}
                  >
                    {canWrite && (
                      <TableCell className="align-middle">
                        <Checkbox
                          checked={isSelected}
                          onCheckedChange={() => selection.toggle(key)}
                          aria-label={`Select ${row.ip_address}`}
                        />
                      </TableCell>
                    )}
                    <TableCell className="truncate align-middle font-mono">
                      <Link
                        to={`/hosts/${row.host_id}`}
                        state={navState}
                        title={row.hostname ? `${row.ip_address} · ${row.hostname}` : row.ip_address}
                        className="rounded text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {row.ip_address}
                      </Link>
                    </TableCell>
                    <TableCell className="hidden truncate align-middle text-muted-foreground lg:table-cell" title={row.hostname ?? undefined}>
                      {row.hostname || '—'}
                    </TableCell>
                    <TableCell className="truncate align-middle" title={reason}>{reason}</TableCell>
                    {/* The age only: every row is the reader's own review. */}
                    <TableCell
                      className="hidden truncate align-middle text-caption text-muted-foreground md:table-cell"
                      title={[
                        row.reviewed_at
                          ? `You reviewed it on ${formatTimestamp(row.reviewed_at)}.`
                          : 'Your review has no recorded date.',
                        row.review_summary ? `“${row.review_summary}”` : null,
                      ].filter(Boolean).join(' ')}
                    >
                      {when || '—'}
                    </TableCell>
                    {canWrite && (
                      <TableCell className="whitespace-nowrap py-xxs text-right align-middle">
                        {/* Gives way to the confirmation, which needs the width. */}
                        {canConfirmReview(row) && armed !== key && (
                          <Button
                            size="sm" variant="ghost" className="h-7 text-info"
                            disabled={busyKey === key || bulkBusy}
                            onClick={() => stillReviewed([row], key)}
                            title="You looked at what changed and your review stands: the review date moves to now, the conclusion stays."
                          >
                            Still reviewed
                          </Button>
                        )}
                        <Button
                          size="sm" variant="ghost" className={cn('h-7', !canConfirmReview(row) && 'text-info')}
                          disabled={busyKey === key || bulkBusy}
                          onClick={() => reopenOne(row)}
                          title="Put this host back In Review. It returns to your queue and your conclusion is cleared — click again to confirm."
                        >
                          {armed === key ? 'Confirm: clears the conclusion' : 'Re-open review'}
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          </div>
          <PagedFooter
            pager={pager}
            shown={rows.length}
            noun="hosts"
            openAll={{
              to: buildHostsUrl({ q: CHANGED_SINCE_REVIEW_QUERY }),
              label: `Open all ${hosts(hostTotal)} in Hosts`,
            }}
          />
        </div>
        )}
      </ListBody>
    </div>
  );
};

export default ChangedSinceReviewSection;
