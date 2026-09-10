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

/** When the scan ran, per its own output — or a plain statement that it doesn't say. */
export const ScanRunCell: React.FC<CellProps> = ({ scan, format }) => {
  const run = describeScanRun(scan, format);
  const detail = [run.durationLabel, run.uploadLagLabel].filter(Boolean).join(' · ');
  return (
    <div className="min-w-0">
      {run.startLabel ? (
        <p className="text-metadata tabular-nums">{run.startLabel}</p>
      ) : (
        <p className="text-metadata text-muted-foreground">Not recorded</p>
      )}
      {detail && <p className="mt-xxs text-caption text-muted-foreground">{detail}</p>}
      <ScanTimeSourceNote
        className="mt-xxs"
        label={run.sourceLabel}
        explanation={run.explanation}
        utc={run.utcLabel}
        caution={run.kind === 'tool_clock'}
      />
    </div>
  );
};

/** When the file reached BlueStick (server clock) and who uploaded it. */
export const ScanUploadedCell: React.FC<CellProps> = ({ scan, format }) => {
  const upload = describeUpload(scan, format);
  return (
    <div className="min-w-0">
      <p className="text-metadata tabular-nums" title={upload.explanation}>
        {upload.label}
      </p>
      {scan.uploaded_by && (
        <p className="mt-xxs truncate text-caption text-muted-foreground" title={scan.uploaded_by}>
          by {scan.uploaded_by}
        </p>
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
