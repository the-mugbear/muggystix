import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ExternalLink, Globe, Image as ImageIcon, Loader2, Lock, Unlock } from 'lucide-react';

import {
  WebInterface,
  getHostWebInterfaces,
  fetchWebInterfaceScreenshot,
} from '../services/api';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import { latestObservations } from '../utils/latestObservations';
import { webObservedAt } from '../utils/portEndpoints';
import { formatRelativeTime } from '../utils/relativeTime';
import ScreenshotLightbox from './ScreenshotLightbox';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { InspectorSection } from './host-inspector/InspectorSection';
import { safeHttpHref } from '../utils/safeHref';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

interface WebInterfacesCardProps {
  hostId: number;
  // Count from HostDetail, used to decide whether to mount + fetch
  // at all.  If 0, the card renders nothing.
  count: number;
  /** v5.297.0 — rows already loaded (a service's own, from the Services
   *  section); skips the fetch. */
  rows?: WebInterface[];
  /** v5.297.0 — inside a service panel: the list without its own section. */
  embedded?: boolean;
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
const WebInterfacesCard: React.FC<WebInterfacesCardProps> = ({ hostId, count, rows: given, embedded = false }) => {
  const [fetched, setRows] = useState<WebInterface[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rows = given ?? fetched;

  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [lightboxLoading, setLightboxLoading] = useState(false);
  const [lightboxError, setLightboxError] = useState<string | null>(null);
  const [lightboxCaption, setLightboxCaption] = useState<string>('');

  useEffect(() => {
    if (count === 0 || given) return;
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
  }, [hostId, count, given]);

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
      // Dated: a screenshot opened from an earlier observation must not read
      // as the site's current state.
      setLightboxCaption(`${row.title ? `${row.url} — ${row.title}` : row.url} · ${whenLabel(row)} · scan #${row.scan_id}`);
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

  // v5.241.0 — one row per scan is kept in the data; the same URL from the
  // same tool is ONE row here (the latest), saying how many scans saw it.
  const observed = latestObservations(
    rows ?? [],
    (r) => `${r.source}|${r.url}`,
    // v5.244.0 — by when it was OBSERVED (the scan's own time), never by
    // `last_seen`, which is the row's write time (utils/portEndpoints).
    webObservedAt,
  );

  const body = (
      <div>
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
          <div className="divide-y divide-border">
            {observed.map(({ latest, members }) => (
              <WebInterfaceRow
                key={latest.id}
                row={latest}
                earlier={members.filter((m) => m.id !== latest.id)}
                onViewScreenshot={openScreenshot}
              />
            ))}
          </div>
        )}

      <ScreenshotLightbox
        open={lightboxOpen}
        onClose={closeLightbox}
        src={lightboxSrc}
        loading={lightboxLoading}
        error={lightboxError}
        caption={lightboxCaption}
      />
      </div>
  );

  if (embedded) return body;
  return (
    <InspectorSection
      id="host-detail-web"
      title="Web interfaces"
      icon={<Globe className="size-4 shrink-0 text-primary" aria-hidden />}
      // Distinct interfaces once loaded (the prop counts per-scan rows).
      count={rows ? observed.length : count}
    >
      {body}
    </InspectorSection>
  );
};

// ---------------------------------------------------------------------------

interface RowProps {
  row: WebInterface;
  /** Earlier observations of this URL by this tool, newest first; `row` is the
   *  latest. Reachable from the row — each with its own screenshot. */
  earlier?: WebInterface[];
  onViewScreenshot: (row: WebInterface) => void;
}

/** "observed 9d ago" when the scan recorded its own time, "imported 9d ago"
 *  when all that is known is the upload — never one word for both. */
const whenLabel = (row: WebInterface): string =>
  `${row.observed_at_basis === 'scan' ? 'observed' : 'imported'} ${formatRelativeTime(webObservedAt(row), { fallback: 'time unknown' })}`;

const whenTitle = (row: WebInterface): string | undefined => {
  const at = webObservedAt(row);
  if (!at) return undefined;
  const scan = row.scan_filename ? ` · ${row.scan_filename}` : '';
  return row.observed_at_basis === 'scan'
    ? `Observed ${new Date(at).toLocaleString()} (the scan's own time) · scan #${row.scan_id}${scan}`
    : `Imported ${new Date(at).toLocaleString()} — the tool recorded no scan time, so this is when the file was uploaded, not when the site was seen · scan #${row.scan_id}${scan}`;
};

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

