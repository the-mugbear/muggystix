/**
 * Scanner observations, one row per ISSUE across the project's hosts
 * (v5.272.0) — the Findings page's second view.
 *
 * Scanner rows used to be reachable one host at a time, in the inspector, so
 * an issue on forty hosts could only be seen and promoted by opening one of
 * them.  Here each issue is listed once with how many hosts carry it and how
 * many a finding already covers.  Several issues are promoted at once, each on
 * every host carrying it or on the hosts ticked under its row: "confirmed" is
 * recorded only where the operator means it (the host-scoped promotion rule).
 * An issue that already has a finding joins it, its status untouched.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';

import {
  getObservationIssueHosts,
  getObservationIssues,
  promoteObservationIssues,
} from '../../services/api';
import type { ObservationIssue, ObservationIssueHost, WeaknessKind } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useLatestRequest } from '../../hooks/useLatestRequest';
import { useListCursor } from '../../hooks/useListCursor';
import { formatApiError } from '../../utils/apiErrors';
import { ENDPOINT_STATUS_LABEL, STATUS_LABEL } from '../../utils/findingStatus';
import { stickyBelowChrome } from '../../utils/uiStyles';
import type { FindingHostStatus, FindingStatus } from '../../services/api';
import { SeverityBadge } from '../ui/SeverityBadge';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Label } from '../ui/label';
import ListFilterBar, { FILTER_TRIGGER_CLASS, ListFilterSearch } from '../ListFilterBar';
import { cn } from '../../utils/cn';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Switch } from '../ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

const PAGE = 50;
/** Hosts listed under an issue; one more is requested to know the list is cut. */
export const HOST_CAP = 100;
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

interface Props {
  /** Analyst or above: may select and promote. */
  canManage: boolean;
}

