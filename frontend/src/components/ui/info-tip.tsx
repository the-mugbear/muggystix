import * as React from 'react';
import { Info } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip';

/**
 * InfoTip — the (i) affordance for plain-English "what is this and how is
 * it derived?" help next to a metric, column header or control.  One shared
 * primitive (extracted from the per-page copies in Segments / Patterns /
 * Evidence, 5.198.0) so every explanatory tooltip looks and behaves the
 * same: a real button (keyboard-focusable, opens on focus and hover), a
 * capped-width popover, caption type.
 *
 * `label` is the accessible name of the trigger; give it something specific
 * ("About names covered") when several tips sit on one surface so a
 * screen-reader user can tell them apart.
 */
export const InfoTip: React.FC<{ text: React.ReactNode; label?: string; className?: string }> = ({
  text,
  label = 'How is this derived?',
  className,
}) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button
        type="button"
        aria-label={label}
        className={
          'inline-flex shrink-0 rounded text-muted-foreground/70 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring' +
          (className ? ` ${className}` : '')
        }
      >
        <Info className="size-3.5" aria-hidden />
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs text-left text-caption leading-snug">{text}</TooltipContent>
  </Tooltip>
);
