/**
 * Scan time presentation (v5.205.0): when a scan ran, where that time came
 * from, and how it relates to the viewer's clock.
 *
 * Pairs with backend `app/services/scan_time.py`. Absolute times arrive with
 * a UTC offset and are shown in the viewer's zone, with the zone named. A
 * scanner wall clock (`time_source: 'tool_clock'`) arrives without an offset.
 * It is shown exactly as the tool printed it and never converted: its zone
 * is unknown, so any conversion would be a guess presented as fact.
 */
import type { Scan } from '../services/api';

export type ScanTimeKind = 'tool_run' | 'tool_records' | 'tool_clock' | 'legacy' | 'none';

/** Test seam: pin the zone/locale instead of reading the browser's. */
export interface TimeFormatOptions {
  timeZone?: string;
  locale?: string;
}

export type ScanTimeFields = Pick<Scan, 'start_time' | 'end_time' | 'time_source' | 'created_at' | 'tool_name'>;

const INSTANT_PARTS: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
};

/** An absolute instant in the viewer's zone, zone abbreviation included. */
export function formatInstant(date: Date, opts: TimeFormatOptions = {}): string {
  return new Intl.DateTimeFormat(opts.locale, {
    ...INSTANT_PARTS,
    timeZone: opts.timeZone,
    timeZoneName: 'short',
  }).format(date);
}

/** `2026-09-10 02:51:07 UTC` — the unambiguous form, for tooltips. */
export function formatUtc(date: Date): string {
  return `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC`;
}

const WALL_CLOCK = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/;

/**
 * A zone-less wall clock (`2024-07-15T10:30:01`) as a Date whose UTC fields
 * are the written digits. Only for arithmetic and display in UTC — never
 * compare it with a real instant.
 */
export function parseWallClock(value: string): Date | null {
  const m = WALL_CLOCK.exec(value);
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] ?? 0)));
}

/** The wall clock exactly as written, in the viewer's date format, no zone. */
export function formatWallClock(value: string, opts: TimeFormatOptions = {}): string | null {
  const d = parseWallClock(value);
  if (!d) return null;
  return new Intl.DateTimeFormat(opts.locale, { ...INSTANT_PARTS, timeZone: 'UTC' }).format(d);
}

