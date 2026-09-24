import React from 'react';
import { Link } from 'react-router-dom';

import type { Scan } from '../../services/api';
import ScanContribution from './ScanContribution';

/**
 * The reconciliation summary at the end of an import (v5.222.0; design
 * review item 5): what this upload did to the inventory, with every count
 * opening the records it counts.
 *
 *   +12 hosts added · 38 already known · 4 conflicts · 7 new open ports · 3 records skipped
 *
 * Built from the per-scan summary the inventory already computes (host and
 * port history, conflict history, the ingestion job's quality trio), so it
 * is the same wherever the scan is shown: the upload banner the moment the
 * job completes, the scan row, the scan page, and the Ingestion Results row.
 */

export interface ImportResultPart {
  key: string;
  text: string;
  /** Where the count opens, when there is a view that lists exactly it. */
  to?: string;
  title: string;
  tone: 'success' | 'muted' | 'warning' | 'default';
}

const num = (n: number) => n.toLocaleString();
const plural = (n: number, one: string, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;

export function importResultParts(scan: Scan): ImportResultPart[] {
  const parts: ImportResultPart[] = [];
  const added = scan.new_hosts ?? 0;
  const known = scan.updated_hosts ?? Math.max((scan.total_hosts ?? 0) - added, 0);
  if (added > 0) {
    parts.push({
      key: 'added',
      text: `+${plural(added, 'host')} added`,
      to: `/hosts?scan_ids=${scan.id}&first_seen_in_scan=true`,
      title: 'Hosts the inventory did not have before this upload. Opens the Hosts page filtered to them.',
      tone: 'success',
    });
  }
  if (known > 0) {
    parts.push({
      key: 'known',
      text: `${num(known)} already known`,
      to: `/hosts?scan_ids=${scan.id}`,
      title: 'Hosts this upload observed that were already in the inventory; their records were updated. Opens every host this scan observed.',
      tone: 'muted',
    });
  }
  if (added === 0 && known === 0) {
    parts.push({ key: 'nohosts', text: 'no hosts', title: 'This upload recorded no hosts.', tone: 'muted' });
  }
  const conflicts = scan.conflicts ?? 0;
  if (conflicts > 0) {
    parts.push({
      key: 'conflicts',
      text: plural(conflicts, 'conflict'),
      to: `/hosts?scan_ids=${scan.id}`,
      title: 'Values this upload reported that disagree with what an earlier scan recorded (OS, state, service). Each host shows a conflict badge; open it to reconcile.',
      tone: 'warning',
    });
  }
  const newPorts = scan.port_breakdown?.new_open_ports ?? 0;
  if (newPorts > 0) {
    parts.push({
      key: 'ports',
      text: `${plural(newPorts, 'new open port')}`,
      to: `/scans/${scan.id}`,
      title: 'Open ports no earlier scan had recorded on these hosts. Opens the scan page.',
      tone: 'success',
    });
  }
  const skipped = scan.import_skipped ?? 0;
  if (skipped > 0) {
    parts.push({
      key: 'skipped',
      text: `${plural(skipped, 'record')} skipped`,
      to: scan.import_job_id != null ? `/parse-errors?job_id=${scan.import_job_id}` : '/parse-errors',
      title: scan.import_warnings || 'Input records the parser could not use. Opens Ingestion Results for the reasons.',
      tone: 'warning',
    });
  }
  if (scan.import_partial) {
    parts.push({
      key: 'partial',
      text: 'file truncated',
      to: scan.import_job_id != null ? `/parse-errors?job_id=${scan.import_job_id}` : '/parse-errors',
      title: 'The parser stopped early: the file ended before its closing structure. An unknown number of records were lost.',
      tone: 'warning',
    });
  }
  return parts;
}

/** Imported cleanly, or imported with gaps the operator should know about. */
export const importHasGaps = (scan: Scan): boolean =>
  (scan.import_skipped ?? 0) > 0 || !!scan.import_partial;

const TONE_CLASS: Record<ImportResultPart['tone'], string> = {
  success: 'text-success',
  muted: 'text-muted-foreground',
  warning: 'text-warning',
  default: 'text-foreground',
};

/** How the file was read, in order: detected → chosen → parsed by → named
 *  tool.  Empty for a scan imported before the chain was recorded.
 *
 *  v5.288.0 — a choice equal to the detection is a confirmation, not an
 *  override: "confirmed by you", neutral.  Only a choice that DIFFERS is the
 *  warning-coloured `override` ("you chose …"); "Detected as NetExec JSON ·
 *  you chose NetExec JSON" was highlighted as if the operator had overruled
 *  detection. */
export function formatChainParts(scan: Scan): Array<{ key: string; lead: string; value: string }> {
  const parts: Array<{ key: string; lead: string; value: string }> = [];
  if (scan.import_detected_format) parts.push({ key: 'detected', lead: 'Detected as', value: scan.import_detected_format });
  if (scan.import_format_override) {
    if (scan.import_format_override === scan.import_detected_format) {
      parts.push({ key: 'confirmed', lead: 'confirmed by you', value: '' });
    } else {
      parts.push({ key: 'override', lead: 'you chose', value: scan.import_format_override });
    }
  }
  if (scan.import_final_format) parts.push({ key: 'final', lead: 'parsed by', value: scan.import_final_format });
  if (scan.import_source_tool) parts.push({ key: 'tool', lead: 'source tool', value: scan.import_source_tool });
  return parts;
}

const ImportResult: React.FC<{
  scan: Scan;
  showContribution?: boolean;
  /** Off where the surrounding row already prints the chain (Ingestion Results). */
  showFormatChain?: boolean;
  className?: string;
}> = ({
  scan,
  showContribution = true,
  showFormatChain = true,
  className,
}) => {
  const parts = importResultParts(scan);
  const chain = showFormatChain ? formatChainParts(scan) : [];
  return (
    <div className={className}>
      <p className="flex min-w-0 flex-wrap items-baseline gap-x-xs gap-y-xxs text-metadata" aria-label="Import result">
        {parts.map((part, i) => (
          <React.Fragment key={part.key}>
            {i > 0 && <span className="text-muted-foreground" aria-hidden>·</span>}
            {part.to ? (
              <Link
                to={part.to}
                title={part.title}
                className={`${TONE_CLASS[part.tone]} font-semibold tabular-nums underline-offset-2 hover:underline`}
              >
                {part.text}
              </Link>
            ) : (
              <span title={part.title} className={`${TONE_CLASS[part.tone]} tabular-nums`}>
                {part.text}
              </span>
            )}
          </React.Fragment>
        ))}
      </p>
      {/* The format chain beside the result: an override that produced "no
          hosts" reads very differently from a confident detection that did. */}
      {chain.length > 0 && (
        <p className="mt-xxs break-words text-caption text-muted-foreground" aria-label="How this file was read">
          {chain.map((c, i) => (
            <React.Fragment key={c.key}>
              {i > 0 && ' · '}
              {c.value ? (
                <>
                  {c.lead} <span className={c.key === 'override' ? 'text-warning' : 'text-foreground'}>{c.value}</span>
                </>
              ) : (
                c.lead
              )}
            </React.Fragment>
          ))}
        </p>
      )}
      {scan.import_warnings && (
        <p className="mt-xxs break-words text-caption text-warning" title="Parser warnings recorded on the ingestion job">
          {scan.import_warnings}
        </p>
      )}
      {showContribution && (
        <div className="mt-xs">
          <ScanContribution scan={scan} />
        </div>
      )}
    </div>
  );
};

export default ImportResult;
