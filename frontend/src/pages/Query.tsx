/**
 * Inventory → Query (5.405.0): SPL-style queries over hosts, ports and scanner
 * observations, and saved / shared queries — the owner, 2026-10-16: "teams are
 * used to Splunk".
 *
 *   observations has:exploit | stats dc(host) as hosts by title | sort -hosts | head 20
 *
 * The committed query is the address (`?q=`): a link, Back and a group's
 * drill-down all land on it, and the answer is `['runQuery', projectId, q]`.
 * The editor's text is the only state — a draft, keyed by the committed query
 * so a new address refills it.  Every stats / top row opens exactly its rows
 * (the server's `drilldowns`).
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { BookOpen, Loader2, Play, Save, Share2 } from 'lucide-react';

import {
  createSavedQuery,
  deleteSavedQuery,
  getQuerySchema,
  listSavedQueries,
  runQuery,
  updateSavedQuery,
} from '../services/api';
import type { QueryAnswer, SavedQuery } from '../services/api';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';
import { InfoTip } from '../components/ui/info-tip';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Textarea } from '../components/ui/textarea';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { formatTimestamp } from '../utils/relativeTime';

export const QUERY_PATH = '/query';
export const queryPath = (q: string) => `${QUERY_PATH}?q=${encodeURIComponent(q)}`;

const EXAMPLE = 'observations | where severity IN ("critical", "high") | stats dc(host) as hosts by title | sort -hosts | head 20';

/** Where the server says the query went wrong (`position`), if it said. */
const errorPosition = (error: unknown): number | null => {
  const position = (error as { response?: { data?: { position?: unknown } } })?.response?.data?.position;
  return typeof position === 'number' ? position : null;
};

const cellText = (value: unknown, type: string): string => {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
  if (type === 'datetime' && typeof value === 'string') return formatTimestamp(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
};

// ---------------------------------------------------------------------------
// The editor: a draft of the query, committed by Run (or Ctrl / ⌘ + Enter).
// ---------------------------------------------------------------------------

const Editor: React.FC<{
  initial: string;
  running: boolean;
  onRun: (text: string) => void;
  onSave: (text: string) => void;
}> = ({ initial, running, onRun, onSave }) => {
  const [draft, setDraft] = useState(initial);
  const text = draft.trim();
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <Label htmlFor="query-text" className="sr-only">Query</Label>
      <Textarea
        id="query-text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && text) {
            e.preventDefault();
            onRun(text);
          }
        }}
        rows={3}
        maxLength={4000}
        spellCheck={false}
        placeholder={EXAMPLE}
        className="font-mono text-metadata"
      />
      <div className="flex flex-wrap items-center gap-xs">
        <Button size="sm" onClick={() => onRun(text)} disabled={!text || running}>
          {running ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Play className="size-4" aria-hidden />}
          Run
        </Button>
        <Button size="sm" variant="outline" onClick={() => onSave(text)} disabled={!text}>
          <Save className="size-4" aria-hidden /> Save…
        </Button>
        <span className="text-caption text-muted-foreground">Ctrl + Enter runs it.</span>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// The answer
// ---------------------------------------------------------------------------

const QueryError: React.FC<{ q: string; error: unknown }> = ({ q, error }) => {
  const position = errorPosition(error);
  const message = queryErrorText(error, 'Could not run the query.');
  return (
    <div role="alert" className="flex min-w-0 flex-col gap-xxs" data-testid="query-error">
      <p className="break-words text-metadata text-destructive">
        {message}{position !== null ? ` (at character ${position + 1})` : ''}
      </p>
      {position !== null && (
        <pre className="overflow-x-auto whitespace-pre rounded-control bg-muted/40 p-xs font-mono text-caption text-muted-foreground">
          {q.slice(0, position)}
          <mark className="rounded-sm bg-destructive/20 text-destructive">{q.slice(position, position + 1) || ' '}</mark>
          {q.slice(position + 1)}
        </pre>
      )}
    </div>
  );
};

