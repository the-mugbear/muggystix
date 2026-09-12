import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { getScans } from '../../services/api';
import type { Scan, ScanBatchSummary } from '../../services/api';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

/** Most recent batches the Scans page loads in one request. */
export const SCAN_BATCH_LIMIT = 200;

// A batch's files load when it is expanded. A sweep of a few hundred files
// fits one request; past that the operator narrows with the page's search.
const FILES_PER_BATCH = 500;

interface ScanBatchListProps {
  batches: ScanBatchSummary[];
  /** The page's filters — an expanded batch lists only its matching files. */
  filters: { search?: string; tool?: string; createdAfter?: string };
  onViewScan: (scanId: number) => void;
  className?: string;
}

const formatWhen = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

/**
 * Upload batches as one row each (v5.207.0). An agent splitting a large scope
 * into hundreds of chunks, or an operator dropping many files at once, used to
 * produce hundreds of flat rows; each row here is one sweep, expandable to its
 * files.
 */
export default function ScanBatchList({ batches, filters, onViewScan, className }: ScanBatchListProps) {
  const [open, setOpen] = useState<Record<number, Scan[] | 'loading' | 'error'>>({});

  const toggle = async (batchId: number) => {
    if (open[batchId]) {
      setOpen(({ [batchId]: _closed, ...rest }) => rest);
      return;
    }
    setOpen((prev) => ({ ...prev, [batchId]: 'loading' }));
    try {
      const files = await getScans(0, FILES_PER_BATCH, {
        ...filters,
        batchId,
        sortBy: 'filename',
        sortOrder: 'asc',
      });
      // Collapsed while loading → stay collapsed.
      setOpen((prev) => (prev[batchId] ? { ...prev, [batchId]: files } : prev));
    } catch (err) {
      console.error('Error loading batch files:', err);
      setOpen((prev) => (prev[batchId] ? { ...prev, [batchId]: 'error' } : prev));
    }
  };

  return (
    <Card className={className}>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[34%]">Upload batch</TableHead>
                <TableHead className="w-[12%]">Files</TableHead>
                <TableHead className="w-[14%]" title="Distinct hosts the files observed, and how many they first discovered">
                  Hosts
                </TableHead>
                <TableHead className="w-[10%]" title="Open-port observations across the files">
                  Open ports
                </TableHead>
                <TableHead className="w-[18%]">Uploaded</TableHead>
                <TableHead className="w-[12%]">
                  <span className="sr-only">Files in batch</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {batches.map((b) => {
                const state = open[b.id];
                const source =
                  b.recon_session_id != null
                    ? `Recon session #${b.recon_session_id}`
                    : b.created_by
                      ? `Uploaded by ${b.created_by}`
                      : 'Upload';
                return (
                  <React.Fragment key={b.id}>
                    <TableRow className="align-top">
                      <TableCell>
                        <div className="min-w-0">
                          <p className="truncate font-semibold" title={b.label}>
                            {b.label}
                          </p>
                          <p className="truncate text-caption text-muted-foreground">{source}</p>
                          {b.tools.length > 0 && (
                            <div className="mt-xxs flex flex-wrap gap-xxs">
                              {b.tools.map((t) => (
                                <Badge key={t} variant="outline" className="max-w-full truncate">
                                  {t}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <p className="tabular-nums">{b.files.toLocaleString()}</p>
                        {b.pending_files > 0 && (
                          <p className="text-caption text-muted-foreground">{b.pending_files} still parsing</p>
                        )}
                        {b.failed_files > 0 && (
                          <p className="text-caption text-destructive">{b.failed_files} failed</p>
                        )}
                      </TableCell>
                      <TableCell>
                        <p className="tabular-nums">{b.hosts.toLocaleString()}</p>
                        <p className="text-caption text-muted-foreground">+{b.new_hosts.toLocaleString()} new</p>
                      </TableCell>
                      <TableCell className="tabular-nums">{b.open_ports.toLocaleString()}</TableCell>
                      <TableCell className="text-caption">
                        <p>{formatWhen(b.last_uploaded)}</p>
                        {b.first_uploaded && b.first_uploaded !== b.last_uploaded && (
                          <p className="text-muted-foreground">from {formatWhen(b.first_uploaded)}</p>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toggle(b.id)}
                          aria-expanded={!!state}
                          aria-label={`${state ? 'Hide' : 'Show'} the files of ${b.label}`}
                        >
                          {state === 'loading' ? (
                            <Loader2 className="size-4 animate-spin" aria-hidden />
                          ) : state ? (
                            <ChevronDown className="size-4" aria-hidden />
                          ) : (
                            <ChevronRight className="size-4" aria-hidden />
                          )}
                          Files
                        </Button>
                      </TableCell>
                    </TableRow>
                    {state && state !== 'loading' && (
                      <TableRow>
                        <TableCell colSpan={6} className="bg-muted/30 p-sm">
                          {state === 'error' ? (
                            <p className="text-metadata text-destructive">
                              Couldn&apos;t load this batch&apos;s files. Collapse it and try again.
                            </p>
                          ) : state.length === 0 ? (
                            <p className="text-metadata text-muted-foreground">
                              No files in this batch match the current filters.
                            </p>
                          ) : (
                            <ul className="divide-y divide-border">
                              {state.map((s) => (
                                <li key={s.id} className="flex min-w-0 items-center gap-sm py-xxs text-metadata">
                                  <button
                                    type="button"
                                    onClick={() => onViewScan(s.id)}
                                    className="min-w-0 flex-1 truncate text-left font-mono text-primary hover:underline focus:outline-none focus-visible:underline"
                                    title={s.filename}
                                  >
                                    {s.filename}
                                  </button>
                                  <span className="w-24 shrink-0 truncate text-muted-foreground">
                                    {s.tool_name || s.scan_type || '—'}
                                  </span>
                                  <span className="w-36 shrink-0 text-right tabular-nums">
                                    {s.total_hosts.toLocaleString()} hosts · +{s.new_hosts.toLocaleString()}
                                  </span>
                                  <span className="w-24 shrink-0 text-right tabular-nums">
                                    {s.open_ports.toLocaleString()} open
                                  </span>
                                </li>
                              ))}
                              {state.length === FILES_PER_BATCH && (
                                <li className="py-xxs text-caption text-muted-foreground">
                                  Showing the first {FILES_PER_BATCH} files by name — use the search above to find
                                  a specific one.
                                </li>
                              )}
                            </ul>
                          )}
                        </TableCell>
                      </TableRow>
                    )}
                  </React.Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
        {batches.length >= SCAN_BATCH_LIMIT && (
          <p className="p-sm text-caption text-muted-foreground">
            Showing the {SCAN_BATCH_LIMIT} most recently uploaded batches — narrow with the filters above.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
