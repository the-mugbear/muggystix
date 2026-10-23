import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDropzone } from 'react-dropzone';
import { Link } from 'react-router-dom';
import {
  ArrowDownToLine,
  Building2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
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
import { queryErrorText } from '../lib/query';
import ScopeExport from '../components/ScopeExport';
import AgentTaskButton from '../components/agent-sessions/AgentTaskButton';
import { agentInstruction } from '../utils/agentRuns';
import { useConfirm } from '../hooks/useConfirm';
import { useUrlSearchDraft } from '../hooks/useUrlSearchDraft';
import type { ListPage } from '../hooks/useListQuery';
import { usePagedList } from '../hooks/usePagedList';
import { useUrlPage } from '../hooks/useUrlPage';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRole } from '../hooks/useProjectRole';
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
import { PagedFooter } from '../components/operations/QueueParts';
import { InfoTip } from '../components/ui/info-tip';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection from '../components/posture/PostureSection';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { IP_OR_CIDR_HINT, isIpOrCidr } from '../utils/ipAddress';
import { formatDate, formatTimestamp } from '../utils/relativeTime';
import { selectAllState } from '../utils/selection';
import { inertWhen } from '../utils/uiStyles';
import { plural } from '../utils/plural';

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
  // The strip under the lead carries the three counts, each a link: the lead
  // says the one thing to act on, not the strip again.
  const out = c.out_of_scope_hosts;
  const named = c.name_reachable_hosts;
  if (out > 0) {
    return {
      sentence: `${out.toLocaleString()} of ${plural(c.total_hosts, 'host')} ${out === 1 ? 'is' : 'are'} outside the scope — confirm whether ${out === 1 ? 'it is' : 'they are'} in scope.`,
      tone: 'warning',
    };
  }
  if (named > 0) {
    return {
      sentence: `No host is outside the scope; ${named.toLocaleString()} ${named === 1 ? 'is' : 'are'} reached only through an in-scope name.`,
      tone: 'clear',
    };
  }
  return {
    sentence: `${c.scoped_hosts.toLocaleString()} of ${plural(c.total_hosts, 'host')} ${c.scoped_hosts === 1 ? 'is' : 'are'} inside scoped subnets.`,
    tone: 'clear',
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

/** Subnets to a page — the Segments table's size.  (The "Load more" list
 *  read 200 at a time; a page that long put its footer four screens down.) */
const SUBNET_PAGE_SIZE = 50;
/** The most subnets one "Apply label" takes — the route's own limit
 *  (`SubnetLabelBulkAssignMany.subnet_ids`; a longer list is refused whole).
 *  The selection stops there and says so.  `tests/subnetBulkLimit.test.ts`
 *  pins the two numbers to each other. */
const SUBNET_BULK_MAX = 1000;
const NO_LABELS: SubnetLabelWithCounts[] = [];
const NO_SELECTION: ReadonlySet<number> = new Set();
type ScopeSubnet = Scope['subnets'][number];
/** One answer of the scope endpoint, as a page of the subnet list. */
type ScopePage = Scope & ListPage<ScopeSubnet>;

const Scopes: React.FC = () => {
  const toast = useToast();
  // Changing the scope is a project analyst's (R32); the page reads the same
  // for a viewer or auditor, without the add row, the editors or the uploads.
  const { canWrite, canExport } = useProjectRole();
  const [confirmEl, confirm] = useConfirm();

  const queryClient = useQueryClient();
  const projectId = useProjectId();

  const [uploadError, setUploadError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const [exportOpen, setExportOpen] = useState(false);

  const [newCidr, setNewCidr] = useState('');
  const [newDescription, setNewDescription] = useState('');
  // v5.290.0 — a rejected entry is explained under the field, not in a
  // toast carrying Python's parser text.
  const [newCidrError, setNewCidrError] = useState<string | null>(null);

  const [editingSubnetId, setEditingSubnetId] = useState<number | null>(null);
  const [editCidrDraft, setEditCidrDraft] = useState('');
  const [editCidrError, setEditCidrError] = useState<string | null>(null);
  const [editDescDraft, setEditDescDraft] = useState('');
  const [editSiteDraft, setEditSiteDraft] = useState('');

  // Subnet search — a case-insensitive substring filter over cidr +
  // description, applied server-side (before pagination) so users can jump
  // straight to an entry instead of paging.  Debounced so we don't fire a
  // request per keystroke; a new search starts from the first page (the one
  // address write drops `page`).
  // In the URL (`?subnet_q=`, replace not push) so a filtered scope can be
  // shared and survives a reload (B15).
  // The address owns the search (5.358.0): `subnetSearch` is what the list is
  // asked for, read from it on every render; only the text being typed is
  // state.  (It was copied into state once and written back by an effect, so
  // Back or a link to another search was overwritten — the one page the lint
  // rule against that pattern still found.)
  const subnetBox = useUrlSearchDraft('subnet_q');
  const subnetSearch = subnetBox.value;

  // v2.86.0 — subnet labels.  The project-wide catalogue is one read, shared
  // with the manager dialog and each row's label editor (the same key): what
  // the dialog changes is here without a callback, and the editors take what
  // this read holds rather than asking once per row.
  const [labelManagerOpen, setLabelManagerOpen] = useState(false);
  const [siteManagerOpen, setSiteManagerOpen] = useState(false);
  // The subnets ticked for "Apply label…".  They belong to the search they
  // were ticked under — a bulk action never reaches a row the current search
  // hides — and are kept from page to page of it: the bar says how many are
  // not on the page on screen.  Another search FORGETS them (while rendering,
  // so no render shows them): they used to be hidden only, and came back —
  // with whatever had been deleted or edited out of the search meanwhile —
  // when the same search was typed again (review 2026-10-10, R9).
  const [selection, setSelection] = useState<{ search: string; ids: ReadonlySet<number> }>(
    { search: '', ids: NO_SELECTION },
  );
  if (selection.search !== subnetSearch) setSelection({ search: subnetSearch, ids: NO_SELECTION });
  const selectedSubnetIds = selection.search === subnetSearch ? selection.ids : NO_SELECTION;
  const selectionFull = selectedSubnetIds.size >= SUBNET_BULK_MAX;
  const changeSelection = (change: (ids: Set<number>) => void) => setSelection((prev) => {
    const ids = new Set(prev.search === subnetSearch ? prev.ids : NO_SELECTION);
    change(ids);
    return { search: subnetSearch, ids };
  });
  /** A subnet that is gone, or may have left the search, is ticked for
   *  nothing — whatever the search on screen is by the time this runs. */
  const untickSubnet = (subnetId: number) => setSelection((prev) => {
    if (!prev.ids.has(subnetId)) return prev;
    const ids = new Set(prev.ids);
    ids.delete(subnetId);
    return { ...prev, ids };
  });

  const labelsQuery = useQuery({
    queryKey: ['listSubnetLabels', projectId],
    queryFn: ({ signal }) => listSubnetLabels(projectId, signal),
  });
  const labelCatalogue = labelsQuery.data ?? NO_LABELS;
  // A failed catalogue is said (R34): it used to look like "this project has
  // no labels", with Apply label… disabled and nothing to explain it.
  const labelCatalogueError = queryErrorText(labelsQuery.error, 'The subnet labels could not be loaded.');

  // v2.94.0 — the subnet list is server-paginated so a 6000-subnet project
  // doesn't block /scopes on a multi-MB payload + 6000-row render.  ONE page
  // at a time, its number in the address (`?page=`): a change to the scope
  // re-reads that page only.  (It was a "Load more" list, and every write
  // re-read each loaded page in turn — thirty requests after 6,000 subnets.)
  // The search is in the key: a slow reload of one search cannot answer for
  // another (R33).  The endpoint answers with the scope AND one page of its
  // subnets: the subnets are the list's rows.
  const subnetList = usePagedList<ScopeSubnet, ScopePage>(
    'getDefaultScope',
    async ({ offset, limit, signal }) => {
      const page = await getDefaultScope(projectId, {
        subnetsSkip: offset,
        subnetsLimit: limit,
        subnetsSearch: subnetSearch,
      }, signal);
      return { ...page, items: page.subnets, total: page.subnets_total };
    },
    [projectId, { subnetsSearch: subnetSearch }],
    { pageSize: SUBNET_PAGE_SIZE, page: useUrlPage(), within: projectId, errorMessage: 'The subnets could not be loaded.' },
  );
  // The rows on screen: this page's — or, while another page or search loads
  // and when it could not be read, the rows of the last one that answered.
  const shownPage = subnetList.response ?? subnetList.lastResponse;
  const subnets = shownPage ? shownPage.items : null;
  const subnetsTotal = shownPage ? shownPage.total : 0;
  // The page or search asked for has no answer of its own (yet, or at all).
  const subnetsArePrevious = subnetList.response == null && shownPage != null;

  // The coverage is a read of its own: it does not depend on the subnet
  // search, so a search never takes the lead and the measures with it.
  const coverageQuery = useQuery({
    queryKey: ['getScopeCoverage', projectId],
    queryFn: ({ signal }) => getScopeCoverage(projectId, undefined, signal),
  });
  const coverage = coverageQuery.data ?? null;

  // The spinner belongs to the first load only.
  const loading = !coverageQuery.isFetched || (subnets == null && subnetList.error == null);
  const error = (subnets == null ? subnetList.error : null)
    ?? (coverage == null ? queryErrorText(coverageQuery.error, 'Failed to load scope data') : null);
  // Nothing asks again by itself (no retry, no poll): without this the reader
  // reloads the page.  Reads again whichever of the two did not answer.
  const retryFirstLoad = () => {
    if (subnets == null && subnetList.error != null) void subnetList.reload();
    if (coverage == null && coverageQuery.isError) void coverageQuery.refetch();
  };

  // A search or a page that could not be read: said under the search box,
  // with Retry, and the rows that were shown stay (they are the ones from
  // before it).
  const staleError = subnetsArePrevious ? subnetList.error : null;

  // Nothing re-reads the scope but a change (no poll, no Refresh), so a
  // failed re-read is a change that the page does not show: the page keeps
  // the state from before it.  Said above the scope, with a Retry that reads
  // again whichever of the two did not answer — not "refresh the page".
  const subnetsReloadFailed = subnetList.response != null && subnetList.error != null;
  const reloadFailed = subnetsReloadFailed || coverageQuery.isRefetchError;
  const retryReload = () => {
    if (subnetsReloadFailed) void subnetList.reload();
    if (coverageQuery.isRefetchError) void coverageQuery.refetch();
  };

  /** After a change to the scope: the page of subnets on screen and where
   *  the hosts stand. */
  const scopeChanged = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['getDefaultScope', projectId] }),
    queryClient.invalidateQueries({ queryKey: ['getScopeCoverage', projectId] }),
  ]);

  const addSubnet = useMutation({
    mutationFn: ({ cidr, description }: { cidr: string; description?: string }) =>
      addScopeSubnets(projectId, [{ cidr, description }]),
    onSuccess: (_added, { cidr }) => {
      toast.success(`Added ${cidr}.`);
      setNewCidr('');
      setNewDescription('');
      return scopeChanged();
    },
    onError: (err) => setNewCidrError(formatApiError(err, 'Failed to add entry.')),
  });
  const addingSubnet = addSubnet.isPending;

  const handleAddSubnet = () => {
    if (!newCidr.trim()) return;
    if (!isIpOrCidr(newCidr)) {
      setNewCidrError(IP_OR_CIDR_HINT);
      return;
    }
    setNewCidrError(null);
    addSubnet.mutate({
      cidr: newCidr.trim(),
      description: newDescription.trim() || undefined,
    });
  };

  const startEditSubnet = (
    id: number, cidr: string, description: string | null, site: string | null,
  ) => {
    setEditingSubnetId(id);
    setEditCidrDraft(cidr);
    setEditCidrError(null);
    setEditDescDraft(description || '');
    setEditSiteDraft(site || '');
  };
  const cancelEditSubnet = () => {
    setEditingSubnetId(null);
    setEditCidrDraft('');
    setEditCidrError(null);
    setEditDescDraft('');
    setEditSiteDraft('');
  };

  const saveSubnet = useMutation({
    mutationFn: ({ subnetId, ...changes }: {
      subnetId: number; cidr: string; description: string; site: string;
    }) => updateSubnet(projectId, subnetId, changes),
    onSuccess: (_saved, { subnetId }) => {
      // Under a search, the entry as saved may not match it any more (the
      // search reads the address and the description): it would stay ticked
      // on no page, and "Apply label…" would reach a row the search hides.
      // Whether it still matches is the server's to say, so it is unticked.
      const unticked = subnetSearch !== '' && selectedSubnetIds.has(subnetId);
      if (unticked) untickSubnet(subnetId);
      toast.success(unticked
        ? 'Entry updated. It is no longer ticked — it may not match the search any more.'
        : 'Entry updated.');
      cancelEditSubnet();
      return scopeChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update entry.')),
  });
  const savingSubnet = saveSubnet.isPending;

  const handleSaveSubnet = (subnetId: number) => {
    if (!isIpOrCidr(editCidrDraft)) {
      setEditCidrError(IP_OR_CIDR_HINT);
      return;
    }
    setEditCidrError(null);
    saveSubnet.mutate({
      subnetId,
      cidr: editCidrDraft.trim(),
      description: editDescDraft.trim(),
      site: editSiteDraft.trim(),
    });
  };

  // v2.86.0 — bulk-apply one label across every selected subnet, on this
  // page of the search or another.
  // Server-idempotent: re-applying to a subnet that already carries
  // the label is a no-op.  Both the scope and the catalogue are re-read
  // so the chips + per-label counts stay accurate.
  const clearSelection = () => setSelection({ search: subnetSearch, ids: NO_SELECTION });
  const bulkApply = useMutation({
    mutationFn: ({ labelId, ids }: { labelId: number; labelName: string; ids: number[] }) =>
      bulkApplySubnetLabel(projectId, labelId, ids),
    onSuccess: (_label, { labelName, ids }) => {
      toast.success(`Applied "${labelName}" to ${ids.length} subnet${ids.length === 1 ? '' : 's'}.`);
      clearSelection();
      return Promise.all([scopeChanged(), queryClient.invalidateQueries({ queryKey: ['listSubnetLabels', projectId] })]);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to apply label.')),
  });
  const bulkApplying = bulkApply.isPending;

  const handleBulkApplyLabel = (labelId: number, labelName: string) => {
    const ids = Array.from(selectedSubnetIds);
    if (ids.length === 0) return;
    bulkApply.mutate({ labelId, labelName, ids });
  };

  // Rows that are not this page's answer take no tick (they are inert on
  // screen too), and nothing is ticked past what one "Apply label" takes.
  const toggleSubnetSelected = (subnetId: number) => {
    if (subnetsArePrevious) return;
    changeSelection((ids) => {
      if (!ids.delete(subnetId) && ids.size < SUBNET_BULK_MAX) ids.add(subnetId);
    });
  };
  // The header box is this PAGE's rows: all of them ticked unticks them, any
  // other state ticks the rest — as far as the limit goes.  Rows ticked on
  // another page are left alone.
  const toggleAllSelected = () => {
    if (subnetsArePrevious) return;
    changeSelection((ids) => {
      const rows = subnets ?? [];
      const all = rows.every((s) => ids.has(s.id));
      rows.forEach((s) => {
        if (all) ids.delete(s.id);
        else if (ids.size < SUBNET_BULK_MAX) ids.add(s.id);
      });
    });
  };

  // A subnet's label chips and their editor — in the Labels column, or inside
  // the row's editor while the row is being edited (v5.289.0).  The editor
  // puts a saved change on the subnet's row itself (the PUT answers with the
  // subnet's labels), without re-reading the scope.
  const renderLabels = (subnet: { id: number; cidr: string }, subnetLabels: SubnetLabelInfo[]) => (
    <div className="flex flex-wrap items-center gap-xxs">
      {subnetLabels.length === 0 ? (
        <span className="text-metadata text-muted-foreground" aria-label="No labels">—</span>
      ) : (
        subnetLabels.map((lbl) => (
          <SubnetLabelChip key={lbl.id} label={lbl} />
        ))
      )}
      {canWrite && (
      <SubnetLabelEditorPopover
        subnetId={subnet.id}
        subnetCidr={subnet.cidr}
        currentLabels={subnetLabels}
      >
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Edit labels for ${subnet.cidr}`}
          // v5.288.0 — with no labels the pencil shows on
          // row hover or keyboard focus, not on every row.  Kept to the
          // text line's height: the full-size icon button made the row's
          // "—" sit lower than the other cells' first line.
          className={cn(
            '-my-xxs size-6',
            subnetLabels.length === 0
              && 'opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100',
          )}
        >
          <Pencil className="size-3.5" aria-hidden />
        </Button>
      </SubnetLabelEditorPopover>
      )}
    </div>
  );

  const removeSubnet = useMutation({
    mutationFn: ({ subnetId }: { subnetId: number; cidr: string }) =>
      deleteSubnet(projectId, subnetId),
    onSuccess: (_void, { subnetId, cidr }) => {
      toast.success(`Deleted ${cidr}.`);
      // A deleted subnet is no longer ticked for anything.  Unconditionally:
      // asking the selection of the search on screen first missed one ticked
      // under a search the reader had left, and one stale id makes the whole
      // "Apply label" request a 404.
      untickSubnet(subnetId);
      return scopeChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to delete entry.')),
  });

  const handleDeleteSubnet = async (subnetId: number, cidr: string) => {
    const ok = await confirm({
      title: 'Delete entry',
      body: 'Any host-subnet mappings for this entry will be removed. This cannot be undone.',
      resourceName: cidr,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    removeSubnet.mutate({ subnetId, cidr });
  };

  const upload = useMutation({
    mutationFn: (file: File) => uploadSubnetFile(projectId, file),
    onMutate: () => {
      setUploadError(null);
      setStatusMessage(null);
    },
    onSuccess: async (response, file) => {
      setStatusMessage(response.message || `Scope file "${file.name}" uploaded successfully!`);
      // A file's label column adds to the catalogue; its domain rows are the
      // domains section's list.
      void queryClient.invalidateQueries({ queryKey: ['listSubnetLabels', projectId] });
      if (response.domains_added) void queryClient.invalidateQueries({ queryKey: ['listScopeDomains', projectId] });
      await scopeChanged();
      setUploadOpen(false);
      setTimeout(() => setStatusMessage(null), 3000);
    },
    onError: (err) => setUploadError(formatApiError(err, 'Upload failed. Please try again.')),
  });
  const uploading = upload.isPending;

  const onDrop = (acceptedFiles: File[]) => {
    const file = acceptedFiles[0];
    if (!file) return;
    upload.mutate(file);
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

  if (loading) {
    return (
      <div className="flex min-h-96 items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  const coverageLead = coverage ? scopeLead(coverage) : null;
  // Whether the scope declares a subnet at all — the coverage's count, not
  // the rows on screen (a search that matches nothing hides them all).
  const noSubnetsDeclared = coverage ? coverage.total_subnets === 0 : subnetsTotal === 0 && !subnetSearch;
  const selectedOnPage = (subnets ?? []).filter((s) => selectedSubnetIds.has(s.id)).length;
  const selectedElsewhere = selectedSubnetIds.size - selectedOnPage;

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-start gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Scope</h1>
        </div>
        {/* v5.269.0 — no "Correlate Hosts": every import, subnet add, CIDR
            change and scope-file upload links hosts to subnets itself, and a
            deleted subnet's links cascade.  The endpoint stays for scripts. */}
        <div className="flex flex-wrap gap-xs">
          {canWrite && (
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
          )}
          {subnets != null && canWrite && (
            // Scanning uploads scans: a project analyst's, as on Operations.
            // 5.313.0 — one way to start an agent: the operator's session,
            // handed the task. The per-scope key is gone; 5.313.1 — so is the
            // recon run: the agent reads the scope and uploads to its session.
            // The task names no scope, and says what the WHOLE scope holds
            // (the coverage's counts, not the page of rows on screen).
            <AgentTaskButton
              variant="default"
              label="Scan with your agent"
              instruction={agentInstruction.scanScope(coverage ? {
                subnets: coverage.total_subnets,
                domains: coverage.total_domains,
              } : undefined)}
              disabled={noSubnetsDeclared}
              title={noSubnetsDeclared ? 'Add a subnet first — the agent scans the declared subnets.' : undefined}
            />
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
          <AlertDescription>
            {error}{' '}
            <button type="button" className="underline hover:no-underline" onClick={retryFirstLoad}>Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {reloadFailed && (
        <p role="alert" className="mb-sm break-words text-metadata text-destructive">
          The change was saved, but the scope could not be reloaded.{' '}
          <button type="button" className="text-info hover:underline" onClick={retryReload}>Retry</button>
        </p>
      )}

      {/* v5.269.0 — the Posture layout (UI_STYLE_GUIDE §7): one sentence,
          one strip of measures, then sections.  Was a collapsible card of
          coloured badges over two more cards. */}
      <div className="flex min-w-0 flex-col gap-lg">
        {coverage && coverageLead && (
          <>
            <PostureLead tone={coverageLead.tone}>
              {coverageLead.sentence}{' '}
              <InfoTip
                label="How scope coverage is decided"
                text="Coverage is by address: a host is in scope when a declared subnet contains it. An in-scope name never makes the address it resolves to in scope."
              />{' '}
              {/* What has been collected on these hosts is Evidence's page. */}
              <Link to="/posture/evidence" className="rounded text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                What has been collected
              </Link>
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

        {subnets == null ? (
          <p className="text-metadata text-muted-foreground">Loading project scope…</p>
        ) : (
          <PostureSection
            title={<span>Subnets and addresses</span>}
            description="Label entries (e.g. “UK DMZ”) and assign sites so your agent and Posture can reason about zones."
            actions={<>
              <span className="tabular-nums text-muted-foreground">
                {subnetsTotal.toLocaleString()} entr{subnetsTotal === 1 ? 'y' : 'ies'}
                {subnetSearch ? ' matching' : ''}
              </span>
              {canExport && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7"
                  onClick={() => setExportOpen(true)}
                  aria-label="Export scope"
                  title="Export the declared networks and domains as a scope file"
                >
                  <ArrowDownToLine className="size-4" aria-hidden /> Export
                </Button>
              )}
            </>}
          >
            {canWrite && (
            <div className="mb-sm flex flex-col gap-xs sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1">
                <Label htmlFor="new-cidr">CIDR or IP</Label>
                <Input
                  id="new-cidr"
                  value={newCidr}
                  onChange={(e) => { setNewCidr(e.target.value); setNewCidrError(null); }}
                  placeholder="10.0.0.0/24 or 10.0.0.5"
                  disabled={addingSubnet}
                  aria-invalid={newCidrError ? true : undefined}
                  aria-describedby={newCidrError ? 'new-cidr-error' : undefined}
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
            )}
            {labelCatalogueError && (
              <p role="status" className="mb-sm flex flex-wrap items-center gap-xs break-words text-caption text-muted-foreground">
                <span className="min-w-0">{labelCatalogueError} Labels already on a subnet are still shown; applying one needs the list.</span>
                <Button variant="link" size="sm" className="h-auto p-0" onClick={() => void labelsQuery.refetch()}>
                  Retry
                </Button>
              </p>
            )}
            {/* Below the row, not inside the field's column: the row aligns
                its items to the bottom, so a message inside would lift the
                input off the buttons' line. */}
            {newCidrError && (
              <p id="new-cidr-error" role="alert" className="-mt-xs mb-sm break-words text-caption text-destructive">
                {newCidrError}
              </p>
            )}

            {/* Subnet search — filters the server-paginated list so users
                can jump to an entry instead of paging.  Debounced; starts
                from the first page and drives subnets_total, so the footer's
                "1–50 of T" and its pages are the search's. */}
            <div className="mb-xs flex items-center gap-xs">
              <div className="relative min-w-0 flex-1">
                <Search
                  className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden
                />
                <Input
                  value={subnetBox.draft}
                  onChange={(e) => subnetBox.setDraft(e.target.value)}
                  placeholder="Search subnets by CIDR or description…"
                  className="pl-8"
                  aria-label="Search subnets by CIDR or description"
                />
              </div>
              {subnetBox.draft && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => subnetBox.commit('')}
                  aria-label="Clear subnet search"
                >
                  <CloseIcon className="size-4" aria-hidden />
                  Clear
                </Button>
              )}
            </div>
            {staleError && (
              <p role="alert" className="mb-xs break-words text-caption text-destructive">
                {staleError} The subnets below are the ones shown before.{' '}
                <button type="button" className="text-info hover:underline" onClick={() => { void subnetList.reload(); }}>Retry</button>
              </p>
            )}

            {/* v2.86.0 — bulk-action bar: hidden until at least one
                subnet is checked.  The selection is kept from page to page
                of a search, so the bar says how many of the ticked subnets
                are not on screen: "Apply label…" reaches those too. */}
            {canWrite && selectedSubnetIds.size > 0 && (
              <div className="mb-xs flex flex-wrap items-center gap-xs border-l-4 border-l-info py-xxs pl-sm">
                <span className="text-metadata">
                  {selectedSubnetIds.size.toLocaleString()} subnet{selectedSubnetIds.size === 1 ? '' : 's'} selected
                  {selectedElsewhere > 0 && (
                    <span className="text-muted-foreground"> · {selectedElsewhere.toLocaleString()} not on this page</span>
                  )}
                  {selectionFull && (
                    <span className="text-warning">
                      {' '}· the most one “Apply label” takes ({SUBNET_BULK_MAX.toLocaleString()}) — apply it, then select the rest
                    </span>
                  )}
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
                  onClick={clearSelection}
                  disabled={bulkApplying}
                >
                  Clear selection
                </Button>
              </div>
            )}

            {/* Rows of the page or search before the one asked for are
                dimmed and inert until theirs arrive (or the read is retried):
                `inert`, so the keyboard cannot reach a checkbox or an editor
                either (`pointer-events-none` stopped the mouse only). */}
            <div
              className={cn('overflow-x-auto', subnetsArePrevious && 'opacity-60')}
              aria-busy={subnetList.loading || undefined}
              data-testid="scope-subnet-rows"
              {...inertWhen(subnetsArePrevious)}
            >
              {/* Fixed layout (UI_STYLE_GUIDE): an unbounded description or
                  label set wraps inside its column instead of widening it. */}
              {/* UX review 2026-09-24 — the fixed columns left Description,
                  the one operators type into, 82px (its header ran into
                  "Site") and the table 44px wider than the page, Actions cut
                  off. Now: 320px of fixed columns plus 43% for subnet / site /
                  labels, so Description keeps ~225px beside the sidebar and
                  ~170px at the 860px floor, and the table fits. */}
              <Table style={{ tableLayout: 'fixed' }} className="min-w-[860px]">
                <TableHeader>
                  <TableRow>
                    {canWrite && (
                      <TableHead className="w-10">
                        <Checkbox
                          // Some of the subnets = the dash, not an empty box.
                          checked={selectAllState(selectedOnPage, subnets.length)}
                          onCheckedChange={toggleAllSelected}
                          // At the limit there is nothing more it could tick.
                          disabled={selectionFull && selectedOnPage < subnets.length}
                          aria-label="Select all subnets on this page for bulk label apply"
                        />
                      </TableHead>
                    )}
                    <TableHead className="w-[15%]">Subnet / IP</TableHead>
                    {/* w-22, not w-16: "12 unscanned" sits under the count on
                        one line (at w-20 it broke after the number and every
                        row grew a line). */}
                    <TableHead className="w-22 pl-0 text-right">Hosts</TableHead>
                    <TableHead>Description</TableHead>
                    {/* A typical site name ("DMZ / Internet-facing") wraps
                        to two lines at most. */}
                    <TableHead className="w-[15%]">Site</TableHead>
                    <TableHead className="w-[13%]">Labels</TableHead>
                    <TableHead className="w-24">Added</TableHead>
                    {canWrite && <TableHead className="w-24 text-right">Actions</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {subnets.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={canWrite ? 8 : 6} className="py-xl text-center text-muted-foreground">
                        {subnetsTotal > 0
                          // A page past the end (its rows were deleted, or an
                          // old link): the list is stepping back to the last.
                          ? 'Loading…'
                          : subnetSearch
                            ? `No subnets match "${subnetSearch}". Try a different search or clear it.`
                            : canWrite
                              ? "No entries in this project's scope yet. Add one above or upload a file."
                              : "No entries in this project's scope yet."}
                      </TableCell>
                    </TableRow>
                  ) : (
                    subnets.map((subnet) => {
                      const isEditing = editingSubnetId === subnet.id;
                      const subnetLabels = subnet.labels ?? [];
                      return (
                        <TableRow
                          key={subnet.id}
                          className="group/row"
                          data-state={selectedSubnetIds.has(subnet.id) ? 'selected' : undefined}
                          {...(canWrite ? { 'aria-selected': selectedSubnetIds.has(subnet.id) } : {})}
                        >
                          {canWrite && (
                            <TableCell>
                              <Checkbox
                                checked={selectedSubnetIds.has(subnet.id)}
                                onCheckedChange={() => toggleSubnetSelected(subnet.id)}
                                disabled={selectionFull && !selectedSubnetIds.has(subnet.id)}
                                title={selectionFull && !selectedSubnetIds.has(subnet.id)
                                  ? `${SUBNET_BULK_MAX.toLocaleString()} subnets are selected — the most one “Apply label” takes.`
                                  : undefined}
                                aria-label={`Select ${subnet.cidr} for bulk label apply`}
                              />
                            </TableCell>
                          )}
                          <TableCell>
                            {isEditing ? (
                              <div className="min-w-0 space-y-xxs">
                                <Input
                                  value={editCidrDraft}
                                  onChange={(e) => { setEditCidrDraft(e.target.value); setEditCidrError(null); }}
                                  aria-label={`CIDR or IP for ${subnet.cidr}`}
                                  aria-invalid={editCidrError ? true : undefined}
                                  aria-describedby={editCidrError ? `subnet-cidr-error-${subnet.id}` : undefined}
                                  autoFocus
                                />
                                {editCidrError && (
                                  <p id={`subnet-cidr-error-${subnet.id}`} role="alert" className="break-words text-caption text-destructive">
                                    {editCidrError}
                                  </p>
                                )}
                              </div>
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
                          {/* No left padding: the cell is right-aligned, and the
                              12px it gives back is what keeps "12 unscanned"
                              on one line (seen wrapping at 88px with it). */}
                          <TableCell className="pl-0 text-right tabular-nums text-metadata">
                            {subnet.host_count != null ? subnet.host_count.toLocaleString() : '—'}
                            {/* How far collection has got in this subnet: the
                                hosts no vulnerability scan has covered, opening
                                exactly them (the subnet filter + Evidence's gap). */}
                            {(subnet.not_vuln_assessed_count ?? 0) > 0 && (
                              <Link
                                to={`/hosts?subnets=${encodeURIComponent(subnet.cidr)}&q=${encodeURIComponent('gap:vuln_assessment')}`}
                                title={`${subnet.not_vuln_assessed_count!.toLocaleString()} of ${(subnet.host_count ?? 0).toLocaleString()} hosts in ${subnet.cidr} have no vulnerability scan — show them`}
                                className="block whitespace-nowrap text-caption text-primary hover:underline focus:outline-none focus-visible:underline"
                              >
                                {subnet.not_vuln_assessed_count!.toLocaleString()} unscanned
                              </Link>
                            )}
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
                                ) : !canWrite ? (
                                  <span className="text-metadata text-muted-foreground" aria-label="No description">—</span>
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
                                ) : !canWrite ? (
                                  <span className="text-metadata text-muted-foreground" aria-label="No site">—</span>
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
                            <span className="block truncate" title={formatTimestamp(subnet.created_at)}>
                              {formatDate(subnet.created_at)}
                            </span>
                          </TableCell>
                          {canWrite && (
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
                                {/* Delete is in the row's menu, never an icon
                                    beside Edit (style guide §47). */}
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      aria-label={`Actions for ${subnet.cidr}`}
                                    >
                                      <MoreHorizontal className="size-4" aria-hidden />
                                    </Button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="end">
                                    <DropdownMenuItem
                                      className="text-destructive focus:text-destructive"
                                      // A tick later: the confirmation must not open while
                                      // the menu is still closing and handing focus back.
                                      onSelect={() => { setTimeout(() => { void handleDeleteSubnet(subnet.id, subnet.cidr); }, 0); }}
                                    >
                                      <Trash2 className="size-4" aria-hidden /> Delete entry…
                                    </DropdownMenuItem>
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              </div>
                            )}
                          </TableCell>
                          )}
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
            {/* The shared footer: "51–100 of 6,012 · page 2 of 121".  While
                another page loads the rows of the one before stay under the
                page that was asked for, so the range is cut at the total. */}
            {subnetsTotal > SUBNET_PAGE_SIZE && (
              <PagedFooter
                pager={{
                  page: subnetList.page, pageSize: SUBNET_PAGE_SIZE, total: subnetsTotal,
                  onPage: subnetList.setPage,
                }}
                shown={Math.max(0, Math.min(subnets.length, subnetsTotal - subnetList.page * SUBNET_PAGE_SIZE))}
                noun="subnets"
                busy={subnetList.loading}
              />
            )}
          </PostureSection>
        )}

        {/* v5.193.0 — domain scope alongside subnet scope.  Refreshes the
            coverage numbers on change (name-reachable hosts move between states). */}
        {subnets != null && (
          <ScopeDomainsCard canEdit={canWrite} />
        )}

        {coverage && coverage.out_of_scope_hosts > 0 && (
          <PostureSection
            title={<span>Hosts outside the scope</span>}
            description="Discovered at addresses no declared subnet or in-scope name covers. Confirm whether they are in scope before testing them; declare the subnet if they are."
            actions={(
              <Link to={buildHostsUrl({ outOfScopeOnly: true })} className="text-info hover:underline">
                View all out-of-scope hosts
              </Link>
            )}
          >
            <ul className="divide-y divide-border/60">
              {coverage.recent_out_of_scope_hosts.map((host) => {
                // The one absolute format ("Sep 25, 2026, 3:46 PM"), not a bare
                // toLocaleString ("9/25/2026, 3:46:06 PM").
                const lastSeen = formatTimestamp(host.last_seen, 'Unknown');
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
                      title={`last seen ${lastSeen}${scanLabel ? ` · ${scanLabel}` : ''}`}
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

      </div>

      {exportOpen && (
        <ScopeExport
          open
          onClose={() => setExportOpen(false)}
        />
      )}

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

      <SubnetLabelManagerDialog open={labelManagerOpen} onOpenChange={setLabelManagerOpen} />

      <SiteManagerDialog open={siteManagerOpen} onOpenChange={setSiteManagerOpen} />

      {confirmEl}
    </div>
  );
};

export default Scopes;
