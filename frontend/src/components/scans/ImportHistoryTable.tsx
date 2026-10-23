import React, { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronUp, Loader2, MoreHorizontal, Trash2,
} from 'lucide-react';

import { getScanCommandExplanation } from '../../services/api';
import type { IngestionJob, Scan } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { rememberFor } from '../../lib/query';
import { cn } from '../../utils/cn';
import type { HistoryRow } from '../../utils/importHistory';
import { Alert, AlertDescription } from '../ui/alert';
import { Badge } from '../ui/badge';
import { BreakableName } from '../ui/breakable-name';
import { Button } from '../ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { ScanBatchRow } from './ScanBatchList';
import ScanContribution from './ScanContribution';
import { ROW_LINK_CLASS, ScanRowActions } from './ScanRowActions';
import { SCAN_TIME_HEADING, ScanWhenCell, ViewerZoneHint, type ScanTimeColumn } from './ScanTimeCells';

export type ScansSortBy = 'created_at' | 'start_time' | 'filename' | 'tool_name' | 'total_hosts' | 'new_hosts';
export type ScansSortOrder = 'asc' | 'desc';
export const SCANS_SORT_COLUMNS: readonly ScansSortBy[] = [
  'created_at', 'start_time', 'filename', 'tool_name', 'total_hosts', 'new_hosts',
];

interface ImportHistoryTableProps {
  /** Upload batches and single files, in the order they are listed. */
  rows: HistoryRow[];
  /** The all-files view: its headers sort. */
  sortable: boolean;
  timeColumn: ScanTimeColumn;
  sortBy: ScansSortBy;
  sortOrder: ScansSortOrder;
  onSort: (column: ScansSortBy) => void;
  /** The tool filter, which a row's tool badge sets and clears. */
  toolFilter: string;
  onToolFilter: (tool: string) => void;
  /** What a batch row needs. */
  stagedByBatch: Map<number, IngestionJob[]>;
  onReviewStaged?: (jobs: IngestionJob[]) => void;
  filters: React.ComponentProps<typeof ScanBatchRow>['filters'];
  onViewScan: (scanId: number) => void;
  /** Deleting a scan is the project admin's (absent: not offered). */
  onDelete?: (scan: Scan) => void;
}

