import React, { useState, useEffect, useRef } from 'react';
import { useDropzone } from 'react-dropzone';
import { Link } from 'react-router-dom';
import {
  ArrowDownToLine,
  Building2,
  Loader2,
  Pencil,
  Plus,
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
import { Button } from '../components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { Checkbox } from '../components/ui/checkbox';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';
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
import SiteManagerDialog from '../components/SiteManagerDialog';
import ScopeDomainsCard from '../components/ScopeDomainsCard';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection from '../components/posture/PostureSection';
import { buildHostsUrl } from '../utils/drilldownLinks';

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/**
 * The page's lead (v5.269.0): where the discovered hosts stand against the
 * declared scope, in one sentence.  Out of scope colours it; the fix is a
 * decision (declare it, or leave it untested), never "scan these".
 */
export function scopeLead(c: ScopeCoverageSummary): { sentence: string; tone: LeadTone } {
  if (c.total_subnets === 0 && c.total_domains === 0) {
    return {
      sentence: 'No scope declared yet — add the subnets or domains you are authorized to assess, or upload a scope file.',
      tone: 'neutral',
    };
  }
  if (c.total_hosts === 0) {
    return {
      sentence: `No hosts discovered yet; ${plural(c.total_subnets, 'subnet')} and ${plural(c.total_domains, 'domain')} declared.`,
      tone: 'neutral',
    };
  }
  const parts = [
    `${c.scoped_hosts.toLocaleString()} of ${plural(c.total_hosts, 'host')} ${c.scoped_hosts === 1 ? 'is' : 'are'} inside scoped subnets`,
    c.name_reachable_hosts > 0 ? `${c.name_reachable_hosts.toLocaleString()} reached only through an in-scope name` : null,
    c.out_of_scope_hosts > 0 ? `${c.out_of_scope_hosts.toLocaleString()} outside every scope` : null,
  ].filter((p): p is string => !!p);
  return {
    sentence: `${parts.join('; ')}.`,
    tone: c.out_of_scope_hosts > 0 ? 'warning' : 'clear',
  };
}

/**
 * An empty, editable subnet cell (v5.288.0): a muted "—" whose pencil appears
 * on row hover or keyboard focus.  It replaced "Click to add description" /
 * "Click to add site" placeholder text repeated on every row.  The button is
 * always in the tab order and named for what it does.
 */
const EmptyCellEdit: React.FC<{ label: string; onClick: () => void }> = ({ label, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    aria-label={label}
    title={label}
    className="group/edit inline-flex items-center gap-2xs rounded text-metadata text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
  >
    <span aria-hidden>—</span>
    <Pencil
      className="size-3.5 opacity-0 group-hover/row:opacity-100 group-focus-visible/edit:opacity-100"
      aria-hidden
    />
  </button>
);

const Scopes: React.FC = () => {
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const recon = useReconPlan();

  const [scope, setScope] = useState<Scope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [coverage, setCoverage] = useState<ScopeCoverageSummary | null>(null);

  const [uploadError, setUploadError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [domainsRefreshKey, setDomainsRefreshKey] = useState(0);

  const [exportScopeId, setExportScopeId] = useState<number | null>(null);
  const [exportScopeName, setExportScopeName] = useState('');
  const [showOutOfScopeDialog, setShowOutOfScopeDialog] = useState(false);

  const [newCidr, setNewCidr] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [addingSubnet, setAddingSubnet] = useState(false);

  const [editingSubnetId, setEditingSubnetId] = useState<number | null>(null);
  const [editCidrDraft, setEditCidrDraft] = useState('');
  const [editDescDraft, setEditDescDraft] = useState('');
  const [editSiteDraft, setEditSiteDraft] = useState('');
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
  const searchRef = useRef(debouncedSubnetSearch);
  searchRef.current = debouncedSubnetSearch;

  // v2.86.0 — subnet-label state.  The project-wide label catalogue
  // is fetched on mount and refreshed whenever the manager dialog
  // mutates it; selectedSubnetIds drives the bulk-apply affordance
  // that appears in the toolbar once any subnet is checked.
  const [labelCatalogue, setLabelCatalogue] = useState<SubnetLabelWithCounts[]>([]);
  const [labelManagerOpen, setLabelManagerOpen] = useState(false);
  const [siteManagerOpen, setSiteManagerOpen] = useState(false);
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
      // Said, not only logged: the page otherwise kept showing the state
      // from before the change the operator just made.
      console.error('Error refreshing scope:', err);
      toast.error('The change was saved, but the scope could not be reloaded — refresh the page.');
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
    // A page asked for under one search is dropped if the search changed
    // while it loaded, instead of being appended to the new result.
    const askedFor = debouncedSubnetSearch;
    setLoadingMore(true);
    try {
      const next = await fetchScopePage(scope.subnets.length, SUBNET_PAGE_SIZE);
      if (askedFor !== searchRef.current) return;
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

  const startEditSubnet = (
    id: number, cidr: string, description: string | null, site: string | null,
  ) => {
    setEditingSubnetId(id);
    setEditCidrDraft(cidr);
    setEditDescDraft(description || '');
    setEditSiteDraft(site || '');
  };
  const cancelEditSubnet = () => {
    setEditingSubnetId(null);
    setEditCidrDraft('');
    setEditDescDraft('');
    setEditSiteDraft('');
  };

  const handleSaveSubnet = async (subnetId: number) => {
    if (!scope) return;
    setSavingSubnet(true);
    try {
      await updateSubnet(scope.id, subnetId, {
        cidr: editCidrDraft.trim(),
        description: editDescDraft.trim(),
        site: editSiteDraft.trim(),
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

  // A subnet's label chips and their editor — in the Labels column, or inside
  // the row's editor while the row is being edited (v5.289.0).
  const renderLabels = (subnet: { id: number; cidr: string }, subnetLabels: SubnetLabelInfo[]) => (
    <div className="flex flex-wrap items-center gap-xxs">
      {subnetLabels.length === 0 ? (
        <span className="text-metadata text-muted-foreground" aria-label="No labels">—</span>
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
          // v5.288.0 — with no labels the pencil shows on
          // row hover or keyboard focus, not on every row.
          className={
            subnetLabels.length === 0
              ? 'opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100'
              : undefined
          }
        >
          <Pencil className="size-3.5" aria-hidden />
        </Button>
      </SubnetLabelEditorPopover>
    </div>
  );

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

  const onDrop = async (acceptedFiles: File[]) => {
    const file = acceptedFiles[0];
    if (!file) return;

    setUploading(true);
    setUploadError(null);
    setStatusMessage(null);

    try {
      const response = await uploadSubnetFile(file);
      setStatusMessage(response.message || `Scope file "${file.name}" uploaded successfully!`);
      await loadData();
      // The domains card owns its own paged list; a file with domain rows
      // has to make it reload.
      if (response.domains_added) setDomainsRefreshKey((k) => k + 1);
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

  const coverageLead = coverage ? scopeLead(coverage) : null;
  const subnetCount = scope ? (scope.subnets_total ?? scope.subnets.length) : 0;

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-start gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Scope</h1>
          <p className="text-metadata text-muted-foreground">
            The subnets, addresses and domains this project is authorized to assess.
          </p>
        </div>
        {/* v5.269.0 — no "Correlate Hosts": every import, subnet add, CIDR
            change and scope-file upload links hosts to subnets itself, and a
            deleted subnet's links cascade.  The endpoint stays for scripts. */}
        <div className="flex flex-wrap gap-xs">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setUploadError(null);
              setUploadOpen(true);
            }}
          >
            <Upload className="size-4" aria-hidden /> Upload scope file
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowOutOfScopeDialog(true)}>
            <ArrowDownToLine className="size-4" aria-hidden /> Export out-of-scope hosts
          </Button>
          {scope != null && (
            <Button
              size="sm"
              disabled={scope.subnets.length === 0}
              onClick={() => recon.openFor(scope.id, 'Project scope')}
              aria-label="Start agentic reconnaissance"
              title={scope.subnets.length === 0 ? 'Add a subnet first — recon runs against declared subnets.' : undefined}
            >
              <Rocket className="size-4" aria-hidden /> Start Agentic Recon
            </Button>
          )}
        </div>
      </div>

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

      {/* v5.269.0 — the Posture layout (UI_STYLE_GUIDE §7): one sentence,
          one strip of measures, then sections.  Was a collapsible card of
          coloured badges over two more cards. */}
      <div className="flex min-w-0 flex-col gap-lg">
        {coverage && coverageLead && (
          <>
            <PostureLead
              tone={coverageLead.tone}
              restsOn="Coverage is by address: a host is in scope when a declared subnet contains it. An in-scope name never makes the address it resolves to in scope."
            >
              {coverageLead.sentence}
            </PostureLead>
            <div className="grid gap-y-md divide-border sm:grid-cols-3 lg:grid-cols-5 lg:divide-x">
              <PostureMeasure
                label="In scope"
                info="Hosts whose address is inside at least one declared subnet."
                value={coverage.scoped_hosts.toLocaleString()}
              >
                of {coverage.total_hosts.toLocaleString()} host{coverage.total_hosts === 1 ? '' : 's'}
              </PostureMeasure>
              <PostureMeasure
                label="Via an in-scope name"
                info="Hosts in no declared subnet that an in-scope name currently resolves to. A third state: not out of scope, but not subnet-in-scope either — the name is approved, the address is not. Declare the subnet if the address itself should be in scope."
                value={coverage.name_reachable_hosts.toLocaleString()}
              >
                name approved, address not
              </PostureMeasure>
              <PostureMeasure
                label="Out of scope"
                info="Hosts in no declared subnet and not reached by any in-scope name."
                value={coverage.out_of_scope_hosts.toLocaleString()}
                to={coverage.out_of_scope_hosts > 0 ? buildHostsUrl({ outOfScopeOnly: true }) : undefined}
                toLabel="Out-of-scope hosts — view"
              >
                {coverage.out_of_scope_hosts > 0 ? 'confirm whether they are in scope' : 'none'}
              </PostureMeasure>
              <PostureMeasure
                label="Subnets"
                info="Subnet and single-address entries declared below."
                value={coverage.total_subnets.toLocaleString()}
              />
              <PostureMeasure
                label="Domains"
                info="Domain-scope entries declared below. They put names in scope, independently of subnets."
                value={coverage.total_domains.toLocaleString()}
              />
            </div>
          </>
        )}

        {scope == null ? (
          <p className="text-metadata text-muted-foreground">Loading project scope…</p>
        ) : (
          <PostureSection
            title={<span>Subnets and addresses</span>}
            description="Label entries (e.g. “UK DMZ”) and assign sites so the recon prompt and Posture can reason about zones."
            actions={<>
              <span className="tabular-nums text-muted-foreground">
                {subnetCount.toLocaleString()} entr{subnetCount === 1 ? 'y' : 'ies'}
                {debouncedSubnetSearch.trim() ? ' matching' : ''}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7"
                onClick={() => handleOpenScopeExport(scope.id, 'Project scope')}
                aria-label="Export scope"
                title="Export scope as txt / csv / json"
              >
                <ArrowDownToLine className="size-4" aria-hidden /> Export
              </Button>
            </>}
          >
            <div className="mb-sm flex flex-col gap-xs sm:flex-row sm:items-end">
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
              <Button variant="outline" onClick={handleAddSubnet} disabled={!newCidr.trim() || addingSubnet}>
                {addingSubnet ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <Plus className="size-4" aria-hidden />
                )}
                Add
              </Button>
              <Button
                variant="ghost"
                onClick={() => setLabelManagerOpen(true)}
                className="shrink-0 whitespace-nowrap"
                aria-label="Manage project subnet labels"
              >
                <TagsIcon className="size-4" aria-hidden />
                Manage labels
              </Button>
              <Button
                variant="ghost"
                onClick={() => setSiteManagerOpen(true)}
                className="shrink-0 whitespace-nowrap"
                aria-label="Manage site criticality and coverage"
              >
                <Building2 className="size-4" aria-hidden />
                Manage sites
              </Button>
            </div>

            {/* Subnet search — filters the server-paginated list so users
                can jump to an entry instead of paging.  Debounced; resets
                to page 0 and drives subnets_total so "Showing N of T" and
                "Load more" stay correct under the filter. */}
            <div className="mb-xs flex items-center gap-xs">
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
              <div className="mb-xs flex flex-wrap items-center gap-xs border-l-4 border-l-info py-xxs pl-sm">
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
              {/* Fixed layout (UI_STYLE_GUIDE): an unbounded description or
                  label set wraps inside its column instead of widening it. */}
              <Table style={{ tableLayout: 'fixed' }} className="min-w-[960px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      <Checkbox
                        checked={scope.subnets.length > 0 && selectedSubnetIds.size === scope.subnets.length}
                        onCheckedChange={toggleAllSelected}
                        aria-label="Select all subnets for bulk label apply"
                      />
                    </TableHead>
                    <TableHead className="w-[18%]">Subnet / IP</TableHead>
                    <TableHead className="w-20 text-right">Hosts</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="w-36">Site</TableHead>
                    <TableHead className="w-[20%]">Labels</TableHead>
                    <TableHead className="w-32">Added</TableHead>
                    <TableHead className="w-32 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {scope.subnets.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={8} className="py-xl text-center text-muted-foreground">
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
                        <TableRow
                          key={subnet.id}
                          className="group/row"
                          data-state={selectedSubnetIds.has(subnet.id) ? 'selected' : undefined}
                        >
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
                              <Link
                                to={`/hosts?subnets=${encodeURIComponent(subnet.cidr)}`}
                                title={`Show hosts in ${subnet.cidr}`}
                                className="break-all font-mono font-semibold text-primary hover:underline focus:outline-none focus-visible:underline"
                              >
                                {subnet.cidr}
                              </Link>
                            )}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-metadata">
                            {subnet.host_count != null ? subnet.host_count.toLocaleString() : '—'}
                          </TableCell>
                          {isEditing ? (
                            // v5.289.0 — the Description column is the table's
                            // flexible one and can be ~75px wide, so single-line
                            // inputs showed "Zone notes, ass" / "DMZ / Internet-f".
                            // While editing, the row's editor spans Description,
                            // Site and Labels: the description wraps in a
                            // textarea and the site input gets the full width.
                            <TableCell colSpan={3} className="align-top">
                              <div className="space-y-xs" data-testid={`subnet-editor-${subnet.id}`}>
                                <div className="space-y-xxs">
                                  <Label htmlFor={`subnet-desc-${subnet.id}`} className="text-caption text-muted-foreground">
                                    Description
                                  </Label>
                                  <Textarea
                                    id={`subnet-desc-${subnet.id}`}
                                    value={editDescDraft}
                                    onChange={(e) => setEditDescDraft(e.target.value)}
                                    placeholder="Zone notes, asset class, owner…"
                                    rows={2}
                                    className="min-h-0 w-full resize-y"
                                  />
                                </div>
                                <div className="flex flex-wrap items-end gap-sm">
                                  <div className="min-w-[12rem] flex-1 space-y-xxs">
                                    <Label htmlFor={`subnet-site-${subnet.id}`} className="text-caption text-muted-foreground">
                                      Site
                                    </Label>
                                    <Input
                                      id={`subnet-site-${subnet.id}`}
                                      value={editSiteDraft}
                                      onChange={(e) => setEditSiteDraft(e.target.value)}
                                      placeholder="Site / location…"
                                      className="w-full"
                                    />
                                  </div>
                                  <div className="min-w-0 space-y-xxs">
                                    <span className="block text-caption text-muted-foreground">Labels</span>
                                    {renderLabels(subnet, subnetLabels)}
                                  </div>
                                </div>
                              </div>
                            </TableCell>
                          ) : (
                            <>
                              <TableCell>
                                {subnet.description ? (
                                  <span className="break-words text-metadata">{subnet.description}</span>
                                ) : (
                                  <EmptyCellEdit
                                    label={`Add a description for ${subnet.cidr}`}
                                    onClick={() => startEditSubnet(subnet.id, subnet.cidr, subnet.description, subnet.site ?? null)}
                                  />
                                )}
                              </TableCell>
                              <TableCell>
                                {subnet.site ? (
                                  <span className="break-words text-metadata">{subnet.site}</span>
                                ) : (
                                  <EmptyCellEdit
                                    label={`Add a site for ${subnet.cidr}`}
                                    onClick={() => startEditSubnet(subnet.id, subnet.cidr, subnet.description, subnet.site ?? null)}
                                  />
                                )}
                              </TableCell>
                              <TableCell>{renderLabels(subnet, subnetLabels)}</TableCell>
                            </>
                          )}
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
                                      onClick={() => startEditSubnet(subnet.id, subnet.cidr, subnet.description, subnet.site ?? null)}
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
          </PostureSection>
        )}

        {/* v5.193.0 — domain scope alongside subnet scope.  Refreshes the
            coverage numbers on change (name-reachable hosts move between states). */}
        {scope != null && (
          <ScopeDomainsCard scopeId={scope.id} refreshKey={domainsRefreshKey} onChanged={loadData} />
        )}

        {coverage && coverage.out_of_scope_hosts > 0 && (
          <PostureSection
            title={<span>Hosts outside every scope</span>}
            description="Discovered at addresses no declared subnet or in-scope name covers. Confirm whether they are in scope before testing them; declare the subnet if they are."
            actions={(
              <Link to={buildHostsUrl({ outOfScopeOnly: true })} className="text-info hover:underline">
                View all out-of-scope hosts
              </Link>
            )}
          >
            <ul className="divide-y divide-border/60">
              {coverage.recent_out_of_scope_hosts.map((host) => {
                const lastSeen = host.last_seen ? new Date(host.last_seen).toLocaleString() : 'Unknown';
                const scanLabel = host.last_scan_filename
                  ? host.last_scan_filename
                  : host.last_scan_id
                  ? `Scan #${host.last_scan_id}`
                  : null;
                return (
                  <li key={`oos-${host.host_id}`} className="flex min-w-0 flex-wrap items-baseline gap-x-sm gap-y-xxs py-xxs">
                    <Link
                      to={`/hosts/${host.host_id}`}
                      className="shrink-0 font-mono text-metadata text-foreground hover:underline"
                    >
                      {host.ip_address}
                    </Link>
                    {host.hostname && (
                      <span className="min-w-0 max-w-[40%] truncate text-caption text-muted-foreground" title={host.hostname}>
                        {host.hostname}
                      </span>
                    )}
                    <span
                      className="ml-auto min-w-0 truncate text-caption text-muted-foreground"
                      title={scanLabel ?? undefined}
                    >
                      last seen {lastSeen}
                      {scanLabel && ` · ${scanLabel}`}
                    </span>
                  </li>
                );
              })}
            </ul>
            {coverage.out_of_scope_hosts > coverage.recent_out_of_scope_hosts.length && (
              <p className="mt-xs text-caption text-muted-foreground">
                Showing the most recent {coverage.recent_out_of_scope_hosts.length} of{' '}
                {coverage.out_of_scope_hosts.toLocaleString()} hosts.
              </p>
            )}
          </PostureSection>
        )}

        {coverage?.top_technologies && coverage.top_technologies.length > 0 && (
          <PostureSection
            title={<span>Technologies observed</span>}
            description="From httpx / EyeWitness / Nikto imports. Each opens the hosts running it."
          >
            <p className="flex flex-wrap gap-x-sm gap-y-xxs text-metadata">
              {coverage.top_technologies.map((t) => (
                <Link
                  key={t.name}
                  to={`/hosts?tech=${encodeURIComponent(t.name)}`}
                  className="text-info hover:underline"
                >
                  {/* v5.288.0 — "Nginx 1.24.0 · 1 host": a bare count read
                      as part of the version. */}
                  {t.name}{' '}
                  <span className="tabular-nums text-muted-foreground">
                    · {t.host_count.toLocaleString()} host{t.host_count === 1 ? '' : 's'}
                  </span>
                </Link>
              ))}
            </p>
          </PostureSection>
        )}
      </div>

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
            <DialogTitle>Upload scope file</DialogTitle>
            <DialogDescription>
              Append subnets and domains to this project's scope.  Accepts
              <code className="font-mono"> .txt </code>(one CIDR, IP or domain per line) or
              <code className="font-mono"> .csv </code>(one entry per row: subnet or domain in
              column 1, optional space-delimited labels in column 2, optional
              description in column 3, optional site in column 4).
            </DialogDescription>
          </DialogHeader>
          <p className="text-metadata text-muted-foreground">
            <code className="font-mono">.txt</code> — one entry per line. {' '}
            <code className="font-mono">.csv</code> — per row{' '}
            <code className="font-mono">192.168.1.0/24,prod internet-facing,UK DMZ,London DC</code>{' '}
            (label, description + site columns optional). A domain row such as{' '}
            <code className="font-mono">portal.example.com</code> declares that exact name;{' '}
            <code className="font-mono">*.example.com</code> declares the domain with subdomains
            (labels and site apply to subnets only). Re-uploading is safe: duplicate
            entries are skipped, labels are <em>added</em> (never replaced), a domain
            only ever widens, and a description updates only when provided.
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

      <SiteManagerDialog open={siteManagerOpen} onOpenChange={setSiteManagerOpen} />

      {confirmEl}
    </div>
  );
};

export default Scopes;
