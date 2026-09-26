import React from 'react';

import { WebPath } from '../services/api';
import { safeHttpHref } from '../utils/safeHref';
import { Badge } from './ui/badge';

/**
 * Paths content discovery found on a host (v5.276.0): ffuf, gobuster,
 * feroxbuster, dirsearch, dirbuster.  They used to be one capped string in the
 * port's service "extra info", lost whenever nmap had named the port.  One
 * line per path — status, size, which tool.  v5.298.0 — rendered in each
 * service's panel (host-inspector/ServiceEvidencePanel); the per-host
 * section is gone.
 */

const statusVariant = (code: number | null | undefined) => {
  if (code == null) return 'outline';
  if (code >= 200 && code < 300) return 'success';
  if (code === 401 || code === 403) return 'warning';
  if (code >= 300 && code < 400) return 'info';
  return 'muted';
};

const formatSize = (bytes: number | null | undefined) => {
  if (bytes == null) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/** One discovered path. */
export const WebPathRow: React.FC<{ row: WebPath }> = ({ row: r }) => (
  <li className="flex min-w-0 items-center gap-sm py-xxs text-metadata">
    <Badge variant={statusVariant(r.status_code) as never} className="w-12 shrink-0 justify-center tabular-nums">
      {r.status_code ?? '—'}
    </Badge>
    <a href={safeHttpHref(r.url)} target="_blank" rel="noopener noreferrer" title={r.url}
      className="min-w-0 flex-1 truncate font-mono text-primary hover:underline">
      {r.port != null ? `:${r.port} ` : ''}{r.path}
    </a>
    <span className="w-20 shrink-0 text-right text-caption tabular-nums text-muted-foreground">
      {formatSize(r.size) ?? ''}
    </span>
    <span className="w-28 shrink-0 truncate text-caption text-muted-foreground" title={r.scans > 1 ? `Reported by ${r.scans} scans` : undefined}>
      {r.source}{r.scans > 1 ? ` · ${r.scans} scans` : ''}
    </span>
  </li>
);
