import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeftRight, Bot } from 'lucide-react';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useSearchFocus } from '../hooks/useSearchFocus';
import { NavigableTableRow, NavigableTableCell } from '../components/NavigableTableRow';
import { getTestPlans, TestPlanSummary } from '../services/api';
import { formatStatusLabel } from '../utils/statusMeta';
import { formatApiError } from '../utils/apiErrors';
import { agentInstruction } from '../utils/agentRuns';
import { useToast } from '../contexts/ToastContext';
import { useProject } from '../contexts/ProjectContext';
import { ListPageSkeleton } from '../components/PageSkeleton';
import LastUpdated from '../components/LastUpdated';
import ListFilterBar, { FILTER_TRIGGER_CLASS, ListFilterSearch } from '../components/ListFilterBar';
import TimeAgo from '../components/TimeAgo';
import AgentTaskButton from '../components/agent-sessions/AgentTaskButton';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

type Tone = 'default' | 'success' | 'warning' | 'destructive' | 'info' | 'muted' | 'secondary' | 'outline';

// 5.313.0 — a plan is draft → in progress → completed, or archived. Nothing
// waits on an approval: a plan records what you or your agent intend to test
// and what came of it.
const planStatusTone = (status: string | null | undefined): Tone => {
  switch (status) {
    case 'draft':
      return 'info';
    case 'in_progress':
      return 'warning';
    case 'completed':
      return 'success';
    case 'archived':
    default:
      return 'muted';
  }
};

function stripAttribution(text: string): string {
  return text.replace(/^🤖\s*\*{0,2}Agent-generated\*{0,2}\s*—\s*\S+\s*/i, '').trimStart();
}

/** v5.288.0 — progress as the fraction it is ("1 of 4 entries done") over a
 *  small bar, instead of a bare "0%" whose empty bar was invisible. Done =
 *  completed or rejected, the same rule as completion_pct. */
const PlanProgress: React.FC<{ plan: TestPlanSummary }> = ({ plan }) => {
  const total = plan.entry_count;
  if (total === 0) {
    return <span className="text-caption text-muted-foreground">No entries yet</span>;
  }
  const done = plan.entries_done ?? Math.round((plan.completion_pct / 100) * total);
  const pct = Math.min(100, Math.max(0, (done / total) * 100));
  return (
    <div className="flex min-w-0 flex-col gap-xxs">
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-label={`${done} of ${total} entries done`}
      >
        <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-caption text-muted-foreground">
        {done.toLocaleString()} of {total.toLocaleString()} {total === 1 ? 'entry' : 'entries'} done
      </p>
    </div>
  );
};

/** The plan author as a person: full name, else username. */
const authorName = (plan: TestPlanSummary): string | null =>
  plan.created_by_full_name?.trim() || plan.created_by_username || null;

