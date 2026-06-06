import React from 'react';
import { cn } from '../utils/cn';

/**
 * Shell-preserving loading skeletons.
 *
 * The UX audit (#7) flagged that most pages replace their entire
 * content with a full-page spinner or bare "Loading…" text while
 * data is in flight.  This wipes out page chrome, erases the user's
 * sense of where they are, and makes perceived latency worse even
 * when actual latency is fine.
 *
 * These primitives render the same *shape* as the final page so the
 * user sees a stable layout that fills in instead of a flash of blank
 * then a completely new layout.  Callers pick the variant that matches
 * their page: ``TableSkeleton`` for list-style pages (Hosts, Scans),
 * ``CardListSkeleton`` for card grids (Test Plans, LLM Providers),
 * ``DetailSkeleton`` for detail pages with a header + content block
 * (TestPlanDetail, HostDetail).
 *
 * All variants use Tailwind's ``animate-pulse`` for a soft fade — no
 * additional animations because animated skeletons in long lists can
 * induce motion sickness.
 */

const SkeletonBlock: React.FC<{
  width?: string | number;
  height?: string | number;
  rounded?: 'sm' | 'control' | 'panel' | 'full';
  className?: string;
}> = ({ width = '100%', height = 16, rounded = 'sm', className }) => (
  <div
    aria-hidden
    style={{
      width: typeof width === 'number' ? `${width}px` : width,
      height: typeof height === 'number' ? `${height}px` : height,
    }}
    className={cn(
      'brand-skeleton animate-pulse bg-muted',
      rounded === 'sm' && 'rounded-sm',
      rounded === 'control' && 'rounded-control',
      rounded === 'panel' && 'rounded-panel',
      rounded === 'full' && 'rounded-full',
      className,
    )}
  />
);

interface TableSkeletonProps {
  rows?: number;
  columns?: number;
  headerRows?: number;
}

export const TableSkeleton: React.FC<TableSkeletonProps> = ({
  rows = 8,
  columns = 5,
  headerRows = 1,
}) => (
  <div
    role="status"
    aria-busy="true"
    aria-live="polite"
    className="overflow-hidden rounded-panel border border-border bg-card"
  >
    <span className="sr-only">Loading table…</span>
    <table className="w-full text-metadata text-foreground">
      <thead className="[&_tr]:border-b [&_tr]:border-border">
        {Array.from({ length: headerRows }).map((_, hi) => (
          <tr key={`h-${hi}`}>
            {Array.from({ length: columns }).map((_, ci) => (
              <th key={ci} className="h-9 px-sm text-left align-middle">
                <SkeletonBlock width={ci === 0 ? '70%' : '50%'} />
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody className="[&_tr:last-child]:border-0">
        {Array.from({ length: rows }).map((_, ri) => (
          <tr key={ri} className="border-b border-border">
            {Array.from({ length: columns }).map((_, ci) => (
              <td key={ci} className="px-sm py-xs align-top">
                <SkeletonBlock
                  width={ci === 0 ? '85%' : ci === columns - 1 ? '40%' : '60%'}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

interface CardListSkeletonProps {
  /** Number of card placeholders to render. */
  count?: number;
  /** Approximate pixel height of each card — tune per page. */
  cardHeight?: number;
}

export const CardListSkeleton: React.FC<CardListSkeletonProps> = ({
  count = 6,
  cardHeight = 128,
}) => (
  <div role="status" aria-busy="true" aria-live="polite" className="space-y-sm">
    <span className="sr-only">Loading list…</span>
    {Array.from({ length: count }).map((_, i) => (
      <SkeletonBlock key={i} width="100%" height={cardHeight} rounded="panel" />
    ))}
  </div>
);

/**
 * Detail page skeleton: back button + title + meta + content block.
 * Used by TestPlanDetail / HostDetail while their initial load is in
 * flight.
 */
export const DetailSkeleton: React.FC = () => (
  <div role="status" aria-busy="true" aria-live="polite" className="p-md sm:p-md md:p-lg">
    <span className="sr-only">Loading…</span>
    <div className="mb-sm flex items-center gap-xs">
      <SkeletonBlock width={32} height={32} rounded="full" />
      <SkeletonBlock width="40%" height={32} />
    </div>
    <SkeletonBlock width="60%" height={20} className="mb-sm" />
    <div className="mb-md flex gap-xs">
      <SkeletonBlock width={90} height={28} rounded="control" />
      <SkeletonBlock width={120} height={28} rounded="control" />
      <SkeletonBlock width={80} height={28} rounded="control" />
    </div>
    <SkeletonBlock width="100%" height={120} rounded="panel" className="mb-sm" />
    <SkeletonBlock width="100%" height={320} rounded="panel" />
  </div>
);

/**
 * Convenience wrapper — renders a heading row then a table skeleton.
 * Matches list-page shells like Scans and Hosts where the heading +
 * actions row should stay visible during loading.
 */
interface ListPageSkeletonProps {
  titleWidth?: number | string;
  actionCount?: number;
  tableProps?: TableSkeletonProps;
}

export const ListPageSkeleton: React.FC<ListPageSkeletonProps> = ({
  titleWidth = 220,
  actionCount = 2,
  tableProps,
}) => (
  <div role="status" aria-busy="true" aria-live="polite">
    <span className="sr-only">Loading…</span>
    <div className="mb-sm flex flex-col gap-xs sm:flex-row sm:items-center sm:justify-between">
      <SkeletonBlock width={titleWidth} height={40} />
      <div className="flex gap-xs">
        {Array.from({ length: actionCount }).map((_, i) => (
          <SkeletonBlock key={i} width={120} height={32} rounded="control" />
        ))}
      </div>
    </div>
    <TableSkeleton {...tableProps} />
  </div>
);
