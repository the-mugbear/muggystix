/**
 * The Query page's builder (5.406.0): pickers that WRITE the query — a
 * dataset, Hosts-language terms, column conditions, a grouping and its
 * measure (or `top`), an order and a size (`utils/queryBuilder`).  The
 * language stays the one thing the server reads; the builder is a way to
 * learn it, not a second request shape.
 *
 * 5.418.0 (review 2026-10-16, Q9 — "the builder and the editor are two
 * minds"): it opens on the EDITOR's query, as the server reads it
 * (`GET /query/parse`; the grammar is not copied here), every change is
 * written into the editor, and the editor's Run is the one Run.  A query the
 * builder cannot show whole says why and is left alone until the reader
 * starts over.
 */
import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, X } from 'lucide-react';

import { parseQueryForBuilder } from '../../services/api';
import type { QuerySchema } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import {
  BUILDER_OPS, MEASURES, blankSpec, buildQuery, specFromShape,
  type BuilderCondition, type BuilderSpec, type MeasureFn,
} from '../../utils/queryBuilder';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

const NONE = '__none';
const MAX_CONDITIONS = 8;
const MAX_GROUPS = 3;

export const QueryBuilder: React.FC<{
  schema: QuerySchema;
  /** The editor's text when the builder opened — what it starts from. */
  seed: string;
  /** Writes the builder's query into the editor. */
  onChange: (text: string) => void;
}> = ({ schema, seed, onChange }) => {
  const projectId = useProjectId();
  const text = seed.trim();
  const parsed = useQuery({
    queryKey: ['parseQueryForBuilder', projectId, text],
    queryFn: ({ signal }) => parseQueryForBuilder(projectId, text, signal),
    enabled: text !== '',
  });
  // The reader's edits over the server's reading (never copied into state).
  const [edits, setEdits] = useState<BuilderSpec | null>(null);
  const first = schema.datasets[0]?.name ?? 'hosts';
  const seeded = text === '' ? blankSpec(first) : parsed.data?.shape ? specFromShape(parsed.data.shape) : null;
  const spec = edits ?? seeded;

  if (spec === null) {
    return (
      <div className="flex min-w-0 flex-col gap-xs border-l-2 border-border pl-sm" data-testid="query-builder">
        {parsed.isPending ? (
          <p role="status" className="text-caption text-muted-foreground">Reading the query above…</p>
        ) : (
          <p className="break-words text-caption text-muted-foreground" data-testid="builder-cannot">
            {parsed.isError
              ? `The query above does not read: ${queryErrorText(parsed.error, 'it could not be read')}.`
              : parsed.data?.reason}
          </p>
        )}
        {!parsed.isPending && (
          <div>
            <Button variant="outline" size="sm" onClick={() => { const next = blankSpec(first); setEdits(next); onChange(buildQuery(next, typesOf(schema, first))); }}>
              Start a new query in the builder
            </Button>
          </div>
        )}
      </div>
    );
  }
  return <Pickers schema={schema} spec={spec} onSpec={(next) => { setEdits(next); onChange(buildQuery(next, typesOf(schema, next.dataset))); }} />;
};

const typesOf = (schema: QuerySchema, dataset: string): Record<string, string> =>
  Object.fromEntries((schema.datasets.find((d) => d.name === dataset)?.columns ?? []).map((c) => [c.name, c.type]));

