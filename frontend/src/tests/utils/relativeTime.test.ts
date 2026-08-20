/**
 * The shared relative-time formatter.
 *
 * Ten components hand-rolled this, four of them byte-identical. Consolidating
 * them is only safe if every surface keeps rendering exactly what it did, so
 * these tests are written as "surface X used to produce Y" — they are the
 * contract the migration had to satisfy, and the reason a future edit to the
 * shared core can't quietly restyle six pages at once.
 */
import { describe, it, expect } from 'vitest';

import { formatRelativeTime, formatTimestamp } from '../../utils/relativeTime';

const NOW = Date.parse('2026-08-19T12:00:00Z');
const ago = (ms: number) => new Date(NOW - ms).toISOString();

const SECOND = 1_000;
const MINUTE = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;

describe('formatRelativeTime', () => {
  it('renders the short ladder the four identical copies produced', () => {
    // AgentActivityRail / Operations / HostLineagePanel / ProjectActivity —
    // the same function four times over.
    const short = (iso: string) =>
      formatRelativeTime(iso, { withSeconds: true, now: NOW });
    expect(short(ago(5 * SECOND))).toBe('5s ago');
    expect(short(ago(5 * MINUTE))).toBe('5m ago');
    expect(short(ago(5 * HOUR))).toBe('5h ago');
    expect(short(ago(5 * DAY))).toBe('5d ago');
    // A future timestamp is clock skew, not a negative age.
    expect(short(new Date(NOW + MINUTE).toISOString())).toBe('just now');
    expect(short('')).toBe('');
    expect(formatRelativeTime(null, { withSeconds: true, now: NOW })).toBe('');
  });

  it('renders the long form the assist session panel uses', () => {
    const long = (iso: string) => formatRelativeTime(iso, { style: 'long', now: NOW });
    expect(long(ago(30 * SECOND))).toBe('just now');
    expect(long(ago(MINUTE))).toBe('1 minute ago');
    expect(long(ago(4 * MINUTE))).toBe('4 minutes ago');
    expect(long(ago(HOUR))).toBe('1 hour ago');
    expect(long(ago(2 * HOUR))).toBe('2 hours ago');
    expect(long(ago(DAY))).toBe('1 day ago');
    expect(long(ago(3 * DAY))).toBe('3 days ago');
    // The panel renders nothing at all rather than a placeholder.
    expect(formatRelativeTime(null, { style: 'long', fallback: null, now: NOW })).toBeNull();
  });

  it('renders the compact form the work card uses', () => {
    const compact = (iso: string) => formatRelativeTime(iso, { style: 'compact', now: NOW });
    // No "ago" — the column header already says what the number means.
    expect(compact(ago(30 * SECOND))).toBe('just now');
    expect(compact(ago(5 * MINUTE))).toBe('5m');
    expect(compact(ago(5 * HOUR))).toBe('5h');
    expect(compact(ago(5 * DAY))).toBe('5d');
  });

  it('falls back to an absolute date once relative stops being useful', () => {
    // Activity and ParseErrors both did this: "412d ago" tells you less than a
    // date does.
    const opts = { absoluteAfterDays: 30, now: NOW } as const;
    expect(formatRelativeTime(ago(29 * DAY), opts)).toBe('29d ago');
    const old = formatRelativeTime(ago(60 * DAY), opts);
    expect(old).not.toContain('ago');
    expect(old).toBe(new Date(NOW - 60 * DAY).toLocaleDateString());
  });

  it('honours a surface-specific "just now" threshold', () => {
    // LastUpdated calls anything under five seconds "just now" — it is a
    // refresh indicator, and "0s ago" flickering is noise.
    expect(formatRelativeTime(ago(3 * SECOND), {
      withSeconds: true, justNowBelowMs: 5_000, now: NOW,
    })).toBe('just now');
    expect(formatRelativeTime(ago(10 * SECOND), {
      withSeconds: true, justNowBelowMs: 5_000, now: NOW,
    })).toBe('10s ago');
  });

  it('rounds up to the smallest displayed unit rather than showing zero', () => {
    // Without seconds, 30s is "just now", not "0m ago" — the failure a naive
    // shared implementation would introduce on the surfaces that omit seconds.
    expect(formatRelativeTime(ago(30 * SECOND), { now: NOW })).toBe('just now');
    expect(formatRelativeTime(ago(90 * SECOND), { now: NOW })).toBe('1m ago');
  });

  it('returns each surface’s own fallback for missing or unparseable input', () => {
    expect(formatRelativeTime(undefined, { now: NOW })).toBe('');
    expect(formatRelativeTime('not-a-date', { now: NOW })).toBe('');
    expect(formatRelativeTime(null, { fallback: '-', now: NOW })).toBe('-');
    expect(formatRelativeTime(null, { fallback: 'never', now: NOW })).toBe('never');
    expect(formatRelativeTime(null, { fallback: null, now: NOW })).toBeNull();
  });

  it('accepts the shapes callers already hold', () => {
    expect(formatRelativeTime(new Date(NOW - 5 * MINUTE), { now: NOW })).toBe('5m ago');
    expect(formatRelativeTime(NOW - 5 * MINUTE, { now: NOW })).toBe('5m ago');
  });
});

describe('formatTimestamp', () => {
  it('formats an absolute local time, with a fallback for nothing', () => {
    expect(formatTimestamp('2026-08-19T12:00:00Z')).toBe(
      new Date('2026-08-19T12:00:00Z').toLocaleString(),
    );
    expect(formatTimestamp(null)).toBe('—');
    expect(formatTimestamp('nonsense')).toBe('—');
    expect(formatTimestamp(null, 'never')).toBe('never');
  });
});
