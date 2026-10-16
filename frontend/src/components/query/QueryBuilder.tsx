/**
 * The Query page's builder (5.406.0): pickers that WRITE the query — a
 * dataset, Hosts-language terms, column conditions, a grouping and its
 * measure, an order and a size — shown as the text they make, which Run
 * hands to the page exactly as if it had been typed (`utils/queryBuilder`).
 * The language stays the one thing the server reads; the builder is a way to
 * learn it, not a second request shape.
 */
import React, { useMemo, useState } from 'react';
import { Plus, X } from 'lucide-react';

import type { QuerySchema } from '../../services/api';
import { BUILDER_OPS, buildQuery, type BuilderCondition, type BuilderSpec } from '../../utils/queryBuilder';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

const NONE = '__none';
const MEASURES = ['count', 'dc', 'min', 'max', 'avg', 'sum'] as const;
const MAX_CONDITIONS = 8;
const MAX_GROUPS = 3;

const blank = (dataset: string): BuilderSpec => ({
  dataset, terms: '', conditions: [], by: [], measure: { fn: 'count', column: 'host' }, mostFirst: true, head: 20,
});

export const QueryBuilder: React.FC<{ schema: QuerySchema; onRun: (text: string) => void }> = ({ schema, onRun }) => {
  const [spec, setSpec] = useState<BuilderSpec>(() => blank(schema.datasets[0]?.name ?? 'hosts'));
  const dataset = schema.datasets.find((d) => d.name === spec.dataset) ?? schema.datasets[0];
  const columns = useMemo(() => dataset?.columns ?? [], [dataset]);
  const types = useMemo(() => Object.fromEntries(columns.map((c) => [c.name, c.type])), [columns]);
  const text = buildQuery(spec, types);
  const set = (patch: Partial<BuilderSpec>) => setSpec((s) => ({ ...s, ...patch }));
  const setCondition = (i: number, patch: Partial<BuilderCondition>) =>
    set({ conditions: spec.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const numeric = columns.filter((c) => c.type === 'int' || c.type === 'float');
  const measureColumns = spec.measure.fn === 'avg' || spec.measure.fn === 'sum'
    ? numeric
    : columns.filter((c) => spec.measure.fn === 'dc' || (c.type !== 'bool' && c.type !== 'enum'));

  return (
    <div className="flex min-w-0 flex-col gap-sm border-l-2 border-border pl-sm" data-testid="query-builder">
      <div className="flex flex-wrap items-end gap-sm">
        <div>
          <Label htmlFor="builder-dataset">Dataset</Label>
          <Select value={spec.dataset} onValueChange={(v) => setSpec(blank(v))}>
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
        {spec.by.length > 0 && (
          <>
            <div>
              <Label htmlFor="builder-measure">Measure</Label>
              <Select value={spec.measure.fn} onValueChange={(v) => set({ measure: { fn: v as BuilderSpec['measure']['fn'], column: (v === 'avg' || v === 'sum') ? (numeric[0]?.name ?? '') : 'host' } })}>
                <SelectTrigger id="builder-measure" className="w-28"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {MEASURES.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            {spec.measure.fn !== 'count' && (
              <div>
                <Label htmlFor="builder-measure-column">of</Label>
                <Select value={spec.measure.column || NONE} onValueChange={(v) => set({ measure: { ...spec.measure, column: v === NONE ? '' : v } })}>
                  <SelectTrigger id="builder-measure-column" className="w-40"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {measureColumns.map((x) => <SelectItem key={x.name} value={x.name}>{x.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="flex items-center gap-xs pb-xs">
              <Checkbox id="builder-most-first" checked={spec.mostFirst} onCheckedChange={(v) => set({ mostFirst: v === true })} />
              <Label htmlFor="builder-most-first">Most first</Label>
            </div>
          </>
        )}
        <div>
          <Label htmlFor="builder-head">At most</Label>
          <Input id="builder-head" type="number" min={0} className="w-24" value={spec.head || ''}
            onChange={(e) => set({ head: Math.max(0, Math.floor(Number(e.target.value) || 0)) })} placeholder="all" />
        </div>
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-sm">
        <code className="min-w-0 flex-1 break-all rounded-control bg-muted/40 px-xs py-xxs font-mono text-caption text-foreground"
          data-testid="builder-text">
          {text}
        </code>
        <Button size="sm" onClick={() => onRun(text)}
          disabled={spec.by.length > 0 && spec.measure.fn !== 'count' && !spec.measure.column}>
          Run this query
        </Button>
      </div>
    </div>
  );
};

export default QueryBuilder;
