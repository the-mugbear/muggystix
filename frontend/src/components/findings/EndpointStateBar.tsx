/**
 * How a finding's endpoints stand, as one thin stacked bar beside the sentence
 * that says it.  A companion to the filter chips, not a second explanation:
 * no legend — each part is a button that applies its state's filter (the chip's
 * own action) and names itself to a pointer and to a screen reader.
 *
 * Hand-built: a small inline visual, like the severity bar.
 */
import React from 'react';

import type { FindingHostStatus } from '../../services/api';
import { cn } from '../../utils/cn';
import { ENDPOINT_STATES, EndpointStateFilter } from '../../utils/findingEndpoints';
import { ENDPOINT_STATUS_FILL, ENDPOINT_STATUS_SHORT_LABEL } from '../../utils/findingStatus';

interface Props {
  counts: Record<FindingHostStatus, number>;
  /** The state the list is filtered to. */
  active: EndpointStateFilter;
  onSelect: (state: FindingHostStatus) => void;
  className?: string;
}

const EndpointStateBar: React.FC<Props> = ({ counts, active, onSelect, className }) => {
  const states = ENDPOINT_STATES.filter((s) => counts[s] > 0);
  if (states.length === 0) return null;
  return (
    <div
      role="group"
      aria-label="Endpoint states"
      className={cn('flex h-3 w-56 max-w-full shrink-0 gap-px overflow-hidden rounded-full', className)}
    >
      {states.map((s) => {
        const word = ENDPOINT_STATUS_SHORT_LABEL[s].toLowerCase();
        const name = `${counts[s].toLocaleString()} ${word} — filter`;
        return (
          <button
            key={s}
            type="button"
            data-endpoint-state={s}
            aria-label={name}
            aria-pressed={active === s}
            title={name}
            onClick={() => onSelect(s)}
            // A share of the bar, but never too thin to see or to click.
            style={{ flexGrow: counts[s], flexBasis: 0, minWidth: '0.5rem' }}
            className={cn(
              'h-full focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
              ENDPOINT_STATUS_FILL[s],
              active !== 'all' && active !== s && 'opacity-40',
            )}
          />
        );
      })}
    </div>
  );
};

export default EndpointStateBar;