const ScannerObservations: React.FC<Props> = ({ canManage }) => {
  const toast = useToast();
  const run = useLatestRequest();
  // The filters live in the URL (review 2026-09-23 B-UI-3), as the Findings
  // list's do: "critical issues on 5+ hosts" can be bookmarked and shared.
  // Own keys, so switching views never mixes the two lists' filters.
  const [params, setParams] = useSearchParams();
  const search = params.get('obs_search') ?? '';
  const severity = params.get('obs_severity') ?? 'all';
  // Any number of hosts by default (v5.288.0): "2 or more" hid every
  // single-host issue, criticals included, with nothing saying so.
  const minHosts = Number(params.get('obs_min') ?? 1) || 1;
  const includeJudged = params.get('obs_judged') === '1';
  // v5.298.0 — misconfigurations (catalog checks) / vulnerabilities.
  const kind = params.get('obs_kind') ?? 'all';
  const setParam = useCallback((key: string, value: string, fallback: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === fallback) next.delete(key);
      else next.set(key, value);
      return next;
    }, { replace: true });
  }, [setParams]);
  const setSeverity = (v: string) => setParam('obs_severity', v, 'all');
  const setMinHosts = (v: number) => setParam('obs_min', String(v), '1');
  const setIncludeJudged = (v: boolean) => setParam('obs_judged', v ? '1' : '0', '0');
  const setKind = (v: string) => setParam('obs_kind', v, 'all');
  const [searchInput, setSearchInput] = useState(search);
  const [issues, setIssues] = useState<ObservationIssue[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Selection spans loads: issue_key → the issue, and the hosts ticked under
  // it (absent = every host carrying it).
  const [selected, setSelected] = useState<Map<string, ObservationIssue>>(new Map());
  const [hostChoice, setHostChoice] = useState<Map<string, Set<number>>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [hostsByKey, setHostsByKey] = useState<Record<string, ObservationIssueHost[] | 'loading' | 'error'>>({});
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [promoting, setPromoting] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setParam('obs_search', searchInput.trim(), ''), 300);
    return () => clearTimeout(t);
  }, [searchInput, setParam]);

  const filters = useMemo(
    () => ({
      search, severity: severity === 'all' ? undefined : severity, minHosts, includeJudged,
      kind: kind === 'all' ? undefined : (kind as WeaknessKind),
    }),
    [search, severity, minHosts, includeJudged, kind],
  );
  // The filters a "Load more" response belongs to: a page that lands after the
  // filters changed is dropped, not appended to the new list.
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const load = useCallback(async () => {
    setLoading(true);
    const r = await run(() => getObservationIssues({ ...filters, limit: PAGE }));
    if (r.stale) return;
    if (r.ok) {
      setIssues(r.value.items);
      setTotal(r.value.total);
      setError(null);
    } else {
      setError(formatApiError(r.error, 'Could not load the scanner observations.'));
    }
    setLoading(false);
  }, [run, filters]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadMore = async () => {
    const asked = filters;
    setLoadingMore(true);
    try {
      const page = await getObservationIssues({ ...asked, skip: issues.length, limit: PAGE });
      if (filtersRef.current !== asked) return;
      setIssues((prev) => {
        const seen = new Set(prev.map((p) => p.issue_key));
        return [...prev, ...page.items.filter((i) => !seen.has(i.issue_key))];
      });
      setTotal(page.total);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not load more issues'));
    } finally {
      setLoadingMore(false);
    }
  };

  const toggleExpanded = async (issue: ObservationIssue) => {
    const key = issue.issue_key;
    const open = expanded.has(key);
    setExpanded((prev) => {
      const next = new Set(prev);
      if (open) next.delete(key);
      else next.add(key);
      return next;
    });
    if (open || Array.isArray(hostsByKey[key])) return;
    setHostsByKey((prev) => ({ ...prev, [key]: 'loading' }));
    try {
      const hosts = await getObservationIssueHosts(key, HOST_CAP + 1);
      setHostsByKey((prev) => ({ ...prev, [key]: hosts }));
    } catch {
      setHostsByKey((prev) => ({ ...prev, [key]: 'error' }));
    }
  };

  // Selecting or deselecting an issue drops any host narrowing: re-selecting
  // means "every host".  A narrowing left behind at zero hosts was sent as
  // ``host_ids: []`` and the server refused the WHOLE batch (review 2026-09-23
  // R11).
  const toggleIssue = (issue: ObservationIssue, on: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (on) next.set(issue.issue_key, issue);
      else next.delete(issue.issue_key);
      return next;
    });
    setHostChoice((prev) => {
      if (!prev.has(issue.issue_key)) return prev;
      const map = new Map(prev);
      map.delete(issue.issue_key);
      return map;
    });
  };

  // Every host starts ticked.  Unticking narrows the issue and selects it;
  // ticking them all again goes back to "every host"; unticking the LAST one
  // deselects the issue and drops the narrowing, so no empty host list can
  // reach the server.
  const toggleHost = (issue: ObservationIssue, hosts: ObservationIssueHost[], hostId: number, on: boolean) => {
    const key = issue.issue_key;
    const current = hostChoice.get(key) ?? new Set(hosts.map((h) => h.host_id));
    const next = new Set(current);
    if (on) next.add(hostId);
    else next.delete(hostId);
    if (next.size === 0) {
      toggleIssue(issue, false);
      return;
    }
    setSelected((prev) => (prev.has(key) ? prev : new Map(prev).set(key, issue)));
    setHostChoice((prev) => {
      const map = new Map(prev);
      if (next.size === hosts.length) map.delete(key);
      else map.set(key, next);
      return map;
    });
  };

  const hostCountFor = (issue: ObservationIssue) => hostChoice.get(issue.issue_key)?.size ?? issue.host_count;
  const chosen = Array.from(selected.values());
  const chosenHosts = chosen.reduce((n, i) => n + hostCountFor(i), 0);
  const pageAllSelected = issues.length > 0 && issues.every((i) => selected.has(i.issue_key));

  const promote = async () => {
    setPromoting(true);
    try {
      const res = await promoteObservationIssues(
        chosen.map((i) => {
          const hosts = hostChoice.get(i.issue_key);
          return hosts ? { issue_key: i.issue_key, host_ids: Array.from(hosts) } : { issue_key: i.issue_key };
        }),
      );
      const created = res.results.filter((r) => r.created).length;
      const joined = res.results.length - created;
      toast.success(
        `Promoted ${plural(res.results.length, 'issue')}: ${plural(created, 'new finding')}`
        + (joined ? `, ${joined} joined an existing finding` : ''),
      );
      setConfirmOpen(false);
      setSelected(new Map());
      setHostChoice(new Map());
      setHostsByKey({});
      setExpanded(new Set());
      await load();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not promote the selected issues'));
    } finally {
      setPromoting(false);
    }
  };

  const waiting = (i: ObservationIssue) => i.host_count - i.judged_host_count;

  // j/k (↓/↑) move a row cursor, Enter shows or hides the issue's hosts.
  const { cursorRowProps } = useListCursor(
    loading || error ? 0 : issues.length,
    (i) => void toggleExpanded(issues[i]),
    { resetKey: `${search}|${severity}|${minHosts}|${includeJudged}|${kind}` },
  );

  return (
    <div>
      <p className="mb-sm text-metadata text-muted-foreground">
        What the scanners reported, grouped by issue across every host. Promote an issue to record it as a finding — on
        every host that carries it, or only the hosts you tick under it.
      </p>

      {/* The shared filter row (v5.294.0), as on the Findings view. */}
      <ListFilterBar className="mb-md">
        <ListFilterSearch
          value={searchInput}
          onChange={setSearchInput}
          placeholder="Search titles or CVE…"
          label="Search scanner observations"
        />
        <Select value={severity} onValueChange={setSeverity}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-36')} aria-label="Severity"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All severities</SelectItem>
            {SEVERITIES.map((s) => (
              <SelectItem key={s} value={s} className="capitalize">{s}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={kind} onValueChange={setKind}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-48')} aria-label="Kind"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All kinds</SelectItem>
            <SelectItem value="misconfiguration">Misconfigurations</SelectItem>
            <SelectItem value="vulnerability">Vulnerabilities</SelectItem>
            <SelectItem value="informational">Informational</SelectItem>
          </SelectContent>
        </Select>
        <Select value={String(minHosts)} onValueChange={(v) => setMinHosts(Number(v))}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-44')} aria-label="Carried by"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="1">Any number of hosts</SelectItem>
            <SelectItem value="2">2 or more hosts</SelectItem>
            <SelectItem value="5">5 or more hosts</SelectItem>
            <SelectItem value="10">10 or more hosts</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex items-center gap-xs">
          <Switch id="observations-judged" checked={includeJudged} onCheckedChange={(v) => setIncludeJudged(v === true)} />
          <Label htmlFor="observations-judged" className="text-metadata">Include issues already covered</Label>
        </div>
      </ListFilterBar>

      {/* v5.290.0 — the action bar's slot is always there, at a fixed height:
          inserting the bar on the first tick pushed every row down, so the
          second click landed on the wrong issue.  Empty, it holds a hint. */}
      {canManage && (
        <div
          className="sticky z-10 mb-sm flex h-11 min-w-0 flex-nowrap items-center gap-sm overflow-hidden border-b border-border bg-background py-xs"
          style={stickyBelowChrome}
          role="region"
          aria-label="Bulk actions"
          data-testid="observations-bulk-slot"
        >
          <span className="min-w-0 truncate text-metadata" aria-live="polite">
            {chosen.length > 0 ? (
              <>
                <strong>{plural(chosen.length, 'issue')}</strong> selected · {plural(chosenHosts, 'host')}
              </>
            ) : (
              <span className="text-muted-foreground">Select issues to promote them to findings</span>
            )}
          </span>
          {chosen.length > 0 && (
            <>
              <Button size="sm" className="shrink-0" onClick={() => setConfirmOpen(true)}>Promote to findings</Button>
              <Button
                size="sm"
                variant="ghost"
                className="shrink-0"
                onClick={() => { setSelected(new Map()); setHostChoice(new Map()); }}
              >
                Clear
              </Button>
            </>
          )}
        </div>
      )}

      {error ? (
        <p role="alert" className="text-metadata text-destructive">{error}</p>
      ) : loading && issues.length === 0 ? (
        <p className="flex items-center gap-xs text-metadata text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading scanner observations…
        </p>
      ) : issues.length === 0 ? (
        <p className="text-metadata text-muted-foreground">
          {includeJudged
            ? 'No scanner observation matches these filters.'
            : 'Nothing waits: every matching issue is already covered by a finding on each of its hosts.'}
        </p>
      ) : (
        <>
          <p className="mb-xs text-caption text-muted-foreground" data-testid="observations-count">
            {plural(total, 'issue')}{minHosts > 1 ? ` carried by ${minHosts} or more hosts` : ''}
            {includeJudged ? '' : ' with hosts not yet judged'} · most severe first
          </p>
          <div className="overflow-x-auto">
            <Table className="min-w-[48rem] table-fixed">
              <TableHeader>
                <TableRow>
                  {canManage && (
                    <TableHead className="w-10">
                      <Checkbox
                        aria-label="Select every issue shown"
                        checked={pageAllSelected}
                        onCheckedChange={(v) => issues.forEach((i) => toggleIssue(i, v === true))}
                      />
                    </TableHead>
                  )}
                  <TableHead>Issue</TableHead>
                  <TableHead className="w-28">Severity</TableHead>
                  <TableHead className="w-40">Hosts</TableHead>
                  <TableHead className="w-28 text-right"><span className="sr-only">Hosts carrying it</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {issues.map((issue, index) => {
                  const key = issue.issue_key;
                  const isOpen = expanded.has(key);
                  const hosts = hostsByKey[key];
                  const narrowed = hostChoice.get(key);
                  return (
                    <React.Fragment key={key}>
                      <TableRow {...cursorRowProps(index, 'align-top')} data-issue-key={key}>
                        {canManage && (
                          <TableCell>
                            <Checkbox
                              aria-label={`Select ${issue.title}`}
                              checked={selected.has(key)}
                              onCheckedChange={(v) => toggleIssue(issue, v === true)}
                            />
                          </TableCell>
                        )}
                        <TableCell className="min-w-0">
                          <p className="line-clamp-2 break-words font-medium" title={issue.title}>{issue.title}</p>
                          <p className="truncate text-caption text-muted-foreground">
                            {[
                              issue.kind === 'misconfiguration' ? 'misconfiguration' : null,
                              issue.cve_id, issue.sources.join(', '),
                            ].filter(Boolean).join(' · ')}
                          </p>
                          {issue.finding_id != null && (
                            <Link to={`/findings/${issue.finding_id}`} className="text-caption text-primary hover:underline">
                              Finding #{issue.finding_id}
                              {issue.finding_status ? ` · ${STATUS_LABEL[issue.finding_status as FindingStatus] ?? issue.finding_status}` : ''}
                            </Link>
                          )}
                        </TableCell>
                        <TableCell>
                          <SeverityBadge severity={issue.severity} />
                        </TableCell>
                        <TableCell className="tabular-nums">
                          <p>{plural(issue.host_count, 'host')}</p>
                          <p className="text-caption text-muted-foreground">
                            {issue.judged_host_count > 0
                              ? `${issue.judged_host_count} covered · ${waiting(issue)} not yet judged`
                              : 'none judged yet'}
                          </p>
                          {narrowed && selected.has(key) && (
                            <p className="text-caption text-primary">{narrowed.size} of {issue.host_count} ticked</p>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => void toggleExpanded(issue)}
                            aria-expanded={isOpen}
                            aria-label={`${isOpen ? 'Hide' : 'Show'} the hosts carrying ${issue.title}`}
                          >
                            {isOpen ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
                            Hosts
                          </Button>
                        </TableCell>
                      </TableRow>
                      {isOpen && (
                        <TableRow>
                          <TableCell colSpan={canManage ? 5 : 4} className="bg-muted/30 p-sm">
                            {hosts === 'loading' || hosts === undefined ? (
                              <p className="flex items-center gap-xs text-caption text-muted-foreground">
                                <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading hosts…
                              </p>
                            ) : hosts === 'error' ? (
                              <p className="text-caption text-destructive">Couldn&apos;t load the hosts. Collapse the row and try again.</p>
                            ) : (() => {
                              // A cut list cannot be narrowed: unticking one of the
                              // first HOST_CAP would promote those, not the rest.
                              const cut = hosts.length > HOST_CAP;
                              const shown = cut ? hosts.slice(0, HOST_CAP) : hosts;
                              return (
                              <>
                                {canManage && !cut && (
                                  <p className="mb-xxs text-caption text-muted-foreground">
                                    Untick the hosts you have not verified; the finding is recorded on the ticked ones.
                                  </p>
                                )}
                                {cut && (
                                  <p className="mb-xxs text-caption text-muted-foreground" data-testid="observation-hosts-cut">
                                    The first {HOST_CAP} of {plural(issue.host_count, 'host')}.{' '}
                                    {canManage && 'Promoting records it on every one; to narrow, open them on Hosts. '}
                                    <Link
                                      to={`/hosts?q=${encodeURIComponent(`issue:"${key.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`)}`}
                                      className="text-primary hover:underline"
                                    >
                                      All {plural(issue.host_count, 'host')} on Hosts
                                    </Link>
                                  </p>
                                )}
                                <ul className="divide-y divide-border">
                                  {shown.map((h) => {
                                    const ticked = narrowed ? narrowed.has(h.host_id) : true;
                                    return (
                                      <li key={h.host_id} className="flex min-w-0 items-center gap-sm py-xxs text-metadata">
                                        {canManage && !cut && (
                                          <Checkbox
                                            aria-label={`Include ${h.ip_address}`}
                                            checked={ticked}
                                            onCheckedChange={(v) => toggleHost(issue, hosts, h.host_id, v === true)}
                                          />
                                        )}
                                        <Link to={`/hosts/${h.host_id}`} className="w-32 shrink-0 truncate font-mono text-primary hover:underline">
                                          {h.ip_address}
                                        </Link>
                                        <span className="min-w-0 flex-1 truncate text-muted-foreground" title={h.hostname ?? undefined}>
                                          {h.hostname || '—'}
                                        </span>
                                        <span className="w-32 shrink-0 truncate text-caption text-muted-foreground">
                                          {h.ports.length > 0 ? `port ${h.ports.join(', ')}` : 'host-level'}
                                        </span>
                                        <span className="w-40 shrink-0 truncate text-caption">
                                          {h.judged
                                            ? `On the finding · ${ENDPOINT_STATUS_LABEL[(h.endpoint_status ?? 'open') as FindingHostStatus] ?? h.endpoint_status}`
                                            : <span className="text-muted-foreground">not yet judged</span>}
                                        </span>
                                      </li>
                                    );
                                  })}
                                </ul>
                              </>
                              );
                            })()}
                          </TableCell>
                        </TableRow>
                      )}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          {issues.length < total && (
            <div className="mt-sm flex justify-center">
              <Button variant="outline" size="sm" onClick={() => void loadMore()} disabled={loadingMore}>
                {loadingMore && <Loader2 className="size-4 animate-spin" aria-hidden />}
                Load {Math.min(PAGE, total - issues.length)} more
              </Button>
            </div>
          )}
        </>
      )}

      <Dialog open={confirmOpen} onOpenChange={(v) => !promoting && setConfirmOpen(v)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Promote {plural(chosen.length, 'issue')} to findings</DialogTitle>
            <DialogDescription>
              Each issue is recorded as a confirmed finding on the hosts listed. Record it only where it has been
              verified. An issue that already has a finding joins it, and that finding&apos;s status is left as it is.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <ul className="flex flex-col divide-y divide-border">
              {chosen.map((i) => (
                <li key={i.issue_key} className="flex min-w-0 items-baseline gap-sm py-xxs text-metadata">
                  <span className="min-w-0 flex-1 truncate" title={i.title}>{i.title}</span>
                  <span className="shrink-0 text-caption text-muted-foreground">
                    {hostChoice.has(i.issue_key)
                      ? `${hostCountFor(i)} of ${i.host_count} hosts`
                      : `all ${plural(i.host_count, 'host')}`}
                    {i.finding_id != null ? ` · joins finding #${i.finding_id}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={promoting}>Cancel</Button>
            <Button onClick={() => void promote()} disabled={promoting}>
              {promoting && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Promote {plural(chosen.length, 'issue')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ScannerObservations;
