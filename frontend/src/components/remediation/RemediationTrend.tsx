/**
 * Is the remediation backlog shrinking? (5.341.0)
 *
 * Three single-series charts (small multiples, like `GrowthCharts`): overdue
 * findings on hosts per recorded day, and — per month — how many were reported fixed
 * on time and how many late.  One series each, so the title names it and no
 * legend is needed; a daily count and a monthly count are different scales,
 * so they are never one plot with two axes.
 *
 * The daily count is RECORDED by the worker from the day the installation
 * began tracking; a deadline is derived, so no earlier day can be rebuilt.
 * The section says when its history starts, and a day nobody recorded is
 * absent, not zero.  Every value is also in the table view.
 */
import React, { useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import * as Plot from '@observablehq/plot';

import { getRemediationTrend } from '../../services/api';
import { GLOBAL, queryErrorText } from '../../lib/query';
import { formatDate } from '../../utils/relativeTime';
import PlotFigure from '../charts/PlotFigure';
import { useWidth } from '../oversight/GrowthCharts';

const ACCENT = 'hsl(var(--info))';
const INK = 'hsl(var(--foreground))';
const STYLE = {
  background: 'transparent', color: 'hsl(var(--muted-foreground))', fontSize: '11px', fontFamily: 'inherit',
  overflow: 'visible',
} as const;
const TIP = { fill: 'hsl(var(--popover))', stroke: 'hsl(var(--border))', textPadding: 6 };

/** Whole-number ticks: 0, a round top at or above the data, and its half when whole. */
const countAxis = (values: number[]): { domain: [number, number]; ticks: number[] } => {
  const max = Math.max(1, ...values);
  const p = 10 ** Math.floor(Math.log10(max));
  const f = max / p;
  const top = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  return { domain: [0, top], ticks: top % 2 === 0 ? [0, top / 2, top] : [0, top] };
};

/** A day tick: the date, never an hour.  The days are UTC midnights of plain
 *  dates, so they are printed in UTC and no zone moves the day. */
export const dayTick = (date: Date): string =>
  date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });

/** The x ticks: one per RECORDED day.  When the days outnumber the labels
 *  that fit, every k-th recorded day, counted back from the last so the
 *  newest day always has its label — never a tick between two days. */
export const dayTicks = (days: Date[], width: number): Date[] => {
  const fit = Math.max(2, Math.floor((width - 100) / 64));
  if (days.length <= fit) return days;
  const step = Math.ceil(days.length / fit);
  return days.filter((_, i) => (days.length - 1 - i) % step === 0);
};

const monthLabel = (month: string): string => {
  const [y, m] = month.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' });
};

export interface RemediationTrendProps {
  scope: 'project' | 'all';
  /** With `all`: one project. */
  projectId?: number;
}

