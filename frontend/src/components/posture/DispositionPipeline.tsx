/**
 * Promoted findings by status, grouped under investigation | confirmed |
 * closed (the vocabulary in utils/findingStatus.ts) — drawn by the shared
 * `DistributionBar` (5.414.0), the same design as the scanner observations by
 * severity above it on Posture.  It was its own bar: 12px, full width, its
 * counts in a line ABOVE the rail, under a 24px severity rail whose counts
 * were below — two designs for one idea, one under the other.
 *
 * Every population is listed, a zero included (a zero is information here).
 * A population of one status is listed under the population's name; one of
 * several (closed: false positive · accepted risk · remediated) under each
 * status's, so nothing is hidden behind a hover.  A gap separates the groups.
 */
import React from 'react';

import DistributionBar, { type DistributionPart } from '../ui/DistributionBar';
import { STATUS_HSL, STATUS_LABEL, POPULATION_STATUSES } from './postureTheme';
import { POPULATION_LABEL } from '../../utils/findingStatus';

interface DispositionPipelineProps {
  byStatus: Record<string, number>;
  /** Drill-down for a status (§26); null = no link. */
  statusHref?: (status: string) => string | null;
}

const DispositionPipeline: React.FC<DispositionPipelineProps> = ({ byStatus, statusHref }) => {
  const parts: DistributionPart[] = [];
  for (const group of POPULATION_STATUSES) {
    const present = group.statuses.filter((s) => byStatus[s]);
    const first = parts.length;
    if (present.length <= 1) {
      const status = present[0] ?? group.statuses[0];
      parts.push({
        key: group.key, label: POPULATION_LABEL[group.key], count: byStatus[status] ?? 0,
        color: STATUS_HSL[status] ?? 'hsl(var(--muted))', href: statusHref?.(status) ?? null,
      });
    } else {
      for (const status of present) {
        parts.push({
          key: status, label: STATUS_LABEL[status] ?? status, count: byStatus[status],
          color: STATUS_HSL[status] ?? 'hsl(var(--muted))', href: statusHref?.(status) ?? null,
        });
      }
    }
    if (first > 0) parts[first].groupStart = true;
  }
  const total = parts.reduce((sum, p) => sum + p.count, 0);

  if (total === 0) {
    return <p className="text-caption text-muted-foreground">No findings recorded yet.</p>;
  }
  return (
    <DistributionBar
      title="Findings by status"
      summary={`${total.toLocaleString()} finding${total === 1 ? '' : 's'}`}
      ariaLabel={parts.filter((p) => p.count > 0).map((p) => `${p.count} ${p.label}`).join(', ')}
      parts={parts}
    />
  );
};

export default DispositionPipeline;
