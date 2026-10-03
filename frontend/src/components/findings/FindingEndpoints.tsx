/**
 * A finding's affected endpoints (review 2026-10-01 C2 / B13).
 *
 * A finding can carry thousands of endpoints.  The table renders at most
 * `ENDPOINT_CAP` rows until asked for more, narrows by state (chips with the
 * counts) and by address or name, and — for a project analyst — selects
 * endpoints to set their state together: after a retest, "these 300 are
 * remediated" is one action, not 300 Selects each followed by a refetch.
 *
 * The finding's own status is the ISSUE's; every control here changes an
 * endpoint row only.  A change updates the page from the route's response.
 *
 * 5.334.0 — an agent's pending endpoint-status proposal is shown and decided
 * ON its row (it was listed in a Proposals section under the table, away from
 * the endpoint it was about).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Trash2, X } from 'lucide-react';

import {
  Finding, FindingHostInfo, FindingHostStatus, Proposal, setFindingEndpointStatus, setFindingEndpointsStatus,
} from '../../services/api';
import { useProposalDecision } from '../../hooks/useProposalDecision';
import { ProposalDecisionControls, ProposalReasons, ProposalSource } from '../proposals/ProposalItem';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import {
  ENDPOINT_BULK_MAX, ENDPOINT_CAP, ENDPOINT_STATES, EndpointStateFilter, chunked, endpointStateCounts,
  filterEndpoints, idRange, sortEndpoints,
} from '../../utils/findingEndpoints';
import { ENDPOINT_STATUS_LABEL } from '../../utils/findingStatus';
import { runLimited } from '../../utils/runLimited';
import { LIST_CURSOR_CLASS } from '../../hooks/useListCursor';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../ui/table';

/** Short chip labels; the row's Select keeps the full "… here" wording. */
const CHIP_LABEL: Record<FindingHostStatus, string> = {
  open: 'Still present',
  retest: 'Retest',
  remediated: 'Remediated',
  false_positive: 'False positive',
};

const READ_ONLY_VARIANT: Record<FindingHostStatus, 'warning' | 'success' | 'outline' | 'info'> = {
  open: 'warning', remediated: 'success', false_positive: 'outline', retest: 'info',
};

/** One pending endpoint-status proposal, on the row it would change. */
const EndpointProposal: React.FC<{
  pr: Proposal; canDecide: boolean; onDecided: (updated: Proposal) => void;
}> = ({ pr, canDecide, onDecided }) => {
  const decision = useProposalDecision(pr, onDecided);
  const to = String(pr.payload?.host_status ?? '') as FindingHostStatus;
  return (
    <div className="mt-xxs min-w-0 space-y-xxs whitespace-normal border-l-2 border-info pl-sm" data-proposal={pr.id}>
      <p className="min-w-0 break-words text-caption">
        <span className="font-medium text-info">Proposed: {ENDPOINT_STATUS_LABEL[to] ?? to}</span>
        <span className="text-muted-foreground"> · <ProposalSource pr={pr} /></span>
      </p>
      <ProposalReasons pr={pr} />
      <ProposalDecisionControls pr={pr} canDecide={canDecide} decision={decision} compact />
    </div>
  );
};

const NO_PROPOSALS = new Map<number, Proposal[]>();

const endpointName = (h: FindingHostInfo) => `${h.fqdn ? `${h.fqdn} on ` : ''}${h.ip_address || h.host_id}`;

interface Props {
  finding: Finding;
  /** Project analyst or above: may change endpoint states and detach. */
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
  onProposalDecided?: (updated: Proposal) => void;
}