const Pickers: React.FC<{ schema: QuerySchema; spec: BuilderSpec; onSpec: (spec: BuilderSpec) => void }> = ({ schema, spec, onSpec }) => {
  const dataset = schema.datasets.find((d) => d.name === spec.dataset) ?? schema.datasets[0];
  const columns = useMemo(() => dataset?.columns ?? [], [dataset]);
  const set = (patch: Partial<BuilderSpec>) => onSpec({ ...spec, ...patch });
  const setCondition = (i: number, patch: Partial<BuilderCondition>) =>
    set({ conditions: spec.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const numeric = columns.filter((c) => c.type === 'int' || c.type === 'float');
  const fn = spec.measure.fn;
  const measureColumns = fn === 'avg' || fn === 'sum'
    ? numeric
    : columns.filter((c) => fn === 'dc' || fn === 'values' || (c.type !== 'bool' && c.type !== 'enum'));
  const grouped = spec.by.length > 0;

  return (
    <div className="flex min-w-0 flex-col gap-sm border-l-2 border-border pl-sm" data-testid="query-builder">
      <p className="text-caption text-muted-foreground">The builder writes the query above; Run runs it.</p>
      <div className="flex flex-wrap items-end gap-sm">
        <div>
          <Label htmlFor="builder-dataset">Dataset</Label>
          <Select value={spec.dataset} onValueChange={(v) => onSpec(blankSpec(v))}>
            <SelectTrigger id="builder-dataset" className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              {schema.datasets.map((d) => <SelectItem key={d.name} value={d.name}>{d.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-0 flex-1">
          <Label htmlFor="builder-terms">Hosts-language terms (optional)</Label>
          <Input id="builder-terms" value={spec.terms} onChange={(e) => set({ terms: e.target.value })}
            placeholder='e.g. has:critical site:"HQ"' className="font-mono" />
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-xs">
        <p className="text-caption font-semibold text-foreground">Where (all of them)</p>
        {spec.conditions.map((c, i) => {
          const col = columns.find((x) => x.name === c.column);
          const valueless = c.op === 'isnull' || c.op === 'isnotnull';
          return (
            // A row is its place in the list (rows are only added at the end or removed).
            <div key={i} className="flex flex-wrap items-center gap-xs" data-testid="builder-condition">
              <Select value={c.column || NONE} onValueChange={(v) => setCondition(i, { column: v === NONE ? '' : v, value: '' })}>
                <SelectTrigger className="w-40" aria-label={`Condition ${i + 1} column`}><SelectValue placeholder="Column" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>Column…</SelectItem>
                  {columns.map((x) => <SelectItem key={x.name} value={x.name}>{x.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <Select value={c.op} onValueChange={(v) => setCondition(i, { op: v })}>
                <SelectTrigger className="w-28" aria-label={`Condition ${i + 1} comparison`}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {BUILDER_OPS.map((op) => <SelectItem key={op} value={op}>{op}</SelectItem>)}
                </SelectContent>
              </Select>
              {!valueless && (col?.values ? (
                <Select value={c.value || NONE} onValueChange={(v) => setCondition(i, { value: v === NONE ? '' : v })}>
                  <SelectTrigger className="w-40" aria-label={`Condition ${i + 1} value`}><SelectValue placeholder="Value" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>Value…</SelectItem>
                    {col.values.map((v) => <SelectItem key={v} value={v}>{v}</SelectItem>)}
                  </SelectContent>
                </Select>
              ) : (
                <Input className="w-48" aria-label={`Condition ${i + 1} value`} value={c.value}
                  onChange={(e) => setCondition(i, { value: e.target.value })}
                  placeholder={col?.type === 'datetime' ? '2026-10-01' : 'Value'} />
              ))}
              <Button variant="ghost" size="icon" aria-label={`Remove condition ${i + 1}`}
                onClick={() => set({ conditions: spec.conditions.filter((_, j) => j !== i) })}>
                <X className="size-4" aria-hidden />
              </Button>
            </div>
          );
        })}
        {spec.conditions.length < MAX_CONDITIONS && (
          <div>
            <Button variant="ghost" size="sm" onClick={() => set({ conditions: [...spec.conditions, { column: '', op: '=', value: '' }] })}>
              <Plus className="size-4" aria-hidden /> Add a condition
            </Button>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-sm">
        {Array.from({ length: Math.min(spec.by.length + 1, MAX_GROUPS) }, (_, i) => (
          <div key={i}>
            <Label htmlFor={`builder-by-${i}`}>{i === 0 ? 'Group by' : 'then by'}</Label>
            <Select value={spec.by[i] ?? NONE} onValueChange={(v) => {
              const by = spec.by.slice(0, i);
              set({ by: v === NONE ? by : [...by, v] });
            }}>
              <SelectTrigger id={`builder-by-${i}`} className="w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>{i === 0 ? 'Nothing (a listing)' : 'Nothing more'}</SelectItem>
                {columns.filter((x) => !spec.by.slice(0, i).includes(x.name)).map((x) => (
                  <SelectItem key={x.name} value={x.name}>{x.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
        {grouped && (
          <>
            <div>
              <Label htmlFor="builder-measure">Measure</Label>
              <Select value={fn} onValueChange={(v) => set({ measure: {
                fn: v as MeasureFn,
                column: (v === 'avg' || v === 'sum') ? (numeric[0]?.name ?? '') : (v === 'count' || v === 'top' ? '' : 'host'),
              } })}>
                <SelectTrigger id="builder-measure" className="w-28"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {MEASURES.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            {fn !== 'count' && fn !== 'top' && (
              <div>
                <Label htmlFor="builder-measure-column">of</Label>
                <Select value={spec.measure.column || NONE} onValueChange={(v) => set({ measure: { ...spec.measure, column: v === NONE ? '' : v, as: null } })}>
                  <SelectTrigger id="builder-measure-column" className="w-40"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {measureColumns.map((x) => <SelectItem key={x.name} value={x.name}>{x.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
            {fn !== 'top' && fn !== 'values' && (
              <div className="flex items-center gap-xs pb-xs">
                <Checkbox id="builder-most-first" checked={spec.mostFirst} onCheckedChange={(v) => set({ mostFirst: v === true })} />
                <Label htmlFor="builder-most-first">Most first</Label>
              </div>
            )}
          </>
        )}
        <div>
          <Label htmlFor="builder-head">{grouped && fn === 'top' ? 'Top' : 'At most'}</Label>
          <Input id="builder-head" type="number" min={0} className="w-24" value={spec.head || ''}
            onChange={(e) => set({ head: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
            placeholder={grouped && fn === 'top' ? '10' : 'all'} />
        </div>
      </div>
    </div>
  );
};

export default QueryBuilder;
