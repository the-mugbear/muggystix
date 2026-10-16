/**
 * The Query page's chart (5.406.0): a stats / top answer with ONE group column
 * as horizontal bars — one number at a time (a second measure is a second
 * choice, never a second axis), in the answer's own order.
 *
 * Drawn with Observable Plot (style guide §35) through `PlotFigure`; one
 * series, so one colour — `--chart-mark`, a step of the theme's info hue
 * validated against each theme's surface (dataviz validator) — and no legend:
 * the heading names the measure.  Thin bars with a 2px gap, rounded at the
 * data end, a recessive grid, a hover tip per bar.  The table view beside it
 * holds every value, and each group's rows open from there.
 */
import React, { useMemo, useState } from 'react';
import * as Plot from '@observablehq/plot';

import type { QueryAnswer } from '../../services/api';
import PlotFigure from '../charts/PlotFigure';
import { useWidth } from '../charts/useWidth';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

/** The most bars drawn; the table has the rest. */
export const CHART_MAX_BARS = 50;
const MARK = 'hsl(var(--chart-mark))';
const LABEL_MAX = 32;

const NUMERIC = new Set(['int', 'float']);

/** The answer's chartable measures — its number columns that are not groups —
 *  or null when it cannot be one chart (not stats, or not ONE group column). */
export const chartMeasures = (answer: QueryAnswer): string[] | null => {
  if (answer.kind !== 'stats' || !answer.by || answer.by.length !== 1 || answer.rows.length === 0) return null;
  const measures = answer.columns.filter((c) => !answer.by!.includes(c.name) && NUMERIC.has(c.type)).map((c) => c.name);
  return measures.length ? measures : null;
};

const shorten = (text: string) => (text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text);

interface Bar { key: string; label: string; value: number }

export const QueryChart: React.FC<{ answer: QueryAnswer; measures: string[] }> = ({ answer, measures }) => {
  const [ref, width] = useWidth();
  const [measure, setMeasure] = useState(measures[0]);
  const active = measures.includes(measure) ? measure : measures[0];
  const groupCol = answer.by![0];
  const gi = answer.columns.findIndex((c) => c.name === groupCol);
  const mi = answer.columns.findIndex((c) => c.name === active);

  const bars = useMemo<Bar[]>(() => answer.rows.slice(0, CHART_MAX_BARS).map((row, i) => {
    const raw = row[gi];
    const label = raw === null || raw === undefined ? '(none)' : String(raw);
    // A key per row: two groups may print the same after shortening.
    return { key: `${i}\u0000${label}`, label, value: Number(row[mi] ?? 0) };
  }), [answer.rows, gi, mi]);

  const options = useMemo((): Plot.PlotOptions => {
    const longest = Math.max(...bars.map((b) => shorten(b.label).length));
    const marginLeft = Math.min(240, 16 + longest * 6.5);
    return {
      width,
      height: bars.length * 22 + 36,
      marginLeft,
      marginRight: 24,
      style: { background: 'transparent', color: 'hsl(var(--muted-foreground))', fontSize: '11px' },
      x: { grid: true, label: active, nice: true, labelAnchor: 'right' },
      y: {
        domain: bars.map((b) => b.key),
        tickFormat: (k: string) => shorten(k.split('\u0000')[1] ?? k),
        label: null,
      },
      marks: [
        Plot.gridX({ stroke: 'currentColor', strokeOpacity: 0.12 }),
        Plot.barX(bars, {
          x: 'value',
          y: 'key',
          fill: MARK,
          insetTop: 1,
          insetBottom: 1,
          rx2: 4,
          // The hover tip: the group whole (its tick may be shortened) and the value.
          tip: { fill: 'hsl(var(--popover))', stroke: 'hsl(var(--border))' },
          title: (b: Bar) => `${b.label}\n${active}: ${b.value.toLocaleString()}`,
        }),
        Plot.ruleX([0], { stroke: 'currentColor', strokeOpacity: 0.35 }),
      ],
    };
  }, [bars, width, active]);

  return (
    <div className="flex min-w-0 flex-col gap-xs" data-testid="query-chart">
      <div className="flex flex-wrap items-center gap-sm">
        <p className="text-caption font-semibold text-foreground">
          {active} by {groupCol}
        </p>
        {measures.length > 1 && (
          <div className="flex items-center gap-xs">
            <Label htmlFor="query-chart-measure" className="text-caption text-muted-foreground">Measure</Label>
            <Select value={active} onValueChange={setMeasure}>
              <SelectTrigger id="query-chart-measure" className="h-7 w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                {measures.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        )}
        {answer.rows.length > CHART_MAX_BARS && (
          <span className="text-caption text-muted-foreground">
            The first {CHART_MAX_BARS} groups; the table shows all {answer.rows.length.toLocaleString()}.
          </span>
        )}
      </div>
      <div ref={ref} className="min-w-0">
        <PlotFigure options={options} label={`Bar chart of ${active} by ${groupCol}`} />
      </div>
    </div>
  );
};

export default QueryChart;
