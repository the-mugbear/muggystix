import React, { useCallback, useEffect, useState } from 'react';
import { KeyRound, Loader2, FolderTree } from 'lucide-react';
import { Link } from 'react-router-dom';

import { NetexecResult, getHostNetexecResults } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { latestObservations } from '../utils/latestObservations';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { InspectorSection } from './host-inspector/InspectorSection';

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
        // v5.274.0 — the --shares table is stored as {name, permissions,
        // remark}: read as words ("READ · Parser lab share"), not as JSON.
        if ('permissions' in obj || 'remark' in obj) {
          const words = [obj.permissions || 'no access', obj.remark].filter(Boolean).map(String);
          return { name: name != null ? String(name) : `Share ${i + 1}`, detail: words.join(' · ') };
        }
        return {
          name: name != null ? String(name) : `Share ${i + 1}`,
          detail: describe(s),
        };
      }
      return { name: String(s), detail: null };
    });
  }
  if (typeof shares === 'object') {
    return Object.entries(shares as Record<string, unknown>).map(([k, v]) => {
      // spider_plus: {share: {path: {size, mtime…}}} — say how many files.
      if (v && typeof v === 'object' && !Array.isArray(v)
        && Object.values(v as Record<string, unknown>).every((f) => f && typeof f === 'object')) {
        const n = Object.keys(v as Record<string, unknown>).length;
        return { name: k, detail: `${n} file${n === 1 ? '' : 's'} listed` };
      }
      return { name: k, detail: describe(v) };
    });
  }
  return [{ name: String(shares), detail: null }];
};

const NetExecResultRow: React.FC<{ result: NetexecResult; seenCount?: number }> = ({ result, seenCount = 1 }) => {
  const shares = normalizeShares(result.shares);
  const host = result.hostname || result.domain_name;
  // v5.296.0 — the tool's own line.  Only the SMB banner and login lines are
  // interpreted, so an LDAP / RDP / VNC flag is readable here or nowhere.  A
  // spider_plus listing ("JSON: {...}") is already summarised as shares.
  const line = result.raw_output && !result.raw_output.startsWith('JSON:') ? result.raw_output : null;
  return (
    // v5.241.0 — a divided row, not a bordered box: a result with no shares is
    // one line (it was a ~100px card to say "auth failed, no shares").
    <div className="py-xs first:pt-0 last:pb-0">
      <div className={`flex flex-wrap items-center gap-xs${shares.length > 0 ? ' mb-xs' : ''}`}>
        <Badge variant={protocolBadgeVariant(result.protocol)}>
          {result.protocol.toUpperCase()}
        </Badge>
        {result.port != null && (
          <span className="font-mono text-caption text-muted-foreground">
            port {result.port}
          </span>
        )}
        {/* v5.276.0 — which tool said so (SMBMap rows sit beside NetExec's). */}
        {result.tool && result.tool !== 'netexec' && (
          <span className="text-caption text-muted-foreground">{result.tool === 'smbmap' ? 'SMBMap' : result.tool}</span>
        )}
        {result.auth_success != null && (
          <Badge variant={result.auth_success ? 'success' : 'outline'}>
            {result.auth_success
              ? (result.username === '' ? 'Null session' : result.username?.toLowerCase() === 'guest' ? 'Guest session' : 'Authenticated')
              : 'Auth failed'}
          </Badge>
        )}
        {result.local_admin && (
          <Badge variant="destructive" title="NetExec reported (Pwn3d!): this credential is a local administrator">
            Local admin
          </Badge>
        )}
        {result.smbv1 && (
          <Badge variant="warning" title="The SMB service accepts SMBv1">SMBv1</Badge>
        )}
        {host && (
          <span className="min-w-0 truncate text-caption text-muted-foreground">
            {host}
          </span>
        )}
        {/* Shares are an SMB matter: "no shares" on a VNC or LDAP row said
            nothing true. */}
        {shares.length === 0 && result.protocol.toLowerCase() === 'smb' && (
          <span className="text-caption text-muted-foreground">· no shares enumerated</span>
        )}
        {seenCount > 1 && (
          <span className="text-caption text-muted-foreground"
            title={`The same result was recorded by ${seenCount} scans; the latest is shown.`}>
            · same result in {seenCount} scans
          </span>
        )}
      </div>
      {line && (
        <p className={`line-clamp-2 break-all font-mono text-caption text-muted-foreground${shares.length > 0 ? ' mb-xs' : ' mt-xxs'}`}
          title={line}>
          {line}
        </p>
      )}

      {shares.length > 0 && (
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

  // v5.241.0 — results are kept one row per scan. The SAME result repeated is
  // one row here; the key includes the outcome (who, whether auth succeeded,
  // which shares), so a probe that came back differently stays its own row.
  const observed = latestObservations(
    rows ?? [],
    // v5.296.0 — the line is part of the result: a VNC banner with
    // "(No Auth:True)" and one without are different results, and folding
    // them showed only the latest line.
    (r) => JSON.stringify([r.protocol, r.port ?? null, r.auth_success ?? null, r.username ?? null,
      r.hostname ?? null, r.domain_name ?? null, r.shares ?? null, r.raw_output ?? null]),
    (r) => r.first_seen,
  );

  return (
    <InspectorSection
      id="host-detail-netexec"
      title="SMB / AD enumeration"
      titleHint="NetExec and SMBMap: protocol probes (SMB / LDAP / WinRM / RDP) — the session or login outcome, local-admin access, SMBv1, and the shares with their permissions."
      icon={<KeyRound className="size-4 shrink-0 text-muted-foreground" aria-hidden />}
      count={rows ? observed.length : null}
    >
      <div className="space-y-sm">
        {/* v5.296.0 — say how far these rows were understood. */}
        <p className="text-caption text-muted-foreground">
          BlueStick interprets the SMB banner and login lines; other flags (LDAP signing, RDP NLA, VNC,
          module results) are only in each row&rsquo;s line.{' '}
          <Link to="/reference/tool-coverage?tool=netexec" className="text-info underline-offset-2 hover:underline">
            What BlueStick reads from NetExec
          </Link>
        </p>
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
        {observed.length > 0 && (
          <div className="divide-y divide-border">
            {observed.map(({ latest, count: seenCount }) => (
              <NetExecResultRow key={latest.id} result={latest} seenCount={seenCount} />
            ))}
          </div>
        )}
      </div>
    </InspectorSection>
  );
};

export default NetExecCard;
