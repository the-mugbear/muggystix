/**
 * Data conflicts on one host (v5.246.0).
 *
 * The panel used to be organised around CONFIDENCE records — one heading per
 * field that had a source ranking — with the disagreements nested beneath the
 * matching heading. A host whose conflict had no confidence record (every
 * conflict the dedup service writes) opened a panel holding only the
 * explainer: the badge said "1 conflict" and nothing on the page said what it
 * was.
 *
 * It now leads with the disagreements themselves, one row each, in words:
 * the field, the value that was held, the value the later scan reported, which
 * of the two the host shows today, and a link to each scan. The source ranking
 * is secondary and sits behind a disclosure.
 */
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';

import type { ConflictHistoryEntry, HostConflict } from '../../services/api';
import { formatRelativeTime } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';

const SOURCE_WEIGHT_TITLE =
  'Source ranking weight for this detection method. It orders sources against each other; '
  + 'it is not a probability that the value is correct.';

const FIELD_LABEL: Record<string, string> = {
  os_name: 'Operating system',
  hostname: 'Hostname',
  state: 'Host state',
};

export interface ConflictPort {
  id: number;
  port_number: number;
  protocol: string;
}

/** "Operating system", or "443/tcp · service name" for a port-level conflict. */
export const conflictSubject = (entry: ConflictHistoryEntry, ports: ConflictPort[]): string => {
  const field = FIELD_LABEL[entry.field_name] ?? entry.field_name.replace(/_/g, ' ');
  if (entry.object_type !== 'port') return field;
  const port = ports.find((p) => p.id === entry.object_id);
  return `${port ? `${port.port_number}/${port.protocol}` : 'A port'} · ${field}`;
};

/** Which side of the disagreement the host shows today, when that is knowable. */
export const keptSide = (entry: ConflictHistoryEntry): 'previous' | 'new' | null => {
  if (entry.current_value == null) return null;
  if (entry.current_value === entry.new_value) return 'new';
  if (entry.current_value === entry.previous_value) return 'previous';
  return null;
};

const ScanRef: React.FC<{ id: number | null; filename?: string | null }> = ({ id, filename }) => {
  if (id == null) return <span className="text-muted-foreground">an earlier scan</span>;
  return (
    <Link
      to={`/scans/${id}`}
      className="inline-block max-w-[16rem] truncate align-bottom text-primary hover:underline"
      title={filename ?? `Scan #${id}`}
    >
      {filename || `scan #${id}`}
    </Link>
  );
};

const Value: React.FC<{ value: string | null; kept: boolean }> = ({ value, kept }) => (
  <span className="inline-flex min-w-0 max-w-full items-center gap-xxs">
    <span className="min-w-0 break-words font-medium text-foreground">{value || '(empty)'}</span>
    {kept && (
      <Badge variant="secondary" className="shrink-0" title="The value this host shows today">
        shown
      </Badge>
    )}
  </span>
);

export interface HostConflictsPanelProps {
  id: string;
  conflictCount: number;
  history: ConflictHistoryEntry[];
  confidence: HostConflict[];
  ports: ConflictPort[];
}

