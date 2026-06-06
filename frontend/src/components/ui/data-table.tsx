import * as React from 'react';
import {
  ColumnDef,
  ExpandedState,
  PaginationState,
  Row,
  RowSelectionState,
  SortingState,
  Table as ReactTable,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Button } from './button';
import { Checkbox } from './checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './select';

/**
 * DataTable — TanStack-backed table primitive.  First consumer is the
 * Hosts list (phase C of the v4 migration).  Designed so a page can
 * either:
 *
 *   (a) Use TanStack-managed sort + pagination (client-side data sets)
 *   (b) Drive sort + pagination externally and pass `manualSorting`
 *       + `manualPagination` (server-paged surfaces like Hosts).
 *
 * Composition:
 *
 *   const table = useDataTable({ data, columns, ... });
 *   return (
 *     <>
 *       <DataTableShell table={table} renderSubRow={...} onRowClick={...} />
 *       <DataTablePagination table={table} totalCount={total} />
 *     </>
 *   );
 *
 * Helpers:
 *   selectionColumn<T>()   — leading checkbox column
 *   expanderColumn<T>()    — leading caret column tied to row.getCanExpand()
 *   sortableHeader(label)  — header cell with sort affordance
 */

export type DataTableInstance<TData> = ReactTable<TData>;

export interface UseDataTableProps<TData> {
  data: TData[];
  columns: ColumnDef<TData, any>[];
  getRowId?: (row: TData, index: number) => string;
  // Sort
  sorting?: SortingState;
  onSortingChange?: (updater: SortingState | ((prev: SortingState) => SortingState)) => void;
  manualSorting?: boolean;
  // Pagination
  pagination?: PaginationState;
  onPaginationChange?: (updater: PaginationState | ((prev: PaginationState) => PaginationState)) => void;
  manualPagination?: boolean;
  pageCount?: number;
  // Selection
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: (updater: RowSelectionState | ((prev: RowSelectionState) => RowSelectionState)) => void;
  enableRowSelection?: boolean | ((row: Row<TData>) => boolean);
  // Expansion
  expanded?: ExpandedState;
  onExpandedChange?: (updater: ExpandedState | ((prev: ExpandedState) => ExpandedState)) => void;
  getRowCanExpand?: (row: Row<TData>) => boolean;
}

export function useDataTable<TData>({
  data,
  columns,
  getRowId,
  sorting,
  onSortingChange,
  manualSorting,
  pagination,
  onPaginationChange,
  manualPagination,
  pageCount,
  rowSelection,
  onRowSelectionChange,
  enableRowSelection,
  expanded,
  onExpandedChange,
  getRowCanExpand,
}: UseDataTableProps<TData>): DataTableInstance<TData> {
  return useReactTable<TData>({
    data,
    columns,
    getRowId,
    state: {
      sorting,
      pagination,
      rowSelection,
      expanded,
    },
    onSortingChange: onSortingChange as any,
    onPaginationChange: onPaginationChange as any,
    onRowSelectionChange: onRowSelectionChange as any,
    onExpandedChange: onExpandedChange as any,
    enableRowSelection,
    getRowCanExpand,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: manualSorting ? undefined : getSortedRowModel(),
    getPaginationRowModel: manualPagination ? undefined : getPaginationRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    manualSorting,
    manualPagination,
    pageCount,
  });
}

// ---------------------------------------------------------------------------
// Row subcomponent
// ---------------------------------------------------------------------------
//
// Audit PRF·M4: previously the row render-loop defined inline arrow
// handlers for onClick + onKeyDown.  Every render produced new function
// identities so memo had nothing to grab onto, and React re-ran every
// row even when only one row's data changed.  Pulling the row into a
// memoised subcomponent with stable useCallback handlers lets React
// skip re-render on the unchanged rows.  Keyboard semantics are
// preserved verbatim from the prior inline implementation.

interface DataTableRowProps<TData> {
  row: Row<TData>;
  selected: boolean;
  onRowClick?: (row: TData, event: React.MouseEvent<HTMLTableRowElement>) => void;
  renderSubRow?: (row: Row<TData>) => React.ReactNode;
  /** Caller-supplied extra className for this row (e.g. a left-border
   *  accent driven by row data). */
  extraClassName?: string;
  /** Native `title` tooltip for the row — used by callers to surface
   *  per-row context (e.g. "Tested · N results") without adding a column. */
  rowTitle?: string;
}