export const RemediationTrend: React.FC<RemediationTrendProps> = ({ scope, projectId }) => {
  const [ref, width] = useWidth();
  const [showTable, setShowTable] = useState(false);
  const across = scope === 'all';
  // Re-read after a save by the write itself (`invalidateRemediationReads`).
  // Another project's history replaces the one on screen when it arrives.
  const query = useQuery({
    queryKey: across ? [GLOBAL, 'getRemediationTrend', 'all', projectId] : ['getRemediationTrend', undefined, projectId],
    queryFn: ({ signal }) => getRemediationTrend(across ? 'all' : undefined, projectId, signal),
    placeholderData: keepPreviousData,
  });
  const trend = query.data ?? null;
  const error = queryErrorText(query.error, 'The history could not be loaded.');

  const daily = useMemo(
    () => (trend?.daily ?? []).map((d) => ({ date: new Date(`${d.day}T00:00:00Z`), value: d.overdue, day: d.day })),
    [trend],
  );
  // Twelve consecutive months ending with the server's: the API leaves out a
  // month in which nothing was closed, and a month that is skipped on the
  // axis reads as if June were next to October (seen in the browser).  A
  // month with nothing closed IS a zero — unlike an unrecorded day.
  const months = useMemo(() => {
    if (!trend || trend.closed_by_month.length === 0) return [];
    const known = new Map(trend.closed_by_month.map((m) => [m.month, m]));
    const [year, month] = trend.as_of.split('-').map(Number);
    return Array.from({ length: 12 }, (_, i) => {
      const at = new Date(Date.UTC(year, month - 1 - (11 - i), 1));
      const key = `${at.getUTCFullYear()}-${String(at.getUTCMonth() + 1).padStart(2, '0')}`;
      return { month: key, on_time: 0, late: 0, no_deadline: 0, ...known.get(key), label: monthLabel(key) };
    });
  }, [trend]);

  const dailyOptions = useMemo((): Plot.PlotOptions | null => {
    if (daily.length < 2) return null;
    const y = countAxis(daily.map((d) => d.value));
    return {
      width, height: 132, marginLeft: 44, marginRight: 56, marginTop: 8, marginBottom: 36, style: STYLE,
      // The data is one count per day: the axis names days.  Left to itself
      // a time scale over a few days labels hours ("12 AM", "3 AM"…).
      x: {
        type: 'utc', ticks: dayTicks(daily.map((d) => d.date), width), tickFormat: dayTick,
        tickSize: 0, tickPadding: 6, label: null,
      },
      y: { domain: y.domain, ticks: y.ticks, tickSize: 0, tickFormat: (v: number) => v.toLocaleString(), label: null },
      marks: [
        Plot.gridY(y.ticks, { stroke: 'currentColor', strokeOpacity: 0.12 }),
        Plot.ruleY([0], { stroke: 'currentColor', strokeOpacity: 0.35 }),
        Plot.areaY(daily, { x: 'date', y: 'value', fill: ACCENT, fillOpacity: 0.1 }),
        Plot.lineY(daily, { x: 'date', y: 'value', stroke: ACCENT, strokeWidth: 2 }),
        Plot.dot(daily, Plot.selectLast({ x: 'date', y: 'value', r: 4, fill: ACCENT, stroke: 'hsl(var(--background))', strokeWidth: 2 })),
        Plot.text(daily, Plot.selectLast({
          x: 'date', y: 'value', text: (d: { value: number }) => d.value.toLocaleString(),
          dx: 10, textAnchor: 'start', fill: INK, fontWeight: 600,
        })),
        Plot.ruleX(daily, Plot.pointerX({ x: 'date', stroke: INK, strokeOpacity: 0.5 })),
        Plot.tip(daily, Plot.pointerX({
          x: 'date', y: 'value', ...TIP,
          title: (d: { day: string; value: number }) => `${formatDate(d.day)}\n${d.value.toLocaleString()} overdue`,
        })),
      ],
    };
  }, [daily, width]);

  const monthOptions = useMemo(() => (key: 'on_time' | 'late', word: string, axis: boolean): Plot.PlotOptions => {
    const y = countAxis(months.map((m) => m[key]));
    return {
      width, height: 84 + (axis ? 28 : 4), marginLeft: 44, marginRight: 56, marginTop: 14, marginBottom: axis ? 28 : 4,
      style: STYLE,
      x: { type: 'band', domain: months.map((m) => m.label), padding: 0.35, axis: axis ? 'bottom' : null, tickSize: 0, tickPadding: 6, label: null },
      y: { domain: y.domain, ticks: y.ticks, tickSize: 0, tickFormat: (v: number) => v.toLocaleString(), label: null },
      marks: [
        Plot.gridY(y.ticks, { stroke: 'currentColor', strokeOpacity: 0.12 }),
        Plot.ruleY([0], { stroke: 'currentColor', strokeOpacity: 0.35 }),
        Plot.barY(months.filter((m) => m[key] > 0), { x: 'label', y: key, fill: ACCENT, rx: 3, insetLeft: 1, insetRight: 1 }),
        // At most twelve columns: each carries its value.
        Plot.text(months.filter((m) => m[key] > 0), {
          x: 'label', y: key, text: (m: Record<string, number>) => m[key].toLocaleString(), dy: -7, fill: INK, fontWeight: 600,
        }),
        Plot.tip(months, Plot.pointerX({
          x: 'label', y: key, ...TIP,
          title: (m: { label: string } & Record<string, number>) => `${m.label}\n${m[key].toLocaleString()} ${word}`,
        })),
      ],
    };
  }, [months, width]);

  if (error) return <p role="alert" className="text-caption text-destructive">{error}</p>;
  if (!trend) return <p className="text-caption text-muted-foreground">Loading…</p>;

  const first = trend.daily[0];
  const last = trend.daily[trend.daily.length - 1];
  const closed = months.reduce((sum, m) => ({ on: sum.on + m.on_time, late: sum.late + m.late, none: sum.none + m.no_deadline }),
    { on: 0, late: 0, none: 0 });

  return (
    <div ref={ref} className="min-w-0 space-y-sm">
      <div className="min-w-0 space-y-xs">
        <p className="text-caption font-medium text-foreground">Overdue findings on hosts, per recorded day</p>
        {!first ? (
          <p className="text-metadata text-muted-foreground">
            No day has been recorded yet. The count is taken while the worker runs, starting the day remediation tracking is turned on.
          </p>
        ) : dailyOptions ? (
          <PlotFigure options={dailyOptions}
            label={`Overdue findings on hosts per day, ${formatDate(first.day)} to ${formatDate(last.day)}: ${last.overdue.toLocaleString()} on the last day`} />
        ) : (
          <p className="text-metadata text-muted-foreground">
            <span className="font-semibold text-foreground tabular-nums">{last.overdue.toLocaleString()}</span> overdue
            on {formatDate(last.day)}, the only day recorded so far. A line needs a second day.
          </p>
        )}
        {first && (
          <p className="text-caption text-muted-foreground">
            History starts {formatDate(first.day)}: a deadline is worked out from today’s timeline and severities, so earlier days cannot be rebuilt.
          </p>
        )}
      </div>

      {months.length === 0 ? (
        <p className="text-metadata text-muted-foreground">Nothing was reported fixed in the last twelve months.</p>
      ) : (
        <div className="min-w-0 space-y-xs">
          <p className="text-caption font-medium text-foreground">Reported fixed on time, per month</p>
          <PlotFigure options={monthOptions('on_time', 'reported fixed on time', false)}
            label={`Findings on hosts reported fixed on time per month: ${closed.on.toLocaleString()} in all`} />
          <p className="text-caption font-medium text-foreground">Reported fixed late, per month</p>
          <PlotFigure options={monthOptions('late', 'reported fixed late', true)}
            label={`Findings on hosts reported fixed late per month: ${closed.late.toLocaleString()} in all`} />
        </div>
      )}

      <p className="text-caption text-muted-foreground">
        In the last twelve months: {closed.on.toLocaleString()} reported fixed on time, {closed.late.toLocaleString()} late
        {closed.none > 0 && `, ${closed.none.toLocaleString()} with no deadline to judge by`}.{' '}
        {(trend.daily.length > 0 || months.length > 0) && (
          <button type="button" className="text-info hover:underline" onClick={() => setShowTable((s) => !s)} aria-expanded={showTable}>
            {showTable ? 'Hide table' : 'Show as table'}
          </button>
        )}
      </p>
      {showTable && (
        <div className="grid min-w-0 gap-md lg:grid-cols-2">
          <div className="max-h-72 overflow-auto rounded-panel border border-border">
            <table className="w-full table-fixed text-caption">
              <caption className="sr-only">Findings on hosts by deadline state, per recorded day</caption>
              <thead className="sticky top-0 bg-background text-muted-foreground">
                <tr>
                  <th className="px-sm py-xxs text-left font-medium">Day</th>
                  <th className="px-sm py-xxs text-right font-medium">Overdue</th>
                  <th className="px-sm py-xxs text-right font-medium">Due soon</th>
                  <th className="px-sm py-xxs text-right font-medium">On track</th>
                  <th className="px-sm py-xxs text-right font-medium">Not assigned</th>
                </tr>
              </thead>
              <tbody>
                {trend.daily.map((d) => (
                  <tr key={d.day} className="border-t border-border">
                    <td className="px-sm py-xxs">{formatDate(d.day)}</td>
                    <td className="px-sm py-xxs text-right tabular-nums">{d.overdue.toLocaleString()}</td>
                    <td className="px-sm py-xxs text-right tabular-nums">{d.due_soon.toLocaleString()}</td>
                    <td className="px-sm py-xxs text-right tabular-nums">{d.on_track.toLocaleString()}</td>
                    <td className="px-sm py-xxs text-right tabular-nums">{d.not_assigned.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="max-h-72 overflow-auto rounded-panel border border-border">
            <table className="w-full table-fixed text-caption">
              <caption className="sr-only">Findings on hosts reported fixed per month</caption>
              <thead className="sticky top-0 bg-background text-muted-foreground">
                <tr>
                  <th className="px-sm py-xxs text-left font-medium">Month</th>
                  <th className="px-sm py-xxs text-right font-medium">On time</th>
                  <th className="px-sm py-xxs text-right font-medium">Late</th>
                  <th className="px-sm py-xxs text-right font-medium">No deadline</th>
                </tr>
              </thead>
              <tbody>
                {months.map((m) => (
                  <tr key={m.month} className="border-t border-border">
                    <td className="px-sm py-xxs">{m.label}</td>
                    <td className="px-sm py-xxs text-right tabular-nums">{m.on_time.toLocaleString()}</td>
                    <td className="px-sm py-xxs text-right tabular-nums">{m.late.toLocaleString()}</td>
                    <td className="px-sm py-xxs text-right tabular-nums">{m.no_deadline.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default RemediationTrend;