export function formatDuration(ms: number): string {
  if (ms <= 0) return 'Instant';
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

export interface ViewerZone {
  /** IANA name, e.g. `America/New_York`. */
  name: string;
  /** e.g. `EDT` (locale-dependent; may be `GMT-4`). */
  abbr: string;
  /** e.g. `UTC-04:00`; empty when the runtime can't produce it. */
  offset: string;
}

function zonePart(zone: string, style: string, at: Date, locale?: string): string {
  try {
    const fmt = new Intl.DateTimeFormat(locale ?? 'en-US', {
      timeZone: zone,
      timeZoneName: style as Intl.DateTimeFormatOptions['timeZoneName'],
    });
    return fmt.formatToParts(at).find((p) => p.type === 'timeZoneName')?.value ?? '';
  } catch {
    return '';
  }
}

/** The zone every converted time on the page is shown in. */
export function viewerTimeZone(opts: TimeFormatOptions = {}, at: Date = new Date()): ViewerZone {
  const name =
    opts.timeZone || new Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const rawOffset = zonePart(name, 'longOffset', at, opts.locale);
  const offset = rawOffset === 'GMT' ? 'UTC+00:00' : rawOffset.replace(/^GMT/, 'UTC');
  return { name, abbr: zonePart(name, 'short', at, opts.locale), offset };
}

export interface ScanRunDescription {
  kind: ScanTimeKind;
  /** When the scan ran, formatted for display; null when the file has no time. */
  startLabel: string | null;
  endLabel: string | null;
  durationLabel: string | null;
  /** `uploaded 3d 2h later` — only for absolute times, when the gap is real. */
  uploadLagLabel: string | null;
  /** Short provenance line shown under the time. */
  sourceLabel: string;
  /** Full sentence for the tooltip. */
  explanation: string;
  /** Absolute kinds: the window in UTC, for cross-checking against logs. */
  utcLabel: string | null;
}

const capitalise = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export function describeScanRun(scan: ScanTimeFields, opts: TimeFormatOptions = {}): ScanRunDescription {
  const tool = scan.tool_name?.trim() || 'the tool';
  const Tool = capitalise(tool);
  const empty = {
    startLabel: null,
    endLabel: null,
    durationLabel: null,
    uploadLagLabel: null,
    utcLabel: null,
  };

  if (!scan.start_time) {
    return {
      ...empty,
      kind: 'none',
      sourceLabel: 'No time in the file',
      explanation: `${Tool} output doesn't record when the scan ran, so the upload time is the only time BlueStick has for it.`,
    };
  }

  if (scan.time_source === 'tool_clock') {
    const start = parseWallClock(scan.start_time);
    const end = scan.end_time ? parseWallClock(scan.end_time) : null;
    return {
      ...empty,
      kind: 'tool_clock',
      startLabel: formatWallClock(scan.start_time, opts) ?? scan.start_time,
      endLabel: scan.end_time ? formatWallClock(scan.end_time, opts) : null,
      durationLabel:
        start && end && end.getTime() >= start.getTime()
          ? formatDuration(end.getTime() - start.getTime())
          : null,
      sourceLabel: 'Scanner clock · zone unknown',
      explanation:
        `${Tool} printed the scanning machine's local clock with no time zone. ` +
        `It is shown exactly as written, not converted to your time zone, so it can be hours off your clock.`,
    };
  }

  const start = new Date(scan.start_time);
  if (Number.isNaN(start.getTime())) {
    return {
      ...empty,
      kind: 'none',
      sourceLabel: 'Unreadable time',
      explanation: `The recorded scan time "${scan.start_time}" could not be read.`,
    };
  }
  const rawEnd = scan.end_time ? new Date(scan.end_time) : null;
  const end = rawEnd && !Number.isNaN(rawEnd.getTime()) && rawEnd >= start ? rawEnd : null;
  const uploaded = new Date(scan.created_at);
  const lag = uploaded.getTime() - (end ?? start).getTime();
  const zone = viewerTimeZone(opts, start);
  const where = `shown in your time zone, ${zone.name}${zone.abbr ? ` (${zone.abbr})` : ''}`;

  const kind: ScanTimeKind =
    scan.time_source === 'tool_run' || scan.time_source === 'tool_records' ? scan.time_source : 'legacy';
  const provenance: Record<'tool_run' | 'tool_records' | 'legacy', [string, string]> = {
    tool_run: [
      `Reported by ${tool}`,
      `${Tool} recorded when this run started${end ? ' and finished' : ''} as an absolute time, ${where}.`,
    ],
    tool_records: [
      `Span of ${tool} records`,
      `${Tool} doesn't record a run start or finish. This is the span from its first to its last timestamped record, ${where}.`,
    ],
    legacy: [
      'Source not recorded',
      `This scan was imported before BlueStick recorded where scan times come from. The time is assumed to be UTC and is ${where}.`,
    ],
  };
  const [sourceLabel, explanation] = provenance[kind as 'tool_run' | 'tool_records' | 'legacy'];

  return {
    kind,
    startLabel: formatInstant(start, opts),
    endLabel: end ? formatInstant(end, opts) : null,
    durationLabel: end ? formatDuration(end.getTime() - start.getTime()) : null,
    uploadLagLabel:
      !Number.isNaN(lag) && lag > 60_000 ? `uploaded ${formatDuration(lag)} later` : null,
    sourceLabel,
    explanation,
    utcLabel: end ? `${formatUtc(start)} – ${formatUtc(end)}` : formatUtc(start),
  };
}

export interface UploadDescription {
  label: string;
  explanation: string;
}

export function describeUpload(scan: Pick<Scan, 'created_at'>, opts: TimeFormatOptions = {}): UploadDescription {
  const uploaded = new Date(scan.created_at);
  if (Number.isNaN(uploaded.getTime())) {
    return { label: 'Unknown', explanation: 'The upload time could not be read.' };
  }
  const zone = viewerTimeZone(opts, uploaded);
  return {
    label: formatInstant(uploaded, opts),
    explanation:
      `When the file was uploaded to BlueStick, from the server's clock, shown in your time zone, ${zone.name}. ` +
      `${formatUtc(uploaded)}.`,
  };
}