function DataTableRowImpl<TData>({
  row,
  selected,
  onRowClick,
  renderSubRow,
  extraClassName,
  rowTitle,
}: DataTableRowProps<TData>) {
  const handleClick = React.useCallback(
    (event: React.MouseEvent<HTMLTableRowElement>) => {
      if (!onRowClick) return;
      onRowClick(row.original, event);
    },
    [onRowClick, row.original],
  );

  const cells = row.getVisibleCells();
  const isExpanded = row.getIsExpanded();

  return (
    <React.Fragment>
      {/* v2.44.1 (UX review #2): dropped role="link" / tabIndex=0 /
          onKeyDown from the row.  The previous keyboard-parity
          attempt was semantically wrong (the row isn't a link),
          produced ambiguous SR output, and competed for focus with
          nested interactives.  Click-anywhere-to-open is preserved
          for mouse users via onClick + cursor-pointer; keyboard
          users activate the row's *primary cell* — pages using
          onRowClick are expected to put a real <button> / <Link> in
          their first column for the keyboard path (see
          useHostColumns.tsx for the canonical pattern). */}
      <tr
        data-state={selected ? 'selected' : undefined}
        {...(onRowClick ? { onClick: handleClick } : {})}
        {...(rowTitle ? { title: rowTitle } : {})}
        className={cn(
          'border-b border-border transition-colors hover:bg-accent/50',
          'data-[state=selected]:bg-accent',
          onRowClick && 'cursor-pointer',
          extraClassName,
        )}
      >
        {cells.map((cell) => (
          <td
            key={cell.id}
            className={cn(
              'px-sm py-xs align-top [&:has([role=checkbox])]:pr-0',
            )}
          >
            {flexRender(cell.column.columnDef.cell, cell.getContext())}
          </td>
        ))}
      </tr>
      {isExpanded && renderSubRow && (
        <tr className="bg-muted/30">
          <td colSpan={cells.length} className="px-sm py-sm">
            {renderSubRow(row)}
          </td>
        </tr>
      )}
    </React.Fragment>
  );
}

const DataTableRow = React.memo(DataTableRowImpl) as typeof DataTableRowImpl;

export interface DataTableShellProps<TData> {
  table: DataTableInstance<TData>;
  /** When set, the header row is `position: sticky`. */
  stickyHeader?: boolean;
  /** Click handler for a body row; receives the row's original data. */
  onRowClick?: (row: TData, event: React.MouseEvent<HTMLTableRowElement>) => void;
  /** When set, expanded rows render this in a full-width <td>. */
  renderSubRow?: (row: Row<TData>) => React.ReactNode;
  /** When true, the row is marked aria-selected via `data-state="selected"`. */
  getRowSelectedState?: (row: Row<TData>) => boolean;
  /** Optional per-row className. Returned string is appended to the row's
   *  default classes — use it for data-driven accents (e.g. a colored
   *  left border when a host has been executed against). */
  getRowClassName?: (row: Row<TData>) => string | undefined;
  /** Optional per-row native `title` tooltip — handy for surfacing
   *  per-row metadata without adding a column. */
  getRowTitle?: (row: Row<TData>) => string | undefined;
  /** Optional className for the outer scrolling div. */
  className?: string;
  /** Inner table class (e.g. for tableLayout). */
  tableClassName?: string;
  /** Max-height of the scrolling viewport (e.g. "60vh"); omit for natural height. */
  maxHeight?: string;
  /** Rendered when `table.getRowModel().rows.length === 0`. */
  emptyState?: React.ReactNode;
}

