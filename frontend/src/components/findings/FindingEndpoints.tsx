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
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Trash2, X } from 'lucide-react';

import {
  Finding, FindingHostInfo, FindingHostStatus, setFindingEndpointStatus, setFindingEndpointsStatus,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import {
  ENDPOINT_BULK_MAX, ENDPOINT_CAP, ENDPOINT_STATES, EndpointStateFilter, chunked, endpointStateCounts,
  filterEndpoints, idRange,
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
}

const FindingEndpoints: React.FC<Props> = ({ finding, canManage, onChanged, onRemove, focusEndpointId = null }) => {
  const toast = useToast();
  const [stateFilter, setStateFilter] = useState<EndpointStateFilter>('all');
  const [text, setText] = useState('');
  const [limit, setLimit] = useState(ENDPOINT_CAP);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const anchor = useRef<number | null>(null);
  // Rows whose own change is in flight — a set: two rows changed in quick
  // succession are both busy, and each stays locked until ITS request is back.
  const [saving, setSaving] = useState<Set<number>>(new Set());
  // Every change answers with the whole finding.  Responses are applied in
  // the order the requests were SENT: one that comes back after a later
  // request's answer was applied is older news and would repaint its rows
  // stale, so it is dropped (M4).
  const sent = useRef(0);
  const applied = useRef(0);
  const applyResponse = (seq: number, updated: Finding) => {
    if (seq < applied.current) return;
    applied.current = seq;
    onChanged(updated);
  };
  const [bulkState, setBulkState] = useState<FindingHostStatus | ''>('');
  const [bulkSummary, setBulkSummary] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);

  const counts = useMemo(
    () => endpointStateCounts(finding.hosts, finding.endpoint_status_counts),
    [finding.hosts, finding.endpoint_status_counts],
  );
  const matching = useMemo(
    () => filterEndpoints(finding.hosts, stateFilter, text),
    [finding.hosts, stateFilter, text],
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
    sent.current += 1;
    const seq = sent.current;
    setSaving((prev) => new Set(prev).add(row.id));
    try {
      // The route answers with the finding: the row is updated from it, not
      // from a second read of thousands of endpoints.
      applyResponse(seq, await setFindingEndpointStatus(finding.id, row.id, hostStatus));
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
    sent.current += 1;
    const seq = sent.current;
    setBulkBusy(true);
    const results = await runLimited(chunks, 1, (ids) =>
      setFindingEndpointsStatus(finding.id, { finding_host_ids: ids, host_status: hostStatus, summary }));
    setBulkBusy(false);
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
    if (latest) applyResponse(seq, latest);
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
                  <TableCell className="truncate">
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