/** The import history's table: batch rows and file rows, in the order given. */
const ImportHistoryTable: React.FC<ImportHistoryTableProps> = ({
  rows, sortable, timeColumn, sortBy, sortOrder, onSort, toolFilter, onToolFilter,
  stagedByBatch, onReviewStaged, filters, onViewScan, onDelete,
}) => {
  // The command's explanation is read by the expanded row itself
  // (`ScanCommandDetail`), the first time it opens.
  const [expandedScanIds, setExpandedScanIds] = useState<number[]>([]);
  const toggleScanExpanded = useCallback((scanId: number) => {
    setExpandedScanIds((prev) => (prev.includes(scanId) ? prev.filter((id) => id !== scanId) : [...prev, scanId]));
  }, []);
  // Grouped by upload is chronological by definition (the server orders
  // batches and single files together), so the headers sort only in the
  // all-files view.
  const header = (column: ScansSortBy, label: string, className?: string) => (sortable
    ? <SortHeader column={column} label={label} className={className} sortBy={sortBy} sortOrder={sortOrder} onSort={onSort} />
    : <TableHead className={className}>{label}</TableHead>);

  return (
    <div className="overflow-x-auto">
      <Table style={{ tableLayout: 'fixed' }}>
        <TableHeader>
          <TableRow>
            {/* Batch rows fill these same five columns. */}
            {header('filename', 'Scan', 'w-[24%]')}
            {/* ONE time column, named for the time it shows on every row: the
                upload time — what this history is ordered by — with the
                scan's own time on a second line.  In the all-files view it
                sorts by either, and sorted by when the scans ran it IS that
                time ("Ran"). */}
            {sortable ? (
              <TableHead className="w-[18%]">
                <span className="inline-flex flex-wrap items-center gap-x-xs">
                  {SCAN_TIME_HEADING[timeColumn]}
                  <ViewerZoneHint />
                  <span className="text-caption font-normal normal-case text-muted-foreground">sort by</span>
                  {(['start_time', 'created_at'] as const).map((col) => {
                    const label = col === 'start_time' ? 'Ran' : 'Uploaded';
                    const sorted = sortBy === col;
                    return (
                      <button
                        key={col}
                        type="button"
                        onClick={() => onSort(col)}
                        aria-label={`Sort by ${label}, currently ${
                          sorted ? (sortOrder === 'asc' ? 'sorted ascending' : 'sorted descending') : 'not sorted'
                        }`}
                        className={cn(
                          'inline-flex items-center gap-xxs rounded-control text-caption normal-case focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                          sorted ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
                        )}
                      >
                        {label}
                        {sorted && (sortOrder === 'asc'
                          ? <ArrowUp className="size-3" aria-hidden />
                          : <ArrowDown className="size-3" aria-hidden />)}
                      </button>
                    );
                  })}
                </span>
              </TableHead>
            ) : (
              // The zone and its explanation ride on the heading (U16): they
              // were a second permanent helper line above the table.
              <TableHead className="w-[18%]">
                <span className="inline-flex flex-wrap items-center gap-x-xs">
                  {SCAN_TIME_HEADING[timeColumn]}
                  <ViewerZoneHint />
                </span>
              </TableHead>
            )}
            {header('new_hosts', 'New hosts', 'w-[10%]')}
            <TableHead
              className="w-[39%]"
              title="What this scan added or observed, counted from the rows it wrote. Hover a line for what it counts."
            >
              What it contributed
            </TableHead>
            <TableHead className="w-[9%]"><span className="sr-only">Actions</span></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (row.kind === 'batch' ? (
            <ScanBatchRow
              key={row.key}
              batch={row.batch}
              stagedJobs={stagedByBatch.get(row.batch.id)}
              onReviewStaged={onReviewStaged}
              filters={filters}
              onViewScan={onViewScan}
              colSpan={5}
            />
          ) : (
            <ScanFileRow
              key={row.scan.id}
              scan={row.scan}
              expanded={expandedScanIds.includes(row.scan.id)}
              onToggle={toggleScanExpanded}
              timeColumn={timeColumn}
              toolFilter={toolFilter}
              onToolFilter={onToolFilter}
              onViewScan={onViewScan}
              onDelete={onDelete}
            />
          )))}
        </TableBody>
      </Table>
    </div>
  );
};

