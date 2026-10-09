/**
 * A finding's affected endpoints.
 *
 * A finding can carry thousands of endpoints, and the report text, the
 * comments and the history sit below this list — so the list is a BOUNDED
 * PANEL: about twelve compact rows tall, scrolling inside itself, with the
 * state chips, the filter and the bulk bar pinned above the rows and the
 * "Showing N of M" line pinned below them.  A finding with three endpoints is
 * the same panel, three rows tall.
 *
 * The rows sit under their NETWORK when the finding spans several: each
 * endpoint's `segment` is the server's (the project's one segment rule — the
 * Posture grid's and the Evidence matrix's columns), never worked out from an
 * address here.  One network is a flat list with no header.
 *
 * Mounted rows are bounded: `ENDPOINT_CAP` to begin with, more on request,
 * never more than `ENDPOINT_MOUNT_MAX` (past that the listed rows are a
 * window that moves on).  Rows of a closed network are not mounted at all.
 *
 * The finding's own status is the ISSUE's; every control here changes an
 * endpoint row only.  A change updates the page from the route's response.
 * An agent's pending endpoint-status proposal is shown and decided ON its row.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { ChevronRight, Loader2, MoreHorizontal, Trash2, X } from 'lucide-react';

import {
  Finding, FindingHostInfo, FindingHostStatus, Proposal, setFindingEndpointStatus, setFindingEndpointsStatus,
} from '../../services/api';
import { type OnProposalDecided, useProposalDecision } from '../../hooks/useProposalDecision';
import { ProposalDecisionControls, ProposalReasons, ProposalSource } from '../proposals/ProposalItem';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import {
  ENDPOINT_BULK_MAX, ENDPOINT_CAP, ENDPOINT_GROUPS_OPEN_MAX, ENDPOINT_MOUNT_MAX, ENDPOINT_STATES,
  EndpointGroup, EndpointStateFilter, EndpointWindow, FIRST_ENDPOINT_WINDOW, chunked, endpointSegmentCount,
  endpointStateCounts, filterEndpoints, groupEndpoints, idRange, revealEndpoints, sortEndpoints,
} from '../../utils/findingEndpoints';
import {
  ENDPOINT_STATUS_LABEL, ENDPOINT_STATUS_SHORT_LABEL, ENDPOINT_STATUS_TONE, describeEndpointStates,
} from '../../utils/findingStatus';
import { runLimited } from '../../utils/runLimited';
import { selectAllState } from '../../utils/selection';
import { LIST_CURSOR_CLASS } from '../../hooks/useListCursor';
import EndpointStateBar from './EndpointStateBar';
import { jumpTargetStyle } from '../SectionJumpBar';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../ui/table';

/** One pending endpoint-status proposal, on the row it would change. */
const EndpointProposal: React.FC<{
  pr: Proposal; from: FindingHostStatus; canDecide: boolean; onDecided?: OnProposalDecided;
}> = ({ pr, from, canDecide, onDecided }) => {
  const decision = useProposalDecision(pr, onDecided);
  const to = String(pr.payload?.host_status ?? '') as FindingHostStatus;
  return (
    <div className="mb-xxs mt-xxs min-w-0 space-y-xxs whitespace-normal border-l-2 border-info pl-sm leading-normal" data-proposal={pr.id}>
      <p className="min-w-0 break-words text-caption">
        {/* From → to on the row itself, beside the decision. */}
        <span className="font-medium text-info">
          Proposed: {ENDPOINT_STATUS_LABEL[from] ?? from} → {ENDPOINT_STATUS_LABEL[to] ?? to}
        </span>
        <span className="text-muted-foreground"> · <ProposalSource pr={pr} /></span>
      </p>
      <ProposalReasons pr={pr} />
      <ProposalDecisionControls pr={pr} canDecide={canDecide} decision={decision} compact />
    </div>
  );
};

const NO_PROPOSALS = new Map<number, Proposal[]>();
const NO_OVERRIDES = new Map<string, boolean>();

