import React, { useCallback, useEffect, useState } from 'react';
import { KeyRound, Loader2, FolderTree } from 'lucide-react';

import { NetexecResult, getHostNetexecResults } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

/**
 * NetExecCard — surfaces NetExec credentialed-enumeration results that
 * the netexec parser stored in `netexec_results` but which had no API
 * surface (and so no UI) before v2.45.7.
 *
 * One row per protocol probe (smb / ldap / winrm / rdp): the
 * authentication outcome and any enumerated SMB shares.  Lazy-loaded —
 * fetches only when the host has results (`count > 0`).
 */

interface NetExecCardProps {
  hostId: number;
  // Count from the host-detail payload; 0 → the card renders nothing.
  count: number;
}

const protocolBadgeVariant = (proto: string): 'info' | 'secondary' | 'outline' => {
  switch (proto.toLowerCase()) {
    case 'smb':
      return 'info';
    case 'ldap':
    case 'winrm':
    case 'rdp':
      return 'secondary';
    default:
      return 'outline';
  }
};

// `shares` is parser-shaped JSON — could be a dict keyed by share name,
// an array, or a scalar.  Normalize to a list of { name, detail } so the
// renderer doesn't have to branch.
interface ShareEntry {
  name: string;
  detail: string | null;
}

const normalizeShares = (shares: unknown): ShareEntry[] => {
  if (!shares) return [];
  const describe = (v: unknown): string | null => {
    if (v == null) return null;
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      return String(v);
    }
    try {
      return JSON.stringify(v);
    } catch {
      return null;
    }
  };
  if (Array.isArray(shares)) {
    return shares.map((s, i) => {
      if (s && typeof s === 'object') {
        const obj = s as Record<string, unknown>;
        const name = obj.name ?? obj.share ?? obj.Share;
        return {
          name: name != null ? String(name) : `Share ${i + 1}`,
          detail: name != null ? describe(s) : describe(s),
        };
      }
      return { name: String(s), detail: null };
    });
  }
  if (typeof shares === 'object') {
    return Object.entries(shares as Record<string, unknown>).map(([k, v]) => ({
      name: k,
      detail: describe(v),
    }));
  }
  return [{ name: String(shares), detail: null }];
};

const NetExecResultRow: React.FC<{ result: NetexecResult }> = ({ result }) => {
  const shares = normalizeShares(result.shares);
  const host = result.hostname || result.domain_name;
  return (
    <div className="rounded-control border border-border bg-card p-sm">
      <div className="mb-xs flex flex-wrap items-center gap-xs">
        <Badge variant={protocolBadgeVariant(result.protocol)}>
          {result.protocol.toUpperCase()}
        </Badge>
        {result.port != null && (
          <span className="font-mono text-caption text-muted-foreground">
            port {result.port}
          </span>
        )}
        {result.auth_success != null && (
          <Badge variant={result.auth_success ? 'success' : 'outline'}>
            {result.auth_success ? 'Authenticated' : 'Auth failed'}
          </Badge>
        )}
        {host && (
          <span className="min-w-0 truncate text-caption text-muted-foreground">
            {host}
          </span>
        )}
        {result.os_version && (
          <span className="min-w-0 truncate text-caption text-muted-foreground">
            {result.os_version}
          </span>
        )}
      </div>

      {shares.length > 0 ? (
        <div>
          <div className="mb-xxs flex items-center gap-xxs text-caption font-semibold">
            <FolderTree className="size-3.5 text-muted-foreground" aria-hidden />
            Shares ({shares.length})
          </div>
          <ul className="space-y-0">
            {shares.map((share, i) => (
              <li
                key={`${share.name}-${i}`}
                className="flex min-w-0 flex-wrap items-baseline gap-xs border-b border-border py-xxs last:border-b-0"
              >
                <span className="font-mono text-caption font-medium">{share.name}</span>
                {share.detail && (
                  <span className="min-w-0 flex-1 truncate text-caption text-muted-foreground">
                    {share.detail}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-caption text-muted-foreground">No shares enumerated.</p>
      )}
    </div>
  );
};

const NetExecCard: React.FC<NetExecCardProps> = ({ hostId, count }) => {
  const [rows, setRows] = useState<NetexecResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await getHostNetexecResults(hostId));
    } catch (err) {
      setError(formatApiError(err, 'Failed to load NetExec results.'));
    } finally {
      setLoading(false);
    }
  }, [hostId]);

  useEffect(() => {
    if (count > 0) load();
  }, [count, load]);

  // Nothing observed — render nothing (host wasn't enumerated with NetExec).
  if (count <= 0) return null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-xs">
          <KeyRound className="size-5 text-muted-foreground" aria-hidden />
          <CardTitle>NetExec Enumeration</CardTitle>
          {rows && <Badge variant="outline">{rows.length}</Badge>}
        </div>
        <p className="mt-xxs text-caption text-muted-foreground">
          Credentialed protocol probes (SMB / LDAP / WinRM / RDP) — authentication outcome and
          enumerated shares.
        </p>
      </CardHeader>
      <CardContent className="space-y-sm">
        {loading && (
          <div className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Loading NetExec results…
          </div>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {!loading && !error && rows && rows.length === 0 && (
          <p className="text-caption text-muted-foreground">No NetExec results recorded.</p>
        )}
        {rows?.map((result) => (
          <NetExecResultRow key={result.id} result={result} />
        ))}
      </CardContent>
    </Card>
  );
};

export default NetExecCard;
