import * as React from 'react';
import { cn } from '../../utils/cn';

/**
 * Table primitive — styled native <table> elements.  Use for static or
 * lightly-paginated tables (reference pages, settings tables).
 *
 * For tables that need virtualization, server-side sort/filter, column
 * resize, or > ~200 rows, use DataTable (TBD, built on TanStack Table)
 * instead.  This primitive is intentionally a thin styling layer over
 * raw HTML so it's predictable and stateless.
 *
 * The outer Table sits inside an overflow-x-auto wrapper so wide tables
 * scroll horizontally within their panel rather than blowing out the
 * page.  Use:
 *
 *   <div className="rounded-panel border border-border overflow-x-auto">
 *     <Table>
 *       <TableHeader>...</TableHeader>
 *       <TableBody>...</TableBody>
 *     </Table>
 *   </div>
 */

export const Table = React.forwardRef<HTMLTableElement, React.HTMLAttributes<HTMLTableElement>>(
  ({ className, ...props }, ref) => (
    <table
      ref={ref}
      // table-fixed is the default per style guide §8 — without it,
      // `<TableCell className="truncate">` is a no-op (the browser
      // auto-sizes columns to widest content, then truncate kicks in
      // only after the table itself has overflowed).  Pages that
      // genuinely need column auto-sizing can opt out with
      // `className="table-auto"`; tailwind-merge handles the override.
      className={cn('w-full caption-bottom text-metadata text-foreground table-fixed', className)}
      {...props}
    />
  ),
);
Table.displayName = 'Table';

export const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead ref={ref} className={cn('[&_tr]:border-b [&_tr]:border-border', className)} {...props} />
));
TableHeader.displayName = 'TableHeader';

export const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody ref={ref} className={cn('[&_tr:last-child]:border-0', className)} {...props} />
));
TableBody.displayName = 'TableBody';

export const TableFooter = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tfoot
    ref={ref}
    className={cn(
      'border-t border-border bg-muted/50 font-medium [&>tr]:last:border-b-0',
      className,
    )}
    {...props}
  />
));
TableFooter.displayName = 'TableFooter';

export const TableRow = React.forwardRef<HTMLTableRowElement, React.HTMLAttributes<HTMLTableRowElement>>(
  ({ className, ...props }, ref) => (
    <tr
      ref={ref}
      className={cn(
        'border-b border-border transition-[background-color,box-shadow] hover:bg-accent/45 hover:shadow-[inset_3px_0_0_hsl(var(--primary)/0.34)]',
        'data-[state=selected]:bg-accent',
        className,
      )}
      {...props}
    />
  ),
);
TableRow.displayName = 'TableRow';

export const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <th
    ref={ref}
    className={cn(
      'h-9 px-sm text-left align-middle text-caption font-semibold uppercase tracking-wider text-muted-foreground',
      '[&:has([role=checkbox])]:pr-0',
      className,
    )}
    {...props}
  />
));
TableHead.displayName = 'TableHead';

export const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td
    ref={ref}
    className={cn(
      'px-sm py-xs align-top [&:has([role=checkbox])]:pr-0',
      className,
    )}
    {...props}
  />
));
TableCell.displayName = 'TableCell';

export const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
  <caption ref={ref} className={cn('mt-sm text-caption text-muted-foreground', className)} {...props} />
));
TableCaption.displayName = 'TableCaption';
