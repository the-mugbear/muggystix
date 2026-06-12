/**
 * Shared visual mapping for the Security Posture surface. Every colour resolves
 * to a theme CSS variable (hsl(var(--token))) so the SVG visuals track the
 * active light/dark theme automatically — no hard-coded hex.
 */
import type { PostureLabel } from '../../services/api';
import type { Severity } from '../../utils/severity';

// Severity vocabulary is canonical in utils/severity — re-exported here so
// existing posture imports keep working off the one source of truth.
export { SEVERITY_HSL, SEVERITY_ORDER, SEVERITY_LABEL } from '../../utils/severity';

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

// ---------------------------------------------------------------------------
// Finding lifecycle — status → colour + label, split into active vs resolved
// for the disposition pipeline. Colours are meaningful: open = needs attention
// (amber), confirmed = real (red), retest = in verification (blue), remediated
// = fixed (green), false-positive / accepted-risk = closed-out (muted).
// ---------------------------------------------------------------------------
export const STATUS_HSL: Record<string, string> = {
  open: 'hsl(var(--warning))',
  confirmed: 'hsl(var(--destructive))',
  retest: 'hsl(var(--info))',
  remediated: 'hsl(var(--success))',
  false_positive: 'hsl(var(--muted-foreground))',
  accepted_risk: 'hsl(var(--muted-foreground))',
};

export const STATUS_LABEL: Record<string, string> = {
  open: 'Open', confirmed: 'Confirmed', retest: 'Retest',
  remediated: 'Remediated', false_positive: 'False positive', accepted_risk: 'Accepted risk',
};

export const ACTIVE_STATUSES = ['open', 'confirmed', 'retest'];
export const RESOLVED_STATUSES = ['remediated', 'false_positive', 'accepted_risk'];

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
