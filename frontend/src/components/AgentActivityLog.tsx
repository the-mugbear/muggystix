import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2, RefreshCw } from 'lucide-react';
import {
  AgentApiCallRow,
  AgentActivityFilters,
  getPlanApiActivity,
  getReconSessionApiActivity,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

type Source =
  | { kind: 'plan'; planId: number }
  | { kind: 'recon'; reconSessionId: number };

interface AgentActivityLogProps {
  source: Source;
  title?: string;
  /** Subtitle hint shown under the title.  Defaults to a generic
   *  "every request the agent made" line. */
  subtitle?: React.ReactNode;
  /** Initial value for the Method filter select.  Useful for splits
   *  like the /api-calls sub-tab that defaults to writes only. */
  defaultMethodFilter?: string;
  /** Initial index into the status preset list.  0 = All, 1 = 2xx,
   *  2 = 4xx, 3 = 5xx (see STATUS_PRESETS). */
  defaultStatusPreset?: number;
}

const STATUS_PRESETS: Array<{ label: string; min?: number; max?: number }> = [
  { label: 'All' },
  { label: 'Success (2xx)', min: 200, max: 299 },
  { label: 'Client error (4xx)', min: 400, max: 499 },
  { label: 'Server error (5xx)', min: 500, max: 599 },
];

const METHOD_OPTIONS = ['', 'GET', 'POST', 'PATCH', 'PUT', 'DELETE'];

type Tone = 'success' | 'warning' | 'destructive' | 'muted';

const statusTone = (code: number): Tone => {
  if (code >= 500) return 'destructive';
  if (code >= 400) return 'warning';
  if (code >= 200 && code < 300) return 'success';
  return 'muted';
};

const fmtBytes = (n?: number | null): string => {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const fmtDuration = (ms: number): string => {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
};

const fmtTime = (iso: string): string => {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const DetailLine: React.FC<{
  label: string;
  value: string | null;
  mono?: boolean;
  multiline?: boolean;
}> = ({ label, value, mono, multiline }) => {
  if (!value) return null;
  return (
    <div className={multiline ? 'flex flex-col gap-xxs' : 'flex flex-wrap gap-md'}>
      <p className="min-w-40 text-metadata text-muted-foreground">{label}</p>
      <p
        className={
          mono
            ? `break-all font-mono text-caption ${multiline ? 'whitespace-pre-wrap' : ''}`
            : `break-all text-metadata ${multiline ? 'whitespace-pre-wrap' : ''}`
        }
      >
        {value}
      </p>
    </div>
  );
};

const ExpandableRow: React.FC<{ row: AgentApiCallRow }> = ({ row }) => {
  const [open, setOpen] = useState(false);
  const refIps = row.referenced_target_ips ?? [];
  const refHosts = row.referenced_host_ids ?? [];

  return (
    <>
      <TableRow>
        <TableCell className="w-8">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setOpen((o) => !o)}
            aria-label="Toggle details"
            aria-expanded={open}
          >
            {open ? (
              <ChevronUp className="size-4" aria-hidden />
            ) : (
              <ChevronDown className="size-4" aria-hidden />
            )}
          </Button>
        </TableCell>
        <TableCell className="whitespace-nowrap text-caption">
          {/* Audit RSP·L8 — keep the timestamp on a single line and
              clip if a future format change widens it. */}
          <span className="truncate block max-w-full">{fmtTime(row.created_at)}</span>
        </TableCell>
        <TableCell className="w-20">
          <Badge variant="outline">{row.method}</Badge>
        </TableCell>
        <TableCell className="min-w-0">
          {/* Audit RSP·H12 — `truncate` on a table-cell is a no-op
              under display: table-cell, so the ellipsis must live on
              the inner span. */}
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="truncate block max-w-full">{row.path_template || row.path}</span>
            </TooltipTrigger>
            <TooltipContent>{row.path}</TooltipContent>
          </Tooltip>
        </TableCell>
        <TableCell className="w-20">
          <Badge variant={statusTone(row.status_code)}>{row.status_code}</Badge>
        </TableCell>
        <TableCell className="w-24 whitespace-nowrap text-caption">
          {fmtDuration(row.duration_ms)}
        </TableCell>
        <TableCell>
          {/* IPs are not categorical state — chips were chip-noise.
              Inline mono text scans faster against many rows; status
              code (and method) stay as badges because those genuinely
              are categorical signals (2xx/4xx/5xx colouring, request
              verb). */}
          {refIps.length > 0 ? (
            <span className="block truncate font-mono text-caption text-foreground">
              {refIps.slice(0, 4).join(', ')}
              {refIps.length > 4 && (
                <span className="text-muted-foreground"> +{refIps.length - 4}</span>
              )}
            </span>
          ) : refHosts.length > 0 ? (
            <span className="text-caption text-muted-foreground">
              {refHosts.length} host{refHosts.length === 1 ? '' : 's'}
            </span>
          ) : null}
        </TableCell>
      </TableRow>
      {open && (
        <TableRow>
          <TableCell colSpan={7} className="bg-accent p-md">
            <div className="flex flex-col gap-xs">
              <DetailLine label="Full path" value={row.path} mono />
              <DetailLine
                label="Path params"
                value={row.path_params ? JSON.stringify(row.path_params) : null}
                mono
              />
              <DetailLine
                label="Query params"
                value={row.query_params ? JSON.stringify(row.query_params) : null}
                mono
              />
              {row.request_body_summary && (
                <DetailLine
                  label="Request body"
                  value={JSON.stringify(row.request_body_summary, null, 2)}
                  mono
                  multiline
                />
              )}
              <DetailLine label="Source IP" value={row.source_ip ?? null} />
              <DetailLine label="API key" value={row.api_key_prefix ?? null} mono />
              <DetailLine
                label="Response size"
                value={row.response_bytes != null ? fmtBytes(row.response_bytes) : null}
              />
              {refHosts.length > 0 && (
                <DetailLine label="Host IDs referenced" value={refHosts.join(', ')} mono />
              )}
              {row.referenced_entry_ids && row.referenced_entry_ids.length > 0 && (
                <DetailLine
                  label="Entry IDs referenced"
                  value={row.referenced_entry_ids.join(', ')}
                  mono
                />
              )}
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
};

const AgentActivityLog: React.FC<AgentActivityLogProps> = ({
  source,
  title = 'Agent API activity',
  subtitle,
  defaultMethodFilter = '',
  defaultStatusPreset = 0,
}) => {
  const [rows, setRows] = useState<AgentApiCallRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusPreset, setStatusPreset] = useState(defaultStatusPreset);
  const [methodFilter, setMethodFilter] = useState(defaultMethodFilter);
  const [targetIpFilter, setTargetIpFilter] = useState('');
  const [limit] = useState(100);
  const [refreshNonce, setRefreshNonce] = useState(0);

  // Audit FBK·H12 + PRF·H4: the Target IP input used to fire a request
  // on every keystroke.  Debounce the *server-bound* value while the
  // input continues to render targetIpFilter live — typing stays
  // responsive, the API call lags 300ms behind the last keystroke.
  const debouncedTargetIp = useDebouncedValue(targetIpFilter, 300);

  const filters = useMemo<AgentActivityFilters>(() => {
    const f: AgentActivityFilters = { limit };
    if (methodFilter) f.method = methodFilter;
    if (debouncedTargetIp.trim()) f.target_ip = debouncedTargetIp.trim();
    const preset = STATUS_PRESETS[statusPreset];
    if (preset.min != null) f.status_min = preset.min;
    if (preset.max != null) f.status_max = preset.max;
    return f;
  }, [methodFilter, debouncedTargetIp, statusPreset, limit]);

  const fetchActivity = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result =
        source.kind === 'plan'
          ? await getPlanApiActivity(source.planId, filters)
          : await getReconSessionApiActivity(source.reconSessionId, filters);
      setRows(result.items);
      setTotal(result.total);
    } catch (e: unknown) {
      // formatApiError unwraps FastAPI error shapes and gives a clean
      // message instead of "Network Error" / "Request failed with
      // status code 500" (audit H13).
      setError(formatApiError(e, 'Failed to load activity log.'));
    } finally {
      setLoading(false);
    }
  }, [source, filters]);

  useEffect(() => {
    fetchActivity();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchActivity, refreshNonce]);

  return (
    <Card>
      <CardContent className="p-md">
        <div className="mb-sm flex flex-col gap-sm sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-subheading font-semibold">{title}</h3>
            <p className="text-metadata text-muted-foreground">
              {subtitle ?? (
                <>
                  Every request the agent made to BlueStick for this{' '}
                  {source.kind === 'plan' ? 'plan' : 'recon session'}. Filter by host or IP to
                  verify the agent queried what you expected.
                </>
              )}
            </p>
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setRefreshNonce((n) => n + 1)}
                disabled={loading}
                aria-label={`Refresh ${title}`}
              >
                <RefreshCw className="size-4" aria-hidden />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Refresh</TooltipContent>
          </Tooltip>
        </div>

        <div className="mb-sm flex flex-col gap-sm sm:flex-row sm:items-end">
          <div className="min-w-44">
            <Label htmlFor="agent-activity-status">Status</Label>
            <Select
              value={String(statusPreset)}
              onValueChange={(v) => setStatusPreset(Number(v))}
            >
              <SelectTrigger id="agent-activity-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_PRESETS.map((p, i) => (
                  <SelectItem key={p.label} value={String(i)}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="min-w-28">
            <Label htmlFor="agent-activity-method">Method</Label>
            <Select value={methodFilter || 'any'} onValueChange={(v) => setMethodFilter(v === 'any' ? '' : v)}>
              <SelectTrigger id="agent-activity-method">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {METHOD_OPTIONS.map((m) => (
                  <SelectItem key={m || 'any'} value={m || 'any'}>
                    {m || 'Any'}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="min-w-44 flex-1">
            <Label htmlFor="agent-activity-ip">Target IP</Label>
            <Input
              id="agent-activity-ip"
              value={targetIpFilter}
              onChange={(e) => setTargetIpFilter(e.target.value)}
              placeholder="10.0.0.5"
            />
          </div>
          <span className="self-end text-metadata text-muted-foreground">
            {loading ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              `${rows.length} of ${total} shown`
            )}
          </span>
        </div>

        {error && (
          <Alert variant="destructive" className="mb-sm">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead className="w-44">Time</TableHead>
                <TableHead className="w-20">Method</TableHead>
                <TableHead>Endpoint</TableHead>
                <TableHead className="w-20">Status</TableHead>
                <TableHead className="w-24">Duration</TableHead>
                <TableHead>Hosts / IPs</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <ExpandableRow key={row.id} row={row} />
              ))}
              {!loading && rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="py-md text-metadata text-muted-foreground">
                    No matching API calls. The agent may not have started this{' '}
                    {source.kind === 'plan' ? 'plan' : 'recon session'} yet, or your filters
                    excluded every call.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>

        {total > rows.length && (
          <div className="mt-sm flex justify-center">
            <Button
              variant="outline"
              onClick={() => setRefreshNonce((n) => n + 1)}
              disabled={loading}
            >
              {loading ? 'Loading…' : 'Refresh'}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default AgentActivityLog;
