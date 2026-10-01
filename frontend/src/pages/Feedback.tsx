/**
 * Agent Feedback — the instance-wide triage queue of what agents reported
 * (Administration hub, global admins; v5.310.0 redesign).
 *
 * A reviewer's job here is to check an agent's claim against what the agent
 * actually did, so every row names its project and the session it came from,
 * with that session's API-call count and a link to the page that lists the
 * calls.  The layout is the house one (§7): a lead sentence, a strip of quiet
 * measures whose counts open their rows, then one section with the shared
 * filter row (ListFilterBar).  Filters live in the URL so a measure is a link.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Loader2, MessageSquareText, Star } from 'lucide-react';

import {
  listAgentFeedback,
  getAgentFeedbackStats,
  updateAgentFeedback,
  AgentFeedbackEntry,
  AgentFeedbackListParams,
  FeedbackStats,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useProject } from '../contexts/ProjectContext';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { ListPage, useListQuery } from '../hooks/useListQuery';
import LastUpdated from '../components/LastUpdated';
import TimeAgo from '../components/TimeAgo';
import PostureLead from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import { ListFilterBar, ListFilterSearch, FILTER_TRIGGER_CLASS } from '../components/ListFilterBar';
import { formatApiError } from '../utils/apiErrors';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Textarea } from '../components/ui/textarea';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { cn } from '../utils/cn';
import { agentSessionPath } from '../utils/agentRuns';

/** The session's page (with its API calls), or null when there is no session
 *  or it has no page (a recon / plan / execution row from before the unified
 *  session). 5.328.0 — the page is addressed by the session id; it used to be
 *  a second id (`session_page_id`). */
const sessionPageOf = (r: AgentFeedbackEntry): string | null =>
  r.agent_session_id != null && r.session_has_page !== false
    ? agentSessionPath(r.agent_session_id)
    : null;

const PAGE = 50;

/** The first page carries the measures' stats beside the rows. */
type FeedbackPage = ListPage<AgentFeedbackEntry> & { stats?: FeedbackStats };

const STATUSES = [
  { value: 'new', label: 'New' },
  { value: 'reviewed', label: 'Reviewed' },
  { value: 'actioned', label: 'Actioned' },
  { value: 'dismissed', label: 'Dismissed' },
] as const;

// Must stay in step with AgentFeedbackSource in app/db/models_agent.py. Since
// unified sessions (v2.337.0) the agent picks this value itself, so it is a
// hint about what the agent was doing, not a record of the session's phases.
const SOURCE_LABELS: Record<string, string> = {
  testing: 'Testing',
  reconnaissance: 'Reconnaissance',
  assist: 'Assist',
  // Labels on rows filed before 5.320.0, when tests lived on plans.
  plan_generation: 'Plan generation',
  in_session_execution: 'Execution',
  exported_execution: 'Exported execution',
};

type Content = '' | 'critiques' | 'suggestions';

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