const Answer: React.FC<{ answer: QueryAnswer }> = ({ answer }) => {
  const shown = answer.rows.length;
  const noun = answer.kind === 'stats' ? (answer.total === 1 ? 'group' : 'groups') : (answer.total === 1 ? 'row' : 'rows');
  const drill = answer.kind === 'stats' && answer.drilldowns;
  if (answer.total === 0) {
    return <p className="text-metadata text-muted-foreground">Nothing matches this query.</p>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <p className="text-caption text-muted-foreground" data-testid="query-count">
        {shown < answer.total
          ? `The first ${shown.toLocaleString()} of ${answer.total.toLocaleString()} ${noun}`
          : `${answer.total.toLocaleString()} ${noun}`}
        {answer.capped ? ` — a query shows at most ${answer.limit.toLocaleString()}` : ''}
        {' · '}{answer.dataset}
      </p>
      <div className="overflow-x-auto">
        <Table className="min-w-[40rem]" style={{ tableLayout: 'fixed' }} data-testid="query-answer">
          <TableHeader>
            <TableRow>
              {answer.columns.map((c) => (
                <TableHead key={c.name} className="truncate" title={c.name}>{c.name}</TableHead>
              ))}
              {drill && <TableHead className="w-28"><span className="sr-only">Rows</span></TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {answer.rows.map((row, i) => (
              // Rows have no id; the answer is replaced whole, never edited in place.
              <TableRow key={i}>
                {answer.columns.map((c, j) => {
                  const text = cellText(row[j], c.type);
                  return (
                    <TableCell
                      key={c.name}
                      className={c.type === 'int' || c.type === 'float' ? 'truncate tabular-nums' : 'truncate'}
                      title={text}
                    >
                      {text}
                    </TableCell>
                  );
                })}
                {drill && (
                  <TableCell>
                    {answer.drilldowns?.[i] && (
                      <Link to={queryPath(answer.drilldowns[i] as string)} className="text-caption text-primary hover:underline">
                        Open rows
                      </Link>
                    )}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Saved queries
// ---------------------------------------------------------------------------

interface SaveForm { id: number | null; name: string; description: string; query: string; shared: boolean }

const SaveDialog: React.FC<{ form: SaveForm | null; onClose: () => void }> = ({ form, onClose }) => (
  <Dialog open={form !== null} onOpenChange={(v) => { if (!v) onClose(); }}>
    <DialogContent>
      {form && <SaveBody key={form.id ?? 'new'} form={form} onClose={onClose} />}
    </DialogContent>
  </Dialog>
);

const SaveBody: React.FC<{ form: SaveForm; onClose: () => void }> = ({ form, onClose }) => {
  const projectId = useProjectId();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState(form.name);
  const [description, setDescription] = useState(form.description);
  const [text, setText] = useState(form.query);
  const [shared, setShared] = useState(form.shared);
  const save = useMutation({
    mutationFn: () => (form.id === null
      ? createSavedQuery(projectId, { name: name.trim(), query: text.trim(), description: description.trim() || undefined, shared })
      : updateSavedQuery(projectId, form.id, { name: name.trim(), query: text.trim(), description: description.trim() || null, shared })),
    onSuccess: (row) => {
      toast.success(`Saved “${row.name}”${row.shared ? ', shared with the project' : ''}`);
      onClose();
      return queryClient.invalidateQueries({ queryKey: ['listSavedQueries'] });
    },
  });
  return (
    <>
      <DialogHeader>
        <DialogTitle>{form.id === null ? 'Save query' : 'Edit saved query'}</DialogTitle>
        <DialogDescription>
          A saved query is yours; shared, every member of the project sees it and can run it. Only you or a
          project admin can change it.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-sm">
        <div>
          <Label htmlFor="saved-query-name">Name</Label>
          <Input id="saved-query-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="saved-query-description">Description (optional)</Label>
          <Input id="saved-query-description" value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="saved-query-text">Query</Label>
          <Textarea id="saved-query-text" rows={3} value={text} maxLength={4000} spellCheck={false}
            className="font-mono text-metadata" onChange={(e) => setText(e.target.value)} />
        </div>
        <div className="flex items-center gap-xs">
          <Checkbox id="saved-query-shared" checked={shared} onCheckedChange={(v) => setShared(v === true)} />
          <Label htmlFor="saved-query-shared">Share with the project</Label>
        </div>
        {save.isError && (
          <p role="alert" className="break-words text-caption text-destructive">
            {formatApiError(save.error, 'Could not save the query')}
          </p>
        )}
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={save.isPending}>Cancel</Button>
        <Button onClick={() => save.mutate()} disabled={save.isPending || !name.trim() || !text.trim()}>
          {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
          Save
        </Button>
      </DialogFooter>
    </>
  );
};

const SavedQueries: React.FC<{ onRun: (q: string) => void; onEdit: (row: SavedQuery) => void }> = ({ onRun, onEdit }) => {
  const projectId = useProjectId();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [confirmEl, confirm] = useConfirm();
  const saved = useQuery({
    queryKey: ['listSavedQueries', projectId],
    queryFn: ({ signal }) => listSavedQueries(projectId, signal),
  });
  const remove = useMutation({
    mutationFn: (row: SavedQuery) => deleteSavedQuery(projectId, row.id),
    onSuccess: (_, row) => {
      toast.success(`Deleted “${row.name}”`);
      return queryClient.invalidateQueries({ queryKey: ['listSavedQueries'] });
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not delete the query')),
  });
  const rows = saved.data;
  return (
    <PostureSection
      title={<>Saved queries {rows && <SectionCount>{rows.length}</SectionCount>}</>}
      description="Yours, and the ones teammates shared with the project. A name runs it."
    >
      {confirmEl}
      {saved.isError ? (
        <p role="alert" className="text-metadata text-destructive">
          {queryErrorText(saved.error, 'Could not load the saved queries.')}{' '}
          <Button variant="ghost" size="sm" onClick={() => void saved.refetch()}>Retry</Button>
        </p>
      ) : !rows ? (
        <p role="status" className="text-metadata text-muted-foreground">Loading saved queries…</p>
      ) : rows.length === 0 ? (
        <p className="text-metadata text-muted-foreground">None yet — write a query, then Save.</p>
      ) : (
        <div className="overflow-x-auto">
          <Table className="min-w-[40rem]" style={{ tableLayout: 'fixed' }} data-testid="saved-queries">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[28%]">Name</TableHead>
                <TableHead>Query</TableHead>
                <TableHead className="w-40">By</TableHead>
                <TableHead className="w-36"><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="min-w-0">
                    <button type="button" className="block max-w-full truncate text-left font-medium text-primary hover:underline"
                      title={row.description ?? row.name} onClick={() => onRun(row.query)}>
                      {row.name}
                    </button>
                    {row.description && <p className="truncate text-caption text-muted-foreground" title={row.description}>{row.description}</p>}
                  </TableCell>
                  <TableCell className="truncate font-mono text-caption text-muted-foreground" title={row.query}>{row.query}</TableCell>
                  <TableCell className="text-caption">
                    <span className="flex min-w-0 items-center gap-xxs">
                      {row.shared && <Badge variant="outline" className="shrink-0"><Share2 className="size-3" aria-hidden /> Shared</Badge>}
                      <span className="truncate text-muted-foreground">{row.mine ? 'you' : row.created_by ?? '—'}</span>
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    {row.can_modify && (
                      <span className="inline-flex gap-xxs">
                        <Button variant="ghost" size="sm" onClick={() => onEdit(row)}>Edit</Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={remove.isPending}
                          onClick={() => void confirm({ title: 'Delete saved query', resourceName: row.name, severity: 'danger', confirmLabel: 'Delete' })
                            .then((ok) => { if (ok) remove.mutate(row); })}
                        >
                          Delete
                        </Button>
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </PostureSection>
  );
};

// ---------------------------------------------------------------------------
// The reference: datasets, columns, commands — from the server's schema
// ---------------------------------------------------------------------------

const Reference: React.FC<{ onRun: (q: string) => void }> = ({ onRun }) => {
  const projectId = useProjectId();
  const [open, setOpen] = useState(false);
  const schema = useQuery({
    queryKey: ['getQuerySchema', projectId],
    queryFn: ({ signal }) => getQuerySchema(projectId, signal),
    enabled: open,
  });
  return (
    <PostureSection
      title="Reference"
      description="The datasets, their columns and the commands."
      actions={(
        <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <BookOpen className="size-4" aria-hidden /> {open ? 'Hide' : 'Show'}
        </Button>
      )}
    >
      {!open ? null : schema.isError ? (
        <p role="alert" className="text-metadata text-destructive">{queryErrorText(schema.error, 'Could not load the reference.')}</p>
      ) : !schema.data ? (
        <p role="status" className="text-metadata text-muted-foreground">Loading…</p>
      ) : (
        <div className="flex min-w-0 flex-col gap-md" data-testid="query-reference">
          <ul className="flex flex-col gap-xxs text-caption">
            {schema.data.commands.map((c) => (
              <li key={c.name} className="min-w-0 break-words">
                <code className="font-mono text-foreground">{c.syntax}</code>
                <span className="text-muted-foreground"> — {c.description}</span>
              </li>
            ))}
          </ul>
          {schema.data.datasets.map((ds) => (
            <div key={ds.name} className="min-w-0">
              <p className="text-metadata font-semibold text-foreground">
                <code className="font-mono">{ds.name}</code> <span className="font-normal text-muted-foreground">— {ds.description}</span>
              </p>
              <p className="break-words text-caption text-muted-foreground">
                {ds.columns.map((c) => `${c.name} (${c.type})`).join(' · ')}
              </p>
              <ul className="mt-xxs flex flex-col gap-xxs">
                {ds.examples.map((ex) => (
                  <li key={ex} className="min-w-0">
                    <button type="button" className="max-w-full truncate text-left font-mono text-caption text-primary hover:underline"
                      title="Run this example" onClick={() => onRun(ex)}>
                      {ex}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <p className="text-caption text-muted-foreground">
            At most {schema.data.caps.rows.toLocaleString()} rows or {schema.data.caps.groups.toLocaleString()} groups
            come back; the count always says how many there were.
          </p>
        </div>
      )}
    </PostureSection>
  );
};

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

const Query: React.FC = () => {
  const projectId = useProjectId();
  const [params, setParams] = useSearchParams();
  const q = (params.get('q') ?? '').trim();
  const run = (text: string) => setParams(text ? { q: text } : {});
  const answer = useQuery({
    queryKey: ['runQuery', projectId, q],
    queryFn: ({ signal }) => runQuery(projectId, q, signal),
    enabled: q !== '',
  });
  const [saving, setSaving] = useState<SaveForm | null>(null);

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      <div className="min-w-0">
        <h1 className="text-page-title font-semibold">Query</h1>
        <p className="flex flex-wrap items-center gap-xxs text-metadata text-muted-foreground">
          Ask hosts, ports and scanner observations Splunk-style: a dataset, search terms, then
          <code className="font-mono">| where</code>, <code className="font-mono">| stats … by</code>,
          <code className="font-mono">| sort</code>, <code className="font-mono">| head</code>.
          <InfoTip
            label="How a query reads"
            text={(
              <>
                Search terms right after the dataset are the Hosts query language (port:, has:, cve:, site:…) and
                always describe the row&apos;s host — on ports, port:445 means the host has 445 open. | where compares
                the dataset&apos;s own columns, and = is exact: where port=445 is the row&apos;s port. Every row of a
                stats or top answer opens exactly its rows.
              </>
            )}
          />
        </p>
      </div>

      <section className="flex min-w-0 flex-col gap-sm">
        <Editor
          key={q}
          initial={q || ''}
          running={answer.isFetching}
          onRun={run}
          onSave={(text) => setSaving({ id: null, name: '', description: '', query: text, shared: false })}
        />
        {q === '' ? (
          <p className="text-metadata text-muted-foreground">
            Write a query, or{' '}
            <button type="button" className="text-primary hover:underline" onClick={() => run(EXAMPLE)}>
              run an example
            </button>
            .
          </p>
        ) : answer.isError ? (
          <QueryError q={q} error={answer.error} />
        ) : answer.data ? (
          <Answer answer={answer.data} />
        ) : (
          <p role="status" className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Running…
          </p>
        )}
      </section>

      <SavedQueries
        onRun={run}
        onEdit={(row) => setSaving({ id: row.id, name: row.name, description: row.description ?? '', query: row.query, shared: row.shared })}
      />
      <Reference onRun={run} />
      <SaveDialog form={saving} onClose={() => setSaving(null)} />
    </div>
  );
};

export default Query;
