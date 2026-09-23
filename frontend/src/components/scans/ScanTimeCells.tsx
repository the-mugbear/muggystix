/**
 * /scans time cells (v5.205.0). Every time on the page says where it came
 * from and how it relates to the viewer's clock; the logic lives in
 * utils/scanTime.ts so ScanDetail renders the same wording.
 */
import React from 'react';

import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { cn } from '../../utils/cn';
import {
  describeScanRun,
  describeUpload,
  viewerTimeZone,
  type ScanTimeFields,
  type TimeFormatOptions,
} from '../../utils/scanTime';

interface SourceNoteProps {
  label: string;
  explanation: string;
  utc?: string | null;
  /** A wall clock of unknown zone — draw attention, it may be hours off. */
  caution?: boolean;
  className?: string;
}

/** The provenance line under a time, with the full explanation on hover/focus. */
export const ScanTimeSourceNote: React.FC<SourceNoteProps> = ({ label, explanation, utc, caution, className }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button
        type="button"
        className={cn(
          'block max-w-full truncate rounded-control text-left text-caption underline decoration-dotted underline-offset-2',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          caution ? 'text-warning' : 'text-muted-foreground',
          className,
        )}
      >
        {label}
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs">
      <p>{explanation}</p>
      {utc && <p className="mt-xxs font-mono text-caption">{utc}</p>}
    </TooltipContent>
  </Tooltip>
);

interface CellProps {
  scan: ScanTimeFields & { uploaded_by?: string | null };
  format?: TimeFormatOptions;
}

/**
 * When a scan happened, in ONE column (v5.270.0): the time it ran, per its own
 * output, else the time it was uploaded.  The other time, the duration and
 * where the time came from ("Reported by nmap") are on hover.  A scanner clock
 * of unknown zone stays visibly flagged — it may be hours off.
 */
export const ScanWhenCell: React.FC<CellProps> = ({ scan, format }) => {
  const run = describeScanRun(scan, format);
  const upload = describeUpload(scan, format);
  const hover = [
    run.startLabel ? `Ran ${run.startLabel}` : 'Run time: not in the file',
    [run.durationLabel, run.uploadLagLabel].filter(Boolean).join(' · ') || null,
    `${run.sourceLabel} — ${run.explanation}`,
    run.utcLabel,
    `Uploaded ${upload.label}${scan.uploaded_by ? ` by ${scan.uploaded_by}` : ''}`,
  ].filter(Boolean).join('\n');
  return (
    <div className="min-w-0" title={hover}>
      <p className="truncate text-metadata tabular-nums">{run.startLabel ?? upload.label}</p>
      {run.startLabel ? (
        run.kind === 'tool_clock' ? (
          <p className="truncate text-caption text-warning">{run.sourceLabel}</p>
        ) : (
          run.durationLabel && run.durationLabel !== 'Instant' && (
            <p className="truncate text-caption text-muted-foreground">took {run.durationLabel}</p>
          )
        )
      ) : (
        <p className="truncate text-caption text-muted-foreground">uploaded · run time unknown</p>
      )}
    </div>
  );
};

/** One line above the table: which zone converted times are in. */
export const ViewerZoneNote: React.FC<{ format?: TimeFormatOptions; className?: string }> = ({ format, className }) => {
  const zone = viewerTimeZone(format);
  const detail = [zone.abbr, zone.offset].filter(Boolean).join(', ');
  return (
    <p className={cn('text-caption text-muted-foreground', className)}>
      Times are shown in your time zone, {zone.name}
      {detail ? ` (${detail})` : ''}. A scanner clock with no recorded zone is shown as written. Hover a
      scan&apos;s time to see where it came from.
    </p>
  );
};