const endpointName = (h: FindingHostInfo) => `${h.fqdn ? `${h.fqdn} on ` : ''}${h.ip_address || h.host_id}`;

/** A sticky cell needs an opaque fill, and draws its own rule: a collapsed
 *  table border does not travel with a stuck cell. */
const STUCK = 'bg-background shadow-[inset_0_-1px_0_hsl(var(--border))]';
const CELL = 'py-1 align-top leading-6';

interface Props {
  finding: Finding;
  /** Project analyst or above: may change endpoint states and remove a row. */
  canManage: boolean;
  /** The finding as the server returned it after a change. */
  onChanged: (updated: Finding) => void;
  onRemove: (row: FindingHostInfo) => void;
  /** An endpoint row to bring into view (`?endpoint=` — a proposal's link). */
  focusEndpointId?: number | null;
  /** Pending endpoint-status proposals by endpoint row id. */
  proposals?: Map<number, Proposal[]>;
  /** Analyst+: may decide them (the server still decides each accept). */
  canDecide?: boolean;
  onProposalDecided?: OnProposalDecided;
}

const FindingEndpoints: React.FC<Props> = ({
  finding, canManage, onChanged, onRemove, focusEndpointId = null,
  proposals = NO_PROPOSALS, canDecide = false, onProposalDecided,
}) => {
  const toast = useToast();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [stateFilter, setStateFilter] = useState<EndpointStateFilter>('all');
  const [text, setText] = useState('');
  const [win, setWin] = useState<EndpointWindow>(FIRST_ENDPOINT_WINDOW);
  // Networks the reader opened or closed, for this visit; the rest follow the
  // size of the filtered list.
  const [openOverride, setOpenOverride] = useState<Map<string, boolean>>(NO_OVERRIDES);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const anchor = useRef<number | null>(null);
  // Rows whose own change is in flight — a set: two rows changed in quick
  // succession are both busy, and each stays locked until ITS request is back.
  const [saving, setSaving] = useState<Set<number>>(new Set());
  // Every change answers with the whole finding as it stood when THAT change
  // committed.  Requests sent together commit in whatever order the server
  // reaches them, so neither the send order nor the arrival order says which
  // answer is the newest: one that committed first and answered last would
  // repaint the other's row with its old state.  Changes therefore go to the
  // server ONE AT A TIME, in the order they were made, and every answer is
  // applied — each is newer than the one before it.
  // A task settles its own failure, so one refused change never holds up or
  // loses the ones queued behind it.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const enqueue = (task: () => Promise<void>): Promise<void> => {
    const run = queue.current.then(task);
    queue.current = run.catch(() => undefined);
    return run;
  };
  // The two writes the queue sends.  Their pending state is not what locks a
  // row: a row is busy from its click (`saving`), the bulk bar from its own
  // (`bulkBusy`), each while its change waits its turn as well.
  const setEndpoint = useMutation({
    mutationFn: (v: { rowId: number; hostStatus: FindingHostStatus }) =>
      setFindingEndpointStatus(finding.id, v.rowId, v.hostStatus),
  });
  const setEndpoints = useMutation({
    mutationFn: (body: { finding_host_ids: number[]; host_status: FindingHostStatus; summary?: string }) =>
      setFindingEndpointsStatus(finding.id, body),
  });
  const [bulkState, setBulkState] = useState<FindingHostStatus | ''>('');
  const [bulkSummary, setBulkSummary] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);

  const counts = useMemo(
    () => endpointStateCounts(finding.hosts, finding.endpoint_status_counts),
    [finding.hosts, finding.endpoint_status_counts],
  );
  const stateSentence = useMemo(
    () => describeEndpointStates(finding.endpoint_status_counts, finding.host_count),
    [finding.endpoint_status_counts, finding.host_count],
  );
  // The table's order is its own (by address, then name), never the
  // response's: the server answers a change with the changed row last, and
  // drawing that moved rows under the reader after every change.
  const ordered = useMemo(() => sortEndpoints(finding.hosts), [finding.hosts]);
  const matching = useMemo(
    () => filterEndpoints(ordered, stateFilter, text),
    [ordered, stateFilter, text],
  );
  const filtered = stateFilter !== 'all' || text.trim() !== '';

  // One network is a flat list: a single header would say nothing.
  const grouped = useMemo(() => endpointSegmentCount(finding.hosts) > 1, [finding.hosts]);
  const groups = useMemo(
    () => (grouped ? groupEndpoints(matching, finding.hosts) : []),
    [grouped, matching, finding.hosts],
  );
  const openByDefault = matching.length <= ENDPOINT_GROUPS_OPEN_MAX;
  const isOpen = (key: string) => openOverride.get(key) ?? openByDefault;
  // The rows that can be listed: every matching row, or those of the open networks.
  const listable = useMemo(
    () => (grouped ? groups.flatMap((g) => ((openOverride.get(g.key) ?? openByDefault) ? g.rows : [])) : matching),
    [grouped, groups, matching, openOverride, openByDefault],
  );
  const shown = useMemo(() => listable.slice(win.start, win.end), [listable, win]);
  const shownIds = useMemo(() => shown.map((h) => h.id), [shown]);
  const shownSet = useMemo(() => new Set(shownIds), [shownIds]);

  // A selection is of the rows the reader was looking at: narrowing the list
  // drops it rather than leaving endpoints selected out of sight.
  const changeFilter = (next: () => void) => {
    next();
    setWin(FIRST_ENDPOINT_WINDOW);
    setSelected(new Set());
    anchor.current = null;
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  };
  const toggleState = (s: EndpointStateFilter) =>
    changeFilter(() => setStateFilter((current) => (current === s ? 'all' : s)));

  // What the bulk bar acts on is always among the rows the current filter
  // matches: a ticked row that has since left the filter — its own state was
  // changed, someone else changed it, it was removed — is not acted on out of
  // sight.
  const matchingIds = useMemo(() => new Set(matching.map((h) => h.id)), [matching]);
  const selectedIds = useMemo(() => [...selected].filter((id) => matchingIds.has(id)), [selected, matchingIds]);
  const isSelected = (id: number) => selected.has(id) && matchingIds.has(id);

  // A linked endpoint (`?endpoint=`): open its network, make sure its row is
  // mounted, then show it — ONCE per link, not again on every "Show more" or
  // keystroke in the filter.
  const focusRow = focusEndpointId == null ? undefined : matching.find((h) => h.id === focusEndpointId);
  const focusKey = focusRow ? focusRow.segment?.key ?? '' : null;
  const focusIndex = focusEndpointId == null ? -1 : listable.findIndex((h) => h.id === focusEndpointId);
  const scrolledTo = useRef<number | null>(null);
  useEffect(() => {
    if (focusEndpointId == null || scrolledTo.current === focusEndpointId || focusKey === null) return;
    if (focusIndex < 0) {
      if (grouped) setOpenOverride((prev) => new Map(prev).set(focusKey, true));
      return;
    }
    if (focusIndex < win.start || focusIndex >= win.end) {
      setWin((w) => revealEndpoints(w, focusIndex, focusIndex + 1));
    }
  }, [focusEndpointId, focusKey, focusIndex, grouped, win]);
  useEffect(() => {
    if (focusEndpointId == null || scrolledTo.current === focusEndpointId) return;
    if (focusIndex < win.start || focusIndex >= win.end) return;
    scrolledTo.current = focusEndpointId;
    // The row is centred inside the panel's own scroll, then the panel is
    // brought to the top of the page (below the chrome and the jump bar).
    const body = bodyRef.current;
    const row = body?.querySelector<HTMLElement>(`[data-endpoint-row="${focusEndpointId}"]`);
    if (body && row) {
      const at = row.getBoundingClientRect();
      body.scrollTop += at.top - body.getBoundingClientRect().top - (body.clientHeight - at.height) / 2;
    }
    rootRef.current?.scrollIntoView?.({ block: 'start' });
  }, [focusEndpointId, focusIndex, win]);

  const toggle = (id: number, on: boolean, range: boolean) => {
    // The anchor is read NOW: an updater runs later, after the line below
    // has already moved the anchor to this row.
    const ids = range ? idRange(shownIds, anchor.current, id) : [id];
    anchor.current = id;
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((x) => { if (on) next.add(x); else next.delete(x); });
      return next;
    });
  };
  const setMany = (ids: ReadonlyArray<number>, on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => { if (on) next.add(id); else next.delete(id); });
      return next;
    });
  };

  const shownSelected = selectAllState(shownIds.filter((id) => selected.has(id)).length, shownIds.length);

  /** Open or close a network.  Opening brings its first rows into the list. */
  const toggleGroup = (g: EndpointGroup<FindingHostInfo>) => {
    const open = !isOpen(g.key);
    setOpenOverride((prev) => new Map(prev).set(g.key, open));
    if (!open) return;
    let first = 0;
    for (const other of groups) {
      if (other.key === g.key) break;
      if (isOpen(other.key)) first += other.rows.length;
    }
    setWin((w) => revealEndpoints(w, first, first + Math.min(g.rows.length, ENDPOINT_CAP)));
  };
  const setAllGroups = (open: boolean) => {
    setOpenOverride(new Map(groups.map((g) => [g.key, open])));
    setWin(FIRST_ENDPOINT_WINDOW);
  };

  const setOne = async (row: FindingHostInfo, hostStatus: FindingHostStatus) => {
    if (row.host_status === hostStatus || saving.has(row.id)) return;
    // Busy from the click, not from the send: a queued row stays locked until
    // ITS request is back.
    setSaving((prev) => new Set(prev).add(row.id));
    await enqueue(async () => {
      try {
        // The route answers with the finding: the row is updated from it, not
        // from a second read of thousands of endpoints.
        onChanged(await setEndpoint.mutateAsync({ rowId: row.id, hostStatus }));
        // A row given its own state is no longer part of "these, together".
        setSelected((prev) => {
          if (!prev.has(row.id)) return prev;
          const next = new Set(prev);
          next.delete(row.id);
          return next;
        });
      } catch (err) {
        toast.error(formatApiError(err, 'Failed to update the endpoint state.'));
      } finally {
        setSaving((prev) => {
          const next = new Set(prev);
          next.delete(row.id);
          return next;
        });
      }
    });
  };

  const applyBulk = async () => {
    if (!bulkState || selectedIds.length === 0) return;
    const hostStatus = bulkState;
    const summary = bulkSummary.trim() || undefined;
    // The route takes 500 ids, all-or-nothing PER CALL.  A larger selection
    // goes in order, one call at a time; what a refused call left unchanged
    // is said and stays selected.
    const submitted = selectedIds;
    const chunks = chunked(submitted, ENDPOINT_BULK_MAX);
    setBulkBusy(true);
    // Behind any single-row change still on its way (the same queue), so its
    // answer is the newest when it is applied.
    let results: PromiseSettledResult<Finding>[] = [];
    try {
      await enqueue(async () => {
        results = await runLimited(chunks, 1, (ids) =>
          setEndpoints.mutateAsync({ finding_host_ids: ids, host_status: hostStatus, summary }));
      });
    } finally {
      setBulkBusy(false);
    }
    let latest: Finding | null = null;
    const doneIds: number[] = [];
    const notDone: number[] = [];
    let firstError: unknown = null;
    results.forEach((r, i) => {
      if (r.status === 'fulfilled') {
        doneIds.push(...chunks[i]);
        latest = r.value;
      } else {
        notDone.push(...chunks[i]);
        firstError ??= r.reason;
      }
    });
    const done = doneIds.length;
    if (latest) onChanged(latest);
    // Only what this request changed leaves the selection: what it could not
    // change stays ticked, and so does anything ticked since it started.
    if (done > 0) {
      setSelected((prev) => {
        const next = new Set(prev);
        doneIds.forEach((id) => next.delete(id));
        return next;
      });
    }
    const label = ENDPOINT_STATUS_LABEL[hostStatus].toLowerCase();
    if (notDone.length === 0) {
      toast.success(`Set ${done.toLocaleString()} endpoint${done === 1 ? '' : 's'} to ${label}.`);
      setBulkState('');
      setBulkSummary('');
    } else if (done === 0) {
      toast.error(formatApiError(firstError, 'No endpoint was changed.'));
    } else {
      toast.warning(
        `Set ${done.toLocaleString()} of ${submitted.length.toLocaleString()} endpoints to ${label}; `
        + `${notDone.length.toLocaleString()} were not changed and are still selected `
        + `(${formatApiError(firstError, 'the server refused them')}).`,
      );
    }
  };

  /** This row's controls wait: its own change, or the bulk one, is in flight. */
  const rowBusy = (id: number) => bulkBusy || saving.has(id);

  const columns = canManage ? 5 : 3;
  const plural = (n: number) => `endpoint${n === 1 ? '' : 's'}`;

  const renderRow = (h: FindingHostInfo) => {
    const names = [h.fqdn, h.hostname].filter(Boolean).join(' · ');
    return (
      <TableRow
        key={h.id}
        data-endpoint-row={h.id}
        data-state={isSelected(h.id) ? 'selected' : undefined}
        {...(canManage ? { 'aria-selected': isSelected(h.id) } : {})}
        className={h.id === focusEndpointId ? LIST_CURSOR_CLASS : undefined}
      >
        {canManage && (
          <TableCell className={CELL}>
            <span className="flex h-6 items-center">
              <Checkbox
                checked={isSelected(h.id)}
                disabled={rowBusy(h.id)}
                // Shift-click selects the range from the last row ticked.
                onClick={(e) => {
                  e.preventDefault();
                  toggle(h.id, !isSelected(h.id), e.shiftKey);
                }}
                aria-label={`Select ${endpointName(h)}`}
              />
            </span>
          </TableCell>
        )}
        <TableCell className={cn(CELL, 'min-w-0')}>
          <Link
            to={`/hosts/${h.host_id}`}
            className="block truncate font-mono text-info hover:underline"
            title={h.ip_address || undefined}
          >
            {h.ip_address || `Host ${h.host_id}`}
          </Link>
        </TableCell>
        <TableCell className={cn(CELL, 'min-w-0')}>
          <div className="truncate" title={names || undefined}>
            {h.fqdn && (h.name_id != null ? (
              <Link to={`/names?name_id=${h.name_id}`} className="font-mono text-caption text-info hover:underline">
                {h.fqdn}
              </Link>
            ) : (
              <span className="font-mono text-caption">{h.fqdn}</span>
            ))}
            {h.hostname && (
              <span className={cn('text-caption text-muted-foreground', h.fqdn && 'ml-xs')}>{h.hostname}</span>
            )}
            {!names && <span className="text-muted-foreground">—</span>}
          </div>
          {(proposals.get(h.id) ?? []).map((pr) => (
            <EndpointProposal key={pr.id} pr={pr} from={h.host_status} canDecide={canDecide} onDecided={onProposalDecided} />
          ))}
        </TableCell>
        <TableCell className={CELL}>
          {canManage ? (
            <Select
              value={h.host_status}
              onValueChange={(v) => void setOne(h, v as FindingHostStatus)}
              disabled={rowBusy(h.id)}
            >
              <SelectTrigger className="h-6 w-full px-xs py-0 text-caption" aria-label={`State of ${endpointName(h)}`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(ENDPOINT_STATUS_LABEL) as FindingHostStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>{ENDPOINT_STATUS_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Badge variant={ENDPOINT_STATUS_TONE[h.host_status] ?? 'info'}>
              {ENDPOINT_STATUS_LABEL[h.host_status] ?? h.host_status}
            </Badge>
          )}
        </TableCell>
        {canManage && (
          <TableCell className={cn(CELL, 'px-0')}>
            {/* Removal is destructive: it lives in the row's menu, never as
                an icon beside the state control. */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost" size="icon" className="size-6"
                  disabled={rowBusy(h.id)}
                  aria-label={`Actions for ${endpointName(h)}`}
                >
                  <MoreHorizontal className="size-4" aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  className="text-destructive focus:text-destructive"
                  // A tick later: the confirmation must not open while the
                  // menu is still closing and handing focus back.
                  onSelect={() => { setTimeout(() => onRemove(h), 0); }}
                >
                  <Trash2 className="size-4" aria-hidden /> Remove from finding…
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </TableCell>
        )}
      </TableRow>
    );
  };

  const renderGroup = (g: EndpointGroup<FindingHostInfo>) => {
    const open = isOpen(g.key);
    const rows = open ? g.rows.filter((h) => shownSet.has(h.id)) : [];
    const states = ENDPOINT_STATES.filter((s) => g.counts[s] > 0)
      .map((s) => `${ENDPOINT_STATUS_SHORT_LABEL[s]} ${g.counts[s].toLocaleString()}`).join(' · ');
    const groupSelected = g.rows.filter((h) => selected.has(h.id)).length;
    const headerCell = cn('sticky top-8 z-[5] py-1 align-middle', STUCK);
    return (
      <React.Fragment key={g.key}>
        <tr data-endpoint-group={g.key}>
          {canManage && (
            <td className={cn(headerCell, 'px-sm pr-0')}>
              <span className="flex h-6 items-center">
                <Checkbox
                  checked={selectAllState(groupSelected, g.rows.length)}
                  // The network's rows the CURRENT filter matches — open or not.
                  onCheckedChange={(v) => setMany(g.rows.map((h) => h.id), v === true)}
                  disabled={bulkBusy}
                  aria-label={`Select every endpoint in ${g.label}`}
                />
              </span>
            </td>
          )}
          {/* The whole header opens the network: the label alone is a small
              target in a row this wide.  The button stays the control a
              keyboard and a screen reader use. */}
          <td
            colSpan={canManage ? columns - 1 : columns}
            className={cn(headerCell, 'cursor-pointer px-sm')}
            onClick={() => toggleGroup(g)}
          >
            <div className="flex min-w-0 items-center gap-sm">
              <button
                type="button"
                aria-expanded={open}
                onClick={(e) => { e.stopPropagation(); toggleGroup(g); }}
                className="flex min-w-0 items-center gap-xs rounded-control text-left font-medium text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ChevronRight className={cn('size-4 shrink-0 transition-transform', open && 'rotate-90')} aria-hidden />
                <span className="truncate" title={g.label}>{g.label}</span>
              </button>
              <span className="shrink-0 whitespace-nowrap tabular-nums text-muted-foreground" data-testid="group-count">
                {g.rows.length === g.total
                  ? `${g.total.toLocaleString()} ${plural(g.total)}`
                  : `${g.rows.length.toLocaleString()} of ${g.total.toLocaleString()} ${plural(g.total)}`}
              </span>
              <span className="min-w-0 flex-1 truncate text-caption text-muted-foreground" title={states} data-testid="group-states">
                {states}
              </span>
              {open && rows.length < g.rows.length && (
                <span className="shrink-0 whitespace-nowrap text-caption text-muted-foreground">
                  {rows.length.toLocaleString()} listed
                </span>
              )}
            </div>
          </td>
        </tr>
        {rows.map(renderRow)}
      </React.Fragment>
    );
  };

  const moreToList = Math.max(0, listable.length - win.end);
  const inClosed = matching.length - listable.length;
  const showFooter = matching.length > shown.length || groups.length > 1;

  return (
    <div id="endpoints" ref={rootRef} className="min-w-0" style={jumpTargetStyle}>
      {/* The finding's status is the issue's; each endpoint keeps its own, so
          "Confirmed" above never means every host.  The bar is the sentence's
          companion and the chips' shortcut — it explains nothing more. */}
      <div className="mb-sm flex min-w-0 flex-wrap items-center gap-x-md gap-y-xs">
        <p className="min-w-0 max-w-3xl text-caption text-muted-foreground">
          The status above is the issue&apos;s; each endpoint has its own
          {stateSentence ? <>: <span className="text-foreground">{stateSentence}</span></> : ' — all still present'}.
        </p>
        {finding.hosts.length > 0 && (
          <EndpointStateBar counts={counts} active={stateFilter} onSelect={toggleState} />
        )}
      </div>

      <div className="min-w-0 rounded-control border border-border" data-testid="endpoints-panel">
        {finding.hosts.length > 0 && (
          // Pinned: the rows scroll under these, they never scroll away.
          <div className="min-w-0 space-y-xs border-b border-border p-xs" data-testid="endpoints-toolbar">
            <div className="flex min-w-0 flex-wrap items-center gap-xs">
              <div className="flex min-w-0 flex-wrap items-center gap-xs" role="group" aria-label="Endpoint state filter">
                {(['all', ...ENDPOINT_STATES] as EndpointStateFilter[]).map((s) => {
                  const n = s === 'all' ? finding.hosts.length : counts[s];
                  if (s !== 'all' && n === 0 && stateFilter !== s) return null;
                  const active = stateFilter === s;
                  return (
                    <button
                      key={s}
                      type="button"
                      aria-pressed={active}
                      onClick={() => toggleState(s)}
                      className={cn(
                        'inline-flex items-center gap-xs whitespace-nowrap rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        active
                          ? 'border-primary bg-primary/10 text-foreground'
                          : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
                      )}
                    >
                      <span>{s === 'all' ? 'All' : ENDPOINT_STATUS_SHORT_LABEL[s]}</span>
                      <strong className="tabular-nums text-foreground">{n.toLocaleString()}</strong>
                    </button>
                  );
                })}
              </div>
              <Input
                value={text}
                onChange={(e) => changeFilter(() => setText(e.target.value))}
                placeholder="Filter by address or name…"
                aria-label="Filter endpoints by address or name"
                className="h-8 w-64 min-w-0 max-w-full"
              />
            </div>

            {canManage && selectedIds.length > 0 && (
              <div
                className="flex min-w-0 flex-wrap items-end gap-xs border-l-4 border-l-info py-xxs pl-sm"
                role="group"
                aria-label="Set the selected endpoints"
              >
                <span className="pb-1 text-metadata tabular-nums">
                  {selectedIds.length.toLocaleString()} selected
                </span>
                <div className="min-w-0">
                  <Label htmlFor="endpoint-bulk-state" className="sr-only">Set the selected endpoints to</Label>
                  <Select value={bulkState} onValueChange={(v) => setBulkState(v as FindingHostStatus)} disabled={bulkBusy}>
                    <SelectTrigger id="endpoint-bulk-state" className="h-8 w-48 text-caption" aria-label="Set the selected endpoints to">
                      <SelectValue placeholder="Set them to…" />
                    </SelectTrigger>
                    <SelectContent>
                      {(Object.keys(ENDPOINT_STATUS_LABEL) as FindingHostStatus[]).map((s) => (
                        <SelectItem key={s} value={s}>{ENDPOINT_STATUS_LABEL[s]}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <Input
                  value={bulkSummary}
                  onChange={(e) => setBulkSummary(e.target.value)}
                  maxLength={2000}
                  disabled={bulkBusy}
                  placeholder="Note for the history (optional), e.g. retest 2026-10-01"
                  aria-label="Note recorded with each endpoint's history line (optional)"
                  className="h-8 min-w-0 max-w-md flex-1 basis-56"
                />
                <Button size="sm" onClick={() => void applyBulk()} disabled={bulkBusy || !bulkState}>
                  {bulkBusy && <Loader2 className="size-4 animate-spin" aria-hidden />}
                  Set {selectedIds.length.toLocaleString()} endpoint{selectedIds.length === 1 ? '' : 's'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} disabled={bulkBusy}>
                  <X className="size-4" aria-hidden /> Clear
                </Button>
              </div>
            )}
            {canManage && shownSelected === true && matching.length > shown.length && selectedIds.length < matching.length && (
              <p className="text-caption text-muted-foreground">
                The {shown.length.toLocaleString()} shown are selected.{' '}
                <Button variant="link" size="sm" className="h-auto p-0" disabled={bulkBusy}
                  onClick={() => setSelected(new Set(matching.map((h) => h.id)))}>
                  Select all {matching.length.toLocaleString()}{filtered ? ' matching' : ''}
                </Button>
              </p>
            )}
          </div>
        )}

        {/* About twelve rows, then the rows scroll here — not the page. */}
        <div ref={bodyRef} className="max-h-[26rem] overflow-auto" data-testid="endpoints-body">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent hover:shadow-none">
                {canManage && (
                  <TableHead className={cn('sticky top-0 z-10 h-8 w-10', STUCK)}>
                    <Checkbox
                      checked={shownSelected}
                      onCheckedChange={(v) => setMany(shownIds, v === true)}
                      disabled={shownIds.length === 0 || bulkBusy}
                      aria-label="Select every endpoint shown"
                    />
                  </TableHead>
                )}
                <TableHead className={cn('sticky top-0 z-10 h-8 w-[17rem]', STUCK)}>Address</TableHead>
                <TableHead className={cn('sticky top-0 z-10 h-8', STUCK)}>Name</TableHead>
                <TableHead className={cn('sticky top-0 z-10 h-8 w-[10.5rem]', STUCK)}>State</TableHead>
                {canManage && (
                  <TableHead className={cn('sticky top-0 z-10 h-8 w-8 px-0', STUCK)}>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {matching.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={columns} className="py-lg text-center text-muted-foreground">
                    {finding.hosts.length === 0 ? 'No hosts attached.' : 'No endpoint matches this filter.'}
                  </TableCell>
                </TableRow>
              ) : grouped ? groups.map(renderGroup) : shown.map(renderRow)}
            </TableBody>
          </Table>
        </div>

        {showFooter && (
          <div className="flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs border-t border-border px-xs py-xxs text-caption text-muted-foreground">
            {matching.length > shown.length && (
              <span data-testid="endpoints-cut" className="contents">
                <span>
                  Showing {win.start > 0
                    ? `${(win.start + 1).toLocaleString()}–${(win.start + shown.length).toLocaleString()}`
                    : shown.length.toLocaleString()} of {matching.length.toLocaleString()}
                  {filtered ? ' matching' : ''} endpoint{matching.length === 1 ? '' : 's'}.
                  {inClosed > 0 && ` ${inClosed.toLocaleString()} in closed networks.`}
                </span>
                {win.start > 0 && (
                  <Button variant="ghost" size="sm" className="h-7"
                    onClick={() => setWin((w) => {
                      const start = Math.max(0, w.start - ENDPOINT_CAP);
                      return { start, end: Math.min(w.end, start + ENDPOINT_MOUNT_MAX) };
                    })}>
                    Show {Math.min(ENDPOINT_CAP, win.start).toLocaleString()} previous
                  </Button>
                )}
                {moreToList > 0 && (
                  <Button variant="ghost" size="sm" className="h-7"
                    onClick={() => setWin((w) => revealEndpoints(w, w.end, Math.min(listable.length, w.end + ENDPOINT_CAP)))}>
                    Show {Math.min(ENDPOINT_CAP, moreToList).toLocaleString()} more
                  </Button>
                )}
                {moreToList > 0 && listable.length <= ENDPOINT_MOUNT_MAX && (
                  <Button variant="ghost" size="sm" className="h-7"
                    onClick={() => setWin({ start: 0, end: listable.length })}>
                    Show all {listable.length.toLocaleString()}
                  </Button>
                )}
              </span>
            )}
            {groups.length > 1 && (
              <span className="ml-auto flex shrink-0 items-center gap-xs">
                <Button variant="ghost" size="sm" className="h-7" onClick={() => setAllGroups(true)}>Open all networks</Button>
                <Button variant="ghost" size="sm" className="h-7" onClick={() => setAllGroups(false)}>Close all</Button>
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default FindingEndpoints;
