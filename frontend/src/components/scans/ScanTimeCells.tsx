/**
 * /scans time cells (v5.205.0). Every time on the page says where it came
 * from and how it relates to the viewer's clock; the logic lives in
 * utils/scanTime.ts so ScanDetail renders the same wording.
 */
import React from 'react';

import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { InfoTip } from '../ui/info-tip';
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
  scan: ScanTimeFields & { uploaded_by?: string | null; uploaded_by_name?: string | null };
  format?: TimeFormatOptions;
  /** Which time the column IS — the one its heading names and the list is
   *  ordered by.  `uploaded` (the default; the import history's own order) or
   *  `ran` (the all-files view sorted by when the scan ran). */
  primary?: ScanTimeColumn;
}

/** The two times a scan has; the column shows one and says which. */
export type ScanTimeColumn = 'uploaded' | 'ran';

/** The time column's heading, for the time it shows. */
export const SCAN_TIME_HEADING: Record<ScanTimeColumn, string> = { uploaded: 'Uploaded', ran: 'Ran' };

/**
 * A scan's time column.  It shows ONE thing on every row — the time its
 * heading names — with the scan's other time on a labelled second line
 * ("ran …" under an upload time, "uploaded …" under a run time).
 *
 * Until 5.383.0 the column was headed "When" and showed the scan's own start
 * time where the file recorded one and the upload time elsewhere, under a list
 * ordered by upload: October 2025 rows sat between October 2026 ones.
 *
 * Where each time came from ("Reported by nmap"), the duration and the UTC
 * window are on hover.  A scanner clock of unknown zone stays visibly flagged
 * — it may be hours off.  Dates here are provenance: nothing is judged old.
 */
export const ScanWhenCell: React.FC<CellProps> = ({ scan, format, primary = 'uploaded' }) => {
  // The hover keeps the zone; the cell drops it — the page names the viewer's
  // zone once, on the column's heading (ViewerZoneHint).
  const run = describeScanRun(scan, format);
  const upload = describeUpload(scan, format);
  const shown = describeScanRun(scan, { ...format, withZone: false });
  const shownUpload = describeUpload(scan, { ...format, withZone: false });
  const uploader = scan.uploaded_by_name || scan.uploaded_by;
  const hover = [
    run.startLabel ? `Ran ${run.startLabel}` : `Run time: not in the file — ${run.explanation}`,
    [run.durationLabel, run.uploadLagLabel].filter(Boolean).join(' · ') || null,
    run.startLabel ? `${run.sourceLabel} — ${run.explanation}` : null,
    run.utcLabel,
    `Uploaded ${upload.label}${uploader ? ` by ${uploader}` : ''}`,
  ].filter(Boolean).join('\n');
  // UX review 2026-09-24 — one time line, no zone per row (it wrapped the
  // column to four lines); a second line only when it says something ("run
  // time unknown" on every row was noise — the hover explains it).
  // v5.287.0 still holds: the lines wrap rather than truncate (a cut-off time
  // is worse than a second line), but without the zone they rarely need to.
  const toolClock = run.kind === 'tool_clock';
  const took = run.durationLabel && run.durationLabel !== 'Instant' ? `took ${run.durationLabel}` : null;
  if (primary === 'ran') {
    return (
      <div className="min-w-0" title={hover}>
        <p className="break-words text-metadata tabular-nums">
          {shown.startLabel ?? <span className="text-muted-foreground">—</span>}
        </p>
        {toolClock && <p className="break-words text-caption text-warning">{run.sourceLabel}</p>}
        <p className="break-words text-caption tabular-nums text-muted-foreground">uploaded {shownUpload.label}</p>
      </div>
    );
  }
  // The scan's own time, where the file has one and it is not the upload's
  // minute.  A scanner clock is always shown: it cannot be compared.
  const ran = shown.startLabel && (toolClock || shown.startLabel !== shownUpload.label)
    ? `ran ${shown.startLabel}`
    : null;
  const second = [ran, toolClock ? 'scanner clock, zone unknown' : took].filter(Boolean).join(' · ');
  return (
    <div className="min-w-0" title={hover}>
      <p className="break-words text-metadata tabular-nums">{shownUpload.label}</p>
      {second && (
        <p className={cn('break-words text-caption tabular-nums', toolClock ? 'text-warning' : 'text-muted-foreground')}>
          {second}
        </p>
      )}
    </div>
  );
};

/** What the WHEN column's (i) says: which zone converted times are in. */
export const viewerZoneExplanation = (format?: TimeFormatOptions): string => {
  const zone = viewerTimeZone(format);
  const detail = [zone.abbr, zone.offset].filter(Boolean).join(', ');
  return `Times are shown in your time zone, ${zone.name}${detail ? ` (${detail})` : ''}. `
    + 'A scanner clock with no recorded zone is shown as written. '
    + "Hover a scan's time to see where it came from.";
};

/**
 * Beside the WHEN column's heading: the viewer's zone in its short form, and
 * the explanation on an (i).  It was a permanent second helper line above the
 * table (UX walkthrough 2026-10-02, U16); the zone stays visible, the
 * sentence is one hover or focus away.
 */
export const ViewerZoneHint: React.FC<{ format?: TimeFormatOptions; className?: string }> = ({ format, className }) => {
  const zone = viewerTimeZone(format);
  return (
    <span className={cn('inline-flex items-center gap-xxs whitespace-nowrap', className)} data-testid="viewer-zone-hint">
      <span className="text-caption font-normal normal-case text-muted-foreground" title={zone.name}>
        {zone.abbr || zone.offset}
      </span>
      <InfoTip text={viewerZoneExplanation(format)} label="About the times in this column" />
    </span>
  );
};
