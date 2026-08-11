import React, { useCallback, useEffect, useState } from 'react';
import { Globe, Loader2, Search } from 'lucide-react';

import {
  getHostDnsRecords,
  performDnsLookup,
  HostDnsRecordRow,
  HostDnsRecordsResponse,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useToast } from '../contexts/ToastContext';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

/**
 * HostDnsRecordsCard — surfaces DNS records stored in `dns_records`
 * that pertain to this host (v4.55.0, UX phase 3).  Records are
 * grouped by ``record_type`` (A / AAAA / PTR / CNAME / MX / NS / TXT
 * / SOA) and each row's ``resolver_name`` is rendered as a badge so
 * the operator can see which resolver produced each answer.  This is
 * where the v2.89.0 (#44.1) per-row resolver column gets surfaced to
 * the analyst.
 *
 * Lazy-loaded — fetches on mount when ``hostId`` is provided.  The
 * card renders nothing when the host has no DNS records to avoid
 * empty card noise on freshly-discovered hosts.
 */

interface HostDnsRecordsCardProps {
  hostId: number;
  /** Needed for the on-demand lookup — the endpoint resolves a NAME, so the
   *  action is only offered when the host has one. */
  hostname?: string | null;
}

// Display ordering — operators read forward records first, then
// reverse, then service-type metadata.  Anything outside this list
// (rare) lands at the end alphabetically.
const RECORD_TYPE_DISPLAY_ORDER = [
  'A',
  'AAAA',
  'PTR',
  'CNAME',
  'MX',
  'NS',
  'TXT',
  'SOA',
] as const;

const sortRecordTypes = (types: string[]): string[] => {
  const known = RECORD_TYPE_DISPLAY_ORDER.filter((t) => types.includes(t));
  const unknown = types
    .filter((t) => !RECORD_TYPE_DISPLAY_ORDER.includes(t as never))
    .sort();
  return [...known, ...unknown];
};

