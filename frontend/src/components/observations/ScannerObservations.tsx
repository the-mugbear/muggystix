/**
 * Scanner observations, one row per ISSUE across the project's hosts — the
 * Inventory page `/observations` (5.404.0; until then the Findings page's
 * second view, v5.272.0).
 *
 * Every observation is listed by default, judged or not; "Only not yet
 * judged" (`obs_pending=1`) narrows it to what still waits.  Opening a row
 * opens the inspector (`ObservationInspector`, `?issue=` in the address): what
 * the scanner wrote, every host carrying it and what it printed on each.
 * Several issues are promoted at once from the bulk bar, each on every host
 * carrying it or on the hosts ticked in its inspector: "confirmed" is recorded
 * only where the operator means it (the host-scoped promotion rule).  An issue
 * that already has a finding joins it, its status untouched.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Ban, Loader2, PanelRightOpen } from 'lucide-react';

import {
  dismissObservationIssue,
  getObservationIssues,
  promoteObservationIssues,
} from '../../services/api';
import type { ObservationIssue, ObservationIssueHost, WeaknessKind } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useListCursor } from '../../hooks/useListCursor';
import { usePagedList } from '../../hooks/usePagedList';
import { useProjectId } from '../../hooks/useProjectId';
import { useUrlPage } from '../../hooks/useUrlPage';
import { useUrlSearchDraft } from '../../hooks/useUrlSearchDraft';
import { formatApiError } from '../../utils/apiErrors';
import { STATUS_LABEL } from '../../utils/findingStatus';
import type { FindingStatus } from '../../services/api';
import { selectAllState } from '../../utils/selection';
import { stickyBelowChrome } from '../../utils/uiStyles';
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
import { Textarea } from '../ui/textarea';
import { PagedFooter } from '../operations/QueueParts';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import ObservationInspector from './ObservationInspector';

const PAGE = 50;
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;
const NO_ISSUES: ObservationIssue[] = [];

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

interface Props {
  /** Analyst or above: may select, promote and dismiss. */
  canManage: boolean;
}

