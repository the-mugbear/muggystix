import React, { useState } from 'react';
import type { ScanInfoEntry } from '../../services/api';

/** Above this many characters the line is clamped behind "Show all". */
const CLAMP_AT = 160;

/**
 * "tcp 1-1000 · udp 53,161" from a scan's `scan_info` rows (nmap's
 * `<scaninfo>`), or '' when the scan carries none. A row with no port list
 * says how many ports it probed when it knows, else just its protocol.
 */
export const describeScannedPorts = (rows: ScanInfoEntry[] | null | undefined): string =>
  (rows ?? [])
    .map((row) => {
      const label = (row.protocol || row.type || '').trim();
      const ports = (row.services || '').trim()
        || (row.numservices != null ? `${row.numservices.toLocaleString()} ports` : '');
      return [label, ports].filter(Boolean).join(' ');
    })
    .filter(Boolean)
    .join(' · ');

/**
 * What the scan was asked to probe — a port outside this list was not looked
 * at, which is not the same as closed. `services` is the tool's own range
 * string and can run to thousands of characters (`-p` with a long list), so it
 * is clamped to two lines with an expander and breaks anywhere: it has no
 * spaces to wrap on. Renders nothing for a scan that reports no list.
 */
const ScannedPorts: React.FC<{ scanInfo: ScanInfoEntry[] | null | undefined; className?: string }> = ({
  scanInfo,
  className,
}) => {
  const [open, setOpen] = useState(false);
  const text = describeScannedPorts(scanInfo);
  if (!text) return null;
  const long = text.length > CLAMP_AT;
  return (
    <div className={`min-w-0 text-metadata ${className ?? ''}`} data-testid="scanned-ports">
      <p className={`break-all ${long && !open ? 'line-clamp-2' : ''}`}>
        <span className="text-muted-foreground">Scanned: </span>
        <span className="font-mono">{text}</span>
      </p>
      {long && (
        <button
          type="button"
          className="mt-xxs rounded-control text-caption text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? 'Show less' : 'Show the whole list'}
        </button>
      )}
    </div>
  );
};

export default ScannedPorts;
