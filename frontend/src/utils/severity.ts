/**
 * Canonical severity vocabulary — the ONE source of truth for severity order,
 * labels, and colour. Several pages grew their own local maps (Operations'
 * SEVERITY_SEGMENTS, Hosts' severityChipClasses, repeated SEVERITY_VARIANT
 * objects); they should all consume this instead. Colours resolve to theme
 * tokens so light/dark track automatically.
 */
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

export const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info',
};

/** Severity → theme token (matches the Badge severity variants). */
export const SEVERITY_HSL: Record<Severity, string> = {
  critical: 'hsl(var(--destructive))',
  high: 'hsl(var(--warning))',
  medium: 'hsl(var(--info))',
  low: 'hsl(var(--success))',
  info: 'hsl(var(--muted-foreground))',
};

/** Severity → Badge `variant` (the shared severity-* badge styles). The
 *  canonical replacement for the per-page SEVERITY_VARIANT maps. */
export const SEVERITY_BADGE_VARIANT: Record<Severity, string> = {
  critical: 'severity-critical',
  high: 'severity-high',
  medium: 'severity-medium',
  low: 'severity-low',
  info: 'muted',
};

/** Sort rank, worst-first. `unknown` sinks below `info`. Replaces the per-page
 *  VULNERABILITY_SEVERITY_ORDER duplicates. */
export const SEVERITY_RANK: Record<string, number> = {
  critical: 0, high: 1, medium: 2, low: 3, info: 4, unknown: 5,
};

/** Sum a (possibly partial) severity-count map. */
export function severityTotal(counts: Partial<Record<Severity, number>>): number {
  return SEVERITY_ORDER.reduce((sum, k) => sum + (counts[k] ?? 0), 0);
}
