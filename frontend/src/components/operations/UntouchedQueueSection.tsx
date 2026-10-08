/**
 * "Untouched, with a reason" (5.329.0; it was "Worth a look", v5.223.0).
 *
 * Hosts nobody has touched — no review, assignment, note, test, evidence or
 * finding — that carry a reason to look, in a STATED tier order (there is
 * deliberately no composite score).  Definition and ranking are unchanged;
 * the design review of 2026-10-02 changed how it reads:
 *
 *  - the tiers are filter chips with counts, in tier order — no bars (they
 *    were scaled to the largest tier, so the tier to act on first had the
 *    smallest mark) and no warning colour ("Scans disagree" is not a severity);
 *  - a row is ONE line: address · name · why · Review.  What is true of every
 *    row by the queue's definition ("scanner-reported, unconfirmed") is said
 *    once, under the heading;
 *  - a selection column and "Review" in bulk.
 *
 * 5.331.0 — the content of Operations' "Pick up" tab: the tab is its heading
 * and carries its count; the list is paged like every tab's (`Pager`).  The
 * footer's link opens the exact Hosts list where a query expresses it — a
 * tier 1–3 chip (`TIER_QUERY`).  No query expresses the whole queue (or tier
 * 4 / 5), so there the link is worded as what it opens: every untouched
 * host, with or without a reason — a larger list than this one.
 */
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';

import type { InvestigateRow, InvestigationQueueResponse } from '../../services/api';
import { followHost, unfollowHost } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useListCursor } from '../../hooks/useListCursor';
import { formatApiError } from '../../utils/apiErrors';
import { buildHostsUrl } from '../../utils/drilldownLinks';
import { isPageShortcutEvent } from '../../utils/keyboard';
import { TIER_QUERY, UNTOUCHED_QUERY, fromOperationsQueue } from '../../utils/operationsQueue';
import { formatRelativeTime, formatTimestamp } from '../../utils/relativeTime';
import { runLimited } from '../../utils/runLimited';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import {
  BulkBar, FilterChips, ListBody, PagedFooter, useRowSelection, type ListState, type Pager,
} from './QueueParts';

export const UNTOUCHED_QUEUE_TITLE = 'Untouched, with a reason';
const BULK_CONCURRENCY = 6;

const hosts = (n: number) => `${n.toLocaleString()} host${n === 1 ? '' : 's'}`;

/** One line of why: the reasons the server stated, in its order. */
export const reasonLine = (row: InvestigateRow): string => row.reasons.map((r) => r.text).join(' · ');

export interface UntouchedQueueSectionProps {
  /** The queue's totals and tiers — the last response, kept while the next
   *  page or tier loads so the chips do not blink; null before the first. */
  data: InvestigationQueueResponse | null;
  /** The page of the queue on screen; null while it loads or when it failed. */
  rows: InvestigateRow[] | null;
  state: ListState;
  pager: Pager;
  /** The tier the rows are narrowed to (null = every tier). */
  tier: number | null;
  onTier: (tier: number | null) => void;
  canWrite: boolean;
  onChanged: () => void;
  /** This list owns the page's j / k / Enter / x keys (the tab on screen). */
  keysActive?: boolean;
}

const NO_ROWS: InvestigateRow[] = [];

