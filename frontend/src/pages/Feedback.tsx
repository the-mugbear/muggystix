import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  RefreshCw,
  Check,
  X as DismissIcon,
  ChevronDown,
  ChevronUp,
  Star,
  Loader2,
} from 'lucide-react';
import {
  listAgentFeedback,
  getAgentFeedbackStats,
  updateAgentFeedback,
  AgentFeedbackEntry,
  AgentFeedbackListParams,
  FeedbackStats,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Textarea } from '../components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';
import { cn } from '../utils/cn';

const STATUS_OPTIONS = ['new', 'reviewed', 'actioned', 'dismissed'] as const;
const SOURCE_OPTIONS = [
  { value: '', label: 'All sources' },
  { value: 'plan_generation', label: 'Plan Generation' },
  { value: 'reconnaissance', label: 'Reconnaissance' },
  { value: 'in_session_execution', label: 'In-Session Execution' },
  { value: 'exported_execution', label: 'Exported Execution' },
];

const STATUS_VARIANT: Record<string, 'default' | 'success' | 'warning' | 'muted'> = {
  new: 'default',
  reviewed: 'warning',
  actioned: 'success',
  dismissed: 'muted',
};

const StarRating: React.FC<{ value: number | null | undefined }> = ({ value }) => {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const v = Math.round(value);
  return (
    <span className="inline-flex" aria-label={`Rating ${v} out of 5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          className={cn(
            'size-3.5',
            i <= v ? 'fill-warning text-warning' : 'text-muted-foreground/40',
          )}
          aria-hidden
        />
      ))}
    </span>
  );
};

const Feedback: React.FC = () => {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<AgentFeedbackEntry[]>([]);
  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const [statusFilter, setStatusFilter] = useState<string>('');
  const [sourceFilter, setSourceFilter] = useState<string>('');
  const [minRating, setMinRating] = useState<string>('');
  const [hasToolSuggestions, setHasToolSuggestions] = useState(false);
  const [hasApiCritiques, setHasApiCritiques] = useState(false);
  const [search, setSearch] = useState('');
  const initialTestPlanFilter = (() => {
    const raw = searchParams.get('test_plan_id');
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) && n > 0 ? n : null;
  })();
  const [testPlanFilter, setTestPlanFilter] = useState<number | null>(initialTestPlanFilter);

  const [notesOpen, setNotesOpen] = useState(false);
  const [notesEntry, setNotesEntry] = useState<AgentFeedbackEntry | null>(null);
  const [notesText, setNotesText] = useState('');
  const [notesSaving, setNotesSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: AgentFeedbackListParams = { limit: 200 };
      if (statusFilter) params.status = statusFilter;
      if (sourceFilter) params.source = sourceFilter;
      if (minRating !== '') params.min_rating = Number(minRating);
      if (hasToolSuggestions) params.has_tool_suggestions = true;
      if (hasApiCritiques) params.has_api_critiques = true;
      if (search.trim()) params.search = search.trim();
      if (testPlanFilter != null) params.test_plan_id = testPlanFilter;
      const [list, s] = await Promise.all([listAgentFeedback(params), getAgentFeedbackStats()]);
      setRows(list);
      setStats(s);
    } catch (err: unknown) {
      const msg = formatApiError(err, 'Failed to load feedback.');
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, sourceFilter, minRating, hasToolSuggestions, hasApiCritiques, search, testPlanFilter, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleRow = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleStatusChange = async (entry: AgentFeedbackEntry, status: string) => {
    try {
      const updated = await updateAgentFeedback(entry.id, { status });
      setRows((prev) => prev.map((r) => (r.id === entry.id ? updated : r)));
      toast.success(`Marked as ${status}.`);
      getAgentFeedbackStats().then(setStats).catch(() => undefined);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update feedback.'));
    }
  };

  const openNotes = (entry: AgentFeedbackEntry) => {
    setNotesEntry(entry);
    setNotesText(entry.reviewer_notes || '');
    setNotesOpen(true);
  };

  const saveNotes = async () => {
    if (!notesEntry) return;
    setNotesSaving(true);
    try {
      const updated = await updateAgentFeedback(notesEntry.id, { reviewer_notes: notesText });
      setRows((prev) => prev.map((r) => (r.id === notesEntry.id ? updated : r)));
      toast.success('Reviewer notes saved.');
      setNotesOpen(false);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to save notes.'));
    } finally {
      setNotesSaving(false);
    }
  };

  const newCount = stats?.by_status?.new ?? 0;
  const topTools = stats?.top_tool_suggestions ?? [];

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-col gap-xs sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-page-title">Agent Feedback</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Structured feedback from agents after each prompt workflow — use this to prioritize API
            improvements, new tool additions, and prompt refinements.
          </p>
        </div>
        <Button variant="outline" onClick={load} disabled={loading}>
          <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden /> Refresh
        </Button>
      </div>

      {/* KPI cards */}
      <div className="mb-md grid grid-cols-2 gap-sm md:grid-cols-4">
        <KpiCard label="Total" value={stats?.total ?? '—'} />
        <KpiCard label="New" value={newCount} accent={newCount > 0} />
        <KpiCard
          label="Avg Rating"
          value={stats?.avg_rating != null ? stats.avg_rating.toFixed(2) : '—'}
        />
        <KpiCard
          label="Top Suggested Tool"
          value={
            topTools[0] ? (
              <span className="flex min-w-0 items-baseline gap-xs">
                <span className="min-w-0 truncate">{topTools[0].name}</span>
                <span className="shrink-0 text-caption text-muted-foreground">
                  ×{topTools[0].count}
                </span>
              </span>
            ) : (
              '—'
            )
          }
        />
      </div>

      {topTools.length > 1 && (
        <Card className="mb-md">
          <CardContent className="p-md">
            <p className="mb-xs text-caption font-semibold text-muted-foreground">
              Top tool suggestions (aggregate)
            </p>
            <div className="flex flex-wrap gap-xxs">
              {topTools.map((t) => (
                <Badge key={t.name} variant="outline">
                  {t.name} ×{t.count}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Filters */}
      <Card className="mb-md">
        <CardContent className="p-md">
          <div className="grid grid-cols-1 gap-md md:grid-cols-12">
            <div className="md:col-span-3">
              <Label htmlFor="fb-status">Status</Label>
              <Select
                value={statusFilter || 'all'}
                onValueChange={(v) => setStatusFilter(v === 'all' ? '' : v)}
              >
                <SelectTrigger id="fb-status">
                  <SelectValue placeholder="All statuses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  {STATUS_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-3">
              <Label htmlFor="fb-source">Source</Label>
              <Select
                value={sourceFilter || 'all'}
                onValueChange={(v) => setSourceFilter(v === 'all' ? '' : v)}
              >
                <SelectTrigger id="fb-source">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SOURCE_OPTIONS.map((o) => (
                    <SelectItem key={o.value || 'all'} value={o.value || 'all'}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-2">
              <Label htmlFor="fb-rating">Min rating</Label>
              <Select
                value={minRating || 'any'}
                onValueChange={(v) => setMinRating(v === 'any' ? '' : v)}
              >
                <SelectTrigger id="fb-rating">
                  <SelectValue placeholder="Any" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="any">Any</SelectItem>
                  {[1, 2, 3, 4, 5].map((r) => (
                    <SelectItem key={r} value={String(r)}>
                      {r}+
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-4">
              <Label htmlFor="fb-search">Search friction notes</Label>
              <Input
                id="fb-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') load();
                }}
              />
            </div>
            <div className="md:col-span-12">
              <div className="flex flex-wrap gap-xxs">
                <button
                  type="button"
                  onClick={() => setHasToolSuggestions((v) => !v)}
                  aria-pressed={hasToolSuggestions}
                  className={cn(
                    'rounded-chip border px-sm py-xxs text-micro font-semibold uppercase transition-colors',
                    hasToolSuggestions
                      ? 'border-transparent bg-primary text-primary-foreground'
                      : 'border-border text-foreground hover:bg-accent',
                  )}
                >
                  Has tool suggestions
                </button>
                <button
                  type="button"
                  onClick={() => setHasApiCritiques((v) => !v)}
                  aria-pressed={hasApiCritiques}
                  className={cn(
                    'rounded-chip border px-sm py-xxs text-micro font-semibold uppercase transition-colors',
                    hasApiCritiques
                      ? 'border-transparent bg-primary text-primary-foreground'
                      : 'border-border text-foreground hover:bg-accent',
                  )}
                >
                  Has API critiques
                </button>
                {testPlanFilter != null && (
                  <Badge variant="default" className="cursor-default">
                    Test plan #{testPlanFilter}
                    <button
                      type="button"
                      onClick={() => {
                        setTestPlanFilter(null);
                        const next = new URLSearchParams(searchParams);
                        next.delete('test_plan_id');
                        setSearchParams(next, { replace: true });
                      }}
                      aria-label="Clear test plan filter"
                      className="ml-xxs"
                    >
                      <DismissIcon className="size-3" aria-hidden />
                    </button>
                  </Badge>
                )}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <div className="flex justify-center py-xxl">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
        </div>
      ) : rows.length === 0 ? (
        <Card>
          <CardContent className="py-xxl text-center text-metadata text-muted-foreground">
            No feedback entries match the current filters.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table className="min-w-[900px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10" />
                    <TableHead className="w-24">Rating</TableHead>
                    <TableHead className="w-40">Source</TableHead>
                    {/* w-24 (96px) wasn't enough for `dismissed` (9 chars
                        + chip padding) — the badge bled into the Version
                        column.  w-32 (128px) comfortably fits every
                        STATUS_VARIANT key. */}
                    <TableHead className="w-32">Status</TableHead>
                    <TableHead className="w-24">Version</TableHead>
                    <TableHead>Friction notes</TableHead>
                    <TableHead className="w-32">Created</TableHead>
                    <TableHead className="w-48">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => {
                    const isOpen = expanded.has(r.id);
                    return (
                      <React.Fragment key={r.id}>
                        <TableRow>
                          <TableCell>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => toggleRow(r.id)}
                              aria-expanded={isOpen}
                              aria-controls={`fb-details-${r.id}`}
                              aria-label={isOpen ? 'Collapse details' : 'Expand details'}
                            >
                              {isOpen ? <ChevronUp className="size-4" aria-hidden /> : <ChevronDown className="size-4" aria-hidden />}
                            </Button>
                          </TableCell>
                          <TableCell>
                            <StarRating value={r.overall_rating} />
                          </TableCell>
                          <TableCell className="truncate">{r.source}</TableCell>
                          <TableCell>
                            <Badge
                              variant={STATUS_VARIANT[r.status] || 'muted'}
                              className="whitespace-nowrap"
                            >
                              {r.status}
                            </Badge>
                          </TableCell>
                          <TableCell className="truncate text-caption text-muted-foreground">
                            {r.prompt_version || '—'}
                          </TableCell>
                          <TableCell>
                            <p className="line-clamp-2 text-metadata text-foreground">
                              {r.friction_notes || <em className="text-muted-foreground">(no notes)</em>}
                            </p>
                          </TableCell>
                          <TableCell className="text-caption text-muted-foreground">
                            {new Date(r.created_at).toLocaleString()}
                          </TableCell>
                          <TableCell>
                            <div className="flex gap-xxs">
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => handleStatusChange(r, 'reviewed')}
                                    aria-label="Mark as reviewed"
                                    className={r.status === 'reviewed' ? 'text-warning' : ''}
                                  >
                                    <Check className="size-4" aria-hidden />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Reviewed</TooltipContent>
                              </Tooltip>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => handleStatusChange(r, 'actioned')}
                                    aria-label="Mark as actioned"
                                    className={r.status === 'actioned' ? 'text-success' : ''}
                                  >
                                    <Check className="size-4" aria-hidden strokeWidth={3} />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Actioned</TooltipContent>
                              </Tooltip>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => handleStatusChange(r, 'dismissed')}
                                    aria-label="Dismiss"
                                  >
                                    <DismissIcon className="size-4" aria-hidden />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Dismiss</TooltipContent>
                              </Tooltip>
                              <Button variant="outline" size="sm" onClick={() => openNotes(r)}>
                                Notes
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                        {isOpen && (
                          <TableRow id={`fb-details-${r.id}`}>
                            <TableCell colSpan={8} className="bg-accent/40 p-md">
                              <div className="grid grid-cols-1 gap-md md:grid-cols-2">
                                <div>
                                  <p className="mb-xs text-caption font-semibold text-foreground">
                                    API critiques
                                  </p>
                                  {r.api_critiques && r.api_critiques.length > 0 ? (
                                    <ul className="list-inside list-disc text-metadata">
                                      {r.api_critiques.map((c, i) => (
                                        <li key={i}>
                                          <strong>{(c as any).endpoint || '(endpoint?)'}:</strong>{' '}
                                          {(c as any).issue}
                                          {(c as any).suggestion && (
                                            <em> — {(c as any).suggestion}</em>
                                          )}
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="text-caption text-muted-foreground">None.</p>
                                  )}
                                </div>
                                <div>
                                  <p className="mb-xs text-caption font-semibold text-foreground">
                                    Tool suggestions
                                  </p>
                                  {r.tool_suggestions && r.tool_suggestions.length > 0 ? (
                                    <div className="flex flex-wrap gap-xxs">
                                      {r.tool_suggestions.map((t, i) => (
                                        <Tooltip key={i}>
                                          <TooltipTrigger asChild>
                                            <Badge variant="outline">
                                              {(t as any).name}
                                              {(t as any).category ? ` · ${(t as any).category}` : ''}
                                            </Badge>
                                          </TooltipTrigger>
                                          {(t as any).rationale && (
                                            <TooltipContent>{(t as any).rationale}</TooltipContent>
                                          )}
                                        </Tooltip>
                                      ))}
                                    </div>
                                  ) : (
                                    <p className="text-caption text-muted-foreground">None.</p>
                                  )}
                                </div>
                                <div className="md:col-span-2">
                                  {/* v2.43.3 — surface the full friction_notes
                                      prose.  The table column line-clamps to
                                      2 lines and the search box filters by
                                      this field; without this block the
                                      reviewer can find a row but can't read
                                      what the agent actually wrote. */}
                                  <p className="mb-xs text-caption font-semibold text-foreground">
                                    Friction notes (full)
                                  </p>
                                  {r.friction_notes ? (
                                    <p className="whitespace-pre-wrap text-metadata text-foreground">
                                      {r.friction_notes}
                                    </p>
                                  ) : (
                                    <p className="text-caption text-muted-foreground">None.</p>
                                  )}
                                </div>
                                <div className="md:col-span-2">
                                  <p className="mb-xs text-caption font-semibold text-foreground">
                                    Agent metrics
                                  </p>
                                  {r.agent_metrics && Object.keys(r.agent_metrics).length > 0 ? (
                                    <pre className="max-h-40 overflow-auto rounded-control bg-card p-xs font-mono text-caption text-foreground">
                                      {JSON.stringify(r.agent_metrics, null, 2)}
                                    </pre>
                                  ) : (
                                    <p className="text-caption text-muted-foreground">None.</p>
                                  )}
                                </div>
                                {r.reviewer_notes && (
                                  <div className="md:col-span-2">
                                    <p className="mb-xs text-caption font-semibold text-foreground">
                                      Reviewer notes
                                    </p>
                                    <p className="text-metadata text-foreground">
                                      {r.reviewer_notes}
                                    </p>
                                  </div>
                                )}
                              </div>
                            </TableCell>
                          </TableRow>
                        )}
                      </React.Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Notes dialog */}
      <Dialog open={notesOpen} onOpenChange={(next) => !next && !notesSaving && setNotesOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reviewer Notes</DialogTitle>
          </DialogHeader>
          <Textarea
            value={notesText}
            onChange={(e) => setNotesText(e.target.value)}
            rows={6}
            placeholder="Triage notes, links to issues, next steps…"
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setNotesOpen(false)} disabled={notesSaving}>
              Cancel
            </Button>
            <Button onClick={saveNotes} disabled={notesSaving}>
              {notesSaving ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Saving…</> : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

const KpiCard: React.FC<{ label: string; value: React.ReactNode; accent?: boolean }> = ({
  label,
  value,
  accent,
}) => (
  // v2.43.2 — `min-w-0 overflow-hidden` on the value container so a long
  // string (e.g. agent-submitted "Top Suggested Tool" with a verbose
  // qualifier) can't push the card wider than its grid cell.  Pre-fix
  // the v2.43.0 overflow-x-hidden removal exposed the overflow at the
  // shell level — the card was wider than its 1/4 grid column.
  <Card className={cn('min-w-0 overflow-hidden', accent && 'border-l-4 border-l-primary')}>
    <CardContent className="p-md">
      <p className="text-micro font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <div className="min-w-0 truncate text-section-title font-semibold text-foreground">
        {value}
      </div>
    </CardContent>
  </Card>
);

export default Feedback;
