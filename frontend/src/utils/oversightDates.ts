/**
 * Oversight date presets → inclusive UTC calendar days (YYYY-MM-DD).
 *
 * The server counts in UTC (BlueStick has no reporting-timezone concept and
 * some timestamps carry no zone), so the presets are UTC days too; the page
 * says so beside the control.
 */
export type DatePreset =
  | 'today' | 'last7' | 'last30' | 'month' | 'quarter' | 'ytd' | 'all' | 'custom';

export const DATE_PRESETS: Array<{ value: DatePreset; label: string }> = [
  { value: 'today', label: 'Today' },
  { value: 'last7', label: 'Last 7 days' },
  { value: 'last30', label: 'Last 30 days' },
  { value: 'month', label: 'This month' },
  { value: 'quarter', label: 'This quarter' },
  { value: 'ytd', label: 'Year to date' },
  { value: 'all', label: 'All time' },
  { value: 'custom', label: 'Custom' },
];

export const DEFAULT_PRESET: DatePreset = 'last30';

const iso = (d: Date): string => d.toISOString().slice(0, 10);

const utcDay = (y: number, m: number, d: number): Date => new Date(Date.UTC(y, m, d));

/** The inclusive range a preset stands for on the UTC day of ``now``.
 *  ``all`` and ``custom`` return an empty range (the caller supplies custom). */
export function presetRange(preset: DatePreset, now: Date = new Date()): { start?: string; end?: string } {
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth();
  const today = utcDay(y, m, now.getUTCDate());
  const daysBack = (n: number) => new Date(today.getTime() - n * 86_400_000);
  switch (preset) {
    case 'today': return { start: iso(today), end: iso(today) };
    case 'last7': return { start: iso(daysBack(6)), end: iso(today) };
    case 'last30': return { start: iso(daysBack(29)), end: iso(today) };
    case 'month': return { start: iso(utcDay(y, m, 1)), end: iso(today) };
    case 'quarter': return { start: iso(utcDay(y, m - (m % 3), 1)), end: iso(today) };
    case 'ytd': return { start: iso(utcDay(y, 0, 1)), end: iso(today) };
    default: return {};
  }
}

/** A custom range the server would refuse, as a sentence; null when valid. */
export function customRangeError(start: string, end: string, now: Date = new Date()): string | null {
  if (!start || !end) return 'Choose both a start and an end date.';
  const today = iso(now);
  if (start > end) return 'The start date must be on or before the end date.';
  if (end > today || start > today) return 'Dates cannot be in the future.';
  return null;
}