const HostConflictsPanel: React.FC<HostConflictsPanelProps> = ({
  id, conflictCount, history, confidence, ports,
}) => {
  const [showRanking, setShowRanking] = useState(false);
  // The count is host-level (the same number as the Hosts-list badge); port
  // disagreements are listed after them and are not part of it.
  const hostEntries = history.filter((h) => h.object_type !== 'port');
  const portEntries = history.filter((h) => h.object_type === 'port');
  const unlisted = Math.max(0, conflictCount - hostEntries.length);

  // One line per disagreement, grouped under its field: nine two-line blocks
  // each repeating "Hostname" / "Was held" / "Then reported" was most of the
  // panel's height for a host with a history.
  const renderEntry = (entry: ConflictHistoryEntry) => {
    const kept = keptSide(entry);
    return (
      <li
        key={entry.id}
        className="grid items-baseline gap-x-sm gap-y-xxs py-xxs text-caption sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_5rem]"
      >
        <span className="min-w-0">
          <Value value={entry.previous_value} kept={kept === 'previous'} />
          <span className="text-muted-foreground"> · </span>
          <ScanRef id={entry.previous_scan_id} filename={entry.previous_scan_filename} />
        </span>
        <span className="min-w-0">
          <Value value={entry.new_value} kept={kept === 'new'} />
          <span className="text-muted-foreground"> · </span>
          <ScanRef id={entry.new_scan_id} filename={entry.new_scan_filename} />
        </span>
        <span className="text-muted-foreground sm:text-right"
          title={entry.resolved_at ? new Date(entry.resolved_at).toLocaleString() : undefined}>
          {formatRelativeTime(entry.resolved_at, { fallback: 'time unknown' })}
        </span>
      </li>
    );
  };

  const renderGroups = (entries: ConflictHistoryEntry[], label: string) => {
    const groups = new Map<string, ConflictHistoryEntry[]>();
    entries.forEach((e) => {
      const key = conflictSubject(e, ports);
      groups.set(key, [...(groups.get(key) ?? []), e]);
    });
    return (
      <div className="space-y-sm" role="group" aria-label={label}>
        {[...groups.entries()].map(([subject, rows]) => {
          const current = rows.find((r) => r.current_value != null)?.current_value ?? null;
          const currentIsListed = rows.some((r) => keptSide(r) !== null);
          return (
            <section key={subject} aria-label={subject}>
              <h3 className="flex flex-wrap items-baseline gap-x-xs text-metadata font-semibold text-foreground">
                {subject}
                <span className="text-caption font-normal text-muted-foreground">
                  {rows.length} disagreement{rows.length === 1 ? '' : 's'}
                </span>
              </h3>
              {current != null && !currentIsListed && (
                <p className="text-caption text-muted-foreground">
                  The host now shows{' '}
                  <span className="break-words font-medium text-foreground">{current}</span>
                  {' '}— none of the values below.
                </p>
              )}
              <div className="hidden gap-x-sm text-micro uppercase tracking-wider text-muted-foreground sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_5rem]">
                <span>Was held</span><span>A later scan reported</span><span className="text-right">Recorded</span>
              </div>
              <ul className="divide-y divide-border">{rows.map(renderEntry)}</ul>
            </section>
          );
        })}
      </div>
    );
  };

  return (
    <Card id={id} className="scroll-mt-20">
      <CardHeader className="p-sm">
        <div className="flex items-center gap-xs">
          <AlertTriangle className="size-4 text-warning" aria-hidden />
          <CardTitle className="text-subheading">
            Scans disagreed about this host
          </CardTitle>
        </div>
        <p className="text-caption text-muted-foreground">
          Both values are kept. Open either scan to see what it recorded and judge which is right.
        </p>
      </CardHeader>
      <CardContent className="space-y-sm p-sm pt-0">
        {hostEntries.length > 0 && renderGroups(hostEntries, 'Host conflicts')}
        {unlisted > 0 && (
          <p className="text-caption text-muted-foreground">
            {hostEntries.length === 0
              ? `${conflictCount} recorded, but the detail could not be listed.`
              : `${unlisted} older disagreement${unlisted === 1 ? '' : 's'} not listed.`}
          </p>
        )}
        {portEntries.length > 0 && (
          <div>
            <p className="text-caption uppercase tracking-wide text-muted-foreground">
              On its ports (not part of the count)
            </p>
            {renderGroups(portEntries, 'Port conflicts')}
          </div>
        )}

        {confidence.length > 0 && (
          <div className="border-t border-border pt-xs">
            <button
              type="button"
              className="inline-flex items-center gap-xxs text-caption text-muted-foreground hover:text-foreground"
              aria-expanded={showRanking}
              onClick={() => setShowRanking((v) => !v)}
            >
              {showRanking ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
              How sources are ranked for this host ({confidence.length})
            </button>
            {showRanking && (
              <div className="mt-xs space-y-xxs">
                <p className="text-caption text-muted-foreground">
                  Each detection method has a fixed weight — Nmap&apos;s <code>-sV</code> version probe (95)
                  outranks Masscan&apos;s basic port check (60). It orders sources; it is not a
                  probability that a value is correct.
                </p>
                <ul className="space-y-xxs">
                  {confidence.map((c) => (
                    <li key={`${c.field_name}-${c.id}`} className="flex min-w-0 flex-wrap items-center gap-xs text-caption">
                      <Badge variant="outline" className="shrink-0" title={SOURCE_WEIGHT_TITLE}>
                        weight {c.confidence_score}
                      </Badge>
                      <span className="min-w-0 break-words">
                        <span className="font-medium text-foreground">{c.field_name.replace(/_/g, ' ')}</span>
                        {' — '}{c.scan_type} · {c.data_source || 'unknown source'} via {c.method || 'default'}
                        {' · '}
                        <Link to={`/scans/${c.scan_id}`} className="text-primary hover:underline">scan #{c.scan_id}</Link>
                        <span className="text-muted-foreground" title={c.updated_at ? new Date(c.updated_at).toLocaleString() : undefined}>
                          {' · recorded '}{formatRelativeTime(c.updated_at, { fallback: 'time unknown' })}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default HostConflictsPanel;
