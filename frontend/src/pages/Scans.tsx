import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useDropzone } from 'react-dropzone';
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Eye,
  GitCompareArrows,
  Hourglass,
  Loader2,
  Search,
  SquareArrowOutUpRight,
  Terminal,
  Trash2,
  Upload,
} from 'lucide-react';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import {
  getScans,
  deleteScan,
  uploadFile,
  getIngestionJob,
  getRecentIngestionJobs,
  dismissIngestionJob,
  getScanCommandExplanation,
} from '../services/api';
import type { Scan, IngestionJob, CommandExplanation } from '../services/api';
import LastUpdated from '../components/LastUpdated';
import { ListPageSkeleton } from '../components/PageSkeleton';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Checkbox } from '../components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { RadioGroup, RadioGroupItem } from '../components/ui/radio-group';
import { Separator } from '../components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import { cn } from '../utils/cn';

type ChipTone = 'default' | 'success' | 'warning' | 'destructive' | 'info' | 'secondary' | 'outline' | 'muted';

// v4.60.0 — full list synced with documentation/UPLOAD_FORMATS.md.
// Alphabetised within sections so operators can scan; recent
// additions (dnsx, httpx, DirBuster family, RustScan) are tagged so
// the auto-detect contract is clear.  Filename hints help the
// content-detection dispatcher when the upload's extension is
// ambiguous (e.g. ``out.json`` could be many tools).
const SUPPORTED_FORMATS: Array<{ tool: string; formats: string; desc: string }> = [
  { tool: 'Nmap', formats: '.xml / .gnmap', desc: 'XML and grepable output.' },
  { tool: 'Masscan', formats: '.xml / .json / .txt', desc: 'High-speed port scan; XML/JSON exports or --output-filename list.' },
  { tool: 'RustScan', formats: '.txt', desc: 'Bracketed-list output (e.g. "10.0.0.1 -> [22,80]"). Include "rustscan" in filename for auto-detect.' },
  { tool: 'Naabu', formats: '.json / .txt', desc: 'Host:port discovery output.' },
  { tool: 'Nessus', formats: '.nessus / .xml', desc: 'Vulnerability scan exports.' },
  { tool: 'OpenVAS / Greenbone', formats: '.xml', desc: 'Streaming-parsed for large reports (v2.86.11+).' },
  { tool: 'httpx (ProjectDiscovery)', formats: '.json / .jsonl', desc: 'Web fingerprint output; feeds the web_interfaces view alongside EyeWitness.' },
  { tool: 'dnsx (ProjectDiscovery)', formats: '.json / .jsonl', desc: 'DNS resolution against operator-supplied resolvers. PTR answers populate Host.hostname; per-record resolver attribution is preserved (v2.89.0).' },
  { tool: 'Amass / Subfinder', formats: '.json / .txt', desc: 'Subdomain discovery; best results with exports that include resolved IPs.' },
  { tool: 'EyeWitness', formats: '.json / .csv / .zip', desc: 'Web screenshot metadata. ZIP bundle accepted; bomb-caps applied (≤50MB/file, ≤500MB/bundle).' },
  { tool: 'Nikto', formats: '.json / .csv / .txt', desc: 'Web findings exports.' },
  { tool: 'NetExec (NXC)', formats: '.json / .txt', desc: 'SMB/LDAP/WMI/WinRM enumeration via Spider or standard text report.' },
  { tool: 'SMBMap', formats: '.json / .txt', desc: 'SMB enumeration output; preserves standard "[+] <ip>" host lines.' },
  { tool: 'BloodHound / SharpHound', formats: '.json', desc: 'Extracted JSON (not the ZIP bundle). Files ≥50MB stream via ijson.' },
  { tool: 'DirBuster / Gobuster / Feroxbuster / ffuf / Dirsearch', formats: '.json / .csv / .txt', desc: 'Directory brute-force output (unified parser). Include tool name in filename for best auto-detect.' },
  { tool: 'DNS records (CSV)', formats: '.csv', desc: 'Columns: record_type, name, address. Used for ad-hoc DNS enrichment.' },
];

const ProgressBar: React.FC<{ value: number; tone?: 'default' | 'success' | 'destructive' }> = ({
  value,
  tone = 'default',
}) => (
  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
    <div
      className={cn(
        'h-full transition-all',
        tone === 'success' && 'bg-success',
        tone === 'destructive' && 'bg-destructive',
        tone === 'default' && 'bg-primary',
      )}
      style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
    />
  </div>
);

const formatDateTime = (value: Date | string | null | undefined): string => {
  if (!value) return 'Unknown';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return 'Unknown';
  return d.toLocaleString();
};

const formatDuration = (ms: number): string => {
  if (ms <= 0) return 'Instant';
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
};

const getStatusTone = (upHosts: number, totalHosts: number): ChipTone => {
  if (totalHosts === 0) return 'muted';
  const ratio = upHosts / totalHosts;
  if (ratio > 0.8) return 'success';
  if (ratio > 0.5) return 'warning';
  return 'destructive';
};

const isNessusScan = (scan: Scan) => (scan.tool_name || scan.scan_type || '').toLowerCase().includes('nessus');
const isMasscanScan = (scan: Scan) => (scan.tool_name || scan.scan_type || '').toLowerCase().includes('masscan');