const StarRating: React.FC<{ value: number | null | undefined }> = ({ value }) => {
  if (value == null) return <span className="text-muted-foreground">Unrated</span>;
  const v = Math.round(value);
  return (
    <span className="inline-flex" role="img" aria-label={`Rated ${v} of 5`} title={`${v} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          className={cn('size-3.5', i <= v ? 'fill-warning text-warning' : 'text-muted-foreground/40')}
          aria-hidden
        />
      ))}
    </span>
  );
};

/** Which client wrote it: what the agent said about itself, else the MCP
 *  client its session connected with, else the key's agent record (which
 *  sessions reuse — a seeded "planner" says nothing about who tested). */
const clientOf = (r: AgentFeedbackEntry): string | null => {
  const said = r.agent_metrics && typeof r.agent_metrics.agent_name === 'string' ? r.agent_metrics.agent_name : null;
  return said || r.client_name || r.agent_name || null;
};

const Feedback: React.FC = () => {
  const toast = useToast();
  const navigate = useNavigate();
  const { projects, currentProject, selectProject } = useProject();
  const [params, setParams] = useSearchParams();

  // Filters live in the URL, so the measures above the list are links to it.
  const status = params.get('status') ?? '';
  const source = params.get('source') ?? '';
  const content = (params.get('content') ?? '') as Content;
  const minRating = params.get('rating') ?? '';
  const projectFilter = params.get('project') ?? '';
  const [search, setSearch] = useState(params.get('q') ?? '');
  const debouncedSearch = useDebouncedValue(search.trim(), 300);

  const setParam = useCallback((key: string, value: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value); else next.delete(key);
      return next;
    }, { replace: true });
  }, [setParams]);

  useEffect(() => { setParam('q', debouncedSearch); }, [debouncedSearch, setParam]);

  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const [notesEntry, setNotesEntry] = useState<AgentFeedbackEntry | null>(null);
  const [notesText, setNotesText] = useState('');
  const [notesSaving, setNotesSaving] = useState(false);

  const query = useMemo<AgentFeedbackListParams>(() => {
    const q: AgentFeedbackListParams = { limit: PAGE };
    if (status) q.status = status;
    if (source) q.source = source;
    if (minRating) q.min_rating = Number(minRating);
    if (content === 'critiques') q.has_api_critiques = true;
    if (content === 'suggestions') q.has_tool_suggestions = true;
    if (projectFilter) q.project_id = Number(projectFilter);
    if (debouncedSearch) q.search = debouncedSearch;
    return q;
  }, [status, source, minRating, content, projectFilter, debouncedSearch]);

  // One request lane for the filters, the refresh and "Show more" (R33): a
  // slow response for an earlier filter or search term never replaces the
  // rows of the current one.
  const list = useListQuery<AgentFeedbackEntry, FeedbackPage>(
    async ({ offset, limit }) => {
      const rowsQuery = listAgentFeedback({ ...query, limit, ...(offset > 0 ? { skip: offset } : {}) });
      if (offset > 0) return rowsQuery;
      const [page, s] = await Promise.all([rowsQuery, getAgentFeedbackStats()]);
      return { ...page, stats: s };
    },
    [query],
    { pageSize: PAGE, errorMessage: 'Could not load agent feedback.' },
  );
  const rows = list.rows ?? [];
  const { total, loading, loadingMore, error, loadedAt: lastFetched } = list;
  const load = list.reload;
  const hasMore = rows.length < total;
  // The measures keep their last value while a new filter loads.
  const latestStats = list.response?.stats;
  useEffect(() => { if (latestStats) setStats(latestStats); }, [latestStats]);

  const loadMore = async () => {
    try {
      await list.loadMore();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Could not load more feedback.'));
    }
  };

  const replaceRow = (updated: AgentFeedbackEntry) =>
    list.setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));

  const changeStatus = async (entry: AgentFeedbackEntry, next: string) => {
    if (next === entry.status) return;
    setUpdatingId(entry.id);
    try {
      replaceRow(await updateAgentFeedback(entry.id, { status: next }));
      getAgentFeedbackStats().then(setStats).catch(() => undefined);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Could not change the status.'));
    } finally {
      setUpdatingId(null);
    }
  };

  const saveNotes = async () => {
    if (!notesEntry) return;
    setNotesSaving(true);
    try {
      replaceRow(await updateAgentFeedback(notesEntry.id, { reviewer_notes: notesText }));
      toast.success('Reviewer note saved.');
      setNotesEntry(null);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Could not save the note.'));
    } finally {
      setNotesSaving(false);
    }
  };

  /** Open the page that lists this session's API calls — in its own project. */
  const openSession = (r: AgentFeedbackEntry) => {
    const target = sessionPageOf(r);
    if (!target) return;
    if (r.project_id != null && r.project_id !== currentProject?.id) {
      const proj = projects.find((p) => p.id === r.project_id);
      if (!proj) {
        toast.error(`You are not a member of ${r.project_name ?? `project #${r.project_id}`}.`);
        return;
      }
      selectProject(proj);
    }
    navigate(target);
  };

  const toggle = (id: number) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const newCount = stats?.by_status?.new ?? 0;
  const critiqueCount = stats?.with_api_critiques ?? 0;
  const suggestionCount = stats?.with_tool_suggestions ?? 0;
  const topTool = stats?.top_tool_suggestions?.[0];
  const filtered = Boolean(status || source || content || minRating || projectFilter || debouncedSearch);
  const projectOptions = useMemo(
    () => [...projects].sort((a, b) => a.name.localeCompare(b.name)),
    [projects],
  );

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-start gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Agent feedback</h1>
          <p className="text-metadata text-muted-foreground">
            What agents reported at the end of their sessions, from every project. Check a claim against the
            session's own API calls before acting on it.
          </p>
        </div>
        <LastUpdated compact lastFetched={lastFetched} onRefresh={() => void load()} isLoading={loading} label="agent feedback" />
      </div>

      {stats && (
        <PostureLead tone={newCount > 0 ? 'info' : 'neutral'} className="mb-md">
          {stats.total === 0
            ? 'No agent has filed feedback yet.'
            : newCount > 0
              ? `${plural(newCount, 'report')} of ${stats.total.toLocaleString()} ${newCount === 1 ? 'is' : 'are'} waiting for triage.`
              : `All ${plural(stats.total, 'report')} have been triaged.`}
        </PostureLead>
      )}

      {stats && stats.total > 0 && (
        <div className="mb-lg grid gap-y-md divide-border sm:grid-cols-3 lg:divide-x">
          <PostureMeasure
            label="Waiting for triage"
            value={newCount.toLocaleString()}
            to="/feedback?status=new"
            toLabel="Show the feedback waiting for triage"
            info="Feedback no one has marked reviewed, actioned or dismissed yet."
          >
            <p className="break-words">
              {(stats.by_status.reviewed ?? 0).toLocaleString()} reviewed · {(stats.by_status.actioned ?? 0).toLocaleString()} actioned · {(stats.by_status.dismissed ?? 0).toLocaleString()} dismissed
            </p>
          </PostureMeasure>
          <PostureMeasure
            label="Name an API problem"
            value={critiqueCount.toLocaleString()}
            to="/feedback?content=critiques"
            toLabel="Show the feedback that names an API problem"
            info="Feedback carrying at least one API critique: an endpoint or tool, what went wrong, and a suggestion. The actionable part of most reports."
          >
            <p className="break-words">each names an endpoint, the issue and a suggestion</p>
          </PostureMeasure>
          <PostureMeasure
            label="Suggest a tool"
            value={suggestionCount.toLocaleString()}
            to="/feedback?content=suggestions"
            toLabel="Show the feedback that suggests a tool"
            info="Feedback asking for a tool BlueStick's tool catalogue does not list. The most requested one is named below."
          >
            <p className="break-words">
              {topTool ? `most asked for: ${topTool.name} (${topTool.count.toLocaleString()})` : 'no tool requested yet'}
            </p>
          </PostureMeasure>
        </div>
      )}

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <PostureSection
        title={<>{filtered ? 'Matching feedback' : 'All feedback'}<SectionCount>{total.toLocaleString()}</SectionCount></>}
        description="Newest first. Open a row for the full report; the session link lists every API call the agent made."
      >
        <ListFilterBar summary={`${rows.length.toLocaleString()} of ${total.toLocaleString()} shown`}>
          <ListFilterSearch value={search} onChange={setSearch} placeholder="Search the notes…" label="Search feedback notes" />
          <Select value={status || 'all'} onValueChange={(v) => setParam('status', v === 'all' ? '' : v)}>
            <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-40')} aria-label="Status"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {STATUSES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={content || 'all'} onValueChange={(v) => setParam('content', v === 'all' ? '' : v)}>
            <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-48')} aria-label="Content"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any content</SelectItem>
              <SelectItem value="critiques">Names an API problem</SelectItem>
              <SelectItem value="suggestions">Suggests a tool</SelectItem>
            </SelectContent>
          </Select>
          <Select value={projectFilter || 'all'} onValueChange={(v) => setParam('project', v === 'all' ? '' : v)}>
            <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-48')} aria-label="Project"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All projects</SelectItem>
              {projectOptions.map((p) => <SelectItem key={p.id} value={String(p.id)}>{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={source || 'all'} onValueChange={(v) => setParam('source', v === 'all' ? '' : v)}>
            <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-44')} aria-label="What the agent was doing"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any activity</SelectItem>
              {Object.entries(SOURCE_LABELS).map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={minRating || 'any'} onValueChange={(v) => setParam('rating', v === 'any' ? '' : v)}>
            <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-36')} aria-label="Minimum rating"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="any">Any rating</SelectItem>
              {[5, 4, 3, 2, 1].map((r) => <SelectItem key={r} value={String(r)}>{r === 5 ? '5 only' : `${r} or more`}</SelectItem>)}
            </SelectContent>
          </Select>
        </ListFilterBar>

        {loading && rows.length === 0 ? (
          <div className="flex justify-center py-xxl">
            <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
          </div>
        ) : rows.length === 0 ? (
          <p className="py-xl text-center text-metadata text-muted-foreground">
            {filtered ? 'No feedback matches these filters.' : 'No agent has filed feedback yet.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table className="min-w-[980px]" style={{ tableLayout: 'fixed' }}>
              <colgroup>
                <col style={{ width: 36 }} />
                <col style={{ width: 96 }} />
                <col style={{ width: 170 }} />
                <col style={{ width: 120 }} />
                <col />
                <col style={{ width: 96 }} />
                <col style={{ width: 140 }} />
                <col style={{ width: 96 }} />
              </colgroup>
              <TableHeader>
                <TableRow>
                  <TableHead><span className="sr-only">Details</span></TableHead>
                  <TableHead>Received</TableHead>
                  <TableHead>Project · session</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>Report</TableHead>
                  <TableHead>Rating</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead><span className="sr-only">Reviewer note</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => {
                  const open = expanded.has(r.id);
                  const critiques = r.api_critiques?.length ?? 0;
                  const suggestions = r.tool_suggestions?.length ?? 0;
                  const client = clientOf(r);
                  const canOpen = sessionPageOf(r) != null;
                  return (
                    <React.Fragment key={r.id}>
                      <TableRow data-testid={`feedback-row-${r.id}`}>
                        <TableCell className="align-top">
                          <Button
                            variant="ghost" size="icon" className="size-7"
                            onClick={() => toggle(r.id)}
                            aria-expanded={open}
                            aria-controls={`fb-details-${r.id}`}
                            aria-label={open ? `Hide report #${r.id}` : `Show report #${r.id}`}
                          >
                            {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
                          </Button>
                        </TableCell>
                        <TableCell className="align-top text-caption text-muted-foreground">
                          <TimeAgo value={r.created_at} />
                        </TableCell>
                        <TableCell className="align-top">
                          <div className="min-w-0 truncate text-metadata" title={r.project_name ?? undefined}>
                            {r.project_name ?? (r.project_id != null ? `Project #${r.project_id}` : 'No project')}
                          </div>
                          {canOpen ? (
                            <button
                              type="button"
                              onClick={() => openSession(r)}
                              className="max-w-full truncate text-caption text-primary underline-offset-4 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              title="Open the session and the API calls it made"
                            >
                              {r.agent_session_id != null ? `Session #${r.agent_session_id}` : 'Open the run'}
                              {r.session_api_calls != null && ` · ${plural(r.session_api_calls, 'call')}`}
                            </button>
                          ) : (
                            <span className="text-caption text-muted-foreground">
                              {r.agent_session_id != null ? `Session #${r.agent_session_id}` : 'No session recorded'}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="align-top">
                          <div className="truncate text-metadata" title={client ?? undefined}>{client ?? '—'}</div>
                          <div className="truncate text-caption text-muted-foreground">
                            {r.prompt_version ? `prompt ${r.prompt_version}` : 'no prompt version'}
                          </div>
                        </TableCell>
                        <TableCell className="align-top">
                          <p className="line-clamp-2 break-words text-metadata text-foreground">
                            {r.friction_notes || <span className="text-muted-foreground">No notes — see the critiques.</span>}
                          </p>
                          {(critiques > 0 || suggestions > 0) && (
                            <p className="mt-xxs text-caption text-muted-foreground">
                              {critiques > 0 && <span className="text-foreground">{plural(critiques, 'API critique')}</span>}
                              {critiques > 0 && suggestions > 0 && ' · '}
                              {suggestions > 0 && plural(suggestions, 'tool suggestion')}
                            </p>
                          )}
                        </TableCell>
                        <TableCell className="align-top text-caption"><StarRating value={r.overall_rating} /></TableCell>
                        <TableCell className="align-top">
                          <Select value={r.status} onValueChange={(v) => changeStatus(r, v)} disabled={updatingId === r.id}>
                            <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-full')} aria-label={`Status of report #${r.id}`}>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {STATUSES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                            </SelectContent>
                          </Select>
                        </TableCell>
                        <TableCell className="align-top">
                          <Button
                            variant="ghost" size="sm" className="h-8 px-xs"
                            onClick={() => { setNotesEntry(r); setNotesText(r.reviewer_notes || ''); }}
                            aria-label={r.reviewer_notes ? `Edit the reviewer note on report #${r.id}` : `Add a reviewer note to report #${r.id}`}
                          >
                            <MessageSquareText className={cn('size-4', r.reviewer_notes ? 'text-primary' : 'text-muted-foreground')} aria-hidden />
                            {r.reviewer_notes ? 'Note' : 'Add'}
                          </Button>
                        </TableCell>
                      </TableRow>
                      {open && (
                        <TableRow id={`fb-details-${r.id}`}>
                          <TableCell colSpan={8} className="bg-accent/30 p-md">
                            <FeedbackDetails r={r} />
                          </TableCell>
                        </TableRow>
                      )}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        {hasMore && (
          <div className="mt-sm flex justify-center">
            <Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>
              {loadingMore && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Show {Math.min(PAGE, total - rows.length).toLocaleString()} more
            </Button>
          </div>
        )}
      </PostureSection>

      <Dialog open={notesEntry != null} onOpenChange={(next) => { if (!next && !notesSaving) setNotesEntry(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reviewer note{notesEntry ? ` — report #${notesEntry.id}` : ''}</DialogTitle>
            <DialogDescription>For other reviewers: what was checked, links to the fix or issue, why it was dismissed.</DialogDescription>
          </DialogHeader>
          <Textarea
            value={notesText}
            onChange={(e) => setNotesText(e.target.value)}
            rows={6}
            aria-label="Reviewer note"
            placeholder="Verified against session #57's calls; fixed in 2.428.1…"
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setNotesEntry(null)} disabled={notesSaving}>Cancel</Button>
            <Button onClick={saveNotes} disabled={notesSaving}>
              {notesSaving && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Save note
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

/** The whole report: critiques first (the actionable part), then the rest. */
const FeedbackDetails: React.FC<{ r: AgentFeedbackEntry }> = ({ r }) => {
  const critiques = r.api_critiques ?? [];
  const suggestions = r.tool_suggestions ?? [];
  const metrics = r.agent_metrics && Object.keys(r.agent_metrics).length > 0 ? r.agent_metrics : null;
  const str = (v: unknown) => (typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v));
  return (
    <div className="grid min-w-0 grid-cols-1 gap-md lg:grid-cols-2">
      <div className="min-w-0 lg:col-span-2">
        <h3 className="mb-xs text-caption font-semibold text-foreground">API critiques</h3>
        {critiques.length > 0 ? (
          <ul className="space-y-xs">
            {critiques.map((c, i) => (
              <li key={i} className="min-w-0 break-words text-metadata">
                <code className="rounded bg-card px-xxs font-mono text-caption">{str(c.endpoint) || 'no endpoint named'}</code>{' '}
                {str(c.issue)}
                {c.suggestion != null && c.suggestion !== '' && (
                  <span className="block text-muted-foreground">Suggests: {str(c.suggestion)}</span>
                )}
              </li>
            ))}
          </ul>
        ) : <p className="text-caption text-muted-foreground">None.</p>}
      </div>
      <div className="min-w-0">
        <h3 className="mb-xs text-caption font-semibold text-foreground">Notes</h3>
        {r.friction_notes
          ? <p className="whitespace-pre-wrap break-words text-metadata">{r.friction_notes}</p>
          : <p className="text-caption text-muted-foreground">None.</p>}
      </div>
      <div className="min-w-0">
        <h3 className="mb-xs text-caption font-semibold text-foreground">Tool suggestions</h3>
        {suggestions.length > 0 ? (
          <ul className="space-y-xxs">
            {suggestions.map((t, i) => (
              <li key={i} className="min-w-0 break-words text-metadata">
                <Badge variant="outline" className="mr-xs">{str(t.name) || 'unnamed'}</Badge>
                {t.category != null && <span className="text-muted-foreground">{str(t.category)} </span>}
                {t.rationale != null && <span>— {str(t.rationale)}</span>}
              </li>
            ))}
          </ul>
        ) : <p className="text-caption text-muted-foreground">None.</p>}
      </div>
      <dl className="grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] gap-x-md gap-y-xxs text-caption lg:col-span-2">
        <dt className="text-muted-foreground">What it was doing</dt>
        <dd className="min-w-0 truncate">{SOURCE_LABELS[r.source] ?? r.source} <span className="text-muted-foreground">(the agent's own label)</span></dd>
        {r.reviewed_at && (<><dt className="text-muted-foreground">Last triaged</dt><dd><TimeAgo value={r.reviewed_at} /></dd></>)}
        {r.reviewer_notes && (<><dt className="text-muted-foreground">Reviewer note</dt><dd className="min-w-0 whitespace-pre-wrap break-words">{r.reviewer_notes}</dd></>)}
      </dl>
      {metrics && (
        <div className="min-w-0 lg:col-span-2">
          <h3 className="mb-xs text-caption font-semibold text-foreground">What the agent said about itself</h3>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-control bg-card p-xs font-mono text-caption">
            {JSON.stringify(metrics, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
};

export default Feedback;