/** A sortable column header: the same column again toggles asc / desc. */
const SortHeader: React.FC<{
  column: ScansSortBy;
  label: string;
  className?: string;
  sortBy: ScansSortBy;
  sortOrder: ScansSortOrder;
  onSort: (column: ScansSortBy) => void;
}> = ({ column, label, className, sortBy, sortOrder, onSort }) => {
  const isSorted = sortBy === column;
  const ariaSort: React.AriaAttributes['aria-sort'] = isSorted
    ? sortOrder === 'asc'
      ? 'ascending'
      : 'descending'
    : 'none';
  return (
    <TableHead className={className} aria-sort={ariaSort}>
      <button
        type="button"
        onClick={() => onSort(column)}
        className="inline-flex items-center gap-xxs rounded-control text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Sort by ${label}, currently ${
          isSorted ? (sortOrder === 'asc' ? 'sorted ascending' : 'sorted descending') : 'not sorted'
        }`}
      >
        {label}
        {!isSorted && <ArrowUpDown className="size-3 opacity-50" aria-hidden />}
        {isSorted && sortOrder === 'asc' && <ArrowUp className="size-3" aria-hidden />}
        {isSorted && sortOrder === 'desc' && <ArrowDown className="size-3" aria-hidden />}
      </button>
    </TableHead>
  );
};

/** One imported file, and its command when expanded. */
const ScanFileRow: React.FC<{
  scan: Scan;
  expanded: boolean;
  onToggle: (scanId: number) => void;
  timeColumn: ScanTimeColumn;
  toolFilter: string;
  onToolFilter: (tool: string) => void;
  onViewScan: (scanId: number) => void;
  onDelete?: (scan: Scan) => void;
}> = ({ scan, expanded: isExpanded, onToggle, timeColumn, toolFilter, onToolFilter, onViewScan, onDelete }) => {
  const hasCommand = !!(scan.command_line && scan.command_line.trim());
  // The tool badge is a filter, as the tool select is.  stopPropagation so it
  // does not also trigger the row's own handlers.
  const toolLabel = scan.tool_name || scan.scan_type || 'Unknown';
  const toolKey = (toolLabel || 'Other').toUpperCase();
  const toolActive = toolFilter.toLowerCase() === toolKey.toLowerCase();
  return (
    <>
      <TableRow className="align-top">
        <TableCell>
          <div className="mb-xxs flex items-center gap-xxs">
            {hasCommand && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="-ml-xxs"
                    onClick={() => onToggle(scan.id)}
                    aria-label={isExpanded ? 'Hide command' : 'Show command'}
                    aria-expanded={isExpanded}
                  >
                    {isExpanded ? (
                      <ChevronUp className="size-4" aria-hidden />
                    ) : (
                      <ChevronDown className="size-4" aria-hidden />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {isExpanded ? 'Hide command' : 'Show command'}
                </TooltipContent>
              </Tooltip>
            )}
            {/* min-w-0 lets the filename shrink + wrap inside the flex row;
                without it a long name overflows into the next column. */}
            <Link
              to={`/scans/${scan.id}`}
              className="min-w-0 rounded font-semibold text-foreground hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <BreakableName name={scan.filename} />
            </Link>
          </div>
          <div className="flex flex-wrap items-center gap-xxs">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onToolFilter(toolActive ? '' : toolKey);
              }}
              aria-pressed={toolActive}
              className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              title={toolActive ? `Clear ${toolKey} filter` : `Show only ${toolKey} scans`}
            >
              <Badge variant={toolActive ? 'default' : 'outline'}>{toolLabel}</Badge>
            </button>
            {scan.version && <Badge variant="outline">v{scan.version}</Badge>}
            {/* The host-query DSL's `scan:` predicate takes the numeric id;
                operators know the upload by its filename. This is where the
                two meet. */}
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="shrink-0 cursor-default font-mono text-caption text-muted-foreground">
                  #{scan.id}
                </span>
              </TooltipTrigger>
              <TooltipContent>
                Scan id — filter hosts with <code>scan:{scan.id}</code>
              </TooltipContent>
            </Tooltip>
          </div>
        </TableCell>
        <TableCell className="min-w-0">
          <ScanWhenCell scan={scan} primary={timeColumn} />
        </TableCell>
        {/* What the scan INTRODUCED, out of what it saw: hosts first
            discovered here, over every host it observed (new + already known). */}
        <TableCell title="Hosts this scan added to the inventory, out of all the hosts it observed">
          {scan.total_hosts === 0 ? (
            <span className="text-caption text-muted-foreground">No hosts</span>
          ) : (
            <>
              {scan.new_hosts > 0 ? (
                <span className="tabular-nums font-semibold text-success">+{scan.new_hosts.toLocaleString()}</span>
              ) : (
                <span className="tabular-nums text-muted-foreground">0</span>
              )}
              <p className="mt-xxs text-caption tabular-nums text-muted-foreground">
                of {scan.total_hosts.toLocaleString()} seen
              </p>
            </>
          )}
        </TableCell>
        {/* Per-kind contribution (ports, findings, web interfaces, names,
            auth), ordered by what the tool is for. */}
        <TableCell className="min-w-0">
          <ScanContribution scan={scan} />
        </TableCell>
        <TableCell>
          {/* The filename opens the scan; "Hosts" is a quiet link; delete
              lives in the row menu, out of reach of a stray click. */}
          <ScanRowActions
            link={scan.total_hosts > 0 ? (
              <Link
                to={`/hosts?scan_ids=${scan.id}`}
                className={ROW_LINK_CLASS}
                title="Open the Hosts page filtered to this scan"
              >
                Hosts
              </Link>
            ) : null}
            menu={
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground"
                  aria-label={`More actions for ${scan.filename || `scan ${scan.id}`}`}
                >
                  <MoreHorizontal className="size-4" aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => onViewScan(scan.id)}>
                  Open scan
                </DropdownMenuItem>
                {onDelete && (
                  <DropdownMenuItem
                    onSelect={() => onDelete(scan)}
                    className="text-destructive focus:text-destructive"
                  >
                    <Trash2 className="size-3.5" aria-hidden /> Delete scan…
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
            }
          />
        </TableCell>
      </TableRow>
      {hasCommand && isExpanded && (
        <TableRow>
          <TableCell colSpan={5} className="py-sm">
            <ScanCommandDetail scan={scan} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
};

/**
 * A file row's expanded detail: the command that produced the scan and what
 * its arguments mean.  The explanation is read the first time the row opens
 * and kept while the page is in use (a command does not change); a read that
 * failed is asked again the next time the row opens.
 */
export const ScanCommandDetail: React.FC<{ scan: Scan }> = ({ scan }) => {
  const hasCommand = !!(scan.command_line && scan.command_line.trim());
  const projectId = useProjectId();
  const explained = useQuery({
    queryKey: ['getScanCommandExplanation', projectId, scan.id],
    queryFn: ({ signal }) => getScanCommandExplanation(projectId, scan.id, signal),
    enabled: hasCommand,
    ...rememberFor(30 * 60_000),
    // This query's own lifecycle: the answer is remembered for the visit, so a
    // remembered FAILURE would be too.  Opening the row again asks again (the
    // shared default is not to, so one component's failure is not hidden by
    // another's mount — here there is one reader, and reopening is the retry).
    retryOnMount: true,
  });
  const explanation = explained.data;

  if (!hasCommand) {
    return (
      <div className="rounded-control bg-accent px-md py-sm text-metadata text-muted-foreground">
        No command line data available for this scan.
        {scan.tool_name && !['nmap', 'masscan'].includes((scan.tool_name || '').toLowerCase()) && (
          <> {scan.tool_name} output does not include producing configuration.</>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-sm rounded-control bg-accent px-md py-sm">
      <div>
        <p className="mb-xxs text-caption font-semibold text-muted-foreground">Command</p>
        <div className="break-words rounded-control border border-border bg-card px-sm py-xs font-mono text-caption">
          {scan.command_line}
        </div>
      </div>
      {(scan.version || scan.tool_name || scan.uploaded_by) && (
        <div className="flex flex-wrap gap-md text-caption text-muted-foreground">
          {scan.version && (
            <span>
              <strong>Version:</strong> {scan.version}
            </span>
          )}
          {scan.tool_name && (
            <span>
              <strong>Tool:</strong> {scan.tool_name}
            </span>
          )}
          {scan.uploaded_by && (
            <span>
              <strong>Uploaded by:</strong> {scan.uploaded_by_name || scan.uploaded_by}
            </span>
          )}
        </div>
      )}
      {!explanation && explained.isError && !explained.isFetching && (
        // Failed is not loading (code review 2026-10-09: the spinner stayed for ever).
        <p role="alert" className="text-caption text-destructive">
          Couldn&rsquo;t load the argument analysis.{' '}
          <button type="button" className="text-info hover:underline" onClick={() => void explained.refetch()}>
            Retry
          </button>
        </p>
      )}
      {!explanation && (!explained.isError || explained.isFetching) && (
        <div className="flex items-center gap-xs text-caption text-muted-foreground">
          <Loader2 className="size-3 animate-spin" aria-hidden />
          Loading argument analysis…
        </div>
      )}
      {explanation?.has_command && explanation.arguments && explanation.arguments.length > 0 && (
        <div>
          <p className="mb-xxs text-caption font-semibold text-muted-foreground">
            Arguments ({explanation.arguments.length})
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/5">Flag</TableHead>
                <TableHead className="w-1/6">Category</TableHead>
                <TableHead>Description</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {explanation.arguments.map((arg, idx) => (
                <TableRow key={idx}>
                  <TableCell className="truncate font-mono">{arg.arg}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{arg.category}</Badge>
                  </TableCell>
                  <TableCell className="truncate">{arg.description}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {explanation?.summary && (
        <Alert variant="info">
          <AlertDescription>{explanation.summary}</AlertDescription>
        </Alert>
      )}
    </div>
  );
};

export default ImportHistoryTable;
