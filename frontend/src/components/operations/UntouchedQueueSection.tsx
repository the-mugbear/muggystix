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
 * The count opens its exact Hosts list where a query expresses the tier
 * (`TIER_QUERY`); where none does, the queue pages in place and the link
 * names what it does open — every untouched host.
 */
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import type { InvestigateRow, InvestigationQueueResponse } from '../../services/api';
import { followHost, unfollowHost } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useListCursor } from '../../hooks/useListCursor';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import { buildHostsUrl } from '../../utils/drilldownLinks';
import { isPageShortcutEvent } from '../../utils/keyboard';
import { TIER_QUERY, UNTOUCHED_QUERY, fromOperationsQueue } from '../../utils/operationsQueue';
import { formatRelativeTime } from '../../utils/relativeTime';
import { runLimited } from '../../utils/runLimited';
import PostureSection, { SectionCount } from '../posture/PostureSection';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { BulkBar, ListFooter, UnavailableLine, useRowSelection } from './QueueParts';

export const UNTOUCHED_QUEUE_TITLE = 'Untouched, with a reason';
/** Rows per page of the queue, and per "Show more". */
export const UNTOUCHED_PAGE = 15;
/** The queue route's ceiling for one request. */
export const UNTOUCHED_MAX_ROWS = 100;
const BULK_CONCURRENCY = 6;

const hosts = (n: number) => `${n.toLocaleString()} host${n === 1 ? '' : 's'}`;

/** One line of why: the reasons the server stated, in its order. */
export const reasonLine = (row: InvestigateRow): string => row.reasons.map((r) => r.text).join(' · ');

/** The tiers as filter chips with their whole-queue counts, in tier order. */
const TierChips: React.FC<{
  tiers: string[];
  counts: number[];
  total: number;
  selected: number | null;
  onSelect?: (tier: number | null) => void;
}> = ({ tiers, counts, total, selected, onSelect }) => {
  const chip = (on: boolean, first: boolean) => cn(
    'inline-flex max-w-full items-center gap-xxs rounded-chip border px-xs py-px text-caption',
    'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
    on ? 'border-primary bg-primary text-primary-foreground' : 'border-border text-foreground hover:bg-accent',
    // The tier to act on first is marked by position and weight, not by size.
    first && 'font-semibold',
  );
  return (
    <div role="group" aria-label="Filter by tier" className="mb-sm flex min-w-0 flex-wrap items-center gap-xs">
      <button type="button" aria-pressed={selected == null} onClick={() => onSelect?.(null)}
        disabled={!onSelect} className={chip(selected == null, false)}>
        All tiers <span className="tabular-nums">{total.toLocaleString()}</span>
      </button>
      {tiers.map((label, i) => {
        const tier = i + 1;
        const n = counts[i] ?? 0;
        const on = selected === tier;
        return n > 0 || on ? (
          <button
            key={label}
            type="button"
            aria-pressed={on}
            disabled={!onSelect}
            onClick={() => onSelect?.(on ? null : tier)}
            title={on ? 'Show every tier' : `Show only: ${label}`}
            className={chip(on, tier === 1)}
          >
            <span className="min-w-0 truncate">{label}</span>
            <span className="tabular-nums">{n.toLocaleString()}</span>
          </button>
        ) : (
          <span key={label} className="inline-flex items-center gap-xxs px-xs text-caption text-muted-foreground">
            {label} <span className="tabular-nums">0</span>
          </span>
        );
      })}
    </div>
  );
};

export interface UntouchedQueueSectionProps {
  data: InvestigationQueueResponse | null;
  loading: boolean;
  unavailable: boolean;
  onRetry: () => void;
  /** The tier the rows are narrowed to (null = every tier). */
  tier: number | null;
  onTier: (tier: number | null) => void;
  /** Bring the next page of rows; absent when the queue cannot hold more. */
  onMore?: () => void;
  moreBusy?: boolean;
  canWrite: boolean;
  onChanged: () => void;
  keysActive?: boolean;
  onActivate?: () => void;
}