const ScannerObservations: React.FC<Props> = ({ canManage }) => {
  const toast = useToast();
  const projectId = useProjectId();
  // The filters live in the address (review 2026-09-23 B-UI-3): "critical
  // issues on 5+ hosts" can be bookmarked and shared.
  const [params, setParams] = useSearchParams();
  // The search box: `value` is the address's and what the list is asked for,
  // `draft` what is being typed — committed a moment after the typing stops,
  // and following the address when it changes from elsewhere (Back, a link).
  // A commit takes `page` out in the same write: the result set changed.
  const searchBox = useUrlSearchDraft('obs_search');
  const search = searchBox.value;
  const severity = params.get('obs_severity') ?? 'all';
  // Any number of hosts by default (v5.288.0): "2 or more" hid every
  // single-host issue, criticals included, with nothing saying so.
  const minHosts = Number(params.get('obs_min') ?? 1) || 1;
  // 5.404.0 — every observation by default; `obs_pending=1` keeps those with
  // a host not yet judged (what the Findings view used to list).
  const pendingOnly = params.get('obs_pending') === '1';
  const includeJudged = !pendingOnly;
  // v5.298.0 — misconfigurations (catalog checks) / vulnerabilities.
  const kind = params.get('obs_kind') ?? 'all';
  const setParam = useCallback((key: string, value: string, fallback: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === fallback) next.delete(key);
      else next.set(key, value);
      // A filter starts from the first page, in this one write.
      next.delete('page');
      return next;
    }, { replace: true });
  }, [setParams]);
  const setSeverity = (v: string) => setParam('obs_severity', v, 'all');
  const setMinHosts = (v: number) => setParam('obs_min', String(v), '1');
  const setPendingOnly = (v: boolean) => setParam('obs_pending', v ? '1' : '0', '0');
  const setKind = (v: string) => setParam('obs_kind', v, 'all');
  // Plan A7 — what to test first: issues with a known exploit, and the most
  // widespread ones.  Both are the server's (a filter and an order over ALL
  // issues, not over the rows loaded so far).
  const exploitable = params.get('obs_exploit') === '1';
  const setExploitable = (v: boolean) => setParam('obs_exploit', v ? '1' : '0', '0');
  const sort: 'severity' | 'hosts' = params.get('obs_sort') === 'hosts' ? 'hosts' : 'severity';
  const setSort = (v: string) => setParam('obs_sort', v, 'severity');

  // The inspector's issue is the address's: a link opens it, Back closes it.
  // Opening from the list is a history entry; Previous / Next REPLACE it, so
  // Back closes the inspector instead of walking back through every issue.
  const openKey = params.get('issue');
  const openIssue = useCallback((key: string, replace = false) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('issue', key);
      return next;
    }, { replace });
  }, [setParams]);
  const closeIssue = useCallback(() => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('issue');
      return next;
    }, { replace: true });
  }, [setParams]);

  // Selection spans pages: issue_key → the issue, and the hosts ticked under
  // it in its inspector (absent = every host carrying it).  The bar counts it
  // and the confirm dialog lists it, whichever page is on screen.
  const [selected, setSelected] = useState<Map<string, ObservationIssue>>(new Map());
  const [hostChoice, setHostChoice] = useState<Map<string, Set<number>>>(new Map());

  const filters = useMemo(
    () => ({
      search, severity: severity === 'all' ? undefined : severity, minHosts, includeJudged,
      kind: kind === 'all' ? undefined : (kind as WeaknessKind),
      exploitable, sort,
    }),
    [search, severity, minHosts, includeJudged, kind, exploitable, sort],
  );

  // One page at a time, the page in the address (`?page=`): 636 issues were
  // thirteen "Load 50 more" clicks, and a reload started again from the top.
  // The filters are the list's key, and a new filter is the first page.
  const list = usePagedList<ObservationIssue>(
    'getObservationIssues',
    ({ offset, limit, signal }) => getObservationIssues(projectId, { ...filters, ...(offset > 0 ? { skip: offset } : {}), limit }, signal),
    [projectId, filters],
    { pageSize: PAGE, errorMessage: 'Could not load the scanner observations.', page: useUrlPage(), within: projectId },
  );
  const issues = list.rows ?? NO_ISSUES;
  const { total, error } = list;
  // No rows of this page yet: loading (or failed — `error` says which).
  const loading = list.rows === null;

  // Where the open issue sits: on this page (its row), and in the whole list.
  const openIndex = openKey !== null ? issues.findIndex((i) => i.issue_key === openKey) : -1;
  const openRow = openIndex >= 0 ? issues[openIndex] : null;
  const absoluteIndex = openIndex >= 0 ? list.page * PAGE + openIndex : -1;
  // Stepping past the page's edge turns the page; the issue at that edge of
  // the new page opens once its rows are in.
  const pendingEdge = useRef<'first' | 'last' | null>(null);
  useEffect(() => {
    if (pendingEdge.current === null || list.rows === null || list.rows.length === 0) return;
    const edge = pendingEdge.current;
    pendingEdge.current = null;
    openIssue(edge === 'first' ? list.rows[0].issue_key : list.rows[list.rows.length - 1].issue_key, true);
  }, [list.rows, openIssue]);
  const step = (delta: 1 | -1) => {
    if (openIndex < 0) {
      // Not on this page any more: Next starts from the top of what is shown.
      if (delta === 1 && issues.length > 0) openIssue(issues[0].issue_key, true);
      return;
    }
    const target = openIndex + delta;
    if (target >= 0 && target < issues.length) {
      openIssue(issues[target].issue_key, true);
      return;
    }
    pendingEdge.current = delta === 1 ? 'first' : 'last';
    list.setPage(list.page + delta);
  };
  const hasPrev = absoluteIndex > 0;
  const hasNext = openIndex < 0 ? issues.length > 0 : absoluteIndex < total - 1;

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

  // What a decision was about leaves the selection; the rest stays chosen.
  const forget = (keys: string[]) => {
    const gone = new Set(keys);
    setSelected((prev) => new Map([...prev].filter(([k]) => !gone.has(k))));
    setHostChoice((prev) => new Map([...prev].filter(([k]) => !gone.has(k))));
  };
  const queryClient = useQueryClient();
  // Findings were made or joined: the lists of them are out of date, and so is
  // this one (an issue's judged count moves; under "only not yet judged" a
  // judged issue leaves it) and the open issue's hosts — busy until it is back.
  const refreshAfterDecision = () => {
    void queryClient.invalidateQueries({ queryKey: ['listFindings'] });
    void queryClient.invalidateQueries({ queryKey: ['getObservationIssueHosts'] });
    return queryClient.invalidateQueries({ queryKey: ['getObservationIssues'] });
  };

  // The issues a Promote dialog is about: the bulk bar's selection, or the
  // one issue its inspector promotes.
  const [promoteTargets, setPromoteTargets] = useState<ObservationIssue[] | null>(null);
  const promotion = useMutation({
    mutationFn: (items: { issue_key: string; host_ids?: number[] }[]) => promoteObservationIssues(projectId, items),
    onSuccess: (res, sent) => {
      const created = res.results.filter((r) => r.created).length;
      const joined = res.results.length - created;
      toast.success(
        `Promoted ${plural(res.results.length, 'observation')}: ${plural(created, 'new finding')}`
        + (joined ? `, ${joined} joined an existing finding` : ''),
      );
      setPromoteTargets(null);
      forget(sent.map((i) => i.issue_key));
      return refreshAfterDecision();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not promote the selected observations')),
  });
  const promoting = promotion.isPending;
  const promote = () => promoteTargets && promotion.mutate(
    promoteTargets.map((i) => {
      const hosts = hostChoice.get(i.issue_key);
      return hosts ? { issue_key: i.issue_key, host_ids: Array.from(hosts) } : { issue_key: i.issue_key };
    }),
  );

  // Marking ONE observation a false positive: on the hosts ticked under it,
  // or — none unticked — on every host carrying it.
  const [toDismiss, setToDismiss] = useState<ObservationIssue | null>(null);
  const [dismissReason, setDismissReason] = useState('');
  const dismissHosts = toDismiss ? hostChoice.get(toDismiss.issue_key) : undefined;
  const openDismiss = (issue: ObservationIssue) => { setDismissReason(''); setToDismiss(issue); };
  const dismissal = useMutation({
    mutationFn: (body: { issue_key: string; host_ids?: number[]; reason?: string }) =>
      dismissObservationIssue(projectId, body),
    onSuccess: (res, sent) => {
      toast.success(sent.host_ids
        ? `Marked a false positive on ${plural(res.host_count, 'host')}`
        : `Marked a false positive on every host carrying it (${res.host_count.toLocaleString()})`);
      setToDismiss(null);
      forget([sent.issue_key]);
      return refreshAfterDecision();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not mark it a false positive')),
  });
  const dismissing = dismissal.isPending;
  const dismiss = () => {
    if (!toDismiss) return;
    const reason = dismissReason.trim();
    dismissal.mutate({
      issue_key: toDismiss.issue_key,
      ...(dismissHosts ? { host_ids: Array.from(dismissHosts) } : {}),
      ...(reason ? { reason } : {}),
    });
  };

  const waiting = (i: ObservationIssue) => i.host_count - i.judged_host_count;

  // j/k (↓/↑) move a row cursor, Enter opens the issue in the inspector.
  const { cursorRowProps } = useListCursor(
    error ? 0 : issues.length,
    (i) => openIssue(issues[i].issue_key),
    // A promotion re-reads the list (under "not yet judged" the issue may leave it).
    { resetKey: `${search}|${severity}|${minHosts}|${includeJudged}|${kind}|${exploitable}|${sort}`, getId: (i) => issues[i]?.issue_key },
  );

  return (
    <div>
      {/* The shared filter row (v5.294.0), as on the Findings list. */}
      <ListFilterBar className="mb-md">
        <ListFilterSearch
          value={searchBox.draft}
          onChange={searchBox.setDraft}
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
        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-48')} aria-label="Order"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="severity">Most severe first</SelectItem>
            <SelectItem value="hosts">Most hosts first</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex items-center gap-xs">
          <Switch id="observations-exploit" checked={exploitable} onCheckedChange={(v) => setExploitable(v === true)} />
          <Label htmlFor="observations-exploit" className="text-metadata">Exploit reported</Label>
        </div>
        <div className="flex items-center gap-xs">
          <Switch id="observations-pending" checked={pendingOnly} onCheckedChange={(v) => setPendingOnly(v === true)} />
          <Label htmlFor="observations-pending" className="text-metadata">Only not yet judged</Label>
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
                <strong>{plural(chosen.length, 'observation')}</strong> selected · {plural(chosenHosts, 'host')}
              </>
            ) : (
              <span className="text-muted-foreground">
                Tick observations to promote them to findings on every host — or open one to choose its hosts
              </span>
            )}
          </span>
          {chosen.length > 0 && (
            <>
              <Button size="sm" className="shrink-0" onClick={() => setPromoteTargets(chosen)}>Promote to findings</Button>
              {/* The host page's dismissal, reached from the list (5.379.0).
                  One observation at a time: a false positive is a judgment
                  of one thing, with its own reason. */}
              {/* Shown with several ticked too, saying why it waits (second
                  review 2026-10-16, O8: it vanished with no word). */}
              <Button
                size="sm"
                variant="outline"
                className="shrink-0"
                disabled={chosen.length !== 1}
                title={chosen.length !== 1 ? 'One observation at a time: a false positive is a judgment of one thing, with its own reason' : undefined}
                onClick={() => openDismiss(chosen[0])}
              >
                <Ban className="size-3.5" aria-hidden /> Mark false positive…
              </Button>
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
      ) : loading ? (
        <p className="flex items-center gap-xs text-metadata text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading scanner observations…
        </p>
      ) : issues.length === 0 ? (
        <p className="text-metadata text-muted-foreground">
          {pendingOnly
            ? 'Nothing waits: every matching observation is already covered by a finding on each of its hosts.'
            : 'No scanner observation matches these filters.'}
        </p>
      ) : (
        <>
          <p className="mb-xs text-caption text-muted-foreground" data-testid="observations-count">
            {/* "Distinct": each is counted once however many hosts carry it —
                Posture's and Portfolio's figures count it once per host. */}
            {plural(total, 'distinct scanner observation')}{minHosts > 1 ? ` carried by ${minHosts} or more hosts` : ''}
            {exploitable ? ' with an exploit reported' : ''}
            {pendingOnly ? `${exploitable ? ' and' : ' with'} hosts not yet judged` : ''}
            {' · '}{sort === 'hosts' ? 'most hosts first' : 'most severe first'}
          </p>
          <div className="overflow-x-auto">
            <Table className="min-w-[48rem] table-fixed">
              <TableHeader>
                <TableRow>
                  {canManage && (
                    <TableHead className="w-10">
                      <Checkbox
                        aria-label="Select every observation shown"
                        // Some of the issues shown = the dash; a click then
                        // selects the REST (an issue already selected keeps
                        // the hosts it was narrowed to).
                        checked={selectAllState(issues.filter((i) => selected.has(i.issue_key)).length, issues.length)}
                        onCheckedChange={(v) => issues.forEach((i) => {
                          if (v === true && selected.has(i.issue_key)) return;
                          toggleIssue(i, v === true);
                        })}
                      />
                    </TableHead>
                  )}
                  <TableHead>Scanner observation</TableHead>
                  <TableHead className="w-28">Severity</TableHead>
                  <TableHead className="w-40">Hosts</TableHead>
                  <TableHead className="w-24 text-right"><span className="sr-only">Open</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {issues.map((issue, index) => {
                  const key = issue.issue_key;
                  const narrowed = hostChoice.get(key);
                  const isOpen = key === openKey;
                  return (
                    <TableRow
                      key={key}
                      {...cursorRowProps(index, cn('align-top', isOpen && 'bg-accent'))}
                      data-issue-key={key}
                      data-state={selected.has(key) ? 'selected' : undefined}
                      aria-current={isOpen ? 'true' : undefined}
                      {...(canManage ? { 'aria-selected': selected.has(key) } : {})}
                    >
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
                        <button
                          type="button"
                          className="line-clamp-2 break-words text-left font-medium text-foreground hover:text-primary hover:underline"
                          title={issue.title}
                          onClick={() => openIssue(key)}
                        >
                          {issue.title}
                        </button>
                        <p className="truncate text-caption text-muted-foreground">
                          {[
                            issue.kind === 'misconfiguration' ? 'misconfiguration' : null,
                            issue.exploitable ? 'exploit reported' : null,
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
                        {/* Said only when it tells rows apart. */}
                        {issue.judged_host_count > 0 && (
                          <p className="text-caption text-muted-foreground">
                            {waiting(issue) > 0
                              ? `${issue.judged_host_count} covered · ${waiting(issue)} not yet judged`
                              : 'all judged'}
                          </p>
                        )}
                        {narrowed && selected.has(key) && (
                          <p className="text-caption text-primary">{narrowed.size} of {issue.host_count} ticked</p>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openIssue(key)}
                          aria-label={`Open ${issue.title}: what the scanner wrote and the hosts carrying it`}
                        >
                          <PanelRightOpen className="size-4" aria-hidden />
                          Open
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          {total > PAGE && (
            <PagedFooter
              pager={{ page: list.page, pageSize: PAGE, total, onPage: list.setPage }}
              shown={issues.length}
              noun="observations"
            />
          )}
        </>
      )}

      <ObservationInspector
        issueKey={openKey}
        issue={openRow}
        canManage={canManage}
        narrowed={openKey !== null ? hostChoice.get(openKey) : undefined}
        onToggleHost={toggleHost}
        onPromote={(issue) => setPromoteTargets([issue])}
        onDismiss={openDismiss}
        position={absoluteIndex >= 0 ? { index: absoluteIndex + 1, total } : null}
        hasPrev={hasPrev}
        hasNext={hasNext}
        onStep={step}
        onClose={closeIssue}
      />

      <Dialog open={promoteTargets !== null} onOpenChange={(v) => { if (!v && !promoting) setPromoteTargets(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Promote {plural(promoteTargets?.length ?? 0, 'observation')} to findings</DialogTitle>
            <DialogDescription>
              Each observation is recorded as a confirmed finding on the hosts listed. Record it only where it has been
              verified. One that already has a finding joins it, and that finding&apos;s status is left as it is.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <ul className="flex flex-col divide-y divide-border">
              {(promoteTargets ?? []).map((i) => (
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
            <Button variant="outline" onClick={() => setPromoteTargets(null)} disabled={promoting}>Cancel</Button>
            <Button onClick={promote} disabled={promoting}>
              {promoting && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Promote {plural(promoteTargets?.length ?? 0, 'observation')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={toDismiss !== null} onOpenChange={(v) => { if (!v && !dismissing) setToDismiss(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark as a false positive</DialogTitle>
            <DialogDescription>
              <span className="block break-words font-medium text-foreground">{toDismiss?.title}</span>
              {toDismiss && (dismissHosts
                ? `On the ${plural(dismissHosts.size, 'host')} ticked under it, of ${toDismiss.host_count.toLocaleString()}: those are recorded as false positives and the others still wait.`
                : `On every host carrying it (${toDismiss.host_count.toLocaleString()}): its finding is recorded as a false positive. To dismiss it on some hosts only, open it and untick the others first.`)}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Label htmlFor="observation-dismiss-reason">Reason</Label>
            <p className="text-caption text-muted-foreground">Optional. Kept on the finding&apos;s history.</p>
            <Textarea
              id="observation-dismiss-reason"
              className="mt-xxs"
              rows={3}
              maxLength={2000}
              value={dismissReason}
              onChange={(e) => setDismissReason(e.target.value)}
              placeholder="How it was ruled out"
            />
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setToDismiss(null)} disabled={dismissing}>Cancel</Button>
            <Button onClick={dismiss} disabled={dismissing}>
              {dismissing && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Mark false positive
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ScannerObservations;