const FindingEndpoints: React.FC<Props> = ({
  finding, canManage, onChanged, onRemove, focusEndpointId = null,
  proposals = NO_PROPOSALS, canDecide = false, onProposalDecided,
}) => {
  const toast = useToast();
  const [stateFilter, setStateFilter] = useState<EndpointStateFilter>('all');
  const [text, setText] = useState('');
  const [limit, setLimit] = useState(ENDPOINT_CAP);
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
  // applied — each is newer than the one before it (review 2026-10-02 H2).
  // A task settles its own failure, so one refused change never holds up or
  // loses the ones queued behind it.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const enqueue = (task: () => Promise<void>): Promise<void> => {
    const run = queue.current.then(task);
    queue.current = run.catch(() => undefined);
    return run;
  };
  const [bulkState, setBulkState] = useState<FindingHostStatus | ''>('');
  const [bulkSummary, setBulkSummary] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);

  const counts = useMemo(
    () => endpointStateCounts(finding.hosts, finding.endpoint_status_counts),
    [finding.hosts, finding.endpoint_status_counts],
  );
  // The table's order is its own (by address, then name), never the
  // response's: the server answers a change with the changed row last, and
  // drawing that moved rows under the reader after every change.
  const ordered = useMemo(() => sortEndpoints(finding.hosts), [finding.hosts]);
  const matching = useMemo(
    () => filterEndpoints(ordered, stateFilter, text),
    [ordered, stateFilter, text],
  );
  const shown = matching.length > limit ? matching.slice(0, limit) : matching;
  const shownIds = useMemo(() => shown.map((h) => h.id), [shown]);
  const filtered = stateFilter !== 'all' || text.trim() !== '';

  // A selection is of the rows the reader was looking at: narrowing the list
  // drops it rather than leaving endpoints selected out of sight.
  const changeFilter = (next: () => void) => {
    next();
    setLimit(ENDPOINT_CAP);
    setSelected(new Set());
    anchor.current = null;
  };

  // What the bulk bar acts on is always among the rows the current filter
  // matches (S3): a ticked row that has since left the filter — its own state
  // was changed, someone else changed it, it was detached — is not acted on
  // out of sight.
  const matchingIds = useMemo(() => new Set(matching.map((h) => h.id)), [matching]);
  const selectedIds = useMemo(() => [...selected].filter((id) => matchingIds.has(id)), [selected, matchingIds]);
  const isSelected = (id: number) => selected.has(id) && matchingIds.has(id);

  // A linked endpoint (`?endpoint=`): make sure it is rendered, then show it
  // — ONCE per link (S7).  It used to scroll back to the row on every "Show
  // more" and every keystroke in the filter.
  const focusIndex = focusEndpointId == null ? -1 : matching.findIndex((h) => h.id === focusEndpointId);
  const scrolledTo = useRef<number | null>(null);
  useEffect(() => {
    if (focusEndpointId == null || scrolledTo.current === focusEndpointId) return;
    if (focusIndex >= limit) setLimit(focusIndex + 1);
  }, [focusEndpointId, focusIndex, limit]);
  useEffect(() => {
    if (focusEndpointId == null || scrolledTo.current === focusEndpointId) return;
    if (focusIndex < 0 || focusIndex >= limit) return;
    scrolledTo.current = focusEndpointId;
    document.querySelector(`[data-endpoint-row="${focusEndpointId}"]`)?.scrollIntoView?.({ block: 'center' });
  }, [focusEndpointId, focusIndex, limit]);

  const toggle = (id: number, on: boolean, range: boolean) => {
    // The anchor is read NOW: an updater runs later, after the line below
    // has already moved the anchor to this row (M3).
    const ids = range ? idRange(shownIds, anchor.current, id) : [id];
    anchor.current = id;
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((x) => { if (on) next.add(x); else next.delete(x); });
      return next;
    });
  };

  const allShownSelected = shownIds.length > 0 && shownIds.every((id) => selected.has(id));
  const someShownSelected = shownIds.some((id) => selected.has(id));
  const toggleAllShown = (on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      shownIds.forEach((id) => { if (on) next.add(id); else next.delete(id); });
      return next;
    });
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
        onChanged(await setFindingEndpointStatus(finding.id, row.id, hostStatus));
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
          setFindingEndpointsStatus(finding.id, { finding_host_ids: ids, host_status: hostStatus, summary }));
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
    // change stays ticked, and so does anything ticked since it started (M5).
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

  const columns = canManage ? 4 : 2;

  return (
    <div id="endpoints" className="min-w-0 scroll-mt-24">
      {finding.hosts.length > 0 && (
        <div className="mb-xs flex min-w-0 flex-wrap items-center gap-xs">
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
                  onClick={() => changeFilter(() => setStateFilter(active ? 'all' : s))}
                  className={cn(
                    'inline-flex items-center gap-xs whitespace-nowrap rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    active
                      ? 'border-primary bg-primary/10 text-foreground'
                      : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
                  )}
                >
                  <span>{s === 'all' ? 'All' : CHIP_LABEL[s]}</span>
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
      )}

      {canManage && selectedIds.length > 0 && (
        <div
          className="mb-xs flex min-w-0 flex-wrap items-end gap-xs border-l-4 border-l-info py-xxs pl-sm"
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
      {canManage && allShownSelected && matching.length > shown.length && selectedIds.length < matching.length && (
        <p className="mb-xs text-caption text-muted-foreground">
          The {shown.length.toLocaleString()} shown are selected.{' '}
          <Button variant="link" size="sm" className="h-auto p-0" disabled={bulkBusy}
            onClick={() => setSelected(new Set(matching.map((h) => h.id)))}>
            Select all {matching.length.toLocaleString()}{filtered ? ' matching' : ''}
          </Button>
        </p>
      )}

      <div className="overflow-x-auto">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              {canManage && (
                <TableHead className="w-10">
                  <Checkbox
                    checked={allShownSelected ? true : someShownSelected ? 'indeterminate' : false}
                    onCheckedChange={(v) => toggleAllShown(v === true)}
                    disabled={shownIds.length === 0 || bulkBusy}
                    aria-label="Select every endpoint shown"
                  />
                </TableHead>
              )}
              <TableHead>Host</TableHead>
              <TableHead className="w-44">State on this endpoint</TableHead>
              {canManage && <TableHead className="w-16" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns} className="py-lg text-center text-muted-foreground">
                  {finding.hosts.length === 0 ? 'No hosts attached.' : 'No endpoint matches this filter.'}
                </TableCell>
              </TableRow>
            ) : (
              shown.map((h) => (
                <TableRow
                  key={h.id}
                  data-endpoint-row={h.id}
                  data-state={isSelected(h.id) ? 'selected' : undefined}
                  {...(canManage ? { 'aria-selected': isSelected(h.id) } : {})}
                  className={h.id === focusEndpointId ? LIST_CURSOR_CLASS : undefined}
                >
                  {canManage && (
                    <TableCell>
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
                    </TableCell>
                  )}
                  <TableCell className="min-w-0">
                    <div className="truncate">
                      <Link to={`/hosts/${h.host_id}`} className="font-mono text-info hover:underline">
                        {h.ip_address || `Host ${h.host_id}`}
                      </Link>
                      {h.hostname && <span className="ml-xs text-caption text-muted-foreground" title={h.hostname}>{h.hostname}</span>}
                      {h.fqdn && h.name_id != null && (
                        <Link
                          to={`/names?name_id=${h.name_id}`}
                          className="ml-xs font-mono text-caption text-info hover:underline"
                          title="Named endpoint this finding applies to on this host"
                        >
                          {h.fqdn}
                        </Link>
                      )}
                    </div>
                    {(proposals.get(h.id) ?? []).map((pr) => (
                      <EndpointProposal key={pr.id} pr={pr} canDecide={canDecide} onDecided={(u) => onProposalDecided?.(u)} />
                    ))}
                  </TableCell>
                  <TableCell>
                    {canManage ? (
                      <Select
                        value={h.host_status}
                        onValueChange={(v) => void setOne(h, v as FindingHostStatus)}
                        disabled={rowBusy(h.id)}
                      >
                        <SelectTrigger className="h-7 w-[10rem] text-caption" aria-label={`State of ${endpointName(h)}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(Object.keys(ENDPOINT_STATUS_LABEL) as FindingHostStatus[]).map((s) => (
                            <SelectItem key={s} value={s}>{ENDPOINT_STATUS_LABEL[s]}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Badge variant={READ_ONLY_VARIANT[h.host_status] ?? 'info'}>
                        {ENDPOINT_STATUS_LABEL[h.host_status] ?? h.host_status}
                      </Badge>
                    )}
                  </TableCell>
                  {canManage && (
                    <TableCell>
                      <Button
                        variant="ghost" size="icon"
                        onClick={() => onRemove(h)}
                        disabled={rowBusy(h.id)}
                        aria-label={`Detach ${endpointName(h)} from finding`}
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      {matching.length > shown.length && (
        <div className="flex flex-wrap items-center gap-sm border-t border-border pt-xs text-caption text-muted-foreground" data-testid="endpoints-cut">
          <span>
            Showing {shown.length.toLocaleString()} of {matching.length.toLocaleString()}
            {filtered ? ' matching' : ''} endpoint{matching.length === 1 ? '' : 's'}.
          </span>
          <Button variant="ghost" size="sm" onClick={() => setLimit((n) => n + ENDPOINT_CAP)}>
            Show {Math.min(ENDPOINT_CAP, matching.length - shown.length).toLocaleString()} more
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setLimit(matching.length)}>
            Show all {matching.length.toLocaleString()}
          </Button>
        </div>
      )}
    </div>
  );
};

export default FindingEndpoints;
