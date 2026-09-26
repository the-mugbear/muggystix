import React, { useState } from 'react';
import { FolderTree } from 'lucide-react';

import { NetexecResult } from '../services/api';
import { latestObservations } from '../utils/latestObservations';
import { Badge } from './ui/badge';

/**
 * NetExec / SMBMap results as the inspector shows them: one row per protocol
 * probe — the login outcome, local-admin access, SMBv1, the shares, and the
 * tool's own line.  v5.298.0 — the per-host section is gone; each service's
 * panel (host-inspector/ServiceEvidencePanel) and the Services section's
 * "results on no listed port" render these rows.
 */

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
interface ShareFile {
  path: string;
  size: string | null;
}

interface ShareEntry {
  name: string;
  detail: string | null;
  /** v5.300.0 — a spider_plus listing's files (was only a count). */
  files?: ShareFile[];
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
      // spider_plus: {share: {path: {size, mtime…}}} — how many files, and
      // (v5.300.0) the files themselves: a count cannot say whether a share
      // holds anything sensitive.
      if (v && typeof v === 'object' && !Array.isArray(v)
        && Object.values(v as Record<string, unknown>).every((f) => f && typeof f === 'object')) {
        const entries = Object.entries(v as Record<string, Record<string, unknown>>);
        const n = entries.length;
        return {
          name: k,
          detail: `${n} file${n === 1 ? '' : 's'} listed`,
          files: entries.map(([path, meta]) => ({
            path,
            size: meta?.size != null ? String(meta.size) : null,
          })),
        };
      }
      return { name: k, detail: describe(v) };
    });
  }
  return [{ name: String(shares), detail: null }];
};

/** What a successful login was, in the protocol's own words (v5.297.0 —
 *  an anonymous FTP login read "Null session"). */
export const loginLabel = (result: NetexecResult): string => {
  const user = (result.username ?? '').trim().toLowerCase();
  const proto = result.protocol.toLowerCase();
  if (result.username === '' || user === 'anonymous') {
    return proto === 'smb' ? 'Null session' : proto === 'ftp' ? 'Anonymous login' : 'Blank user';
  }
  if (user === 'guest') return proto === 'smb' ? 'Guest session' : 'Guest login';
  return 'Authenticated';
};

const FILE_PREVIEW = 20;

/** A spider_plus share's files, the first few until asked for (v5.300.0). */
const ShareFiles: React.FC<{ share: string; files: ShareFile[] }> = ({ share, files }) => {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  return (
    <div className="w-full min-w-0">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        aria-label={`${open ? 'Hide' : 'Show'} the ${files.length} files listed in ${share}`}
        className={linkButton}>
        {files.length} file{files.length === 1 ? '' : 's'} listed · {open ? 'hide' : 'show'}
      </button>
      {open && (
        <ul className="mt-xxs">
          {(all ? files : files.slice(0, FILE_PREVIEW)).map((f) => (
            <li key={f.path} className="flex min-w-0 items-baseline gap-xs text-caption">
              <span className="min-w-0 flex-1 truncate font-mono" title={f.path}>{f.path}</span>
              {f.size && <span className="shrink-0 tabular-nums text-muted-foreground">{f.size}</span>}
            </li>
          ))}
          {files.length > FILE_PREVIEW && (
            <li>
              <button type="button" className={linkButton} onClick={() => setAll((v) => !v)}>
                {all ? 'show fewer' : `show all ${files.length}`}
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
};

const linkButton =
  'rounded text-caption text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

// Longer than this (or several lines), the output is clamped until asked for.
const LONG_OUTPUT = 240;

const NetExecResultRow: React.FC<{ result: NetexecResult; seenCount?: number }> = ({ result, seenCount = 1 }) => {
  const [outputOpen, setOutputOpen] = useState(false);
  const shares = normalizeShares(result.shares);
  const host = result.hostname || result.domain_name;
  // v5.296.0 — the tool's own line.  Only the SMB banner and login lines are
  // interpreted, so an LDAP / RDP / VNC flag is readable here or nowhere.  A
  // spider_plus listing ("JSON: {...}") is already summarised as shares.
  const line = result.raw_output && !result.raw_output.startsWith('JSON:') ? result.raw_output : null;
  // v5.300.0 — the whole output is served now (it was cut at 2 000
  // characters, silently); long output opens on request.
  const long = line != null && (line.length > LONG_OUTPUT || line.includes('\n'));
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
            {result.auth_success ? loginLabel(result) : 'Auth failed'}
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
        <div className={shares.length > 0 ? 'mb-xs' : 'mt-xxs'}>
          <p className={`${long && !outputOpen ? 'line-clamp-2 ' : ''}whitespace-pre-wrap break-all font-mono text-caption text-muted-foreground`}>
            {line}
          </p>
          {(long || result.raw_output_truncated) && (
            <div className="flex flex-wrap items-center gap-xs">
              {long && (
                <button type="button" className={linkButton} aria-expanded={outputOpen}
                  onClick={() => setOutputOpen((v) => !v)}>
                  {outputOpen ? 'show less' : `show all output (${line.length.toLocaleString()} characters)`}
                </button>
              )}
              {result.raw_output_truncated && (
                <span className="text-caption text-warning">
                  cut at {line.length.toLocaleString()} characters on import — the rest is in the original file
                </span>
              )}
            </div>
          )}
        </div>
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
                {share.files ? (
                  <ShareFiles share={share.name} files={share.files} />
                ) : share.detail && (
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

/**
 * One row per distinct result (v5.241.0): results are kept one row per scan,
 * and the SAME result repeated is one row here.  The key includes the outcome
 * (who, whether auth succeeded, which shares) and the line (v5.296.0: a VNC
 * banner with "(No Auth:True)" and one without are different results), with
 * whitespace — nxc's column padding, which differs between runs — ignored.
 */
export const foldNetexecRows = (rows: NetexecResult[]) =>
  latestObservations(
    rows,
    (r) => JSON.stringify([r.protocol, r.port ?? null, r.auth_success ?? null, r.username ?? null,
      r.hostname ?? null, r.domain_name ?? null, r.shares ?? null,
      r.raw_output ? r.raw_output.replace(/\s+/g, ' ').trim() : null]),
    (r) => r.first_seen,
  );

export { NetExecResultRow };