export default function Scans() {
  const navigate = useNavigate();
  const toast = useToast();
  const [scans, setScans] = useState<Scan[]>([]);
  const [loading, setLoading] = useState(true);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [scanToDelete, setScanToDelete] = useState<Scan | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  // v2.43.1 — bug fix: per-file upload entries gained `startedAt` so the
  // watchdog effect below can detect entries that get stuck in the
  // 'uploading' state (we've seen this happen when xhr.upload.onprogress
  // never fires on small fast uploads AND something in the .then path
  // silently no-ops).  Stuck entries auto-clear with a console.warn after
  // 60s so the user isn't trapped in a "Uploading: 0%" banner forever.
  const [uploadProgress, setUploadProgress] = useState<
    Record<
      string,
      {
        filename: string;
        percent: number;
        status: 'uploading' | 'parsing' | 'done' | 'error';
        error?: string;
        startedAt: number;
      }
    >
  >({});
  const [enrichDns, setEnrichDns] = useState(false);
  const [dnsServerType, setDnsServerType] = useState<'default' | 'custom'>('default');
  const [customDnsServer, setCustomDnsServer] = useState('');

  const [activeJobIds, setActiveJobIds] = useState<number[]>([]);
  const [activeJobs, setActiveJobs] = useState<Record<number, IngestionJob>>({});
  const [recentJobs, setRecentJobs] = useState<IngestionJob[]>([]);
  const [recentJobsFetched, setRecentJobsFetched] = useState<Date | null>(null);
  const [recentJobsLoading, setRecentJobsLoading] = useState(false);

  const [expandedScanIds, setExpandedScanIds] = useState<number[]>([]);

  // ---------------------------------------------------------------------
  // Scan Inventory filters + pagination (v4.47.0 QoL pass).
  //
  // All filtering and sorting is server-side: the user has projects with
  // >100 scans, and client-side filtering on the first 100 would silently
  // miss matching scans further down the list.  The URL persists every
  // filter so analysts can bookmark / share a filtered view.
  //
  // Pagination is "Load more" rather than paged navigation — keeps the
  // append model simple, avoids needing a server-side total count, and
  // works smoothly with sort/filter changes (those reset back to skip=0).
  //
  // v2.86.2 — bumped initial page from 100 → 250 after a field report
  // that the page "capped at 100" because the Load More button at the
  // bottom of the table was off-screen and not noticed.  250 covers the
  // vast majority of installations in one page; the button stays as
  // the fallback for the long tail.
  // ---------------------------------------------------------------------
  const SCAN_LIMIT = 250;
  const DATE_RANGE_PRESETS: ReadonlyArray<{ label: string; days: number | null }> = [
    { label: 'All time', days: null },
    { label: 'Last 7d', days: 7 },
    { label: 'Last 30d', days: 30 },
    { label: 'Last 90d', days: 90 },
  ];
  type SortBy = 'created_at' | 'filename' | 'tool_name' | 'total_hosts';
  type SortOrder = 'asc' | 'desc';

  const [urlParams, setUrlParams] = useSearchParams();
  const [toolFilter, setToolFilter] = useState(() => urlParams.get('tool') || '');
  const [searchText, setSearchText] = useState(() => urlParams.get('search') || '');
  const [dateRangeDays, setDateRangeDays] = useState<number | null>(() => {
    const raw = urlParams.get('days');
    if (!raw) return null;
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  });
  const [sortBy, setSortBy] = useState<SortBy>(() => {
    const raw = urlParams.get('sort_by') as SortBy | null;
    return raw && ['created_at', 'filename', 'tool_name', 'total_hosts'].includes(raw)
      ? raw
      : 'created_at';
  });
  const [sortOrder, setSortOrder] = useState<SortOrder>(() => {
    const raw = urlParams.get('sort_order');
    return raw === 'asc' ? 'asc' : 'desc';
  });
  const debouncedSearchText = useDebouncedValue(searchText, 300);
  const [hasMoreScans, setHasMoreScans] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Avoid the URL-sync effect firing during the very first render before
  // the user has touched anything — react-router would still write an
  // empty query string, which churns the browser history.
  const filtersInitialized = useRef(false);

  const hasActiveFilters = toolFilter !== '' || debouncedSearchText.trim() !== '' || dateRangeDays !== null;
  const createdAfterIso = useMemo(() => {
    if (dateRangeDays == null) return undefined;
    return new Date(Date.now() - dateRangeDays * 24 * 60 * 60 * 1000).toISOString();
  }, [dateRangeDays]);
  const [expandedJobIds, setExpandedJobIds] = useState<Set<number>>(new Set());
  const [commandCache, setCommandCache] = useState<Record<number, CommandExplanation>>({});

  const toggleJobExpanded = (id: number) => {
    setExpandedJobIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const fetchRecentJobs = useCallback(async () => {
    setRecentJobsLoading(true);
    try {
      const jobs = await getRecentIngestionJobs(25);
      setRecentJobs(jobs);
      setRecentJobsFetched(new Date());
    } catch (err) {
      console.error('Error fetching ingestion jobs:', err);
    } finally {
      setRecentJobsLoading(false);
    }
  }, []);

  const fetchScans = useCallback(async () => {
    try {
      const data = await getScans(0, SCAN_LIMIT, {
        search: debouncedSearchText.trim() || undefined,
        tool: toolFilter || undefined,
        createdAfter: createdAfterIso,
        sortBy,
        sortOrder,
      });
      setScans(data);
      setHasMoreScans(data.length === SCAN_LIMIT);
    } catch (err) {
      console.error('Error fetching scans:', err);
    } finally {
      setLoading(false);
    }
  }, [toolFilter, debouncedSearchText, createdAfterIso, sortBy, sortOrder]);

  const loadMoreScans = useCallback(async () => {
    if (loadingMore || !hasMoreScans) return;
    setLoadingMore(true);
    try {
      const data = await getScans(scans.length, SCAN_LIMIT, {
        search: debouncedSearchText.trim() || undefined,
        tool: toolFilter || undefined,
        createdAfter: createdAfterIso,
        sortBy,
        sortOrder,
      });
      setScans((prev) => [...prev, ...data]);
      setHasMoreScans(data.length === SCAN_LIMIT);
    } catch (err) {
      console.error('Error loading more scans:', err);
    } finally {
      setLoadingMore(false);
    }
  }, [
    scans.length,
    toolFilter,
    debouncedSearchText,
    createdAfterIso,
    sortBy,
    sortOrder,
    loadingMore,
    hasMoreScans,
  ]);

  // Per-row tool badge rendered as a clickable filter — same behaviour
  // as the section-header chips.  stopPropagation so it doesn't also
  // trigger the row's own expand/click handlers.
  const renderInlineToolBadge = (scan: Scan) => {
    const label = scan.tool_name || scan.scan_type || 'Unknown';
    const toolKey = (label || 'Other').toUpperCase();
    const active = toolFilter.toLowerCase() === toolKey.toLowerCase();
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setToolFilter(active ? '' : toolKey);
        }}
        aria-pressed={active}
        className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title={active ? `Clear ${toolKey} filter` : `Show only ${toolKey} scans`}
      >
        <Badge variant={active ? 'default' : 'outline'}>{label}</Badge>
      </button>
    );
  };

  // Sortable column header — clicking the same column toggles asc/desc;
  // clicking a different column switches sort and resets to desc.
  const handleSort = (column: SortBy) => {
    if (sortBy === column) {
      setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortBy(column);
    setSortOrder('desc');
  };
  const renderSortHeader = (column: SortBy, label: string, className?: string) => {
    const isSorted = sortBy === column;
    const ariaSort: React.AriaAttributes['aria-sort'] = isSorted
      ? sortOrder === 'asc'
        ? 'ascending'
        : 'descending'
      : 'none';
    return (
      <TableHead className={className} aria-sort={ariaSort}>
        <button
          type="button"
          onClick={() => handleSort(column)}
          className="inline-flex items-center gap-xxs rounded-control text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Sort by ${label}, currently ${
            isSorted ? (sortOrder === 'asc' ? 'sorted ascending' : 'sorted descending') : 'not sorted'
          }`}
        >
          {label}
          {!isSorted && <ArrowUpDown className="size-3 opacity-50" aria-hidden />}
          {isSorted && sortOrder === 'asc' && <ArrowUp className="size-3" aria-hidden />}
          {isSorted && sortOrder === 'desc' && <ArrowDown className="size-3" aria-hidden />}
        </button>
      </TableHead>
    );
  };

  // URL sync — write the active filters/sort back to the URL whenever
  // they change so the browser back/forward + bookmark/share use cases
  // work.  Skipped on the very first render so we don't churn history
  // with a no-op write.
  useEffect(() => {
    if (!filtersInitialized.current) {
      filtersInitialized.current = true;
      return;
    }
    const next = new URLSearchParams(urlParams);
    if (debouncedSearchText.trim()) next.set('search', debouncedSearchText.trim());
    else next.delete('search');
    if (toolFilter) next.set('tool', toolFilter);
    else next.delete('tool');
    if (dateRangeDays != null) next.set('days', String(dateRangeDays));
    else next.delete('days');
    if (sortBy !== 'created_at') next.set('sort_by', sortBy);
    else next.delete('sort_by');
    if (sortOrder !== 'desc') next.set('sort_order', sortOrder);
    else next.delete('sort_order');
    setUrlParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearchText, toolFilter, dateRangeDays, sortBy, sortOrder]);

  useEffect(() => {
    fetchScans();
    fetchRecentJobs();
  }, [fetchScans, fetchRecentJobs]);

  const toggleScanExpanded = useCallback(
    (scanId: number) => {
      setExpandedScanIds((prev) => (prev.includes(scanId) ? prev.filter((id) => id !== scanId) : [...prev, scanId]));
      if (!commandCache[scanId]) {
        getScanCommandExplanation(scanId)
          .then((data) => setCommandCache((prev) => ({ ...prev, [scanId]: data })))
          // Audit FBK·L6 — on failure DON'T write to the cache.  The
          // pre-audit shape stored a synthetic "Failed to load" entry,
          // which then short-circuited every subsequent re-open via the
          // `!commandCache[scanId]` guard and the user could never
          // retry.  Leaving the entry undefined means the next click
          // re-triggers the fetch.
          .catch(() => {
            /* leave cache untouched so the next click retries */
          });
      }
    },
    [commandCache],
  );

  const onDrop = (acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return;

    if (enrichDns && dnsServerType === 'custom' && customDnsServer.trim() === '') {
      setUploadError('Please enter a custom DNS server or select "Use system default DNS servers"');
      return;
    }

    setUploadError(null);
    setUploadSuccess(null);

    const dnsConfig = enrichDns
      ? { enabled: true, server: dnsServerType === 'custom' ? customDnsServer.trim() : undefined }
      : { enabled: false };

    // v2.43.1 — bug fix: keys generated UPFRONT (one per file) before
    // any setState call.  Pre-fix the keys lived inside a setUploadProgress
    // updater that ALSO mutated a closure-captured `progressKeys` object —
    // a side-effect-in-reducer antipattern that strict-mode runs twice,
    // making the relationship between the outer for loop and the
    // per-file closures fragile.  Now the upload-per-file helper owns the
    // key and the entry's lifecycle end-to-end.
    const startedAt = Date.now();
    const fileKeys = acceptedFiles.map((file) => ({
      file,
      key: `${startedAt}-${Math.random().toString(36).slice(2, 8)}-${file.name}`,
    }));

    setUploadProgress((prev) => {
      const next = { ...prev };
      for (const { file, key } of fileKeys) {
        next[key] = {
          filename: file.name,
          percent: 0,
          status: 'uploading',
          startedAt,
        };
      }
      return next;
    });

    setUploading(true);

    let remaining = fileKeys.length;
    const markDone = () => {
      remaining -= 1;
      if (remaining === 0) setUploading(false);
    };

    for (const { file, key } of fileKeys) {
      uploadFile(file, dnsConfig, (percent) => {
        setUploadProgress((prev) => {
          const existing = prev[key];
          if (!existing) return prev; // entry already cleaned up — ignore late progress
          return {
            ...prev,
            [key]: {
              ...existing,
              percent,
              status: percent >= 100 ? 'parsing' : 'uploading',
            },
          };
        });
      })
        .then((result) => {
          // Flip to 'done' so the banner reads "Upload complete: X%".
          // Use prev as the source of truth — if the entry was removed
          // (watchdog, manual dismiss), this is a no-op.
          setUploadProgress((prev) => {
            const existing = prev[key];
            return {
              ...prev,
              [key]: {
                ...(existing ?? { filename: file.name, startedAt }),
                percent: 100,
                status: 'done',
              },
            };
          });
          if (result?.job_id != null) {
            setActiveJobIds((prev) =>
              prev.includes(result.job_id) ? prev : [...prev, result.job_id],
            );
          }
          // Auto-dismiss the success banner after a short pause.
          setTimeout(() => {
            setUploadProgress((prev) => {
              const { [key]: _removed, ...rest } = prev;
              return rest;
            });
          }, 1500);
        })
        .catch((err: unknown) => {
          setUploadProgress((prev) => {
            const existing = prev[key];
            return {
              ...prev,
              [key]: {
                ...(existing ?? { filename: file.name, percent: 0, startedAt }),
                status: 'error',
                // Audit FBK·H11 — route through formatApiError so the user
                // sees the same normalized error shape (validation list,
                // detail, fallback) used elsewhere instead of a raw
                // response.data.detail that may be a list or undefined.
                error: formatApiError(err, 'Upload failed'),
              },
            };
          });
        })
        .finally(markDone);
    }

    setUploadDialogOpen(false);
  };

  // v2.43.1 watchdog: clear per-file entries that never escaped 'uploading'.
  // We've seen "stuck at 0%" banners that survive page-life because some
  // combination of strict-mode + the old key-in-reducer antipattern dropped
  // the .then state update; this fail-safe prevents the user from being
  // trapped behind a stale banner.  Successful flow clears within ~1.5s of
  // upload completion, so 60s is a generous floor.
  useEffect(() => {
    const stuck = Object.entries(uploadProgress).filter(
      ([, entry]) => entry.status === 'uploading' && Date.now() - entry.startedAt > 60_000,
    );
    if (stuck.length === 0) return;
    const timer = setTimeout(() => {
      setUploadProgress((prev) => {
        const next = { ...prev };
        for (const [key, entry] of stuck) {
          if (next[key]?.status === 'uploading') {
            console.warn(
              '[scans] upload watchdog clearing stale entry %o (no progress in 60s); ' +
                'check the network tab — the .then/onprogress path may have silently failed.',
              entry,
            );
            delete next[key];
          }
        }
        return next;
      });
    }, 1000);
    return () => clearTimeout(timer);
  }, [uploadProgress]);

  const { getRootProps, getInputProps, isDragActive, fileRejections } = useDropzone({
    onDrop,
    accept: {
      'text/xml': ['.xml', '.nessus'],
      'application/json': ['.json'],
      'text/csv': ['.csv'],
      'text/plain': ['.txt', '.gnmap'],
    },
    // v4.28.0 — keep in lockstep with nginx (ssl-nginx.conf
    // `client_max_body_size`) and backend (`MAX_FILE_SIZE` in .env).
    // v2.63.0 raised both to 2GB but missed this client-side gate,
    // so 600MB+ Nessus uploads were instantly rejected before
    // hitting the network.  Reject client-side so the user gets an
    // instant error instead of a multi-minute upload ending in 413
    // (audit H5).
    maxSize: 2 * 1024 * 1024 * 1024,
    multiple: true,
  });

  useEffect(() => {
    if (activeJobIds.length === 0) return undefined;
    let cancelled = false;

    const pollJobs = async () => {
      // Audit H20: was sequential per-job awaits; 10 active jobs on
      // a 200ms-RTT network was 2s of waterfall per poll tick.  Fan
      // out via Promise.all so the round-trip is one batch.
      const results = await Promise.allSettled(activeJobIds.map(getIngestionJob));
      if (cancelled) return;
      const finishedIds: number[] = [];
      let anyCompleted = false;
      const next: Record<number, IngestionJob> = {};
      results.forEach((r, idx) => {
        if (r.status !== 'fulfilled') return;
        const job = r.value;
        next[activeJobIds[idx]] = job;
        if (job.status === 'completed' || job.status === 'failed') {
          finishedIds.push(activeJobIds[idx]);
          if (job.status === 'completed') anyCompleted = true;
        }
      });
      setActiveJobs((prev) => ({ ...prev, ...next }));
      if (finishedIds.length > 0) {
        setActiveJobIds((prev) => prev.filter((id) => !finishedIds.includes(id)));
        fetchRecentJobs();
        if (anyCompleted) fetchScans();
      }
    };

    pollJobs();
    const interval = setInterval(pollJobs, 4000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [activeJobIds, fetchScans, fetchRecentJobs]);

  // Recent-jobs polling — depend only on the boolean, not the full
  // recentJobs array.  Pre-audit (H20) this effect re-ran on every
  // poll tick (because each tick called setRecentJobs), which cleared
  // and recreated the interval, producing uneven cadence and
  // occasional duplicate intervals.
  const hasActiveRecent = useMemo(
    () => recentJobs.some((j) => j.status === 'queued' || j.status === 'processing'),
    [recentJobs],
  );
  useEffect(() => {
    if (!hasActiveRecent) return undefined;
    const interval = setInterval(fetchRecentJobs, 5000);
    return () => clearInterval(interval);
  }, [hasActiveRecent, fetchRecentJobs]);

  const groupedScans = useMemo(
    () =>
      scans.reduce<Record<string, Scan[]>>((acc, scan) => {
        const key = (scan.tool_name || scan.scan_type || 'Other').toUpperCase();
        if (!acc[key]) acc[key] = [];
        acc[key].push(scan);
        return acc;
      }, {}),
    [scans],
  );

  const orderedToolGroups = useMemo(
    () => Object.keys(groupedScans).sort((a, b) => a.localeCompare(b)),
    [groupedScans],
  );

  // Rows render in the order the server returned them (`scans` is used
  // directly below — no client-side re-sort).  getScans is called with
  // sortBy/sortOrder and each appended page preserves that order; a
  // client-side re-sort here previously discarded the server order, so the
  // filename / host-count headers and created_at-ascending appeared dead —
  // the arrow flipped but the rows never moved.

  // v4.18.0 — completed jobs are excluded from the Ingestion Queue
  // display.  Successful ingests already appear in Your Scans below as
  // the canonical Scan row; showing them twice was duplicate-feeling
  // and made the queue look perpetually busy.  Keep `queued`,
  // `processing`, `failed` so the queue is useful for its actual
  // intent: "what's still in flight or needs my attention?".
  const pendingJobs = useMemo(
    () => recentJobs.filter((j) => j.status !== 'completed'),
    [recentJobs],
  );

  const scanSummary = useMemo(() => {
    const totalHosts = scans.reduce((sum, scan) => sum + (scan.total_hosts || 0), 0);
    const upHosts = scans.reduce((sum, scan) => sum + (scan.up_hosts || 0), 0);
    const openServices = scans.reduce((sum, scan) => sum + (scan.open_ports || 0), 0);
    const queuedOrProcessing = recentJobs.filter((j) => j.status === 'queued' || j.status === 'processing').length;
    return { totalHosts, upHosts, openServices, queuedOrProcessing };
  }, [scans, recentJobs]);

  const handleViewScan = (scanId: number) => navigate(`/scans/${scanId}`);
  const handleDeleteClick = (scan: Scan) => {
    setScanToDelete(scan);
    setDeleteDialogOpen(true);
  };
  const handleDeleteConfirm = async () => {
    if (!scanToDelete || deleteLoading) return;
    setDeleteLoading(true);
    try {
      await deleteScan(scanToDelete.id);
      setScans((prev) => prev.filter((s) => s.id !== scanToDelete.id));
      toast.success(`Scan "${scanToDelete.filename}" deleted.`);
      setDeleteDialogOpen(false);
      setScanToDelete(null);
    } catch (err) {
      // Pre-audit shape silently swallowed failures and closed the
      // dialog, leaving the row in the list while the user believed
      // the delete succeeded.  Keep the dialog open on failure so the
      // user can retry; surface a real toast (audit C8).
      toast.error(formatApiError(err, `Failed to delete scan "${scanToDelete.filename}".`));
    } finally {
      setDeleteLoading(false);
    }
  };

  const getScanWindow = (scan: Scan) => {
    const start = scan.start_time ? new Date(scan.start_time) : new Date(scan.created_at);
    let end = scan.end_time ? new Date(scan.end_time) : new Date(scan.created_at);
    if (Number.isNaN(start.getTime())) start.setTime(new Date(scan.created_at).getTime());
    if (Number.isNaN(end.getTime()) || end < start) end = new Date(start.getTime());
    const durationMs = Math.max(end.getTime() - start.getTime(), 0);
    return { start, end, durationMs };
  };

  // v2.59.0 — Scan Timeline removed from this page and replaced by the
  // cross-project /tool-activity surface, which plots SOC-correlation
  // markers using the scan's actual start_time (the SOC use case) rather
  // than upload time.  See ActivityTimeline component for the
  // generalised lane-packing + bar-vs-dot rendering.

  // Pre-v4.11.5 this returned per-scan-type "headline" badges (host%
  // for default, open services for masscan, findings count for Nessus)
  // that the Metrics column already conveyed.  Reduced to default/
  // masscan null in 4.11.1; finishing the job here by dropping the
  // Nessus branch too — critical/high/total are in metricBadges and
  // re-rendering them in the Scan column was the same duplication
  // pattern.  Kept for now as a no-op so the JSX call sites stay
  // small and a future workflow that genuinely needs a Scan-column
  // headline has somewhere to land.
  const statusBadge = (_scan: Scan): React.ReactNode => null;

  const metricBadges = (scan: Scan): React.ReactNode => {
    if (isNessusScan(scan)) {
      const s = scan.vulnerability_summary;
      const total = s?.total ?? 0;
      const crit = s?.critical ?? 0;
      const high = s?.high ?? 0;
      return (
        <div className="flex flex-wrap gap-xxs">
          <Badge variant={crit > 0 ? 'severity-critical' : 'outline'}>{crit} critical</Badge>
          <Badge variant={high > 0 ? 'severity-high' : 'outline'}>{high} high</Badge>
          <Badge variant={total > 0 ? 'info' : 'outline'}>{total} findings</Badge>
        </div>
      );
    }
    if (isMasscanScan(scan)) {
      const tcp = scan.port_breakdown?.open_tcp_ports ?? scan.open_ports ?? 0;
      const udp = scan.port_breakdown?.open_udp_ports ?? 0;
      const unique = scan.port_breakdown?.unique_ports ?? 0;
      return (
        <div className="flex flex-wrap gap-xxs">
          <Badge variant={tcp > 0 ? 'default' : 'outline'}>{tcp} TCP</Badge>
          <Badge variant={udp > 0 ? 'secondary' : 'outline'}>{udp} UDP</Badge>
          <Badge variant={unique > 0 ? 'info' : 'outline'}>{unique} unique</Badge>
        </div>
      );
    }
    // Default-scan metric is the port-up ratio in a single badge,
    // parallel to the Hosts column's "up/total" shape.  The previous
    // two-badge form ("{open} open" + "{total} ports") repeated the
    // same dimension twice and made the cell busier than it needed.
    const open = scan.open_ports || 0;
    const total = scan.total_ports || 0;
    return (
      <Badge variant={open > 0 ? 'success' : 'outline'}>
        {open}/{total} open
      </Badge>
    );
  };

  const commandDetail = (scan: Scan) => {
    const explanation = commandCache[scan.id];
    const hasCommand = !!(scan.command_line && scan.command_line.trim());

    if (!hasCommand) {
      return (
        <div className="rounded-control bg-accent px-md py-sm text-metadata text-muted-foreground">
          No command line data available for this scan.
          {scan.tool_name && !['nmap', 'masscan'].includes((scan.tool_name || '').toLowerCase()) && (
            <> {scan.tool_name} output does not include producing configuration.</>
          )}
        </div>
      );
    }

    return (
      <div className="flex flex-col gap-sm rounded-control bg-accent px-md py-sm">
        <div>
          <p className="mb-xxs text-caption font-semibold text-muted-foreground">Command</p>
          <div className="break-words rounded-control border border-border bg-card px-sm py-xs font-mono text-caption">
            {scan.command_line}
          </div>
        </div>
        {(scan.version || scan.tool_name) && (
          <div className="flex flex-wrap gap-md text-caption text-muted-foreground">
            {scan.version && (
              <span>
                <strong>Version:</strong> {scan.version}
              </span>
            )}
            {scan.tool_name && (
              <span>
                <strong>Tool:</strong> {scan.tool_name}
              </span>
            )}
          </div>
        )}
        {!explanation && (
          <div className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-3 animate-spin" aria-hidden />
            Loading argument analysis…
          </div>
        )}
        {explanation?.has_command && explanation.arguments && explanation.arguments.length > 0 && (
          <div>
            <p className="mb-xxs text-caption font-semibold text-muted-foreground">
              Arguments ({explanation.arguments.length})
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/5">Flag</TableHead>
                  <TableHead className="w-1/6">Category</TableHead>
                  <TableHead>Description</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {explanation.arguments.map((arg, idx) => (
                  <TableRow key={idx}>
                    <TableCell className="truncate font-mono">{arg.arg}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{arg.category}</Badge>
                    </TableCell>
                    <TableCell className="truncate">{arg.description}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {explanation?.summary && (
          <Alert variant="info">
            <AlertDescription>{explanation.summary}</AlertDescription>
          </Alert>
        )}
      </div>
    );
  };

  if (loading) {
    return <ListPageSkeleton titleWidth={160} actionCount={2} tableProps={{ rows: 8, columns: 6 }} />;
  }

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-start justify-between gap-sm">
        <div>
          <h1 className="text-page-title font-semibold">Scans</h1>
          <p className="text-metadata text-muted-foreground">
            Import tool output, track ingestion, and review scan inventory from one place.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-xs">
          <Button variant="outline" onClick={() => navigate('/scans/compare')}>
            <GitCompareArrows className="size-4" aria-hidden /> Compare scans
          </Button>
          <Button onClick={() => setUploadDialogOpen(true)}>
            <Upload className="size-4" aria-hidden /> Upload scans
          </Button>
        </div>
      </div>

      <div className="mb-md grid grid-cols-1 gap-sm sm:grid-cols-2 md:grid-cols-4">
        {[
          { label: 'Scans', value: scans.length.toLocaleString() },
          {
            label: 'Hosts up',
            value: `${scanSummary.upHosts.toLocaleString()} / ${scanSummary.totalHosts.toLocaleString()}`,
          },
          { label: 'Open services', value: scanSummary.openServices.toLocaleString() },
          { label: 'Queue active', value: scanSummary.queuedOrProcessing.toLocaleString() },
        ].map((metric) => (
          <Card key={metric.label}>
            <CardContent className="p-md">
              <p className="text-caption uppercase tracking-wide text-muted-foreground">
                {metric.label}
              </p>
              <p className="mt-xxs text-section-title font-semibold">{metric.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {uploadError && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription className="flex items-center justify-between gap-sm whitespace-pre-line">
            <span>{uploadError}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate('/parse-errors')}
              className="shrink-0"
            >
              View Details
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {uploadSuccess && (
        <Alert variant={activeJobIds.length > 0 ? 'info' : 'success'} className="mb-sm">
          <AlertDescription>{uploadSuccess}</AlertDescription>
        </Alert>
      )}

      {/* In-flight upload transfers — aria-live so screen readers
          announce progress when the dialog has closed (audit C10). */}
      {Object.keys(uploadProgress).length > 0 && (
        <div className="mb-sm flex flex-col gap-xs" aria-live="polite" aria-atomic="false">
          {Object.entries(uploadProgress).map(([key, p]) => {
            const variant =
              p.status === 'error' ? 'destructive' : p.status === 'done' ? 'success' : 'info';
            const label =
              p.status === 'uploading'
                ? 'Uploading'
                : p.status === 'parsing'
                ? 'Finishing upload'
                : p.status === 'done'
                ? 'Upload complete'
                : 'Upload failed';
            return (
              <Alert key={key} variant={variant}>
                <AlertDescription className="flex flex-col gap-xxs">
                  <div className="flex items-baseline justify-between gap-sm">
                    <span className="truncate font-semibold">
                      {label}: {p.filename}
                    </span>
                    {p.status !== 'error' && (
                      <span className="shrink-0 text-caption text-muted-foreground">{p.percent}%</span>
                    )}
                    {(p.status === 'done' || p.status === 'error') && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6 shrink-0"
                        aria-label={`Dismiss ${p.filename} progress`}
                        onClick={() =>
                          setUploadProgress((prev) => {
                            const { [key]: _removed, ...rest } = prev;
                            return rest;
                          })
                        }
                      >
                        <ChevronUp className="size-3" aria-hidden />
                      </Button>
                    )}
                  </div>
                  {p.status === 'error' ? (
                    <span>{p.error || 'Upload failed'}</span>
                  ) : (
                    <ProgressBar value={p.percent} tone={p.status === 'done' ? 'success' : 'default'} />
                  )}
                </AlertDescription>
              </Alert>
            );
          })}
        </div>
      )}

      {/* Active job progress */}
      {activeJobIds.length > 0 && (
        <div className="mb-sm flex flex-col gap-xs" aria-live="polite" aria-atomic="false">
          {activeJobIds.map((jobId) => {
            const job = activeJobs[jobId];
            if (!job) return null;
            return (
              <Alert key={jobId} variant="info">
                <AlertDescription className="break-words">
                  <strong>{job.status === 'processing' ? 'Processing' : 'Queued'}:</strong>{' '}
                  {job.original_filename || `Job #${jobId}`}{' '}
                  <span className="text-muted-foreground">{job.message || 'Waiting…'}</span>
                </AlertDescription>
              </Alert>
            );
          })}
        </div>
      )}

      {/* Ingestion Queue — v4.18.0: filtered to non-completed jobs only.
          Pre-fix, every successful upload showed up here AND in "Your
          Scans" below, which read as duplicate info.  Now the queue
          shows only what's actionable from a queue perspective:
          in-flight (`queued` / `processing`) and recent failures.
          Successful uploads appear exclusively in Your Scans. */}
      {pendingJobs.length > 0 && (
        <Card className="mb-md">
          <CardContent className="p-md">
            <div className="mb-sm flex flex-wrap items-center justify-between gap-xs">
              <div>
                <h2 className="text-section-title font-semibold">Ingestion Queue</h2>
                <p className="text-metadata text-muted-foreground">
                  In-flight uploads + recent failures.  Successful uploads
                  appear in Your Scans below.
                </p>
              </div>
              <LastUpdated
                lastFetched={recentJobsFetched}
                onRefresh={fetchRecentJobs}
                isLoading={recentJobsLoading}
                label="ingestion jobs"
                intervalMs={15000}
              />
            </div>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {/* w-12 (48px) — the expand chevron is a 40px icon
                        button (control density raised in 4.7.13); the
                        old w-10 (40px) column clipped it and let it
                        bleed into the Status column. */}
                    <TableHead className="w-12" />
                    <TableHead className="w-32">Status</TableHead>
                    <TableHead className="w-1/5">File</TableHead>
                    <TableHead className="w-24">Tool</TableHead>
                    <TableHead className="w-20 text-right">Size</TableHead>
                    <TableHead>Message</TableHead>
                    <TableHead className="w-36">Submitted</TableHead>
                    <TableHead className="w-24">Duration</TableHead>
                    <TableHead className="w-24 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pendingJobs.map((job) => {
                    const isFailure = job.status === 'failed';
                    const isExpanded = expandedJobIds.has(job.id);

                    const elapsed = (() => {
                      if (job.started_at && job.completed_at) {
                        return formatDuration(
                          new Date(job.completed_at).getTime() - new Date(job.started_at).getTime(),
                        );
                      }
                      if (job.started_at && job.status === 'processing') {
                        return `${formatDuration(Date.now() - new Date(job.started_at).getTime())}…`;
                      }
                      return '-';
                    })();

                    const fileSize = job.file_size
                      ? job.file_size > 1048576
                        ? `${(job.file_size / 1048576).toFixed(1)} MB`
                        : `${(job.file_size / 1024).toFixed(0)} KB`
                      : '-';

                    const displayMessage = isFailure
                      ? job.error_message || job.message || 'Unknown error'
                      : job.message || '-';

                    return (
                      <React.Fragment key={job.id}>
                        <TableRow className={cn(job.status === 'completed' && 'opacity-75')}>
                          <TableCell className="p-xxs">
                            {isFailure && (
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => toggleJobExpanded(job.id)}
                                aria-label={isExpanded ? 'Hide full error message' : 'Show full error message'}
                                aria-expanded={isExpanded}
                              >
                                {isExpanded ? (
                                  <ChevronUp className="size-4" aria-hidden />
                                ) : (
                                  <ChevronDown className="size-4" aria-hidden />
                                )}
                              </Button>
                            )}
                          </TableCell>
                          <TableCell>
                            {/* Status is encoded by icon + colour alone —
                                the previous "icon + Badge with the status
                                word" rendered the same dimension twice.
                                The sr-only span keeps the value reachable
                                for screen readers without the visual
                                redundancy. */}
                            <span
                              className="inline-flex items-center"
                              aria-label={`Status: ${job.status}`}
                            >
                              {job.status === 'completed' && (
                                <CheckCircle2 className="size-4 text-success" aria-hidden />
                              )}
                              {job.status === 'failed' && (
                                <AlertCircle className="size-4 text-destructive" aria-hidden />
                              )}
                              {job.status === 'processing' && (
                                <Loader2 className="size-4 animate-spin text-info" aria-hidden />
                              )}
                              {job.status === 'queued' && (
                                <Hourglass className="size-4 text-muted-foreground" aria-hidden />
                              )}
                              <span className="sr-only">{job.status}</span>
                            </span>
                          </TableCell>
                          <TableCell className="truncate" title={job.original_filename}>
                            {job.original_filename}
                          </TableCell>
                          <TableCell className="truncate">{job.tool_name || '-'}</TableCell>
                          <TableCell className="text-right font-mono">{fileSize}</TableCell>
                          <TableCell>
                            <div
                              className={cn(
                                isFailure && 'text-destructive',
                                isFailure && !isExpanded && 'truncate',
                              )}
                            >
                              {displayMessage}
                            </div>
                            {job.parse_error_id && (
                              <button
                                type="button"
                                onClick={() => navigate('/parse-errors')}
                                className="mt-xxs rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              >
                                <Badge variant="outline" className="cursor-pointer border-destructive/40 text-destructive hover:bg-destructive/10">
                                  Error #{job.parse_error_id}
                                </Badge>
                              </button>
                            )}
                          </TableCell>
                          <TableCell className="text-caption">
                            {new Date(job.created_at).toLocaleString()}
                          </TableCell>
                          <TableCell className="font-mono text-caption">{elapsed}</TableCell>
                          <TableCell className="text-right">
                            {job.scan_id && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => navigate(`/scans/${job.scan_id}`)}
                                aria-label={`Open scan for ${job.original_filename}`}
                              >
                                Open
                              </Button>
                            )}
                            {/* v2.86.2 — failed jobs were stuck in the
                                queue with no acknowledge path; this
                                dismiss button writes dismissed_at so
                                the row drops out on the next refetch.
                                Preserves the failure for the audit
                                trail (admins can re-surface dismissed
                                rows via ?include_dismissed=true). */}
                            {isFailure && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={async () => {
                                  try {
                                    await dismissIngestionJob(job.id);
                                    await fetchRecentJobs();
                                  } catch (err) {
                                    console.error('Failed to dismiss ingestion job', err);
                                  }
                                }}
                                aria-label={`Dismiss failed ingestion for ${job.original_filename}`}
                              >
                                Dismiss
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                        {isFailure && isExpanded && (
                          <TableRow>
                            <TableCell colSpan={9} className="bg-accent p-md">
                              <p className="mb-xxs text-metadata font-semibold text-destructive">
                                Full error message
                              </p>
                              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-card p-sm text-caption">
                                {displayMessage}
                              </pre>
                            </TableCell>
                          </TableRow>
                        )}
                      </React.Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      <Separator className="mb-md" />

      <h2 className="mb-sm text-section-title font-semibold">Your Scans</h2>

      {/* Scan Timeline moved to /tool-activity in v2.59.0 — that page
          plots scans by their actual scan_start (SOC-correlation
          intent) across ALL projects, and adds recon + execution
          sessions to the same axis.  Per-project scan inventory still
          lives here in tabular form below. */}

      {!hasActiveFilters && scans.length === 0 ? (
        <div className="py-xl text-center">
          <Upload className="mx-auto mb-sm size-16 text-muted-foreground" aria-hidden />
          <p className="text-subheading text-muted-foreground">No scans uploaded yet</p>
          <p className="text-metadata text-muted-foreground">
            Use Upload scans to import Nmap, Nessus, Masscan, OpenVAS, httpx, dnsx, BloodHound,
            EyeWitness, NetExec, and other supported scanner exports — expand "Supported
            formats" below the upload area for the full list and per-tool notes.
          </p>
        </div>
      ) : (
        <div>
          <div className="mb-sm flex flex-wrap items-start justify-between gap-sm">
            <div>
              <h3 className="text-subheading font-semibold">Scan Inventory</h3>
              <p className="text-metadata text-muted-foreground">
                Most recent uploads first. Compare timing, host coverage, and findings without
                scanning across two-column cards.
              </p>
              {/* v2.86.2 — explicit "Showing N" hint so the page makes
                  it obvious whether you're looking at a partial or
                  complete view.  Pre-fix the only signal was the Load
                  More button at the bottom of the table, which sat
                  off-screen on long lists and was field-reported as a
                  "100-row cap". */}
              {scans.length > 0 && (
                <p className="mt-xxs text-caption text-muted-foreground">
                  Showing {scans.length} scan{scans.length === 1 ? '' : 's'}
                  {hasMoreScans ? ' — more available, see Load button below' : hasActiveFilters ? ' (filtered)' : ''}
                </p>
              )}
            </div>
            {/* v4.47.0 QoL pass — full filter row.  Search runs against
                filename/tool_name/scan_type server-side (debounced 300ms).
                Tool chips and date-range chips are clickable; everything
                persists to the URL for shareable views. */}
            <div className="flex w-full flex-col items-stretch gap-xs lg:w-auto lg:items-end">
              <div className="relative lg:w-96">
                <Search
                  className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden
                />
                <Input
                  type="search"
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  placeholder="Search filename, tool, scan type…"
                  aria-label="Search scan inventory"
                  className="pl-xl"
                />
              </div>
              <div
                className="flex flex-wrap items-center gap-xs"
                role="group"
                aria-label="Filter scans by tool"
              >
                <span className="text-caption text-muted-foreground">Tool:</span>
                <button
                  type="button"
                  onClick={() => setToolFilter('')}
                  aria-pressed={toolFilter === ''}
                  className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Badge variant={toolFilter === '' ? 'default' : 'outline'}>
                    All: {scans.length}
                  </Badge>
                </button>
                {orderedToolGroups.map((group) => {
                  const active = toolFilter.toLowerCase() === group.toLowerCase();
                  return (
                    <button
                      key={group}
                      type="button"
                      onClick={() => setToolFilter(active ? '' : group)}
                      aria-pressed={active}
                      className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      title={active ? `Clear ${group} filter` : `Show only ${group} scans`}
                    >
                      <Badge variant={active ? 'default' : 'outline'}>
                        {group}: {groupedScans[group].length}
                      </Badge>
                    </button>
                  );
                })}
              </div>
              <div
                className="flex flex-wrap items-center gap-xs"
                role="group"
                aria-label="Filter scans by upload date"
              >
                <span className="text-caption text-muted-foreground">Range:</span>
                {DATE_RANGE_PRESETS.map((preset) => {
                  const active = dateRangeDays === preset.days;
                  return (
                    <button
                      key={preset.label}
                      type="button"
                      onClick={() => setDateRangeDays(preset.days)}
                      aria-pressed={active}
                      className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Badge variant={active ? 'default' : 'outline'}>{preset.label}</Badge>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {scans.length === 0 ? (
            // Filter-aware empty state — section header + filters
            // remain visible so the user can clear or refine without
            // navigating away.
            <Card>
              <CardContent className="flex flex-col items-center gap-sm p-xl text-center">
                <AlertCircle className="size-12 text-muted-foreground" aria-hidden />
                <p className="text-subheading font-semibold">No scans match these filters</p>
                <p className="max-w-md text-metadata text-muted-foreground">
                  Adjust the search, tool, or date range above — or clear them to see every scan
                  in this project.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSearchText('');
                    setToolFilter('');
                    setDateRangeDays(null);
                  }}
                >
                  Clear filters
                </Button>
              </CardContent>
            </Card>
          ) : (
          <>
          {/* Mobile cards */}
          <div className="flex flex-col gap-sm md:hidden">
            {scans.map((scan) => {
              const windowInfo = getScanWindow(scan);
              const isExpanded = expandedScanIds.includes(scan.id);
              const hasCommand = !!(scan.command_line && scan.command_line.trim());
              return (
                <Card key={scan.id}>
                  <CardContent className="flex flex-col gap-sm p-md">
                    <div className="flex flex-wrap items-start justify-between gap-sm">
                      <div className="min-w-0 flex-1">
                        <div className="mb-xxs flex flex-wrap items-center gap-xs">
                          <h4 className="break-words text-subheading font-semibold">{scan.filename}</h4>
                          {renderInlineToolBadge(scan)}
                        </div>
                        <p className="text-caption text-muted-foreground">
                          Uploaded: {formatDateTime(scan.created_at)}
                        </p>
                        <p className="text-caption text-muted-foreground">
                          Window: {formatDateTime(windowInfo.start)} → {formatDateTime(windowInfo.end)} ({formatDuration(windowInfo.durationMs)})
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-xs">
                        {scan.total_hosts > 0 && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => navigate(`/hosts?scan_ids=${scan.id}`)}
                            title="Open the Hosts page filtered to this scan"
                          >
                            <SquareArrowOutUpRight className="size-4" aria-hidden /> Hosts
                          </Button>
                        )}
                        <Button size="sm" onClick={() => handleViewScan(scan.id)}>
                          <Eye className="size-4" aria-hidden /> View
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDeleteClick(scan)}
                          aria-label={`Delete scan ${scan.filename || scan.id}`}
                          className="text-muted-foreground hover:text-destructive"
                        >
                          <Trash2 className="size-4" aria-hidden />
                        </Button>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-xxs">
                      <Badge variant={scan.total_hosts > 0 ? 'success' : 'outline'}>
                        {scan.total_hosts > 0 ? `${scan.total_hosts} host${scan.total_hosts === 1 ? '' : 's'}` : 'No hosts detected'}
                      </Badge>
                      {statusBadge(scan)}
                    </div>
                    {metricBadges(scan)}
                    {hasCommand && (
                      <>
                        <Separator />
                        <Button
                          variant="ghost"
                          size="sm"
                          className="self-start"
                          onClick={() => toggleScanExpanded(scan.id)}
                        >
                          {isExpanded ? (
                            <ChevronUp className="size-4" aria-hidden />
                          ) : (
                            <Terminal className="size-4" aria-hidden />
                          )}
                          {isExpanded ? 'Hide command' : 'Show command'}
                        </Button>
                        {isExpanded && commandDetail(scan)}
                      </>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block">
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {renderSortHeader('filename', 'Scan', 'w-[28%]')}
                      {renderSortHeader('created_at', 'Uploaded', 'w-[16%]')}
                      <TableHead className="w-[18%]">Window</TableHead>
                      {renderSortHeader('total_hosts', 'Hosts', 'w-[12%]')}
                      <TableHead className="w-[16%]">Metrics</TableHead>
                      <TableHead className="w-[10%]">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {scans.map((scan) => {
                      const windowInfo = getScanWindow(scan);
                      const isExpanded = expandedScanIds.includes(scan.id);
                      const hasCommand = !!(scan.command_line && scan.command_line.trim());
                      return (
                        <React.Fragment key={scan.id}>
                          <TableRow className="align-top">
                            <TableCell>
                              <div className="mb-xxs flex items-center gap-xxs">
                                {hasCommand && (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon"
                                        className="-ml-xxs"
                                        onClick={() => toggleScanExpanded(scan.id)}
                                        aria-label={isExpanded ? 'Hide command' : 'Show command'}
                                        aria-expanded={isExpanded}
                                      >
                                        {isExpanded ? (
                                          <ChevronUp className="size-4" aria-hidden />
                                        ) : (
                                          <ChevronDown className="size-4" aria-hidden />
                                        )}
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>
                                      {isExpanded ? 'Hide command' : 'Show command'}
                                    </TooltipContent>
                                  </Tooltip>
                                )}
                                {/* min-w-0 lets the filename shrink + wrap
                                    inside the flex row; without it the
                                    span keeps its content width and a long
                                    name overflows into the next column. */}
                                <span className="min-w-0 break-words font-semibold">{scan.filename}</span>
                              </div>
                              <div className="flex flex-wrap gap-xxs">
                                {renderInlineToolBadge(scan)}
                                {scan.version && <Badge variant="outline">v{scan.version}</Badge>}
                                {statusBadge(scan)}
                              </div>
                            </TableCell>
                            <TableCell>
                              <p className="text-metadata">{formatDateTime(scan.created_at)}</p>
                              {scan.start_time && (
                                <p className="mt-xxs text-caption text-muted-foreground">
                                  Scanned: {formatDateTime(scan.start_time)}
                                </p>
                              )}
                            </TableCell>
                            <TableCell>
                              <p className="text-metadata">{formatDateTime(windowInfo.start)}</p>
                              <p className="mt-xxs text-caption text-muted-foreground">
                                {formatDuration(windowInfo.durationMs)}
                              </p>
                            </TableCell>
                            <TableCell>
                              {/* Just the up/total ratio — the ratio
                                  already conveys the total, so a separate
                                  "N total" badge was redundant. */}
                              <Badge variant={scan.up_hosts > 0 ? getStatusTone(scan.up_hosts, scan.total_hosts) : 'outline'}>
                                {scan.up_hosts}/{scan.total_hosts} up
                              </Badge>
                            </TableCell>
                            <TableCell>{metricBadges(scan)}</TableCell>
                            <TableCell>
                              <div className="flex flex-wrap items-center gap-xs">
                                {scan.total_hosts > 0 && (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() => navigate(`/hosts?scan_ids=${scan.id}`)}
                                    title="Open the Hosts page filtered to this scan"
                                  >
                                    <SquareArrowOutUpRight className="size-4" aria-hidden /> Hosts
                                  </Button>
                                )}
                                <Button size="sm" onClick={() => handleViewScan(scan.id)}>
                                  <Eye className="size-4" aria-hidden /> View
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => handleDeleteClick(scan)}
                                  aria-label={`Delete scan ${scan.filename || scan.id}`}
                                  className="text-muted-foreground hover:text-destructive"
                                >
                                  <Trash2 className="size-4" aria-hidden />
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                          {hasCommand && isExpanded && (
                            <TableRow>
                              <TableCell colSpan={6} className="py-sm">
                                {commandDetail(scan)}
                              </TableCell>
                            </TableRow>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>
          </>
          )}

          {hasMoreScans && (
            <div className="mt-md flex justify-center">
              <Button
                variant="outline"
                onClick={loadMoreScans}
                disabled={loadingMore}
              >
                {loadingMore ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <ChevronDown className="size-4" aria-hidden />
                )}
                {loadingMore ? 'Loading…' : `Load ${SCAN_LIMIT} more`}
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Upload dialog */}
      <Dialog open={uploadDialogOpen} onOpenChange={setUploadDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Upload scans</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-sm">
            <div
              {...getRootProps()}
              aria-label="Scan file upload drop zone"
              className={cn(
                'flex flex-col items-center gap-xs rounded-panel border-2 border-dashed p-lg text-center transition-colors',
                isDragActive
                  ? 'cursor-pointer border-primary bg-accent'
                  : 'cursor-pointer border-border hover:border-primary hover:bg-accent',
              )}
            >
              <input {...getInputProps()} />
              <Upload className="size-10 text-primary" aria-hidden />
              <p className="text-subheading font-semibold">
                {isDragActive ? 'Drop the files here…' : 'Drop files here'}
              </p>
              <p className="text-metadata text-muted-foreground">
                Click to select one or more scan files.
              </p>
            </div>

            {fileRejections.length > 0 && (
              <Alert variant="destructive">
                <AlertDescription>
                  {fileRejections.map(({ file, errors }) => (
                    <div key={file.name} className="break-words">
                      <strong>{file.name}</strong>: {errors.map((e) => e.message).join('; ')}
                    </div>
                  ))}
                </AlertDescription>
              </Alert>
            )}

            <div className="rounded-control border border-border bg-accent p-sm">
              <div className="flex items-center gap-xs">
                <Checkbox
                  id="enrich-dns"
                  checked={enrichDns}
                  onCheckedChange={(v) => setEnrichDns(v === true)}
                />
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Label htmlFor="enrich-dns" className="cursor-pointer">
                      Enrich with DNS data
                    </Label>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-metadata font-semibold">DNS Data Enrichment</p>
                    <p className="mt-xxs text-caption">
                      Adds reverse DNS lookups, forward DNS resolution, and permitted zone-transfer
                      checks while processing files.
                    </p>
                    <p className="mt-xxs text-caption italic">
                      Enrichment may increase processing time.
                    </p>
                  </TooltipContent>
                </Tooltip>
              </div>
              {enrichDns && (
                <div className="mt-sm pl-lg">
                  <p className="mb-xs text-metadata font-semibold">DNS server</p>
                  <RadioGroup
                    value={dnsServerType}
                    onValueChange={(v) => setDnsServerType(v as 'default' | 'custom')}
                  >
                    <div className="flex items-center gap-xs">
                      <RadioGroupItem id="dns-default" value="default" />
                      <Label htmlFor="dns-default">Use system default DNS servers</Label>
                    </div>
                    <div className="flex items-center gap-xs">
                      <RadioGroupItem id="dns-custom" value="custom" />
                      <Label htmlFor="dns-custom">Use custom DNS server</Label>
                    </div>
                  </RadioGroup>
                  {dnsServerType === 'custom' && (
                    <div className="mt-sm">
                      <Label htmlFor="dns-server">Custom DNS Server</Label>
                      <Input
                        id="dns-server"
                        placeholder="8.8.8.8 or dns.company.com"
                        value={customDnsServer}
                        onChange={(e) => setCustomDnsServer(e.target.value)}
                      />
                      <p
                        className={cn(
                          'mt-xxs text-caption',
                          customDnsServer.trim() === ''
                            ? 'text-destructive'
                            : 'text-muted-foreground',
                        )}
                      >
                        Enter an IP address or hostname.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>

            <Accordion type="single" collapsible>
              <AccordionItem value="formats">
                <AccordionTrigger>Supported formats</AccordionTrigger>
                <AccordionContent>
                  <div className="grid grid-cols-1 gap-xs sm:grid-cols-2">
                    {SUPPORTED_FORMATS.map((item) => (
                      <div
                        key={`${item.tool}-${item.formats}`}
                        className="flex items-baseline gap-xs"
                      >
                        <p className="min-w-20 text-metadata font-semibold">{item.tool}</p>
                        <div>
                          <p className="text-caption font-mono text-primary">{item.formats}</p>
                          <p className="text-caption text-muted-foreground">{item.desc}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            {uploading && (
              <Alert variant="info">
                <AlertDescription>
                  Uploads started — you can close this dialog and keep working. Progress continues
                  in the banner below.
                </AlertDescription>
              </Alert>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setUploadDialogOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <Dialog
        open={deleteDialogOpen}
        onOpenChange={(next) => !deleteLoading && setDeleteDialogOpen(next)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Scan</DialogTitle>
          </DialogHeader>
          <p className="text-metadata">
            Are you sure you want to delete the scan &quot;{scanToDelete?.filename}&quot;? This
            action cannot be undone.
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
              disabled={deleteLoading}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={deleteLoading}
            >
              {deleteLoading ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  Deleting…
                </>
              ) : (
                'Delete'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
