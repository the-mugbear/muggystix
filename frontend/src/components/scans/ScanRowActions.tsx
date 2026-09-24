/**
 * The last column of every Scans history row (UX review 2026-09-24).
 *
 * File rows had "Hosts ···", a file with no hosts only "···", batches a "›
 * Files" button: three shapes in one column, nothing lined up. Now every row
 * has the same two slots — a quiet text link on the left, the row menu on the
 * right — and an empty slot keeps its width, so the menus form one column.
 */
import React from 'react';

import { cn } from '../../utils/cn';

/** The class of the quiet text link (or button) in the first slot. */
export const ROW_LINK_CLASS =
  'inline-flex items-center gap-xxs whitespace-nowrap rounded text-caption text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

export const ScanRowActions: React.FC<{
  link?: React.ReactNode;
  menu?: React.ReactNode;
  className?: string;
}> = ({ link, menu, className }) => (
  <div className={cn('flex items-center justify-end gap-xs', className)} data-testid="scan-row-actions">
    <span className="flex min-w-0 justify-end">{link}</span>
    <span className="flex size-7 shrink-0 items-center justify-center">{menu}</span>
  </div>
);

export default ScanRowActions;