export function DataTableShell<TData>({
  table,
  stickyHeader = true,
  onRowClick,
  renderSubRow,
  getRowSelectedState,
  getRowClassName,
  getRowTitle,
  className,
  tableClassName,
  maxHeight,
  emptyState,
}: DataTableShellProps<TData>) {
  const rows = table.getRowModel().rows;
  // Live-region announcement when sort changes.  Screen readers
  // (NVDA/JAWS) read aria-sort on column headers only when the user
  // focuses that header; without a polite live region a sort change
  // driven from elsewhere (or the user staying on the row body) is
  // silent.  Reads the column's accessor key as a label fallback.
  // `table.getState().sorting` is `undefined` when the table was
  // configured without sorting state (some legacy call sites do this).
  // Coalesce to an empty array — pre-fix this null deref crashed the
  // entire Hosts page with "can't access property 'length' of undefined".
  const sorting = table.getState().sorting ?? [];
  const sortAnnouncement = React.useMemo(() => {
    if (sorting.length === 0) return 'Sort cleared';
    const first = sorting[0];
    const col = table.getColumn(first.id);
    const headerDef = col?.columnDef.header;
    const label = typeof headerDef === 'string' ? headerDef : first.id;
    return `Sorted by ${label} ${first.desc ? 'descending' : 'ascending'}`;
  }, [sorting, table]);
  return (
    <div
      className={cn('relative rounded-panel border border-border bg-card', className)}
      style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
    >
      <div role="status" aria-live="polite" className="sr-only">
        {sortAnnouncement}
      </div>
      <table className={cn('w-full caption-bottom text-metadata text-foreground', tableClassName)}>
        <thead
          className={cn(
            '[&_tr]:border-b [&_tr]:border-border',
            stickyHeader && 'sticky top-0 z-10 bg-card',
          )}
        >
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  scope="col"
                  colSpan={header.colSpan}
                  // aria-sort MUST live on the <th>, not on a child button.
                  // Screen readers ignore aria-sort placed elsewhere.
                  // See: WAI-ARIA Authoring Practices — Sortable Grid.
                  aria-sort={
                    header.column.getCanSort()
                      ? header.column.getIsSorted() === 'asc'
                        ? 'ascending'
                        : header.column.getIsSorted() === 'desc'
                          ? 'descending'
                          : 'none'
                      : undefined
                  }
                  style={{ width: header.getSize() === 150 ? undefined : header.getSize() }}
                  className={cn(
                    'h-9 px-sm text-left align-middle text-caption font-semibold uppercase tracking-wider text-muted-foreground',
                    '[&:has([role=checkbox])]:pr-0 [&:has([role=checkbox])]:w-10',
                  )}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody className="[&_tr:last-child]:border-0">
          {rows.length === 0 && emptyState ? (
            <tr>
              <td colSpan={table.getAllLeafColumns().length} className="px-sm py-xl text-center text-metadata text-muted-foreground">
                {emptyState}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              // Only call TanStack's selection helpers when the caller
              // opted into selection — otherwise getIsSelected throws on
              // tables that never registered the row-selection feature.
              const selected = getRowSelectedState ? getRowSelectedState(row) : false;
              return (
                <DataTableRow
                  key={row.id}
                  row={row}
                  selected={selected}
                  onRowClick={onRowClick}
                  renderSubRow={renderSubRow}
                  extraClassName={getRowClassName ? getRowClassName(row) : undefined}
                  rowTitle={getRowTitle ? getRowTitle(row) : undefined}
                />
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sortable header
// ---------------------------------------------------------------------------

export interface SortableHeaderProps {
  label: React.ReactNode;
  sortDirection?: 'asc' | 'desc' | false;
  onToggle?: () => void;
  className?: string;
  /** Override aria-label for the toggle button. */
  ariaLabel?: string;
}

export const SortableHeader: React.FC<SortableHeaderProps> = ({
  label,
  sortDirection,
  onToggle,
  className,
  ariaLabel,
}) => {
  // aria-sort now lives on the parent <th> in DataTableShell — putting
  // it on a <button> is silently ignored by screen readers.  The button
  // still gets an accessible name so users hear "Sort by Hostname,
  // button" instead of just an arrow icon.
  const sortStateLabel =
    sortDirection === 'asc' ? 'sorted ascending' : sortDirection === 'desc' ? 'sorted descending' : 'not sorted';
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={
        ariaLabel ??
        (typeof label === 'string' ? `Sort by ${label}, currently ${sortStateLabel}` : undefined)
      }
      className={cn(
        'inline-flex items-center gap-xxs text-caption font-semibold uppercase tracking-wider text-muted-foreground',
        'hover:text-foreground focus-visible:outline-none focus-visible:underline',
        className,
      )}
    >
      {label}
      {sortDirection === 'asc' ? (
        <ArrowUp className="size-3" aria-hidden />
      ) : sortDirection === 'desc' ? (
        <ArrowDown className="size-3" aria-hidden />
      ) : (
        <ArrowUpDown className="size-3 opacity-60" aria-hidden />
      )}
    </button>
  );
};

// ---------------------------------------------------------------------------
// Standard helper columns (selection + expander)
// ---------------------------------------------------------------------------

/**
 * Header + cell checkbox column.  Drop at the front of a `columns`
 * array when the page wants multi-row selection.
 */
export function selectionColumn<TData>(opts?: {
  ariaLabel?: (row: Row<TData>) => string;
  size?: number;
}): ColumnDef<TData, unknown> {
  return {
    id: '__select',
    size: opts?.size ?? 40,
    header: ({ table }) => (
      <Checkbox
        checked={
          table.getIsAllPageRowsSelected()
            ? true
            : table.getIsSomePageRowsSelected()
              ? 'indeterminate'
              : false
        }
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(Boolean(value))}
        aria-label="Select all rows on this page"
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        checked={row.getIsSelected()}
        disabled={!row.getCanSelect()}
        onCheckedChange={(value) => row.toggleSelected(Boolean(value))}
        onClick={(event) => event.stopPropagation()}
        aria-label={opts?.ariaLabel ? opts.ariaLabel(row) : 'Select row'}
      />
    ),
    enableSorting: false,
  };
}

// ---------------------------------------------------------------------------
// Pagination footer
// ---------------------------------------------------------------------------

export interface DataTablePaginationProps<TData> {
  table?: DataTableInstance<TData>;
  /** When `table` is omitted, the caller supplies these explicitly. */
  pageIndex?: number;
  pageSize?: number;
  totalCount?: number;
  onPageChange?: (pageIndex: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pageSizeOptions?: number[];
  /** Hides the rows-per-page picker. */
  hidePageSizeControl?: boolean;
  /** Label for the selected-rows summary on the left.  Pass `null` to omit. */
  leftLabel?: React.ReactNode | null;
  className?: string;
}

export function DataTablePagination<TData>({
  table,
  pageIndex,
  pageSize,
  totalCount,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50, 100],
  hidePageSizeControl,
  leftLabel,
  className,
}: DataTablePaginationProps<TData>) {
  const currentPage =
    pageIndex ?? table?.getState().pagination.pageIndex ?? 0;
  const currentSize =
    pageSize ?? table?.getState().pagination.pageSize ?? pageSizeOptions[0];
  const total = totalCount ?? table?.getRowCount() ?? 0;
  const pageCount = Math.max(Math.ceil(total / Math.max(currentSize, 1)), 1);
  const firstVisible = total === 0 ? 0 : currentPage * currentSize + 1;
  const lastVisible = Math.min((currentPage + 1) * currentSize, total);

  const setPage = (next: number) => {
    const clamped = Math.max(0, Math.min(next, pageCount - 1));
    if (onPageChange) {
      onPageChange(clamped);
    } else if (table) {
      table.setPageIndex(clamped);
    }
  };

  const setSize = (next: number) => {
    if (onPageSizeChange) {
      onPageSizeChange(next);
    } else if (table) {
      table.setPageSize(next);
    }
  };

  // Audit PRF·L4: `getFilteredSelectedRowModel()` walks every row.
  // Only call it when the caller actually opted into row selection —
  // otherwise this fired on every pagination render for tables that
  // never selected a row in their life.
  const selectedCount = table?.options.enableRowSelection
    ? table.getFilteredSelectedRowModel().rows.length
    : undefined;
  const summaryLeft =
    leftLabel !== undefined
      ? leftLabel
      : selectedCount !== undefined && selectedCount > 0
        ? `${selectedCount} of ${total} selected`
        : `${firstVisible}–${lastVisible} of ${total}`;

  return (
    <div
      className={cn(
        'flex flex-col gap-xs px-xs py-xs sm:flex-row sm:items-center sm:justify-between',
        className,
      )}
    >
      <div className="text-caption text-muted-foreground">{summaryLeft}</div>
      <div className="flex flex-wrap items-center gap-md">
        {!hidePageSizeControl && (
          <div className="flex items-center gap-xs">
            <label className="text-caption text-muted-foreground" htmlFor="dt-page-size">
              Rows
            </label>
            <Select
              value={String(currentSize)}
              onValueChange={(value) => setSize(Number(value))}
            >
              <SelectTrigger id="dt-page-size" className="w-[5.5rem]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {pageSizeOptions.map((opt) => (
                  <SelectItem key={opt} value={String(opt)}>
                    {opt}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="flex items-center gap-xxs">
          <span className="text-caption text-muted-foreground">
            Page {currentPage + 1} of {pageCount}
          </span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="First page"
            disabled={currentPage === 0}
            onClick={() => setPage(0)}
          >
            <ChevronsLeft className="size-4" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Previous page"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            <ChevronLeft className="size-4" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Next page"
            disabled={currentPage >= pageCount - 1}
            onClick={() => setPage(currentPage + 1)}
          >
            <ChevronRight className="size-4" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Last page"
            disabled={currentPage >= pageCount - 1}
            onClick={() => setPage(pageCount - 1)}
          >
            <ChevronsRight className="size-4" aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}
