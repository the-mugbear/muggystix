/**
 * Relative timestamps — "5m ago", "5 minutes ago", "5m".
 *
 * v5.179.0. Ten components had hand-rolled this, and **four of them were
 * byte-identical** (AgentActivityRail, Operations, HostLineagePanel,
 * ProjectActivity): the same try/catch, the same s/m/h/d ladder, copied. The
 * other six differ in ways that are real and deliberate — a dense card wants
 * `5m` with no "ago", a session panel wants "5 minutes ago", a refresh
 * indicator calls anything under five seconds "just now", and two surfaces fall
 * back to an absolute date past a month.
 *
 * So this is one arithmetic core with explicit options, not one hardcoded
 * format. The options exist because the differences already existed; each one
 * maps to a behaviour some surface was already implementing, and the tests pin
 * every existing output so consolidating changed nothing a user sees. Adding a
 * knob here that no call site needs would be the wrong direction — prefer a
 * local wrapper in the component over a seventh option.
 *
 * **Deliberately NOT migrated** — these look similar and are not:
 *   * ``MyActivityCard.dayBucket`` buckets by CALENDAR day ("Today",
 *     "Yesterday"), so an event at 23:50 last night is "Yesterday" where
 *     elapsed time would say "8h ago". Different question, different answer.
 *   * ``ProvenanceCard.formatAge`` is day-and-month granularity for scan age
 *     ("3 days ago", "4 months ago") — a months bucket nothing else wants.
 *   * ``SecurityPosture`` / ``PortfolioDashboard`` / ``Operations`` format a
 *     day COUNT the server already computed (``days_since_last_scan``); there
 *     is no timestamp to parse and no ladder to share.
 *   * ``TestPlanLayout.formatTimeLeft`` counts DOWN to an expiry, not up from
 *     a past event.
 */

export type RelativeTimeStyle =
  /** "5m ago" — the default, and what most surfaces use. */
  | 'short'
  /** "5m" — for dense cards where the column header already says "age". */
  | 'compact'
  /** "5 minutes ago" — for prose-ish panels with room to breathe. */
  | 'long';

export interface RelativeTimeOptions<F = string> {
  style?: RelativeTimeStyle;
  /** Show a seconds bucket ("30s ago") instead of rounding up to a minute. */
  withSeconds?: boolean;
  /** Below this many ms, say "just now" rather than a number. Default 0, i.e.
   *  only a future timestamp (clock skew) reads as "just now". */
  justNowBelowMs?: number;
  /** Past this many days, return a locale date instead — an exact date is more
   *  use than "412d ago". */
  absoluteAfterDays?: number;
  /** Returned for null/undefined/unparseable input. Surfaces disagree here:
   *  '' in a table cell, '-' in another, null when the caller renders nothing,
   *  'never' for a refresh indicator. */
  fallback?: F;
  /** Injectable clock, so tests don't race real time. */
  now?: number;
}

const UNITS: Array<{ ms: number; short: string; long: string }> = [
  { ms: 86_400_000, short: 'd', long: 'day' },
  { ms: 3_600_000, short: 'h', long: 'hour' },
  { ms: 60_000, short: 'm', long: 'minute' },
  { ms: 1_000, short: 's', long: 'second' },
];

const toMillis = (value: string | number | Date): number => {
  if (value instanceof Date) return value.getTime();
  if (typeof value === 'number') return value;
  return new Date(value).getTime();
};

export function formatRelativeTime<F = string>(
  value: string | number | Date | null | undefined,
  options: RelativeTimeOptions<F> = {},
): string | F {
  const {
    style = 'short',
    withSeconds = false,
    justNowBelowMs = 0,
    absoluteAfterDays,
    now = Date.now(),
  } = options;
  // `fallback` defaults to '' but must survive an explicit `null`, so it is read
  // separately rather than destructured with a default.
  const fallback = ('fallback' in options ? options.fallback : '') as F;

  if (value === null || value === undefined || value === '') return fallback;
  const then = toMillis(value);
  if (Number.isNaN(then)) return fallback;

  const elapsed = now - then;
  // A future timestamp means clock skew between the browser and the server, not
  // a negative age — "in -3 minutes" would be nonsense on every surface.
  if (elapsed < justNowBelowMs || elapsed < 0) return 'just now';

  if (absoluteAfterDays !== undefined && elapsed >= absoluteAfterDays * 86_400_000) {
    return new Date(then).toLocaleDateString();
  }

  const smallest = withSeconds ? 1_000 : 60_000;
  for (const unit of UNITS) {
    if (unit.ms < smallest) break;
    const count = Math.floor(elapsed / unit.ms);
    if (count < 1) continue;
    if (style === 'long') {
      return `${count} ${unit.long}${count === 1 ? '' : 's'} ago`;
    }
    return style === 'compact' ? `${count}${unit.short}` : `${count}${unit.short} ago`;
  }
  return 'just now';
}

/** Absolute local timestamp, or a fallback — the other half of the pair most
 *  of these components needed, hand-rolled almost as often. */
export function formatTimestamp<F = string>(
  value: string | number | Date | null | undefined,
  fallback: F = '—' as unknown as F,
): string | F {
  if (value === null || value === undefined || value === '') return fallback;
  const ms = toMillis(value);
  if (Number.isNaN(ms)) return fallback;
  return new Date(ms).toLocaleString();
}
