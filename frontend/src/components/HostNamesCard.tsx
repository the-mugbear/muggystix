import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Tag } from 'lucide-react';

import { getHostNames, HostNameBinding, HostNamesResponse } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

/**
 * HostNamesCard — every name bound to this address (v5.193.0).
 *
 * The host row shows ONE display name; a load balancer carries forty.  This
 * card lists them all, split by what the evidence establishes: names with an
 * A/AAAA observation at this address ("resolve here") versus names seen here
 * only by HTTP contact, a certificate, a scanner or PTR ("served here").  It
 * also says when the host is reachable through an in-scope name — the third
 * coverage state, distinct from subnet membership.
 *
 * Renders nothing when no name has ever been observed at the address.
 */

interface HostNamesCardProps {
  hostId: number;
}

const fmtTime = (iso: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const BindingRow: React.FC<{ b: HostNameBinding }> = ({ b }) => (
  <li className="flex items-center gap-xs py-2xs">
    <Link
      to={`/names?name_id=${b.name_id}`}
      className="min-w-0 flex-1 truncate font-mono text-metadata text-primary hover:underline"
      title={b.fqdn}
    >
      {b.fqdn}
    </Link>
    {b.kind === 'wildcard' && <Badge variant="muted">wildcard</Badge>}
    {b.in_scope && <Badge variant="success-outline">in scope</Badge>}
    <span className="hidden shrink-0 gap-2xs sm:flex">
      {b.record_types.map((t) => (
        <Badge key={t} variant="outline">
          {t}
        </Badge>
      ))}
    </span>
    <span className="shrink-0 text-caption text-muted-foreground" title="Last observed">
      {fmtTime(b.last_observed)}
    </span>
  </li>
);

const HostNamesCard: React.FC<HostNamesCardProps> = ({ hostId }) => {
  const [data, setData] = useState<HostNamesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Same cancellation discipline as HostDnsRecordsCard: the inspector stays
  // mounted across prev/next, so a slow response for host A must not paint
  // into host B.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    getHostNames(hostId)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(formatApiError(err, 'Names could not be loaded for this host.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hostId]);

  const total = (data?.current.length ?? 0) + (data?.other.length ?? 0);
  if (!loading && !error && total === 0) return null;

  return (
    <Card id="host-detail-names">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-xs">
          <Tag className="size-5 text-primary" aria-hidden />
          <CardTitle>Names at this address</CardTitle>
          {total > 0 && <Badge variant="outline">{total}</Badge>}
          {data?.in_scope_via_names && (
            <Badge variant="success-outline" title="An in-scope name resolves to this address. This is not subnet membership.">
              reachable via in-scope name
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {loading && (
          <div className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading names…
          </div>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {data && data.current.length > 0 && (
          <section className="mb-sm">
            <h4 className="mb-2xs text-caption font-medium uppercase tracking-wide text-muted-foreground">
              Resolve here (A / AAAA)
            </h4>
            <ul className="divide-y divide-border">
              {data.current.map((b) => (
                <BindingRow key={b.name_id} b={b} />
              ))}
            </ul>
          </section>
        )}
        {data && data.other.length > 0 && (
          <section>
            <h4 className="mb-2xs text-caption font-medium uppercase tracking-wide text-muted-foreground">
              Served here (HTTP / certificate / scanner / PTR)
            </h4>
            <ul className="divide-y divide-border">
              {data.other.map((b) => (
                <BindingRow key={b.name_id} b={b} />
              ))}
            </ul>
          </section>
        )}
      </CardContent>
    </Card>
  );
};

export default HostNamesCard;
