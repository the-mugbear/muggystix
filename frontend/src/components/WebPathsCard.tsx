import React, { useCallback, useEffect, useState } from 'react';
import { FolderSearch, Loader2 } from 'lucide-react';

import { WebPath, getHostWebPaths } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { InspectorSection } from './host-inspector/InspectorSection';

/**
 * Paths content discovery found on this host (v5.276.0): ffuf, gobuster,
 * feroxbuster, dirsearch, dirbuster.  They used to be one capped string in the
 * port's service "extra info", lost whenever nmap had named the port.  One
 * line per path — status, size, which tool — ordered by port then path;
 * lazily loaded when the host has any.
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

const WebPathsCard: React.FC<{ hostId: number; count: number }> = ({ hostId, count }) => {
  const [rows, setRows] = useState<WebPath[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await getHostWebPaths(hostId));
    } catch (err) {
      setError(formatApiError(err, 'Failed to load discovered paths.'));
    } finally {
      setLoading(false);
    }
  }, [hostId]);

  useEffect(() => {
    if (count > 0) void load();
  }, [count, load]);

  if (count <= 0) return null;

  return (
    <InspectorSection
      id="host-detail-web-paths"
      title="Discovered paths"
      titleHint="Paths found by content discovery (ffuf, gobuster, feroxbuster, dirsearch, dirbuster): the HTTP status and size of the latest response, and which tool found it."
      icon={<FolderSearch className="size-4 shrink-0 text-muted-foreground" aria-hidden />}
      count={rows ? rows.length : null}
    >
      {loading && (
        <p className="flex items-center gap-xs text-caption text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading discovered paths…
        </p>
      )}
      {error && (
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
      )}
      {rows && rows.length > 0 && (
        <ul className="divide-y divide-border">
          {rows.map((r) => (
            <li key={r.url} className="flex min-w-0 items-center gap-sm py-xxs text-metadata">
              <Badge variant={statusVariant(r.status_code) as never} className="w-12 shrink-0 justify-center tabular-nums">
                {r.status_code ?? '—'}
              </Badge>
              <a href={r.url} target="_blank" rel="noopener noreferrer" title={r.url}
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
          ))}
        </ul>
      )}
    </InspectorSection>
  );
};

export default WebPathsCard;
