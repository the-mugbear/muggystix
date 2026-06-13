import React, { useEffect, useMemo, useState } from 'react';
import {
  Code,
  Download,
  FileArchive,
  FileDown,
  FileText,
  Globe,
  Loader2,
  ServerCog,
  Table as TableIcon,
} from 'lucide-react';
import {
  generateHostsReport,
  enqueueReportJob,
  getReportJob,
  downloadReportJob,
  type AsyncReportFormat,
} from '../services/api';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { cn } from '../utils/cn';

interface ReportsDialogProps {
  open: boolean;
  onClose: () => void;
  filters: Record<string, string | boolean | number | undefined>;
  totalHosts: number;
}

type ReportType = 'inventory' | 'comprehensive';
type HumanFormat = 'pdf' | 'html' | 'csv' | 'json';
type StructuredFormat = 'markdown-bundle' | 'agent-package';

// Keep in sync with the backend REPORT_MAX_HOSTS. The CSV inventory streams the
// full set; every other format (HTML streamed, and the async PDF/JSON/.zip
// bundles generated on the report worker) caps here.
const REPORT_HOST_CAP = 50000;

const REPORT_TYPES: Array<{ value: ReportType; label: string; description: string }> = [
  {
    value: 'comprehensive',
    label: 'Comprehensive Security Report',
    description:
      'Everything per host — findings, vulnerabilities, services, site context — plus project hotspots. The security review hand-off.',
  },
  {
    value: 'inventory',
    label: 'Host Inventory',
    description:
      'Concise one-row-per-host list (identity, OS, open ports, vuln counts, SMB signing, tags). The "these hosts are problematic" handout.',
  },
];

// Allowed output formats per report type.
const HUMAN_FORMATS: Record<ReportType, Array<{ value: HumanFormat; label: string; Icon: typeof Globe }>> = {
  comprehensive: [
    { value: 'pdf', label: 'PDF', Icon: FileDown },
    { value: 'html', label: 'HTML', Icon: Globe },
    { value: 'json', label: 'JSON', Icon: Code },
  ],
  inventory: [
    { value: 'pdf', label: 'PDF', Icon: FileDown },
    { value: 'html', label: 'HTML', Icon: Globe },
    { value: 'csv', label: 'CSV', Icon: TableIcon },
  ],
};

// Machine-readable exports — not human reports. Segregated so a manager
// clicking "Export" doesn't end up with a zip of NDJSON.
const STRUCTURED_FORMATS: Array<{
  value: StructuredFormat;
  label: string;
  Icon: typeof FileArchive;
  description: string;
}> = [
  {
    value: 'markdown-bundle',
    label: 'Markdown bundle (.zip)',
    Icon: FileText,
    description: 'Human-readable report.md + companion CSVs (hosts, vulnerabilities, canonical findings, execution findings, notes, scans).',
  },
  {
    value: 'agent-package',
    label: 'Agent dataset — NDJSON (.zip)',
    Icon: ServerCog,
    description: 'Structured per-host NDJSON + schema for feeding an AI agent. Not a human report.',
  },
];

