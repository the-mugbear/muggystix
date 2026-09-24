/**
 * The kind of an agent run — one badge wherever runs are listed (v5.294.0).
 *
 * Operations and Agent Runs each had their own: "PLAN GEN" as an outline chip
 * on one, a filled blue "PLAN-GEN" on the other, and different colours for
 * recon and assist.
 */
import React from 'react';

import { Badge } from './ui/badge';
import { cn } from '../utils/cn';

type Variant = React.ComponentProps<typeof Badge>['variant'];

const KINDS: Record<string, { label: string; variant: Variant }> = {
  project: { label: 'Session', variant: 'default' },
  recon: { label: 'Recon', variant: 'secondary' },
  plan_generation: { label: 'Plan generation', variant: 'info' },
  execution: { label: 'Execution', variant: 'success' },
  assist: { label: 'Assist', variant: 'warning' },
};

export function runKindLabel(kind: string): string {
  return KINDS[kind]?.label ?? (kind.charAt(0).toUpperCase() + kind.slice(1).replace(/_/g, ' '));
}

export const RunKindBadge: React.FC<{ kind: string; className?: string }> = ({ kind, className }) => (
  <Badge variant={KINDS[kind]?.variant ?? 'muted'} className={cn('whitespace-nowrap', className)}>
    {runKindLabel(kind)}
  </Badge>
);

export default RunKindBadge;
