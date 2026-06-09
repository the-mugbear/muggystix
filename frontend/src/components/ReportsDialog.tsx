import React, { useState } from 'react';
import {
  Code,
  Download,
  FileArchive,
  FileDown,
  FileText,
  Globe,
  Loader2,
  Table as TableIcon,
} from 'lucide-react';
import { generateHostsReport } from '../services/api';
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

interface ReportsDialogProps {
  open: boolean;
  onClose: () => void;
  filters: Record<string, string | boolean | number | undefined>;
  totalHosts: number;
}

type ReportFormat = 'csv' | 'html' | 'json' | 'agent-package' | 'markdown-bundle';

const REPORT_FORMATS: Array<{
  value: ReportFormat;
  label: string;
  Icon: typeof FileArchive;
  description: string;
}> = [
  {
    value: 'agent-package',
    label: 'Agent Package (.zip)',
    Icon: FileArchive,
    description: 'Full structured export for agentic LLM planning and execution',
  },
  {
    value: 'markdown-bundle',
    label: 'Markdown Report Bundle (.zip)',
    Icon: FileText,
    description: 'Human-readable Markdown report with companion CSV files',
  },
  {
    value: 'csv',
    label: 'CSV',
    Icon: TableIcon,
    description: 'Best for spreadsheet analysis',
  },
  {
    value: 'html',
    label: 'HTML Report',
    Icon: Globe,
    description: 'Formatted web page with styling',
  },
  {
    value: 'json',
    label: 'JSON Data',
    Icon: Code,
    description: 'Machine-readable structured data',
  },
];

const ReportsDialog: React.FC<ReportsDialogProps> = ({
  open,
  onClose,
  filters,
  totalHosts,
}) => {
  const [selectedFormat, setSelectedFormat] = useState<ReportFormat>('agent-package');
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGenerateReport = async () => {
    setIsGenerating(true);
    setError(null);
    try {
      await generateHostsReport(selectedFormat, filters);
      onClose();
    } catch (err) {
      setError(
        `Failed to generate report: ${err instanceof Error ? err.message : 'Unknown error'}`,
      );
    } finally {
      setIsGenerating(false);
    }
  };

  const renderActiveFilters = (): string[] => {
    const activeFilters: string[] = [];
    if (filters.search) activeFilters.push(`Search: "${filters.search}"`);
    if (filters.state) activeFilters.push(`State: ${filters.state}`);
    if (filters.ports) activeFilters.push(`Ports: ${filters.ports}`);
    if (filters.services) activeFilters.push(`Services: ${filters.services}`);
    if (filters.port_states) activeFilters.push(`Port states: ${filters.port_states}`);
    if (filters.has_open_ports !== undefined)
      activeFilters.push(`Has open ports: ${filters.has_open_ports ? 'Yes' : 'No'}`);
    if (filters.os_filter) activeFilters.push(`OS: ${filters.os_filter}`);
    if (filters.subnets) activeFilters.push(`Subnets: ${filters.subnets}`);
    if (filters.has_critical_vulns) activeFilters.push('Critical vulnerabilities');
    if (filters.has_high_vulns) activeFilters.push('High vulnerabilities');
    if (filters.follow_status) activeFilters.push(`Follow: ${filters.follow_status}`);
    if (filters.out_of_scope_only) activeFilters.push('Out of scope');
    if (filters.scan_ids) activeFilters.push(`Scan IDs: ${filters.scan_ids}`);
    if (filters.first_seen_in_scan) activeFilters.push('First seen in selected scans');
    if (filters.with_notes_only) activeFilters.push('With notes only');
    return activeFilters;
  };

  const activeFilters = renderActiveFilters();
  const selectedFormatInfo = REPORT_FORMATS.find((f) => f.value === selectedFormat);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !isGenerating && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <FileDown className="size-5" aria-hidden />
            Generate Host Report
          </DialogTitle>
        </DialogHeader>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="space-y-xs">
          <h3 className="text-subheading">Report Summary</h3>
          <p className="text-metadata text-muted-foreground">
            This report will include approximately <strong>{totalHosts}</strong> host
            {totalHosts === 1 ? '' : 's'} based on your current filters.
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

        <div className="space-y-xxs">
          <Label htmlFor="reports-format">Report format</Label>
          <Select
            value={selectedFormat}
            onValueChange={(v) => setSelectedFormat(v as ReportFormat)}
          >
            <SelectTrigger id="reports-format">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REPORT_FORMATS.map((format) => (
                <SelectItem key={format.value} value={format.value}>
                  {format.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {selectedFormatInfo && (
          <div className="rounded-control bg-muted/50 p-sm">
            <p className="text-metadata">
              <strong>{selectedFormatInfo.label}:</strong> {selectedFormatInfo.description}
            </p>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isGenerating}>
            Cancel
          </Button>
          <Button onClick={handleGenerateReport} disabled={isGenerating}>
            {isGenerating ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Generating…
              </>
            ) : (
              <>
                <Download className="size-4" aria-hidden />
                Generate Report
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ReportsDialog;