const ReportsDialog: React.FC<ReportsDialogProps> = ({ open, onClose, filters, totalHosts }) => {
  const [reportType, setReportType] = useState<ReportType>('comprehensive');
  const [format, setFormat] = useState<HumanFormat>('pdf');
  // Tracks which action is in flight ('pdf', 'agent-package', …) so only that
  // button spins and the rest disable.
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set when the server reports it capped the export (X-Report-Truncated): the
  // file still downloaded, so we keep the dialog open and warn rather than close.
  const [truncated, setTruncated] = useState(false);

  // Clear a prior partial-export warning whenever the dialog is reopened.
  useEffect(() => {
    if (open) {
      setTruncated(false);
      setError(null);
    }
  }, [open]);

  const allowedFormats = HUMAN_FORMATS[reportType];

  const handleTypeChange = (t: ReportType) => {
    setReportType(t);
    // Keep the format valid for the new type (csv↔json swap on type change).
    if (!HUMAN_FORMATS[t].some((f) => f.value === format)) {
      setFormat(HUMAN_FORMATS[t][0].value);
    }
  };

  // After a download, surface a partial-export warning (keep the dialog open) or
  // close on a complete one.
  const settleDownload = (wasTruncated: boolean) => {
    if (wasTruncated) {
      setTruncated(true);
    } else {
      onClose();
    }
  };

  const runExport = async (fmt: HumanFormat | StructuredFormat, type?: ReportType) => {
    setBusy(fmt);
    setError(null);
    setTruncated(false);
    try {
      // CSV + HTML stream synchronously from the API and download directly.
      if (fmt === 'csv' || fmt === 'html') {
        const { truncated: wasTruncated } = await generateHostsReport(fmt, filters, type);
        settleDownload(wasTruncated);
        return;
      }

      // Heavy formats run on the report worker: enqueue → poll → download.
      let job = await enqueueReportJob(fmt as AsyncReportFormat, filters, type);
      // Cap the wait at the server-side report timeout (~15 min) so a wedged
      // job doesn't poll forever.
      const maxAttempts = 450; // 450 × 2s = 15 min
      let attempts = 0;
      while ((job.status === 'queued' || job.status === 'processing') && attempts < maxAttempts) {
        await new Promise((r) => setTimeout(r, 2000));
        attempts += 1;
        job = await getReportJob(job.id);
      }
      if (job.status === 'completed') {
        const { truncated: wasTruncated } = await downloadReportJob(job.id);
        settleDownload(wasTruncated);
      } else if (job.status === 'failed') {
        setError(`Report generation failed: ${job.error_message || job.last_error || 'unknown error'}`);
      } else {
        setError('Report is taking longer than expected — it may still finish; try again shortly.');
      }
    } catch (err) {
      setError(`Failed to generate report: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setBusy(null);
    }
  };

  const activeFilters = useMemo((): string[] => {
    const f = filters;
    const out: string[] = [];
    if (f.search) out.push(`Search: "${f.search}"`);
    if (f.q) out.push(`Query: ${f.q}`);
    if (f.state) out.push(`State: ${f.state}`);
    if (f.sites) out.push(`Site: ${f.sites}`);
    if (f.subnets) out.push(`Subnets: ${f.subnets}`);
    if (f.subnet_labels) out.push(`Subnet labels: ${f.subnet_labels}`);
    if (f.tags) out.push(`Tags: ${f.tags}`);
    if (f.ports) out.push(`Ports: ${f.ports}`);
    if (f.services) out.push(`Services: ${f.services}`);
    if (f.port_states) out.push(`Port states: ${f.port_states}`);
    if (f.has_open_ports !== undefined) out.push(`Has open ports: ${f.has_open_ports ? 'Yes' : 'No'}`);
    if (f.os_filter) out.push(`OS: ${f.os_filter}`);
    if (f.tech) out.push(`Tech: ${f.tech}`);
    if (f.has_critical_vulns) out.push('Critical vulnerabilities');
    if (f.has_high_vulns) out.push('High vulnerabilities');
    if (f.has_medium_vulns) out.push('Medium vulnerabilities');
    if (f.has_low_vulns) out.push('Low vulnerabilities');
    if (f.has_exploit_available) out.push('Exploit available');
    if (f.has_test_execution) out.push('Has test execution');
    if (f.has_web_interface !== undefined) out.push(`Web interface: ${f.has_web_interface ? 'Yes' : 'No'}`);
    if (f.follow_status) out.push(`Follow: ${f.follow_status}`);
    if (f.assigned_to) out.push(`Assigned: ${f.assigned_to}`);
    if (f.out_of_scope_only) out.push('Out of scope');
    if (f.scan_ids) out.push(`Scan IDs: ${f.scan_ids}`);
    if (f.first_seen_in_scan) out.push('First seen in selected scans');
    if (f.with_notes_only) out.push('With notes only');
    return out;
  }, [filters]);

  // Every format except the streamed CSV caps at REPORT_HOST_CAP (the heavy
  // formats now generate on the report worker, so they get the full cap back).
  const overCap = totalHosts > REPORT_HOST_CAP;
  const isBusy = busy !== null;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !isBusy && onClose()}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <FileDown className="size-5" aria-hidden />
            Export Host Report
          </DialogTitle>
        </DialogHeader>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {truncated && (
          <Alert variant="warning">
            <AlertDescription>
              The report downloaded, but the server capped it — it is{' '}
              <strong>incomplete</strong>. Narrow your filters to capture everything, or use the{' '}
              <strong>CSV</strong> inventory which streams the full set.
            </AlertDescription>
          </Alert>
        )}

        <div className="space-y-xs">
          <p className="text-metadata text-muted-foreground">
            Based on your current filters this covers <strong>{totalHosts.toLocaleString()}</strong> host
            {totalHosts === 1 ? '' : 's'}.
          </p>
          {overCap && (
            <Alert variant="warning">
              <AlertDescription>
                Your filters match {totalHosts.toLocaleString()} hosts. PDF, HTML, JSON and the
                .zip bundles include the first {REPORT_HOST_CAP.toLocaleString()} — narrow your
                filters to capture everything, or use the <strong>CSV</strong> inventory which
                streams the full set.
              </AlertDescription>
            </Alert>
          )}
          <p className="text-caption text-muted-foreground">
            PDF, JSON and the .zip bundles are generated in the background and download
            automatically when ready — large reports may take a moment.
          </p>
          {activeFilters.length > 0 && (
            <div className="space-y-xxs">
              <p className="text-caption font-semibold text-muted-foreground">
                Active filters ({activeFilters.length}):
              </p>
              <div className="flex flex-wrap gap-xxs">
                {activeFilters.map((filter, index) => (
                  <Badge key={index} variant="outline">
                    {filter}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Step 1 — report type */}
        <div className="space-y-xxs">
          <Label>Report type</Label>
          <div className="grid grid-cols-1 gap-xs sm:grid-cols-2">
            {REPORT_TYPES.map((t) => {
              const selected = reportType === t.value;
              return (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => handleTypeChange(t.value)}
                  aria-pressed={selected}
                  className={cn(
                    'rounded-control border p-sm text-left transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    selected ? 'border-primary bg-accent/40' : 'border-border hover:bg-accent/20',
                  )}
                >
                  <p className="text-metadata font-semibold text-foreground">{t.label}</p>
                  <p className="mt-xxs text-caption text-muted-foreground">{t.description}</p>
                </button>
              );
            })}
          </div>
        </div>

        {/* Step 2 — format */}
        <div className="space-y-xxs">
          <Label htmlFor="reports-format">Format</Label>
          <Select value={format} onValueChange={(v) => setFormat(v as HumanFormat)}>
            <SelectTrigger id="reports-format">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {allowedFormats.map((f) => (
                <SelectItem key={f.value} value={f.value}>
                  {f.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isBusy}>
            Cancel
          </Button>
          <Button onClick={() => runExport(format, reportType)} disabled={isBusy}>
            {busy === format ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Generating…
              </>
            ) : (
              <>
                <Download className="size-4" aria-hidden />
                Generate {format.toUpperCase()}
              </>
            )}
          </Button>
        </DialogFooter>

        {/* Structured / AI exports — separated from human reports */}
        <div className="mt-xs border-t border-border pt-sm">
          <p className="text-caption font-semibold text-muted-foreground">Structured &amp; agent data</p>
          <div className="mt-xxs space-y-xxs">
            {STRUCTURED_FORMATS.map((s) => (
              <button
                key={s.value}
                type="button"
                onClick={() => runExport(s.value)}
                disabled={isBusy}
                className={cn(
                  'flex w-full items-start gap-xs rounded-control border border-border p-xs text-left',
                  'hover:bg-accent/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  'disabled:opacity-60',
                )}
              >
                {busy === s.value ? (
                  <Loader2 className="mt-xxs size-4 shrink-0 animate-spin" aria-hidden />
                ) : (
                  <s.Icon className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
                )}
                <span className="min-w-0">
                  <span className="block text-metadata font-medium text-foreground">{s.label}</span>
                  <span className="block text-caption text-muted-foreground">{s.description}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ReportsDialog;
