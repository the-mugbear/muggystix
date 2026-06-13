import React, { useCallback, useEffect, useState } from 'react';
import { ExternalLink, Globe, Image as ImageIcon, Loader2, Lock, Unlock } from 'lucide-react';

import {
  WebInterface,
  getHostWebInterfaces,
  fetchWebInterfaceScreenshot,
} from '../services/api';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import ScreenshotLightbox from './ScreenshotLightbox';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

interface WebInterfacesCardProps {
  hostId: number;
  // Count from HostDetail, used to decide whether to mount + fetch
  // at all.  If 0, the card renders nothing.
  count: number;
}

/**
 * Host-detail card rendering every web interface observed on a host
 * by any web-fingerprint tool (httpx, eyewitness, nikto, …).  Rows
 * come from the unified ``web_interfaces`` table via
 * ``GET /hosts/{id}/web-interfaces``.
 *
 * Lazy-loads: we don't fetch until the card actually mounts (i.e.
 * ``count > 0``).  Screenshots (when present) are fetched
 * on-demand when the user clicks the thumbnail trigger — the initial
 * list request stays cheap even for hosts with many interfaces.
 */
const WebInterfacesCard: React.FC<WebInterfacesCardProps> = ({ hostId, count }) => {
  const [rows, setRows] = useState<WebInterface[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [lightboxLoading, setLightboxLoading] = useState(false);
  const [lightboxError, setLightboxError] = useState<string | null>(null);
  const [lightboxCaption, setLightboxCaption] = useState<string>('');

  useEffect(() => {
    if (count === 0) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getHostWebInterfaces(hostId)
      .then((data) => {
        if (cancelled) return;
        setRows(data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(formatApiError(err, 'Failed to load web interfaces.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hostId, count]);

  // Revoke the blob URL when the lightbox closes or a different
  // screenshot is loaded.  Avoids memory leaks from chained opens.
  useEffect(() => {
    return () => {
      if (lightboxSrc) URL.revokeObjectURL(lightboxSrc);
    };
  }, [lightboxSrc]);

  const openScreenshot = useCallback(
    async (row: WebInterface) => {
      if (lightboxSrc) {
        URL.revokeObjectURL(lightboxSrc);
        setLightboxSrc(null);
      }
      setLightboxError(null);
      setLightboxCaption(row.title ? `${row.url} — ${row.title}` : row.url);
      setLightboxOpen(true);
      setLightboxLoading(true);
      try {
        const url = await fetchWebInterfaceScreenshot(row.id);
        if (url === null) {
          setLightboxError('Screenshot not available on the server.');
        } else {
          setLightboxSrc(url);
        }
      } catch (err: unknown) {
        const detail = asAxiosError(err).response?.data?.detail;
        setLightboxError(typeof detail === 'string' ? detail : 'Failed to load screenshot');
      } finally {
        setLightboxLoading(false);
      }
    },
    [lightboxSrc],
  );

  const closeLightbox = useCallback(() => {
    setLightboxOpen(false);
  }, []);

  if (count === 0) return null;

  return (
    <Card className="mb-md">
      <CardHeader>
        <div className="flex items-center gap-xs">
          <Globe className="size-5 text-primary" aria-hidden />
          <CardTitle>Web Interfaces</CardTitle>
          <span className="text-metadata text-muted-foreground">({count})</span>
        </div>
      </CardHeader>
      <CardContent>
        {loading && (
          <div className="flex items-center gap-xs text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            <span className="text-metadata">Loading web interfaces…</span>
          </div>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {!loading && !error && rows && rows.length === 0 && (
          <p className="text-metadata text-muted-foreground">No web interfaces recorded.</p>
        )}

        {!loading && !error && rows && rows.length > 0 && (
          <div className="space-y-sm">
            {rows.map((row) => (
              <WebInterfaceRow
                key={row.id}
                row={row}
                onViewScreenshot={() => openScreenshot(row)}
              />
            ))}
          </div>
        )}
      </CardContent>

      <ScreenshotLightbox
        open={lightboxOpen}
        onClose={closeLightbox}
        src={lightboxSrc}
        loading={lightboxLoading}
        error={lightboxError}
        caption={lightboxCaption}
      />
    </Card>
  );
};

// ---------------------------------------------------------------------------

interface RowProps {
  row: WebInterface;
  onViewScreenshot: () => void;
}

const statusVariant = (
  status?: number | null,
): 'success' | 'warning' | 'destructive' | 'outline' => {
  if (status == null) return 'outline';
  if (status >= 200 && status < 300) return 'success';
  if (status >= 300 && status < 400) return 'warning';
  if (status >= 400) return 'destructive';
  return 'outline';
};

const sourceBadgeVariant = (source: string): 'info' | 'secondary' | 'warning' | 'outline' => {
  switch (source) {
    case 'httpx':
      return 'info';
    case 'eyewitness':
      return 'secondary';
    case 'nikto':
      return 'warning';
    default:
      return 'outline';
  }
};

// v4.7.9 — pull a compact, human-readable summary out of the
// httpx `tls` blob.  The blob is a loose Record<string, unknown>
// (ProjectDiscovery's shape varies by httpx version), so every
// access is defensive.  Returns null when there's nothing useful
// to show so the caller can skip the whole TLS line.
const tlsStr = (tls: Record<string, unknown>, ...keys: string[]): string | null => {
  for (const k of keys) {
    const v = tls[k];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return null;
};

interface TlsSummary {
  version: string | null;
  issuer: string | null;
  expiry: string | null;
  daysToExpiry: number | null; // computed from not_after; null if unparseable
  sanCount: number;
  flags: string[]; // self-signed / expired / mismatched
}

const summarizeTls = (tls: Record<string, unknown> | null | undefined): TlsSummary | null => {
  if (!tls || typeof tls !== 'object') return null;
  const sans = tls.subject_an ?? tls.subject_alt_names;
  const flags: string[] = [];
  if (tls.self_signed === true) flags.push('self-signed');
  if (tls.expired === true) flags.push('expired');
  if (tls.mismatched === true) flags.push('hostname mismatch');
  if (tls.wildcard_certificate === true) flags.push('wildcard');
  const expiry = tlsStr(tls, 'not_after');
  // Derive expiry status from the date itself — httpx doesn't reliably set the
  // `expired` boolean, so a cert past (or near) not_after would otherwise look
  // identical to one valid for years.
  let daysToExpiry: number | null = null;
  if (expiry) {
    const t = Date.parse(expiry);
    if (!Number.isNaN(t)) daysToExpiry = Math.floor((t - Date.now()) / 86_400_000);
  }
  // The date-derived "expired" badge already conveys this, so drop the tool's
  // duplicate 'expired' flag when we could read the date.
  const dedupedFlags =
    daysToExpiry != null && daysToExpiry < 0
      ? flags.filter((f) => f !== 'expired')
      : flags;
  const summary: TlsSummary = {
    version: tlsStr(tls, 'tls_version', 'version'),
    issuer: tlsStr(tls, 'issuer_cn', 'issuer_org', 'issuer_dn'),
    expiry,
    daysToExpiry,
    sanCount: Array.isArray(sans) ? sans.length : 0,
    flags: dedupedFlags,
  };
  // Nothing worth a line if every field came back empty.
  if (!summary.version && !summary.issuer && !summary.expiry && summary.sanCount === 0 && flags.length === 0) {
    return null;
  }
  return summary;
};

// Human-readable byte size for content_length.
const fmtBytes = (n: number): string => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

const WebInterfaceRow: React.FC<RowProps> = ({ row, onViewScreenshot }) => {
  const isHttps = (row.protocol || '').toLowerCase() === 'https';
  const tls = summarizeTls(row.tls_info);
  return (
    <div className="flex flex-col gap-sm rounded-control border border-border bg-card p-sm md:flex-row">
      <div className="min-w-0 flex-1">
        <div className="mb-xxs flex items-center gap-xs">
          {isHttps ? (
            <Lock className="size-4 text-success" aria-hidden />
          ) : (
            <Unlock className="size-4 text-muted-foreground" aria-hidden />
          )}
          <a
            href={row.url}
            target="_blank"
            rel="noopener noreferrer"
            className="min-w-0 flex-1 truncate font-mono text-body text-primary underline-offset-4 hover:underline"
            aria-label={`Open ${row.url} in new tab`}
          >
            {row.url}
          </a>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button asChild variant="ghost" size="icon" aria-label={`Open ${row.url} in new tab`}>
                <a href={row.url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="size-4" aria-hidden />
                </a>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Open in new tab</TooltipContent>
          </Tooltip>
        </div>
        <div className="mb-xxs flex flex-wrap items-center gap-xs">
          {row.status_code != null && (
            <Badge variant={statusVariant(row.status_code)}>{row.status_code}</Badge>
          )}
          <Badge variant={sourceBadgeVariant(row.source)}>{row.source}</Badge>
          {row.server_header && (
            <span className="min-w-0 flex-1 truncate font-mono text-caption text-muted-foreground">
              {row.server_header}
            </span>
          )}
        </div>
        {row.title && <p className="mb-xxs line-clamp-2 text-metadata">{row.title}</p>}
        {row.technologies && row.technologies.length > 0 && (
          <div className="flex flex-wrap gap-xxs">
            {row.technologies.map((tech, i) => (
              <Badge key={`${tech}-${i}`} variant="outline" className="max-w-[12rem]">
                <span className="truncate">{tech}</span>
              </Badge>
            ))}
          </div>
        )}

        {/* v4.7.9 — TLS certificate summary.  httpx -tls-probe populates
            this; pre-fix the data was stored but never shown, so an
            operator couldn't see an expired/self-signed/mismatched cert
            without raw-SQL. */}
        {tls && (
          <div className="mt-xs flex flex-wrap items-center gap-xxs">
            <Lock className="size-3 text-muted-foreground" aria-hidden />
            {tls.version && (
              <Badge variant="outline" className="text-caption">{tls.version}</Badge>
            )}
            {tls.issuer && (
              <span className="min-w-0 max-w-[16rem] truncate text-caption text-muted-foreground">
                CA: {tls.issuer}
              </span>
            )}
            {tls.expiry && (
              tls.daysToExpiry != null && tls.daysToExpiry < 0 ? (
                <Badge variant="destructive" className="text-caption">
                  expired {tls.expiry.slice(0, 10)}
                </Badge>
              ) : tls.daysToExpiry != null && tls.daysToExpiry <= 30 ? (
                <Badge
                  variant="outline"
                  className="border-amber-500 text-caption text-amber-600"
                >
                  expires in {tls.daysToExpiry}d
                </Badge>
              ) : (
                <span className="text-caption text-muted-foreground">
                  expires {tls.expiry.slice(0, 10)}
                </span>
              )
            )}
            {tls.sanCount > 0 && (
              <span className="text-caption text-muted-foreground">
                {tls.sanCount} SAN{tls.sanCount === 1 ? '' : 's'}
              </span>
            )}
            {tls.flags.map((flag) => (
              <Badge key={flag} variant="destructive" className="text-caption">
                {flag}
              </Badge>
            ))}
          </div>
        )}

        {/* v4.7.9 — content length + favicon hash.  Both stored by the
            httpx parser; favicon hash is the mmh3 value used to pivot
            to other hosts serving the same favicon. */}
        {(row.content_length != null || row.favicon_hash) && (
          <div className="mt-xxs flex flex-wrap items-center gap-sm text-caption text-muted-foreground">
            {row.content_length != null && (
              <span>body {fmtBytes(row.content_length)}</span>
            )}
            {row.favicon_hash && (
              <span className="min-w-0 max-w-[12rem] truncate font-mono">
                favicon {row.favicon_hash}
              </span>
            )}
          </div>
        )}
      </div>

      {row.has_screenshot && (
        <button
          type="button"
          onClick={onViewScreenshot}
          aria-label={`View screenshot of ${row.url}`}
          className="flex min-h-20 min-w-[6rem] flex-col items-center justify-center gap-xxs rounded-control border border-dashed border-primary px-sm py-xs text-primary transition-colors hover:bg-accent focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 md:min-w-[7.5rem]"
        >
          <ImageIcon className="size-5" aria-hidden />
          <span className="text-caption">View screenshot</span>
        </button>
      )}
    </div>
  );
};

export default WebInterfacesCard;