const WebInterfaceRow: React.FC<RowProps> = ({ row, earlier = [], onViewScreenshot }) => {
  const [historyOpen, setHistoryOpen] = useState(false);
  const isHttps = (row.protocol || '').toLowerCase() === 'https';
  const tls = summarizeTls(row.tls_info);
  return (
    // v5.241.0 — a divided row, not a bordered box per URL: a plain interface
    // is two lines (URL; status · source · server · title · size).
    <div className="flex flex-col gap-sm py-xs first:pt-0 last:pb-0 md:flex-row">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-xs">
          {isHttps ? (
            <Lock className="size-4 text-success" aria-hidden />
          ) : (
            <Unlock className="size-4 text-muted-foreground" aria-hidden />
          )}
          <a
            href={safeHttpHref(row.url)}
            target="_blank"
            rel="noopener noreferrer"
            className="min-w-0 flex-1 truncate font-mono text-body text-primary underline-offset-4 hover:underline"
            aria-label={`Open ${row.url} in new tab`}
          >
            {row.url}
          </a>
          {safeHttpHref(row.url) && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button asChild variant="ghost" size="icon" className="size-7 shrink-0" aria-label={`Open ${row.url} in new tab`}>
                  <a href={safeHttpHref(row.url)} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="size-3.5" aria-hidden />
                  </a>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Open in new tab</TooltipContent>
            </Tooltip>
          )}
        </div>
        <div className="mb-xxs flex flex-wrap items-center gap-xs">
          {row.status_code != null && (
            <Badge variant={statusVariant(row.status_code)}>{row.status_code}</Badge>
          )}
          <Badge variant={sourceBadgeVariant(row.source)}>{row.source}</Badge>
          {row.fqdn && row.name_id != null && (
            <Link
              to={`/names?name_id=${row.name_id}`}
              className="max-w-[20rem] truncate font-mono text-caption text-primary hover:underline"
              title={`Named endpoint: ${row.fqdn}`}
            >
              {row.fqdn}
            </Link>
          )}
          {row.server_header && (
            <span className="min-w-0 max-w-[16rem] truncate font-mono text-caption text-muted-foreground" title={row.server_header}>
              {row.server_header}
            </span>
          )}
          {row.title && (
            <span className="min-w-0 max-w-[24rem] truncate text-metadata" title={row.title}>{row.title}</span>
          )}
          {/* v4.7.9 — content length + favicon hash (the mmh3 value used to
              pivot to other hosts serving the same favicon). */}
          {row.content_length != null && (
            <span className="shrink-0 text-caption text-muted-foreground">body {fmtBytes(row.content_length)}</span>
          )}
          {row.favicon_hash && (
            <span className="min-w-0 max-w-[12rem] truncate font-mono text-caption text-muted-foreground" title={row.favicon_hash}>
              favicon {row.favicon_hash}
            </span>
          )}
          {/* When it was observed — and, for a re-scanned URL, that the row is
              the latest of several rather than the only one. */}
          <span className="shrink-0 text-caption text-muted-foreground" title={whenTitle(row)}>
            {whenLabel(row)}
          </span>
          {/* v5.244.0 — the earlier observations are one click away. "seen in
              2 scans" used to be a count with nothing behind it. */}
          {earlier.length > 0 && (
            <button
              type="button"
              onClick={() => setHistoryOpen((v) => !v)}
              aria-expanded={historyOpen}
              className="shrink-0 rounded text-caption text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {earlier.length} earlier observation{earlier.length === 1 ? '' : 's'}
              {/* The latest scan took no screenshot, an earlier one did: say so
                  here, or the evidence reads as absent. */}
              {!row.has_screenshot && earlier.some((m) => m.has_screenshot) && ' (with a screenshot)'}
              {' · '}{historyOpen ? 'hide' : 'show'}
            </button>
          )}
        </div>
        {/* v5.276.0 — what the page said (EyeWitness's captured text),
            two lines; the full text is on hover. */}
        {row.page_text && row.page_text.trim() && (
          <p className="line-clamp-2 break-words text-caption text-muted-foreground" title={row.page_text.slice(0, 2000)}>
            {row.page_text.trim()}
          </p>
        )}
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

        {/* Earlier observations of this URL by this tool, newest first. What
            CHANGED is what an analyst wants from history, so each line carries
            its own status, server and title — and its OWN screenshot, dated:
            an older capture is never offered as if it showed the latest state. */}
        {historyOpen && earlier.length > 0 && (
          <ul className="mt-xs divide-y divide-border rounded-control border border-border px-sm" aria-label={`Earlier observations of ${row.url}`}>
            {earlier.map((old) => (
              <li key={old.id} className="flex min-w-0 flex-wrap items-center gap-xs py-xxs">
                <span className="shrink-0 text-caption text-muted-foreground" title={whenTitle(old)}>
                  {whenLabel(old)}
                </span>
                {old.status_code != null && (
                  <Badge variant={statusVariant(old.status_code)}>{old.status_code}</Badge>
                )}
                {old.server_header && (
                  <span className="min-w-0 max-w-[14rem] truncate font-mono text-caption text-muted-foreground" title={old.server_header}>
                    {old.server_header}
                  </span>
                )}
                {old.title && (
                  <span className="min-w-0 max-w-[20rem] truncate text-caption" title={old.title}>{old.title}</span>
                )}
                <span className="shrink-0 text-caption text-muted-foreground">scan #{old.scan_id}</span>
                {old.has_screenshot && (
                  <button
                    type="button"
                    onClick={() => onViewScreenshot(old)}
                    className="ml-auto inline-flex shrink-0 items-center gap-xxs rounded text-caption text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={`View the screenshot from scan #${old.scan_id} of ${row.url}`}
                  >
                    <ImageIcon className="size-3.5" aria-hidden /> screenshot from this scan
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {row.has_screenshot && (
        <button
          type="button"
          onClick={() => onViewScreenshot(row)}
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