export const UntouchedQueueSection: React.FC<UntouchedQueueSectionProps> = ({
  data, rows: loaded, state, pager, tier, onTier, canWrite, onChanged, keysActive = true,
}) => {
  const toast = useToast();
  const navigate = useNavigate();
  const rows = loaded ?? NO_ROWS;
  const keys = React.useMemo(() => rows.map((r) => r.host_id), [rows]);
  const selection = useRowSelection(keys);
  const [takingId, setTakingId] = React.useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = React.useState(false);
  const [outcome, setOutcome] = React.useState<string | null>(null);

  const queueTotal = data?.queue_total ?? 0;
  // The list being paged: the whole queue, or the chosen tier's hosts.
  const listTotal = pager.total;
  const navState = fromOperationsQueue(keys, UNTOUCHED_QUEUE_TITLE,
    { partial: listTotal > rows.length, tab: 'pickup' }).state;

  const { cursorRowProps, cursorId } = useListCursor(
    rows.length,
    (i) => navigate(`/hosts/${rows[i].host_id}`, { state: navState }),
    { enabled: keysActive, resetKey: tier, getId: (i) => keys[i] },
  );
  React.useEffect(() => {
    if (!keysActive || !canWrite) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'x' || !isPageShortcutEvent(e)) return;
      if (typeof cursorId !== 'number') return;
      e.preventDefault();
      selection.toggle(cursorId);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // Take the host: In Review under the reader.  The queue lists only hosts
  // nobody follows, so removing the new review is an exact undo.
  const take = async (row: InvestigateRow) => {
    setTakingId(row.host_id);
    try {
      await followHost(row.host_id, 'in_review');
      toast.success(`${row.ip_address} is now in your review queue`, {
        autoHideMs: 6000,
        action: {
          label: 'Undo',
          onClick: () => {
            unfollowHost(row.host_id)
              .then(onChanged)
              .catch((err) => toast.error(formatApiError(err, 'Could not undo.')));
          },
        },
      });
      onChanged();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not take the host into review.'));
    } finally {
      setTakingId(null);
    }
  };

  const takeMany = async () => {
    const ids = selection.selected;
    setBulkBusy(true);
    setOutcome(null);
    const results = await runLimited(ids, BULK_CONCURRENCY, (id) => followHost(id, 'in_review'));
    const failed = results.filter((r) => r.status === 'rejected') as PromiseRejectedResult[];
    const done = results.length - failed.length;
    if (failed.length === 0) {
      toast.success(`${hosts(done)} now in your review queue`, { autoHideMs: 3000 });
    } else {
      setOutcome(
        `Took ${done} of ${hosts(results.length)} into review; ${failed.length} could not be taken `
        + `(${formatApiError(failed[0].reason, 'the request failed')}). They are still listed below.`,
      );
    }
    selection.clear();
    setBulkBusy(false);
    if (done > 0) onChanged();
  };

  const description = (
    'Hosts nobody has touched — no review, assignment, note, test, evidence or finding — that carry a '
    + 'reason to look. Every reason here is scanner-reported and unconfirmed. Ordered by the stated tier, '
    + `never a score${canWrite ? '; Review takes a host into your queue' : ''}.`
  );

  // The exact Hosts list of what is paged here, where a query expresses it.
  const tierQuery = tier != null ? TIER_QUERY[tier] : undefined;
  const untouchedTotal = data?.untouched_total ?? 0;
  const openAll = tierQuery
    ? { to: buildHostsUrl({ q: tierQuery }), label: `Open all ${hosts(listTotal)} in Hosts` }
    // No query expresses this set (every tier together, or tier 4 / 5): the
    // link says what it opens — a LARGER list than this one.
    : data
      ? {
          to: buildHostsUrl({ q: UNTOUCHED_QUERY }),
          label: `All ${untouchedTotal.toLocaleString()} untouched host${untouchedTotal === 1 ? '' : 's'} in Hosts, with or without a reason`,
        }
      : undefined;

  const empty = tier != null && queueTotal > 0
    ? 'Nothing here — no untouched host is in this tier.'
    : untouchedTotal > 0
      ? <>
          Nothing here —{' '}
          <Link to={buildHostsUrl({ q: UNTOUCHED_QUERY })} className="text-info hover:underline">
            {hosts(untouchedTotal)}
          </Link>{' '}
          {untouchedTotal === 1 ? 'is' : 'are'} untouched, none with a weakness or change on record. A host shows here
          when a scan reports one on it.
        </>
      : 'Nothing here — every host has been touched by someone. A newly imported host shows here when it carries a weakness or a change.';

  return (
    <div className="min-w-0">
      <p className="mb-sm text-caption text-muted-foreground">{description}</p>
      {data?.tier_counts && queueTotal > 0 && (
        <FilterChips
          label="Filter by tier"
          allLabel="All tiers"
          allCount={queueTotal}
          chips={data.tiers.map((label, i) => ({
            key: i + 1,
            label,
            count: data.tier_counts?.[i] ?? 0,
            // The tier to act on first is marked by position and weight.
            strong: i === 0,
          }))}
          selected={tier}
          onSelect={onTier}
        />
      )}
      <ListBody rows={loaded} state={state} what="the untouched hosts with a reason to look" empty={empty}>
        {() => (
        <div>
          {canWrite && (
            <BulkBar count={selection.selected.length} noun="host" onClear={selection.clear} outcome={outcome}>
              <Button size="sm" variant="outline" className="h-7" disabled={bulkBusy} onClick={() => void takeMany()}>
                {bulkBusy ? 'Taking…' : `Review (${selection.selected.length})`}
              </Button>
            </BulkBar>
          )}
          <div className="min-w-0 overflow-x-auto">
          <Table aria-label={UNTOUCHED_QUEUE_TITLE}>
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
                    tier give way first (both stay on the row's tooltips). */}
                <TableHead className="w-[8.5rem]">Host</TableHead>
                <TableHead className="hidden w-[18%] lg:table-cell">Name</TableHead>
                <TableHead>Why</TableHead>
                {tier == null && <TableHead className="hidden w-[20%] md:table-cell">Tier</TableHead>}
                {canWrite && <TableHead className="w-[5.5rem] text-right">Action</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, i) => {
                const isSelected = selection.isSelected(row.host_id);
                const why = reasonLine(row);
                // Provenance only when there is some: which tools saw the
                // host, and when it was last observed (a date, not a state).
                const sources = row.evidence.sources.length ? row.evidence.sources.join(', ') : null;
                const seen = row.evidence.last_seen
                  ? formatRelativeTime(row.evidence.last_seen, { style: 'compact' }) : null;
                const provenance = sources ? `${sources}${seen ? ` · last observed ${seen}` : ''}` : null;
                const tip = [
                  `${row.tier_label}: ${why}`,
                  !row.next_action.generic ? row.next_action.text : null,
                  sources ? `Reported by ${sources}.` : null,
                  row.evidence.last_seen ? `Last observed ${formatTimestamp(row.evidence.last_seen)}.` : null,
                ].filter(Boolean).join(' — ');
                return (
                  <TableRow
                    key={row.host_id}
                    data-tier={row.tier}
                    {...cursorRowProps(i)}
                    data-state={isSelected ? 'selected' : undefined}
                    {...(canWrite ? { 'aria-selected': isSelected } : {})}
                  >
                    {canWrite && (
                      <TableCell className="align-middle">
                        <Checkbox
                          checked={isSelected}
                          onCheckedChange={() => selection.toggle(row.host_id)}
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
                    <TableCell className="truncate align-middle" title={tip}>
                      {why}
                      {provenance && <span className="text-caption text-muted-foreground"> · {provenance}</span>}
                    </TableCell>
                    {tier == null && (
                      <TableCell className="hidden truncate align-middle text-caption text-muted-foreground md:table-cell" title={row.tier_label}>
                        {row.tier_label}
                      </TableCell>
                    )}
                    {canWrite && (
                      <TableCell className="whitespace-nowrap py-xxs text-right align-middle">
                        <Button
                          size="sm" variant="ghost" className="h-7 text-info"
                          disabled={takingId === row.host_id || bulkBusy}
                          onClick={() => void take(row)}
                          title="Mark this host In Review under you. It leaves this queue and joins your own."
                        >
                          {takingId === row.host_id ? 'Taking…' : 'Review'}
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          </div>
          <PagedFooter pager={pager} shown={rows.length} noun="hosts" openAll={openAll} />
        </div>
        )}
      </ListBody>
    </div>
  );
};

export default UntouchedQueueSection;