const HostDnsRecordsCard: React.FC<HostDnsRecordsCardProps> = ({ hostId, hostname }) => {
  const toast = useToast();
  const [data, setData] = useState<HostDnsRecordsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setData(await getHostDnsRecords(hostId));
    } catch (err) {
      setError(formatApiError(err, 'DNS records could not be loaded for this host.'));
    } finally {
      setLoading(false);
    }
  }, [hostId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await load();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  // On-demand resolution (v2.243.0).  NOTE: this query leaves the BlueStick
  // SERVER, not the operator's machine — the target's nameserver sees the
  // server's address, which may not be in the engagement's authorised source
  // range.  That's why it's an explicit button and not something the page
  // does on load.  Zone transfer is deliberately not offered here.
  const handleResolve = useCallback(async () => {
    if (!hostname) return;
    setResolving(true);
    try {
      await performDnsLookup(hostname);
      await load();
      toast.success(`Resolved ${hostname} — records refreshed.`);
    } catch (err) {
      toast.error(formatApiError(err, `Lookup failed for ${hostname}.`));
    } finally {
      setResolving(false);
    }
  }, [hostname, load, toast]);

  // Render-nothing path: only when there's no DNS data ANYWHERE in the
  // project.  If records exist project-wide but none match this host, we
  // still render the card with an explicit "none match" hint (below) so an
  // ingested-but-unmatched DNS upload isn't mistaken for "no DNS evidence".
  const projectTotal = data?.project_total ?? 0;
  // Self-suppress on hosts with nothing to show AND nothing to look up.  When
  // the host has a hostname the card stays, otherwise the resolve action would
  // be unreachable exactly on the fresh hosts that most need it.
  if (
    !loading && !error && !hostname
    && (!data || (data.total === 0 && projectTotal === 0))
  ) {
    return null;
  }
  const noneMatchThisHost =
    !loading && !error && !!data && data.total === 0 && projectTotal > 0;

  // Group records by type so the card renders one section per record
  // type instead of a single flat list.
  const grouped: Record<string, HostDnsRecordRow[]> = {};
  if (data) {
    for (const row of data.items) {
      const key = row.record_type || 'unknown';
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(row);
    }
  }
  const sortedTypes = data ? sortRecordTypes(Object.keys(grouped)) : [];

  return (
    <Card id="host-detail-dns">
      <CardHeader>
        <div className="flex items-center gap-xs">
          <Globe className="size-5 text-primary" aria-hidden />
          <CardTitle>DNS Evidence</CardTitle>
          {data && data.total > 0 && (
            <Badge variant="outline">{data.total}</Badge>
          )}
          {hostname && (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={resolving || loading}
              onClick={() => void handleResolve()}
              title={`Resolve ${hostname} from the BlueStick server (not your workstation)`}
            >
              {resolving
                ? <Loader2 className="size-3.5 animate-spin" aria-hidden />
                : <Search className="size-3.5" aria-hidden />}
              Resolve now
            </Button>
          )}
        </div>
        {data && data.resolvers.length > 0 && (
          <p className="text-caption text-muted-foreground">
            {data.total} record{data.total === 1 ? '' : 's'} · {data.resolvers.length}{' '}
            resolver{data.resolvers.length === 1 ? '' : 's'} ·{' '}
            {data.record_types.join(', ')}
          </p>
        )}
        {data && data.total > 0 && data.resolvers.length === 0 && (
          // All records come from pre-v2.89.0 sources (CSV DNSParser,
          // amass) so no resolver attribution is available.  Honest
          // signal — not a failure mode.
          <p className="text-caption text-muted-foreground">
            {data.total} record{data.total === 1 ? '' : 's'} — uploaded from a source
            that didn&apos;t carry resolver attribution (CSV or amass).
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-md">
        {loading && (
          <div className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            Loading DNS records…
          </div>
        )}
        {error && (
          <Alert variant="warning">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {noneMatchThisHost && (
          <div className="rounded-control border border-border bg-muted/30 p-sm text-caption text-muted-foreground">
            <strong className="font-semibold text-foreground">
              {projectTotal} DNS record{projectTotal === 1 ? '' : 's'}
            </strong>{' '}
            ingested in this project, but none match this host&apos;s IP or hostname —
            they may belong to a different host, or the resolved value/name doesn&apos;t
            line up with this host&apos;s record.
          </div>
        )}
        {data &&
          sortedTypes.map((recordType) => {
            const rows = grouped[recordType];
            // Within each type, group identical (domain, value) tuples
            // so the same answer from multiple resolvers renders as
            // one entry with a row of resolver badges.  Closes the
            // "show me records resolver A returned that resolver B
            // didn't" loop without a SQL diff query.
            const byValue: Record<string, HostDnsRecordRow[]> = {};
            for (const r of rows) {
              const key = `${r.domain}||${r.value}`;
              if (!byValue[key]) byValue[key] = [];
              byValue[key].push(r);
            }
            return (
              <div key={recordType} className="space-y-xs">
                <h3 className="text-metadata font-semibold uppercase tracking-wider text-muted-foreground">
                  {recordType} ({rows.length})
                </h3>
                <ul className="space-y-xxs">
                  {Object.entries(byValue).map(([groupKey, groupRows]) => {
                    const first = groupRows[0];
                    const resolversForGroup = groupRows
                      .map((r) => r.resolver_name)
                      .filter((r): r is string => Boolean(r));
                    return (
                      <li
                        key={groupKey}
                        className="flex flex-wrap items-baseline gap-xs border-b border-border pb-xxs last:border-b-0 last:pb-0"
                      >
                        <span className="font-mono text-caption text-foreground">
                          {first.domain}
                        </span>
                        <span className="text-caption text-muted-foreground">→</span>
                        <span className="break-all font-mono text-caption text-foreground">
                          {first.value}
                        </span>
                        {first.ttl !== null && first.ttl !== undefined && (
                          <span className="text-caption text-muted-foreground">
                            ttl {first.ttl}
                          </span>
                        )}
                        {resolversForGroup.length > 0 ? (
                          <span className="flex flex-wrap gap-xxs">
                            {resolversForGroup.map((resolver, idx) => (
                              <Badge
                                key={`${groupKey}-${resolver}-${idx}`}
                                variant="outline"
                                className="font-mono"
                              >
                                {resolver}
                              </Badge>
                            ))}
                          </span>
                        ) : (
                          <span className="text-caption text-muted-foreground">
                            (resolver unknown)
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
      </CardContent>
    </Card>
  );
};

export default HostDnsRecordsCard;
