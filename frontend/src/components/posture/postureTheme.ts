/**
 * Shared visual mapping for the Security Posture surface. Every colour resolves
 * to a theme CSS variable (hsl(var(--token))) so the SVG visuals track the
 * active light/dark theme automatically — no hard-coded hex.
 */
import type { PostureLabel, Severity } from '../../services/api';

/** Severity → theme token (matches the Badge severity variants). */
export const SEVERITY_HSL: Record<Severity, string> = {
  critical: 'hsl(var(--destructive))',
  high: 'hsl(var(--warning))',
  medium: 'hsl(var(--info))',
  low: 'hsl(var(--success))',
  info: 'hsl(var(--muted-foreground))',
};

export const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info',
};

/** Site criticality tier → colour (tier 1 = hottest). */
export function tierHsl(tier: number | null): string {
  switch (tier) {
    case 1: return 'hsl(var(--destructive))';
    case 2: return 'hsl(var(--warning))';
    case 3: return 'hsl(var(--info))';
    case 4: return 'hsl(var(--muted-foreground))';
    default: return 'hsl(var(--muted-foreground))';
  }
}

export const TIER_LABEL: Record<number, string> = {
  1: 'Tier 1 — Critical', 2: 'Tier 2 — High', 3: 'Tier 3 — Standard', 4: 'Tier 4 — Low',
};

/** The deterministic posture label → tone (drives the banner + accent). */
export interface LabelTone {
  text: string;
  hsl: string;
  /** tailwind text colour class for inline use. */
  textClass: string;
  /** soft background tint class for the banner. */
  tintClass: string;
  borderClass: string;
}

export const LABEL_TONE: Record<PostureLabel, LabelTone> = {
  action_required: {
    text: 'Action required', hsl: 'hsl(var(--destructive))',
    textClass: 'text-destructive', tintClass: 'bg-destructive/10', borderClass: 'border-l-destructive',
  },
  needs_assessment: {
    text: 'Needs assessment', hsl: 'hsl(var(--warning))',
    textClass: 'text-warning', tintClass: 'bg-warning/10', borderClass: 'border-l-warning',
  },
  no_urgent_signals: {
    text: 'No urgent signals', hsl: 'hsl(var(--success))',
    textClass: 'text-success', tintClass: 'bg-success/10', borderClass: 'border-l-success',
  },
};

/** Priority-row kind → short label + tone for the chip. */
export const PRIORITY_KIND: Record<string, { label: string; severity: Severity }> = {
  ownership: { label: 'Ownership', severity: 'high' },
  systemic: { label: 'Systemic', severity: 'critical' },
  site: { label: 'Site', severity: 'critical' },
  blocked: { label: 'Blocked run', severity: 'high' },
  coverage: { label: 'Coverage', severity: 'medium' },
  triage: { label: 'Triage', severity: 'medium' },
  approval: { label: 'Approval', severity: 'low' },
  onboard: { label: 'Onboard', severity: 'medium' },
};