const TestPlans: React.FC = () => {
  const navigate = useNavigate();
  const toast = useToast();
  const { currentProject } = useProject();
  const [plans, setPlans] = useState<TestPlanSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  // FRX·H4: client-side search over title + author username.  300ms
  // debounce so very fast typing doesn't thrash the filter loop.
  const [searchText, setSearchText] = useState('');
  // v2.43.0 — UX review #7: subscribe to the global `/` shortcut so the
  // documented "press / to focus search" behavior actually fires.
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  useSearchFocus(searchInputRef);
  const debouncedSearchText = useDebouncedValue(searchText, 300);

  const [lastFetched, setLastFetched] = useState<Date | null>(null);
  const loadPlans = useCallback(() => {
    setLoading(true);
    setError(null);
    getTestPlans({ status: statusFilter || undefined })
      .then((list) => {
        setPlans(list);
        setLastFetched(new Date());
      })
      .catch((err) => setError(formatApiError(err, 'Failed to load test plans.')))
      .finally(() => setLoading(false));
  }, [statusFilter]);

  // FRX·H4: client-side filter over title + author username.  Kept
  // local for now; server-side search is out of scope.
  const filteredPlans = useMemo(() => {
    const q = debouncedSearchText.trim().toLowerCase();
    if (!q) return plans;
    return plans.filter((p) => {
      const title = (p.title || '').toLowerCase();
      const agent = (p.agent_name || '').toLowerCase();
      const author = (p.created_by_username || '').toLowerCase();
      const fullName = (p.created_by_full_name || '').toLowerCase();
      return title.includes(q) || agent.includes(q) || author.includes(q) || fullName.includes(q);
    });
  }, [plans, debouncedSearchText]);

  // Re-fetch whenever the active project changes — without this, a
  // project switch via the topbar leaves the previous project's plan
  // list visible until something else (status filter, page mount)
  // forces a refresh.  Also re-fetches on mount, which covers the
  // navigate-back-from-detail case the user reported during 4.1.0
  // regression: after generating a plan, navigating to detail, and
  // returning to /test-plans the new plan was missing because the
  // component was already mounted with stale `plans`.
  useEffect(() => {
    loadPlans();
  }, [loadPlans, currentProject?.id]);

  const toggleSelect = (id: number) => {
    setSelectedIds((curr) => {
      if (curr.includes(id)) {
        // Unchecking — always allowed.
        return curr.filter((x) => x !== id);
      }
      if (curr.length >= 2) {
        // FBK·M4: previously we silently dropped the oldest selection
        // to make room for the new one.  That's a hidden mutation; the
        // user clicks a 3rd checkbox and one of the existing two just
        // disappears without explanation.  Warn instead and require
        // the user to deliberately uncheck one first.
        toast.warning('Compare supports two plans — uncheck one to swap.', {
          id: 'compare-limit',
        });
        return curr;
      }
      return [...curr, id];
    });
  };

  const compareEnabled = selectedIds.length === 2;
  const onCompare = () => {
    if (!compareEnabled) return;
    navigate(`/test-plans/compare?a=${selectedIds[0]}&b=${selectedIds[1]}`);
  };

  // 5.313.0 — the one entry point for an agent-drafted plan: the operator's
  // agent session, handed the task. (The "Generate with AI" dialog minted a
  // plan-only key; it went with plan approval.)
  const draftWithAgent = (
    <AgentTaskButton
      variant="default"
      label="Draft with your agent"
      instruction={agentInstruction.draftPlan()}
    />
  );

  return (
    <div className="p-md md:p-lg">
      {/* v5.294.0 — the page's actions top-right like every other page; the
          search and status filter on the shared filter row below. They were
          one wrapping toolbar that put the buttons under the title. */}
      <div className="mb-md flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Test Plans</h1>
          <p className="mt-xxs max-w-4xl text-metadata text-muted-foreground">
            What you or your agent intend to test and what came of it — one entry per host,
            with the commands to run and the results recorded against them.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-xs" data-testid="page-actions">
          <LastUpdated
            compact
            lastFetched={lastFetched}
            onRefresh={loadPlans}
            isLoading={loading}
            label="test plans"
          />
          <Button
            variant="outline"
            disabled={!compareEnabled}
            onClick={onCompare}
            size="sm"
          >
            <ArrowLeftRight className="size-4" aria-hidden />
            {compareEnabled ? 'Compare selected (2)' : `Compare (${selectedIds.length}/2)`}
          </Button>
          {draftWithAgent}
        </div>
      </div>

      <ListFilterBar
        summary={loading ? undefined : `${filteredPlans.length.toLocaleString()} ${filteredPlans.length === 1 ? 'plan' : 'plans'}`}
      >
        {/* FRX·H4: client-side search over title + author; "/" focuses it. */}
        <ListFilterSearch
          inputRef={searchInputRef}
          value={searchText}
          onChange={setSearchText}
          placeholder="Search title or author"
          label="Search test plans"
        />
        <Select value={statusFilter || 'all'} onValueChange={(v) => setStatusFilter(v === 'all' ? '' : v)}>
          <SelectTrigger className={`${FILTER_TRIGGER_CLASS} w-40`} aria-label="Filter test plans by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="draft">Draft</SelectItem>
            <SelectItem value="in_progress">In Progress</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="archived">Archived</SelectItem>
          </SelectContent>
        </Select>
      </ListFilterBar>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        // FBK·H3: previously a bare Loader2 inside a centred flex div,
        // which collapsed the table region and made the page feel
        // empty on every project switch.  ListPageSkeleton matches
        // the eventual shape so the layout stays stable.
        <ListPageSkeleton actionCount={4} tableProps={{ rows: 6, columns: 6 }} />
      ) : filteredPlans.length === 0 ? (
        // v5.288.0 — on the page, not in a card (§7).
        <div className="border-t border-border py-xl text-center" data-testid="plans-empty">
          <div>
            <Bot className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
            {plans.length === 0 && !statusFilter ? (
              // Genuinely empty: no plans for this project AND no
              // server-side status filter narrowing them away.
              <>
                <p className="text-metadata text-muted-foreground">
                  No test plans in{' '}
                  {currentProject?.name ? <strong>{currentProject.name}</strong> : 'this project'} yet.
                </p>
                <p className="mt-xxs text-caption text-muted-foreground">
                  Test plans are scoped to a project. If you wrote one under a different project,
                  switch projects from the selector to find it. Your agent can draft one, or start
                  one from a host selection on the Hosts page.
                </p>
                <div className="mt-sm flex justify-center">{draftWithAgent}</div>
              </>
            ) : (
              // Plans exist but the active status filter and/or search
              // text excluded them — don't claim the project is empty.
              <>
                <p className="text-metadata text-muted-foreground">
                  No test plans match the current filters.
                </p>
                <Button
                  variant="outline"
                  onClick={() => { setSearchText(''); setStatusFilter(''); }}
                  className="mt-sm"
                >
                  Clear filters
                </Button>
              </>
            )}
          </div>
        </div>
      ) : (
        <>
          {/* Desktop-only product: the table is the sole renderer. v5.288.0 —
              on the page, not in a bordered card (§7). v5.294.0 — the column
              budget fits the content width (a 960px minimum scrolled 34px
              sideways at a 1246px viewport); Title takes what is left. */}
                <Table data-testid="plans-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10"><span className="sr-only">Select</span></TableHead>
                      <TableHead>Title</TableHead>
                      <TableHead className="w-28">Status</TableHead>
                      <TableHead className="w-40">Author</TableHead>
                      <TableHead className="w-20 text-center">Entries</TableHead>
                      <TableHead className="w-36">Progress</TableHead>
                      <TableHead className="w-28">Created</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredPlans.map((plan) => {
                      const strippedDesc = plan.description
                        ? stripAttribution(plan.description)
                        : '';
                      return (
                          <NavigableTableRow key={plan.id} selected={selectedIds.includes(plan.id)}>
                            <TableCell className="w-10 pr-0">
                              <Checkbox
                                checked={selectedIds.includes(plan.id)}
                                onCheckedChange={() => toggleSelect(plan.id)}
                                aria-label={`Select plan ${plan.id} for comparison`}
                              />
                            </TableCell>
                            {/* Title links to detail — flat row matching the
                                execution workflow list (no inline
                                expand). */}
                            <NavigableTableCell
                              to={`/test-plans/${plan.id}`}
                              ariaLabel={`Open plan #${plan.id}: ${plan.title}`}
                            >
                              {/* Audit RSP·H6 — wrap in min-w-0
                                  block so `truncate` actually clips
                                  with long titles inside table-cell. */}
                              {/* v5.288.0 — the title wraps to two lines
                                  (full text in the tooltip) instead of
                                  cutting off after a few words. */}
                              <div className="min-w-0 max-w-full" title={plan.title}>
                                <p className="line-clamp-2 break-words">
                                  {plan.title}
                                  {/* Version lived only in the removed
                                      mobile card; a plan's version matters
                                      when several revisions exist, so it
                                      moves here rather than being dropped.
                                      Matches ExecutionsList's `#id v{n}`. */}
                                  <span className="ml-xxs shrink-0 text-caption font-normal text-muted-foreground">
                                    v{plan.version}
                                  </span>
                                </p>
                                {strippedDesc && (
                                  <p className="truncate text-caption text-muted-foreground">
                                    {strippedDesc}
                                  </p>
                                )}
                              </div>
                            </NavigableTableCell>
                            <TableCell>
                              <Badge variant={planStatusTone(plan.status)} className="whitespace-nowrap">
                                {formatStatusLabel(plan.status)}
                              </Badge>
                            </TableCell>
                            <TableCell>
                              {/* v5.288.0 — the person first, by full name
                                  (username in the tooltip); the agent that
                                  drafted it under them. Both wrap. */}
                              <p
                                className="line-clamp-2 break-words"
                                title={plan.created_by_username ?? undefined}
                              >
                                {authorName(plan) ?? plan.agent_name ?? '-'}
                              </p>
                              {plan.agent_name && authorName(plan) && (
                                <p
                                  className="line-clamp-2 break-words text-caption text-muted-foreground"
                                  title={plan.agent_name}
                                >
                                  agent: {plan.agent_name}
                                </p>
                              )}
                            </TableCell>
                            <TableCell className="text-center">{plan.entry_count}</TableCell>
                            <TableCell>
                              <PlanProgress plan={plan} />
                            </TableCell>
                            <TableCell className="truncate">
                              <TimeAgo value={plan.created_at} absoluteAfterDays={7} />
                            </TableCell>
                          </NavigableTableRow>
                      );
                    })}
                  </TableBody>
                </Table>
        </>
      )}
    </div>
  );
};

export default TestPlans;
