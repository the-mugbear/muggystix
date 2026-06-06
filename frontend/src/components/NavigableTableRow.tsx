import React from 'react';
import { Link } from 'react-router-dom';

import { TableCell, TableRow } from './ui/table';
import { cn } from '../utils/cn';

/**
 * Accessible "navigable row" pattern (v2.43.0 — UX review #2/#9).
 *
 * Pre-v2.43.0 every "click this row to open detail" surface was implemented
 * as `<TableRow role="link" tabIndex={0} onClick={...}>` with a delegated
 * click that walked the event target to avoid double-firing on nested
 * buttons/checkboxes.  The pattern was semantically invalid (a <tr> isn't
 * a link), broke roving focus, confused screen readers, and was getting
 * copy-pasted into every new table.
 *
 * The replacement:
 *
 *   <NavigableTableRow selected={isSelected}>
 *     <NavigableTableCell to="/test-plans/42" ariaLabel="Open plan #42">
 *       <strong>Plan title</strong>
 *       <p className="text-muted-foreground">Description …</p>
 *     </NavigableTableCell>
 *     <TableCell>{otherData}</TableCell>
 *     <TableCell>
 *       <Checkbox ... />   // nested controls work normally
 *     </TableCell>
 *   </NavigableTableRow>
 *
 * What changes:
 *   * The <tr> is NOT interactive — no role, no tabIndex, no onClick.
 *   * Hover styling on the whole row is purely decorative (CSS only)
 *     so users still get the "this is clickable" affordance.
 *   * The PRIMARY cell wraps its children in a real <Link>, focusable
 *     with Tab, navigable with Enter, announced as "link" by screen
 *     readers.
 *   * Other cells (checkbox, action buttons, badges) remain independent
 *     interactive children with their own focus order.
 *
 * The pattern intentionally drops "click anywhere in the row to navigate".
 * If a row currently relies on whole-row click for selection-toggle,
 * keep the dedicated checkbox; if it relies on it for navigation, the
 * primary cell now carries that intent explicitly.
 */
export interface NavigableTableRowProps
  extends React.HTMLAttributes<HTMLTableRowElement> {
  /** Optional selection-state styling (e.g. compare picker). */
  selected?: boolean;
}

export const NavigableTableRow = React.forwardRef<
  HTMLTableRowElement,
  NavigableTableRowProps
>(({ selected, className, children, ...rest }, ref) => (
  <TableRow
    ref={ref}
    className={cn(
      'group transition-colors',
      // Decorative whole-row hover so the row still reads as
      // interactive; the actual interaction is the Link in the
      // primary cell.
      'hover:bg-accent/45',
      selected && 'bg-accent',
      className,
    )}
    {...rest}
  >
    {children}
  </TableRow>
));
NavigableTableRow.displayName = 'NavigableTableRow';


export interface NavigableTableCellProps
  extends Omit<React.TdHTMLAttributes<HTMLTableCellElement>, 'onClick'> {
  /** Route to navigate to.  null/undefined renders children without a Link. */
  to: string | null | undefined;
  /** Accessible label for the link.  Required when the cell content is
   *  visually rich (icons, badges) so AT users get a meaningful description. */
  ariaLabel?: string;
}

export const NavigableTableCell = React.forwardRef<
  HTMLTableCellElement,
  NavigableTableCellProps
>(({ to, ariaLabel, className, children, ...rest }, ref) => {
  if (!to) {
    return (
      <TableCell ref={ref} className={className} {...rest}>
        {children}
      </TableCell>
    );
  }
  return (
    <TableCell ref={ref} className={cn('p-0', className)} {...rest}>
      {/* The Link fills the cell so the click target matches the
          visible cell area. Padding moves from the cell to the link
          to preserve the visual layout. */}
      <Link
        to={to}
        aria-label={ariaLabel}
        className={cn(
          'block px-md py-xs',
          'text-inherit no-underline',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        )}
      >
        {children}
      </Link>
    </TableCell>
  );
});
NavigableTableCell.displayName = 'NavigableTableCell';
