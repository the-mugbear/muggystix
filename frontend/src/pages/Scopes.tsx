import React, { useState, useEffect, useRef } from 'react';
import { useDropzone } from 'react-dropzone';
import { useNavigate } from 'react-router-dom';
import {
  ArrowDownToLine,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Rocket,
  Save,
  Search,
  Tags as TagsIcon,
  Trash2,
  Upload,
  X as CloseIcon,
} from 'lucide-react';
import {
  getDefaultScope,
  uploadSubnetFile,
  correlateAllHosts,
  addScopeSubnets,
  updateSubnet,
  deleteSubnet,
  Scope,
  ScopeCoverageSummary,
  getScopeCoverage,
  // v2.86.0 — subnet labels.
  listSubnetLabels, bulkApplySubnetLabel,
  SubnetLabelWithCounts, SubnetLabelInfo,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import ScopeExport from '../components/ScopeExport';
import OutOfScopeExport from '../components/OutOfScopeExport';
import StartReconDialog from '../components/StartReconDialog';
import { useConfirm } from '../hooks/useConfirm';
import { useReconPlan } from '../hooks/useReconPlan';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { Checkbox } from '../components/ui/checkbox';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { cn } from '../utils/cn';
import {
  SubnetLabelManagerDialog,
  SubnetLabelEditorPopover,
  SubnetLabelChip,
} from '../components/SubnetLabelManager';

type CoverageTone = 'success' | 'warning' | 'destructive' | 'muted';

const coverageTone = (pct: number, hasScope: boolean): CoverageTone => {
  if (!hasScope) return 'muted';
  if (pct >= 90) return 'success';
  if (pct >= 50) return 'warning';
  if (pct > 0) return 'destructive';
  return 'muted';
};

const Scopes: React.FC = () => {
  const navigate = useNavigate();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const recon = useReconPlan();

  const [scope, setScope] = useState<Scope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [correlating, setCorrelating] = useState(false);
  const [coverage, setCoverage] = useState<ScopeCoverageSummary | null>(null);

  const [uploadError, setUploadError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const [coverageOpen, setCoverageOpen] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem('scopes.coverageOpen') === 'true';
  });

  const [exportScopeId, setExportScopeId] = useState<number | null>(null);
  const [exportScopeName, setExportScopeName] = useState('');
  const [showOutOfScopeDialog, setShowOutOfScopeDialog] = useState(false);

  const [newCidr, setNewCidr] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [addingSubnet, setAddingSubnet] = useState(false);

  const [editingSubnetId, setEditingSubnetId] = useState<number | null>(null);
  const [editCidrDraft, setEditCidrDraft] = useState('');
  const [editDescDraft, setEditDescDraft] = useState('');
  const [savingSubnet, setSavingSubnet] = useState(false);

  // v2.94.0 — the subnet list is server-paginated so a 6000-subnet project
  // doesn't block /scopes on a multi-MB payload + 6000-row render.  We load
  // a page at a time and append via "Load more"; reloads after a mutation
  // re-fetch as many subnets as are currently shown so the view is stable.
  const [loadingMore, setLoadingMore] = useState(false);

  // Subnet search — a case-insensitive substring filter over cidr +
  // description, applied server-side (before pagination) so users can jump
  // straight to an entry instead of paging.  Debounced so we don't fire a
  // request per keystroke; the result resets the list to page 0.
  const [subnetSearch, setSubnetSearch] = useState('');
  const debouncedSubnetSearch = useDebouncedValue(subnetSearch, 300);

  // v2.86.0 — subnet-label state.  The project-wide label catalogue
  // is fetched on mount and refreshed whenever the manager dialog
  // mutates it; selectedSubnetIds drives the bulk-apply affordance
  // that appears in the toolbar once any subnet is checked.
  const [labelCatalogue, setLabelCatalogue] = useState<SubnetLabelWithCounts[]>([]);
  const [labelManagerOpen, setLabelManagerOpen] = useState(false);
  const [selectedSubnetIds, setSelectedSubnetIds] = useState<Set<number>>(new Set());
  const [bulkApplying, setBulkApplying] = useState(false);

  const fetchLabelCatalogue = async () => {
    try {
      const rows = await listSubnetLabels();
      setLabelCatalogue(rows);
    } catch (err) {
      console.warn('Failed to load subnet label catalogue', err);
    }
  };

  useEffect(() => {
    loadData(true);
    fetchLabelCatalogue();
  }, []);

  const SUBNET_PAGE_SIZE = 200;

  // Re-fetch as many subnets as are currently shown (at least one page) so a
  // reload triggered by a mutation keeps the user's "load more" progress.
  const currentSubnetWindow = () =>
    Math.max(scope?.subnets.length ?? 0, SUBNET_PAGE_SIZE);

  // Single place that shapes the paginated scope fetch and injects the active
  // search term.  The four callers below (initial load, refresh, search,
  // load-more) all route through here so a new param (e.g. withFindingsOnly)
  // only has to be threaded in once instead of four times.
  const fetchScopePage = (skip: number, limit: number) =>
    getDefaultScope({
      subnetsSkip: skip,
      subnetsLimit: limit,
      subnetsSearch: debouncedSubnetSearch,
    });

  const loadData = async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    try {
      const [scopeData, coverageData] = await Promise.all([
        fetchScopePage(0, currentSubnetWindow()),
        getScopeCoverage(),
      ]);
      setScope(scopeData);
      setCoverage(coverageData);
      setError(null);
    } catch (err) {
      setError('Failed to load scope data');
      console.error('Error loading scope data:', err);
    } finally {
      if (showSpinner) setLoading(false);
    }
  };

  const refreshScope = async () => {
    try {
      const [scopeData, coverageData] = await Promise.all([
        fetchScopePage(0, currentSubnetWindow()),
        getScopeCoverage(),
      ]);
      setScope(scopeData);
      setCoverage(coverageData);
    } catch (err) {
      console.error('Error refreshing scope:', err);
    }
  };

  // Re-fetch the first page whenever the (debounced) search term changes.
  // Skip the initial mount — the [] effect's loadData() already covers it,
  // so this only fires on real search edits.
  const searchInitialized = useRef(false);
  useEffect(() => {
    if (!searchInitialized.current) {
      searchInitialized.current = true;
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const scopeData = await fetchScopePage(0, SUBNET_PAGE_SIZE);
        if (!cancelled) setScope(scopeData);
      } catch (err) {
        console.error('Error searching subnets:', err);
        toast.error('Failed to search subnets.');
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSubnetSearch]);

  const loadMoreSubnets = async () => {
    if (!scope || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = await fetchScopePage(scope.subnets.length, SUBNET_PAGE_SIZE);
      setScope((prev) =>
        prev
          ? {
              ...prev,
              subnets: [...prev.subnets, ...next.subnets],
              subnets_total: next.subnets_total ?? prev.subnets_total,
            }
          : next,
      );
    } catch (err) {
      console.error('Error loading more subnets:', err);
      toast.error('Failed to load more subnets.');
    } finally {
      setLoadingMore(false);
    }
  };

  const handleAddSubnet = async () => {
    if (!scope || !newCidr.trim()) return;
    setAddingSubnet(true);
    try {
      await addScopeSubnets(scope.id, [
        { cidr: newCidr.trim(), description: newDescription.trim() || undefined },
      ]);
      toast.success(`Added ${newCidr.trim()}.`);
      setNewCidr('');
      setNewDescription('');
      await refreshScope();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to add entry.'));
    } finally {
      setAddingSubnet(false);
    }
  };

  const startEditSubnet = (id: number, cidr: string, description: string | null) => {
    setEditingSubnetId(id);
    setEditCidrDraft(cidr);
    setEditDescDraft(description || '');
  };
  const cancelEditSubnet = () => {
    setEditingSubnetId(null);
    setEditCidrDraft('');
    setEditDescDraft('');
  };

  const handleSaveSubnet = async (subnetId: number) => {
    if (!scope) return;
    setSavingSubnet(true);
    try {
      await updateSubnet(scope.id, subnetId, {
        cidr: editCidrDraft.trim(),
        description: editDescDraft.trim(),
      });
      toast.success('Entry updated.');
      cancelEditSubnet();
      await refreshScope();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update entry.'));
    } finally {
      setSavingSubnet(false);
    }
  };

  // v2.86.0 — bulk-apply one label across every selected subnet.
  // Server-idempotent: re-applying to a subnet that already carries
  // the label is a no-op.  We refresh both the scope and the catalogue
  // so the chips + per-label counts stay accurate.
  const handleBulkApplyLabel = async (labelId: number, labelName: string) => {
    const ids = Array.from(selectedSubnetIds);
    if (ids.length === 0) return;
    setBulkApplying(true);
    try {
      await bulkApplySubnetLabel(labelId, ids);
      toast.success(`Applied "${labelName}" to ${ids.length} subnet${ids.length === 1 ? '' : 's'}.`);
      setSelectedSubnetIds(new Set());
      await Promise.all([refreshScope(), fetchLabelCatalogue()]);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to apply label.'));
    } finally {
      setBulkApplying(false);
    }
  };

  const toggleSubnetSelected = (subnetId: number) => {
    setSelectedSubnetIds((prev) => {
      const next = new Set(prev);
      if (next.has(subnetId)) next.delete(subnetId); else next.add(subnetId);
      return next;
    });
  };
  const toggleAllSelected = () => {
    setSelectedSubnetIds((prev) => {
      if (!scope) return prev;
      if (prev.size === scope.subnets.length) return new Set();
      return new Set(scope.subnets.map((s) => s.id));
    });
  };

  // Reflect a single-subnet label change locally without a full reload.
  // The PUT already authoritative-set the labels, so we patch state in
  // place and just kick the catalogue counts.
  const applyLabelEditToLocal = (subnetId: number, nextLabels: SubnetLabelInfo[]) => {
    setScope((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        subnets: prev.subnets.map((s) =>
          s.id === subnetId ? { ...s, labels: nextLabels } : s,
        ),
      };
    });
    fetchLabelCatalogue();
  };

  const handleDeleteSubnet = async (subnetId: number, cidr: string) => {
    if (!scope) return;
    const ok = await confirm({
      title: 'Delete entry',
      body: 'Any host-subnet mappings for this entry will be removed. This cannot be undone.',
      resourceName: cidr,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await deleteSubnet(scope.id, subnetId);
      toast.success(`Deleted ${cidr}.`);
      await refreshScope();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to delete entry.'));
    }
  };

  const toggleCoverage = () => {
    const next = !coverageOpen;
    setCoverageOpen(next);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('scopes.coverageOpen', next ? 'true' : 'false');
    }
  };

  const onDrop = async (acceptedFiles: File[]) => {
    const file = acceptedFiles[0];
    if (!file) return;

    setUploading(true);
    setUploadError(null);
    setStatusMessage(null);

    try {
      const response = await uploadSubnetFile(file);
      setStatusMessage(response.message || `Subnet file "${file.name}" uploaded successfully!`);
      await loadData();
      setUploadOpen(false);
      setTimeout(() => setStatusMessage(null), 3000);
    } catch (err: unknown) {
      setUploadError(formatApiError(err, 'Upload failed. Please try again.'));
    } finally {
      setUploading(false);
    }
  };

  const { getRootProps, getInputProps, isDragActive, fileRejections } = useDropzone({
    onDrop,
    accept: { 'text/plain': ['.txt'], 'text/csv': ['.csv'] },
    // 50 MB hard cap matches what's reasonable for a subnet-CSV upload;
    // anything larger is almost certainly the wrong file type.  Reject
    // client-side so the user gets immediate feedback instead of a
    // multi-minute round-trip ending in a 413.
    maxSize: 50 * 1024 * 1024,
    multiple: false,
  });

  const handleCorrelateAll = async () => {
    const ok = await confirm({
      title: 'Correlate all hosts',
      body: 'This will re-compute host-to-subnet mappings for every host in the project. For large datasets it may take several seconds.',
      severity: 'warning',
      confirmLabel: 'Correlate',
    });
    if (!ok) return;
    try {
      setCorrelating(true);
      setError(null);
      const result = await correlateAllHosts();
      if (result?.message) {
        setStatusMessage(result.message);
        setTimeout(() => setStatusMessage(null), 3000);
      }
      await loadData();
    } catch (err: unknown) {
      setError(formatApiError(err, 'Failed to correlate hosts to subnets.'));
      console.error('Error correlating hosts:', err);
    } finally {
      setCorrelating(false);
    }
  };

  const handleOpenScopeExport = (scopeId: number, scopeName: string) => {
    setExportScopeId(scopeId);
    setExportScopeName(scopeName);
  };

  if (loading) {
    return (
      <div className="flex min-h-96 items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  const tone = coverage ? coverageTone(coverage.coverage_percentage, coverage.has_scope_configuration) : 'muted';

  return (
    <div className="p-md md:p-lg">
      <div className="mb-xs flex flex-col gap-xs sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-page-title font-semibold">Scope</h1>
        <div className="flex flex-wrap gap-xs">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setUploadError(null);
              setUploadOpen(true);
            }}
          >
            <Upload className="size-4" aria-hidden /> Upload File
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleCorrelateAll}
            disabled={correlating}
            className="text-success hover:text-success"
          >
            {correlating ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="size-4" aria-hidden />
            )}
            {correlating ? 'Correlating…' : 'Correlate Hosts'}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowOutOfScopeDialog(true)}
          >
            <ArrowDownToLine className="size-4" aria-hidden /> Export OOS
          </Button>
        </div>
      </div>
      <p className="mb-md text-metadata text-muted-foreground">
        Subnets and individual addresses this project is authorized to assess. Add entries one at a
        time, upload a file, or label existing entries (e.g. &quot;UK DMZ&quot;) so the agentic
        recon prompt can reason about zones.
      </p>

      {uploadError && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{uploadError}</AlertDescription>
        </Alert>
      )}
      {statusMessage && (
        <Alert variant="success" className="mb-sm">
          <AlertDescription>{statusMessage}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {coverage && (
        <Card className="mb-md">
          <CardContent className="p-sm">
            <button
              type="button"
              onClick={toggleCoverage}
              aria-expanded={coverageOpen}
              className="flex w-full flex-wrap items-center gap-sm rounded-control text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="text-section-title font-semibold">Scope Coverage</span>
              <Badge variant={tone}>{coverage.coverage_percentage.toFixed(1)}% covered</Badge>
              <Badge variant="outline">{coverage.total_hosts} hosts</Badge>
              <Badge variant="outline" className="border-success/40 text-success">
                {coverage.scoped_hosts} in scope
              </Badge>
              {coverage.out_of_scope_hosts > 0 && (
                <Badge variant="outline" className="border-destructive/40 text-destructive">
                  {coverage.out_of_scope_hosts} out of scope
                </Badge>
              )}
              <Badge variant="outline">{coverage.total_subnets} subnets</Badge>
              <span className="ml-auto inline-flex size-7 items-center justify-center rounded-control text-muted-foreground">
                {coverageOpen ? (
                  <ChevronUp className="size-4" aria-hidden />
                ) : (
                  <ChevronDown className="size-4" aria-hidden />
                )}
              </span>
            </button>

            {coverageOpen && (
              <div className="pt-sm">
                {coverage.out_of_scope_hosts > 0 ? (
                  <div>
                    <p className="mb-xs text-metadata font-semibold">
                      Hosts discovered outside configured scopes
                    </p>
                    <ul className="max-h-60 divide-y divide-border overflow-auto rounded-control border border-border">
                      {coverage.recent_out_of_scope_hosts.map((host) => {
                        const lastSeen = host.last_seen
                          ? new Date(host.last_seen).toLocaleString()
                          : 'Unknown';
                        const scanLabel = host.last_scan_filename
                          ? host.last_scan_filename
                          : host.last_scan_id
                          ? `Scan #${host.last_scan_id}`
                          : null;
                        return (
                          <li key={`oos-${host.host_id}`}>
                            <button
                              type="button"
                              onClick={() => navigate(`/hosts/${host.host_id}`)}
                              className="flex w-full flex-col gap-xxs px-sm py-xs text-left hover:bg-accent focus:outline-none focus-visible:bg-accent"
                            >
                              <span className="flex items-center justify-between gap-xs">
                                <span className="font-mono text-metadata">{host.ip_address}</span>
                                {host.hostname && (
                                  <span className="max-w-[60%] truncate text-caption text-muted-foreground">
                                    {host.hostname}
                                  </span>
                                )}
                              </span>
                              <span className="text-caption text-muted-foreground">
                                Last seen {lastSeen}
                                {scanLabel && ` · ${scanLabel}`}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                    {coverage.out_of_scope_hosts > coverage.recent_out_of_scope_hosts.length && (
                      <p className="mt-xs text-caption text-muted-foreground">
                        Showing the most recent {coverage.recent_out_of_scope_hosts.length} of{' '}
                        {coverage.out_of_scope_hosts} hosts.
                      </p>
                    )}
                    <Button
                      variant="link"
                      size="sm"
                      className="mt-xs h-auto p-0"
                      onClick={() => navigate('/hosts?out_of_scope=true')}
                    >
                      View all out-of-scope hosts
                    </Button>
                  </div>
                ) : coverage.has_scope_configuration ? (
                  <Alert variant="success" className="mt-xs">
                    <AlertDescription>All hosts currently map to defined scopes.</AlertDescription>
                  </Alert>
                ) : (
                  <Alert variant="info" className="mt-xs">
                    <AlertDescription>
                      No subnet scopes configured yet. Create one or upload a subnet file.
                    </AlertDescription>
                  </Alert>
                )}

                {coverage.top_technologies && coverage.top_technologies.length > 0 && (
                  <div className="mt-md">
                    <p className="mb-xs text-metadata font-semibold">Top technologies observed</p>
                    <div className="flex flex-wrap gap-xxs">
                      {coverage.top_technologies.map((t) => (
                        <button
                          key={t.name}
                          type="button"
                          onClick={() => navigate(`/hosts?tech=${encodeURIComponent(t.name)}`)}
                          className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <Badge
                            variant="outline"
                            className="cursor-pointer border-info/40 text-info hover:bg-info/10"
                          >
                            {t.name} · {t.host_count}
                          </Badge>
                        </button>
                      ))}
                    </div>
                    <p className="mt-xxs text-caption text-muted-foreground">
                      Click a chip to filter Hosts by that technology. Derived from httpx /
                      eyewitness / nikto ingest.
                    </p>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {scope == null ? (
        <Card>
          <CardContent className="p-lg text-center text-metadata text-muted-foreground">
            Loading project scope…
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="flex flex-wrap items-center gap-xs border-b border-border p-sm">
              <span className="flex-1 text-metadata text-muted-foreground">
                {(scope.subnets_total ?? scope.subnets.length).toLocaleString()} entr
                {(scope.subnets_total ?? scope.subnets.length) === 1 ? 'y' : 'ies'}
                {debouncedSubnetSearch.trim() ? ' matching' : ''}
              </span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    disabled={scope.subnets.length === 0}
                    onClick={() => recon.openFor(scope.id, 'Project scope')}
                    aria-label="Start agentic reconnaissance"
                  >
                    <Rocket className="size-4" aria-hidden /> Start Agentic Recon
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Start agentic reconnaissance</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleOpenScopeExport(scope.id, 'Project scope')}
                    aria-label="Export scope"
                  >
                    <ArrowDownToLine className="size-4" aria-hidden />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Export scope as txt / csv / json</TooltipContent>
              </Tooltip>
              {/* v4.52.1 — "View hosts in scope" drill-in removed.  In
                  the single-default-scope world a project's scope holds
                  every host; /hosts?subnets=<all CIDRs> resolves to
                  exactly the same view as plain /hosts, so the icon was
                  only adding noise to the toolbar. */}
            </div>

            <div className="flex flex-col gap-xs border-b border-border bg-accent/30 p-sm sm:flex-row sm:items-end">
              <div className="flex-1">
                <Label htmlFor="new-cidr">CIDR or IP</Label>
                <Input
                  id="new-cidr"
                  value={newCidr}
                  onChange={(e) => setNewCidr(e.target.value)}
                  placeholder="10.0.0.0/24 or 10.0.0.5"
                  disabled={addingSubnet}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && newCidr.trim()) handleAddSubnet();
                  }}
                />
              </div>
              <div className="flex-[2]">
                <Label htmlFor="new-desc">Description (e.g. UK DMZ)</Label>
                <Input
                  id="new-desc"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  disabled={addingSubnet}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && newCidr.trim()) handleAddSubnet();
                  }}
                />
              </div>
              <Button onClick={handleAddSubnet} disabled={!newCidr.trim() || addingSubnet}>
                {addingSubnet ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <Plus className="size-4" aria-hidden />
                )}
                Add
              </Button>
              <Button
                variant="outline"
                onClick={() => setLabelManagerOpen(true)}
                className="shrink-0 whitespace-nowrap"
                aria-label="Manage project subnet labels"
              >
                <TagsIcon className="size-4" aria-hidden />
                Manage labels
              </Button>
            </div>

            {/* Subnet search — filters the server-paginated list so users
                can jump to an entry instead of paging.  Debounced; resets
                to page 0 and drives subnets_total so "Showing N of T" and
                "Load more" stay correct under the filter. */}
            <div className="flex items-center gap-xs border-b border-border px-sm py-xs">
              <div className="relative min-w-0 flex-1">
                <Search
                  className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden
                />
                <Input
                  value={subnetSearch}
                  onChange={(e) => setSubnetSearch(e.target.value)}
                  placeholder="Search subnets by CIDR or description…"
                  className="pl-8"
                  aria-label="Search subnets by CIDR or description"
                />
              </div>
              {subnetSearch && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSubnetSearch('')}
                  aria-label="Clear subnet search"
                >
                  <CloseIcon className="size-4" aria-hidden />
                  Clear
                </Button>
              )}
            </div>

            {/* v2.86.0 — bulk-action bar: hidden until at least one
                subnet is checked.  Mirrors the ScopeDetail surface so
                the affordance is in the same place on both pages. */}
            {selectedSubnetIds.size > 0 && (
              <div className="flex flex-wrap items-center gap-xs border-b border-border bg-muted/30 px-sm py-xs">
                <span className="text-metadata">
                  {selectedSubnetIds.size} subnet{selectedSubnetIds.size === 1 ? '' : 's'} selected
                </span>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="sm" disabled={bulkApplying || labelCatalogue.length === 0}>
                      {bulkApplying && <Loader2 className="size-4 animate-spin" aria-hidden />}
                      Apply label…
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    {labelCatalogue.length === 0 ? (
                      <DropdownMenuItem disabled>
                        No labels — open Manage labels to create one
                      </DropdownMenuItem>
                    ) : (
                      labelCatalogue.map((lbl) => (
                        <DropdownMenuItem
                          key={lbl.id}
                          onSelect={() => handleBulkApplyLabel(lbl.id, lbl.name)}
                        >
                          <SubnetLabelChip label={{ id: lbl.id, name: lbl.name, color: lbl.color }} />
                        </DropdownMenuItem>
                      ))
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelectedSubnetIds(new Set())}
                  disabled={bulkApplying}
                >
                  Clear selection
                </Button>
              </div>
            )}

            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      <Checkbox
                        checked={scope.subnets.length > 0 && selectedSubnetIds.size === scope.subnets.length}
                        onCheckedChange={toggleAllSelected}
                        aria-label="Select all subnets for bulk label apply"
                      />
                    </TableHead>
                    <TableHead className="w-1/5">Subnet / IP</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="min-w-[180px]">Labels</TableHead>
                    <TableHead className="w-32">Added</TableHead>
                    <TableHead className="w-32 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {scope.subnets.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="py-xl text-center text-muted-foreground">
                        {debouncedSubnetSearch.trim()
                          ? `No subnets match "${debouncedSubnetSearch.trim()}". Try a different search or clear it.`
                          : "No entries in this project's scope yet. Add one above or upload a file."}
                      </TableCell>
                    </TableRow>
                  ) : (
                    scope.subnets.map((subnet) => {
                      const isEditing = editingSubnetId === subnet.id;
                      const subnetLabels = subnet.labels ?? [];
                      return (
                        <TableRow key={subnet.id} data-state={selectedSubnetIds.has(subnet.id) ? 'selected' : undefined}>
                          <TableCell>
                            <Checkbox
                              checked={selectedSubnetIds.has(subnet.id)}
                              onCheckedChange={() => toggleSubnetSelected(subnet.id)}
                              aria-label={`Select ${subnet.cidr} for bulk label apply`}
                            />
                          </TableCell>
                          <TableCell>
                            {isEditing ? (
                              <Input
                                value={editCidrDraft}
                                onChange={(e) => setEditCidrDraft(e.target.value)}
                                autoFocus
                              />
                            ) : (
                              <span className="font-mono font-semibold">{subnet.cidr}</span>
                            )}
                          </TableCell>
                          <TableCell>
                            {isEditing ? (
                              <Input
                                value={editDescDraft}
                                onChange={(e) => setEditDescDraft(e.target.value)}
                                placeholder="Zone notes, asset class, owner…"
                              />
                            ) : subnet.description ? (
                              <span className="break-words text-metadata">{subnet.description}</span>
                            ) : (
                              <button
                                type="button"
                                onClick={() => startEditSubnet(subnet.id, subnet.cidr, subnet.description)}
                                className="text-metadata italic text-muted-foreground hover:text-foreground focus:outline-none focus-visible:underline"
                              >
                                Click to add description
                              </button>
                            )}
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-wrap items-center gap-xxs">
                              {subnetLabels.length === 0 ? (
                                <span className="text-caption text-muted-foreground">No labels</span>
                              ) : (
                                subnetLabels.map((lbl) => (
                                  <SubnetLabelChip key={lbl.id} label={lbl} />
                                ))
                              )}
                              <SubnetLabelEditorPopover
                                subnetId={subnet.id}
                                subnetCidr={subnet.cidr}
                                currentLabels={subnetLabels}
                                catalogue={labelCatalogue}
                                onSaved={(next) => applyLabelEditToLocal(subnet.id, next)}
                              >
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  aria-label={`Edit labels for ${subnet.cidr}`}
                                >
                                  <Pencil className="size-3.5" aria-hidden />
                                </Button>
                              </SubnetLabelEditorPopover>
                            </div>
                          </TableCell>
                          <TableCell className="text-caption text-muted-foreground">
                            {new Date(subnet.created_at).toLocaleDateString()}
                          </TableCell>
                          <TableCell className="text-right">
                            {isEditing ? (
                              <div className="flex justify-end gap-xxs">
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      disabled={savingSubnet || !editCidrDraft.trim()}
                                      onClick={() => handleSaveSubnet(subnet.id)}
                                      aria-label={`Save changes to ${subnet.cidr}`}
                                    >
                                      {savingSubnet ? (
                                        <Loader2 className="size-4 animate-spin" aria-hidden />
                                      ) : (
                                        <Save className="size-4" aria-hidden />
                                      )}
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Save</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={cancelEditSubnet}
                                      aria-label="Cancel subnet edit"
                                    >
                                      <CloseIcon className="size-4" aria-hidden />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Cancel</TooltipContent>
                                </Tooltip>
                              </div>
                            ) : (
                              <div className="flex justify-end gap-xxs">
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => startEditSubnet(subnet.id, subnet.cidr, subnet.description)}
                                      aria-label={`Edit subnet ${subnet.cidr}`}
                                    >
                                      <Pencil className="size-4" aria-hidden />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Edit</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => handleDeleteSubnet(subnet.id, subnet.cidr)}
                                      aria-label={`Delete subnet ${subnet.cidr}`}
                                      className="text-muted-foreground hover:text-destructive"
                                    >
                                      <Trash2 className="size-4" aria-hidden />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Delete</TooltipContent>
                                </Tooltip>
                              </div>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
            {scope.subnets_total !== undefined &&
              scope.subnets.length < scope.subnets_total && (
                <div className="flex items-center justify-center gap-sm border-t p-sm">
                  <span className="text-caption text-muted-foreground">
                    Showing {scope.subnets.length} of {scope.subnets_total}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={loadMoreSubnets}
                    disabled={loadingMore}
                  >
                    {loadingMore && (
                      <Loader2 className="mr-xxs size-4 animate-spin" aria-hidden />
                    )}
                    Load more
                  </Button>
                </div>
              )}
          </CardContent>
        </Card>
      )}

      {exportScopeId !== null && (
        <ScopeExport
          open
          onClose={() => setExportScopeId(null)}
          scopeId={exportScopeId}
          scopeName={exportScopeName}
        />
      )}

      <OutOfScopeExport
        open={showOutOfScopeDialog}
        onClose={() => setShowOutOfScopeDialog(false)}
      />

      <Dialog
        open={uploadOpen}
        onOpenChange={(next) => {
          if (!next && !uploading) setUploadOpen(false);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Upload Subnet File</DialogTitle>
            <DialogDescription>
              Append subnets to this project's scope.  Accepts
              <code className="font-mono"> .txt </code>(one CIDR/IP per line) or
              <code className="font-mono"> .csv </code>(one entry per row:
              subnet in column 1, optional space-delimited labels in column 2).
            </DialogDescription>
          </DialogHeader>
          <p className="text-metadata text-muted-foreground">
            <code className="font-mono">.txt</code> — one subnet per line. {' '}
            <code className="font-mono">.csv</code> — per row{' '}
            <code className="font-mono">192.168.1.0/24,prod internet-facing</code>{' '}
            (column 2 labels are optional, space-delimited). Re-uploading is safe:
            duplicate subnets are skipped and labels are <em>added</em>, never replaced.
          </p>
          <div
            {...getRootProps()}
            className={cn(
              'flex flex-col items-center gap-xs rounded-panel border-2 border-dashed p-lg text-center transition-colors',
              uploading
                ? 'cursor-not-allowed border-border opacity-60'
                : isDragActive
                ? 'cursor-pointer border-primary bg-accent'
                : 'cursor-pointer border-border hover:border-primary hover:bg-accent',
            )}
          >
            <input {...getInputProps()} disabled={uploading} />
            <Upload className="size-8 text-primary" aria-hidden />
            <p className="text-metadata">
              {isDragActive ? 'Drop the file here…' : 'Drag & drop or click to select'}
            </p>
          </div>
          {uploading && (
            <div className="flex items-center gap-xs text-metadata">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Uploading and processing…
            </div>
          )}
          {uploadError && (
            <Alert variant="destructive">
              <AlertDescription>{uploadError}</AlertDescription>
            </Alert>
          )}
          {/* Surface react-dropzone rejection reasons so users see WHY
              a .docx / oversized file disappeared (audit H5). */}
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
          <DialogFooter>
            <Button variant="outline" onClick={() => setUploadOpen(false)} disabled={uploading}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Extracted dialog markup — same UI is now reused by
          ReconRunsList's "Start Recon" affordance. */}
      <StartReconDialog recon={recon} />

      <SubnetLabelManagerDialog
        open={labelManagerOpen}
        onOpenChange={setLabelManagerOpen}
        onCatalogueChange={() => {
          fetchLabelCatalogue();
          // Reload the scope so any renamed/deleted labels in the
          // chips column refresh (a delete cascades server-side and
          // the local `subnet.labels` would otherwise show stale
          // entries).
          refreshScope();
        }}
      />

      {confirmEl}
    </div>
  );
};

export default Scopes;