export const UntouchedQueueSection: React.FC<UntouchedQueueSectionProps> = ({
  data, loading, unavailable, onRetry, tier, onTier, onMore, moreBusy = false,
  canWrite, onChanged, keysActive = false, onActivate,
}) => {
  const toast = useToast();
  const navigate = useNavigate();
  const rows = React.useMemo(() => data?.items ?? [], [data]);
  const keys = React.useMemo(() => rows.map((r) => r.host_id), [rows]);
  const selection = useRowSelection(keys);
  const [takingId, setTakingId] = React.useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = React.useState(false);
  const [outcome, setOutcome] = React.useState<string | null>(null);

  const queueTotal = data?.queue_total ?? 0;
  const listTotal = tier != null ? (data?.tier_counts?.[tier - 1] ?? rows.length) : queueTotal;
  const navState = fromOperationsQueue(keys, UNTOUCHED_QUEUE_TITLE, { partial: listTotal > rows.length }).state;

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

  const title = (
    <>
      <span>{UNTOUCHED_QUEUE_TITLE}</span>
      {data && queueTotal > 0 && <SectionCount>{queueTotal.toLocaleString()}</SectionCount>}
    </>
  );
  const description = (
    'Hosts nobody has touched — no review, assignment, note, test, evidence or finding — that carry a '
    + 'reason to look. Every reason here is scanner-reported and unconfirmed. Ordered by the stated tier, '
    + `never a score${canWrite ? '; Review takes a host into your queue' : ''}.`
  );

  if (unavailable) {
    return (
      <PostureSection title={<span>{UNTOUCHED_QUEUE_TITLE}</span>}>
        <UnavailableLine onRetry={onRetry}>
          Unavailable — this queue could not be computed, so it says nothing about whether
          hosts are waiting. Your own work above is unaffected.
        </UnavailableLine>
      </PostureSection>
    );
  }
  if (!data) {
    return loading ? (
      <PostureSection title={<span>{UNTOUCHED_QUEUE_TITLE}</span>}>
        <p role="status" className="flex items-center gap-xs text-caption text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Finding untouched hosts with a reason to look…
        </p>
      </PostureSection>
    ) : null;
  }

  // The list this count opens: the tier's own query where one exists.
  const tierQuery = tier != null ? TIER_QUERY[tier] : undefined;
  const openAll = tierQuery
    ? { to: buildHostsUrl({ q: tierQuery }), label: `Open all ${hosts(listTotal)} in Hosts` }
    // No query expresses this set (every tier together, or tier 4 / 5): the
    // link opens what it says — every untouched host — and the queue pages here.
    : {
        to: buildHostsUrl({ q: UNTOUCHED_QUERY }),
        label: `Open all ${data.untouched_total.toLocaleString()} untouched hosts in Hosts`,
      };
  const canPage = !tierQuery && rows.length < listTotal && rows.length < UNTOUCHED_MAX_ROWS && onMore;
  const nextPage = Math.min(UNTOUCHED_PAGE, listTotal - rows.length, UNTOUCHED_MAX_ROWS - rows.length);

  return (
    <PostureSection title={title} description={description}>
      {data.tier_counts && queueTotal > 0 && (
        <TierChips tiers={data.tiers} counts={data.tier_counts} total={queueTotal} selected={tier} onSelect={onTier} />
      )}
      {rows.length === 0 ? (
        <p className="text-metadata text-muted-foreground">
          {tier != null && queueTotal > 0
            ? 'No untouched host is in this tier.'
            : data.untouched_total > 0
              ? <>
                  <Link to={buildHostsUrl({ q: UNTOUCHED_QUERY })} className="text-info hover:underline">
                    {hosts(data.untouched_total)}
                  </Link>{' '}
                  {data.untouched_total === 1 ? 'is' : 'are'} untouched, none with a weakness or change on record.
                </>
              : 'Every host has been touched by someone.'}
        </p>
      ) : (
        <div onMouseEnter={onActivate} onFocusCapture={onActivate}>
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
                  row.evidence.last_seen ? `Last observed ${new Date(row.evidence.last_seen).toLocaleString()}.` : null,
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
          <ListFooter
            shown={rows.length}
            total={listTotal}
            openAll={openAll}
            more={canPage ? { label: `Show ${nextPage} more`, onClick: onMore, busy: moreBusy } : undefined}
          >
            {!tierQuery && rows.length >= UNTOUCHED_MAX_ROWS && rows.length < listTotal && (
              <span className="text-caption text-muted-foreground">
                The first {UNTOUCHED_MAX_ROWS} are listed here — narrow to a tier, or take some into review, to reach the rest.
              </span>
            )}
          </ListFooter>
        </div>
      )}
    </PostureSection>
  );
};

export default UntouchedQueueSection;
