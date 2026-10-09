/**
 * The remediation history's day axis: one count per recorded day, so one
 * tick per recorded day, said as a date.  Left to itself the time scale
 * labelled a few days by the hour ("12 AM Oct 8", "3 AM", "6 AM"…).
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getRemediationTrend = vi.fn();
vi.mock('../../services/api', () => ({
  getRemediationTrend: (...a: unknown[]) => getRemediationTrend(...a),
}));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));
interface Captured { label: string; options: { x?: { type?: string; ticks?: Date[]; tickFormat?: (d: Date) => string; domain?: string[] } } }
const figures = vi.hoisted(() => [] as unknown[]);
vi.mock('../../components/charts/PlotFigure', () => ({
  default: (props: { label: string }) => { figures.push(props); return <div role="img" aria-label={props.label} />; },
}));

import RemediationTrend, { dayTick, dayTicks } from '../../components/remediation/RemediationTrend';

const day = (d: string, overdue: number) => ({ day: d, overdue, due_soon: 1, on_track: 2, not_assigned: 3, deferred: 0, closed: 0 });
const utc = (d: string) => new Date(`${d}T00:00:00Z`);
const figure = (name: RegExp): Captured => {
  const all = (figures as Captured[]).filter((f) => name.test(f.label));
  return all[all.length - 1];
};

beforeEach(() => { getRemediationTrend.mockReset(); figures.length = 0; });

describe('RemediationTrend — the day axis', () => {
  it('has one tick per recorded day, formatted as a date and never as an hour', async () => {
    getRemediationTrend.mockResolvedValue({
      as_of: '2026-10-10', days: 90,
      // Three recorded days with a gap: the day nobody recorded gets no tick.
      daily: [day('2026-10-07', 9), day('2026-10-08', 8), day('2026-10-10', 7)],
      closed_by_month: [{ month: '2026-10', on_time: 4, late: 1, no_deadline: 0 }],
    });
    render(<RemediationTrend scope="project" />);
    await screen.findByRole('img', { name: /Overdue findings on hosts per day/ });
    const x = figure(/Overdue findings on hosts per day/).options.x!;
    expect(x.type).toBe('utc');
    expect(x.ticks!.map((t) => t.toISOString())).toEqual([
      '2026-10-07T00:00:00.000Z', '2026-10-08T00:00:00.000Z', '2026-10-10T00:00:00.000Z',
    ]);
    const labels = x.ticks!.map((t) => x.tickFormat!(t));
    expect(labels).toEqual([dayTick(utc('2026-10-07')), dayTick(utc('2026-10-08')), dayTick(utc('2026-10-10'))]);
    labels.forEach((label) => {
      expect(label).toMatch(/\d/);
      expect(label).not.toMatch(/AM|PM|:/);
    });
    expect(new Set(labels).size).toBe(3);
  });

  it('says a day in UTC, so no time zone moves it to the day before', () => {
    const label = dayTick(utc('2026-10-08'));
    expect(label).toContain('8');
    expect(label).not.toContain('7');
  });

  it('thins the labels to what fits, always on recorded days and always with the last one', () => {
    const days = Array.from({ length: 90 }, (_, i) => new Date(Date.UTC(2026, 6, 1 + i)));
    const ticks = dayTicks(days, 640);
    expect(ticks.length).toBeLessThanOrEqual(Math.floor((640 - 100) / 64) + 1);
    expect(ticks.length).toBeGreaterThan(2);
    expect(ticks.every((t) => days.includes(t))).toBe(true);
    expect(ticks[ticks.length - 1]).toBe(days[89]);
    // Few days: every one of them.
    expect(dayTicks(days.slice(0, 5), 640)).toEqual(days.slice(0, 5));
    // A narrow chart still names the two ends of a short history.
    expect(dayTicks(days.slice(0, 2), 280)).toEqual(days.slice(0, 2));
  });

  it('one recorded day is its count and its date in words — no axis at all', async () => {
    getRemediationTrend.mockResolvedValue({ as_of: '2026-10-08', days: 90, daily: [day('2026-10-08', 7)], closed_by_month: [] });
    render(<RemediationTrend scope="project" />);
    expect(await screen.findByText(/the only day recorded so far/)).toBeInTheDocument();
    expect(figure(/Overdue findings on hosts per day/)).toBeUndefined();
  });

  it('the monthly charts are bands of twelve named months, not a time axis', async () => {
    getRemediationTrend.mockResolvedValue({
      as_of: '2026-10-10', days: 90, daily: [],
      closed_by_month: [{ month: '2026-08', on_time: 4, late: 1, no_deadline: 0 }],
    });
    render(<RemediationTrend scope="project" />);
    await screen.findByRole('img', { name: /reported fixed late per month/ });
    for (const name of [/reported fixed on time per month/, /reported fixed late per month/]) {
      const x = figure(name).options.x!;
      expect(x.type).toBe('band');
      expect(x.domain).toHaveLength(12);
      expect(new Set(x.domain).size).toBe(12);
      x.domain!.forEach((label) => expect(label).not.toMatch(/AM|PM|:/));
    }
  });
});
