import React, { useCallback, useMemo, useRef, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { Download, Loader2, Trash2, Upload } from 'lucide-react';

import {
  deleteName,
  getName,
  getNamesSummary,
  exportNames,
  importNames,
  listNames,
  NameAddress,
  NameDetail,
  NameImportRequest,
  NameImportResponse,
  NameRow,
  NamesSummary,
  NameStateFilter,
} from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRole } from '../hooks/useProjectRole';
import { useToast } from '../contexts/ToastContext';
import { invalidateReads, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { useConfirm } from '../hooks/useConfirm';
import { useListCursor } from '../hooks/useListCursor';
import { usePagedList } from '../hooks/usePagedList';
import { useUrlPage } from '../hooks/useUrlPage';
import { useUrlSearchDraft } from '../hooks/useUrlSearchDraft';
import { TableSkeleton } from '../components/PageSkeleton';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import { InfoTip } from '../components/ui/info-tip';
import { PostureSection, SectionCount } from '../components/posture/PostureSection';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { Label } from '../components/ui/label';
import {
  SideSheet,
  SideSheetBody,
  SideSheetContent,
  SideSheetDescription,
  SideSheetFooter,
  SideSheetHeader,
  SideSheetTitle,
} from '../components/ui/side-sheet';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Textarea } from '../components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { ListFilterBar, ListFilterSearch } from '../components/ListFilterBar';
import LastUpdated from '../components/LastUpdated';
import TimeAgo from '../components/TimeAgo';
import { cn } from '../utils/cn';
import { formatTimestamp } from '../utils/relativeTime';

/**
 * Names — the FQDN inventory (v5.193.0).
 *
 * A name is an identity; a host is an address.  This page lists every name
 * the engagement knows about, whether or not anything resolves it yet, and
 * shows what the uploaded evidence says: the address batch it currently
 * resolves to, the addresses it used to, and every other name sharing those
 * addresses.  Nothing on this page is resolved live — BlueStick never
 * originates a DNS query; the operator uploads dnsx / amass / httpx output.
 *
 * Every count in the header is a filter: click it to see the rows behind it.
 */

const PAGE_SIZE = 100;
const NO_ROWS: NameRow[] = [];

const STATE_OPTIONS: Array<{ value: NameStateFilter; label: string; hint: string }> = [
  { value: 'all', label: 'All', hint: 'Every name in the project' },
  { value: 'unresolved', label: 'Unresolved', hint: 'No A/AAAA observation yet — imported or discovered only' },
  { value: 'resolved', label: 'Resolved', hint: 'At least one A/AAAA observation' },
  { value: 'in_scope', label: 'In scope', hint: 'Covered by a domain declared in scope' },
  { value: 'out_of_scope', label: 'Out of scope', hint: 'No scope domain covers this name' },
  { value: 'shared', label: 'Shared address', hint: 'Names that resolve to an address another name also resolves to' },
  // v5.288.0 — "Wildcard" on the chip AND on the row tag (was "Wildcards" /
  // "pattern": two words for one thing).
  { value: 'wildcard', label: 'Wildcard', hint: 'Wildcard names (*.example.com) from certificates or enumeration' },
];

// Evidence kinds a reader recognises at a glance; anything else falls back to
// the raw record type.
const EVIDENCE_LABEL: Record<string, string> = {
  IMPORT: 'imported',
  DISCOVERED: 'discovered',
  SCANNER: 'scanner',
  HTTP: 'http',
  CERT: 'cert',
  TESTED: 'tested',
};
const EVIDENCE_ORDER = ['A', 'AAAA', 'CNAME', 'PTR', 'MX', 'NS', 'TXT', 'SRV', 'IMPORT', 'DISCOVERED', 'SCANNER', 'HTTP', 'CERT', 'TESTED'];

// v5.288.0 — what each evidence chip means (the chips were unexplained).
// Every kind is an observation from an upload or a recorded test; BlueStick
// never resolves a name itself.
const EVIDENCE_MEANING: Record<string, string> = {
  A: 'DNS A record (IPv4 address) in uploaded resolver output',
  AAAA: 'DNS AAAA record (IPv6 address) in uploaded resolver output',
  CNAME: 'DNS CNAME record (alias) in uploaded resolver output',
  PTR: 'Reverse DNS (PTR): an address whose reverse lookup gave this name',
  MX: 'DNS MX record (mail server) in uploaded resolver output',
  NS: 'DNS NS record (name server) in uploaded resolver output',
  TXT: 'DNS TXT record in uploaded resolver output',
  SRV: 'DNS SRV record (service location) in uploaded resolver output',
  IMPORT: 'An operator imported this name (a pasted or uploaded list)',
  DISCOVERED: 'An enumeration tool (amass, subfinder…) found the name, without an address',
  SCANNER: 'A scanner (Nmap, Nessus…) reported this name for an address',
  HTTP: 'The name was contacted over HTTP at an address (httpx…)',
  CERT: 'A TLS certificate presented at an address carried this name',
  TESTED: 'A recorded test command ran against this name at an address',
};

const evidenceChipTitle = (kind: string, count: number): string => {
  const meaning = EVIDENCE_MEANING[kind] ?? `${kind} observation`;
  return `${meaning} — ${count.toLocaleString()} observation${count === 1 ? '' : 's'} recorded`;
};

const fmtTime = (iso?: string | null): string => formatTimestamp(iso);

/** "Last seen" in a table cell (UX review 2026-09-24): how long ago, the
 *  exact moment on hover — the list convention (TimeAgo). It was a date and a
 *  time on two lines, one of five date formats across the pages. */
const LastSeen: React.FC<{ iso?: string | null }> = ({ iso }) => (
  <TimeAgo value={iso} absoluteAfterDays={30} />
);

/** A single-label name (dc01, file01) — reported by a scanner or SMB, not a
 *  fully-qualified domain name. */
const isShortName = (row: Pick<NameRow, 'fqdn' | 'kind'>) => row.kind !== 'wildcard' && !row.fqdn.includes('.');

// Addresses shown per row before "+N more" (all of them are in the title and
// the detail sheet); an address itself is never truncated.
const ADDRESSES_SHOWN = 3;
/** Refused scope entries listed after an import; the rest are counted. */
const SCOPE_REFUSED_SHOWN = 10;

const sortEvidence = (evidence: Record<string, number>): Array<[string, number]> =>
  Object.entries(evidence).sort(([a], [b]) => {
    const ia = EVIDENCE_ORDER.indexOf(a);
    const ib = EVIDENCE_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
  });

const EvidenceChips: React.FC<{ evidence: Record<string, number>; max?: number }> = ({ evidence, max = 5 }) => {
  const entries = sortEvidence(evidence);
  const shown = entries.slice(0, max);
  const rest = entries.length - shown.length;
  return (
    <span className="flex flex-wrap gap-2xs">
      {shown.map(([kind, count]) => (
        <Badge key={kind} variant="outline" title={evidenceChipTitle(kind, count)}>
          {EVIDENCE_LABEL[kind] ?? kind}
          {count > 1 && <span className="ml-2xs tabular-nums text-muted-foreground">{count}</span>}
        </Badge>
      ))}
      {rest > 0 && (
        <Badge
          variant="muted"
          title={`${rest} more kind${rest === 1 ? '' : 's'}: ${entries
            .slice(max)
            .map(([k, c]) => `${EVIDENCE_LABEL[k] ?? k} ${c}`)
            .join(', ')}`}
        >
          +{rest}
        </Badge>
      )}
    </span>
  );
};

/** The legend behind the section's (i): every evidence kind in one place. */
const EvidenceLegend: React.FC = () => (
  <span className="flex flex-col gap-2xs">
    <span>Each chip is a kind of observation; the number counts how many were recorded (shown when more than one).</span>
    {EVIDENCE_ORDER.filter((k) => EVIDENCE_MEANING[k]).map((k) => (
      <span key={k}>
        <span className="font-medium">{EVIDENCE_LABEL[k] ?? k}</span> — {EVIDENCE_MEANING[k]}
      </span>
    ))}
    <span>
      <span className="font-medium">+N previous</span> — addresses the name resolved to before its latest resolution.{' '}
      <span className="font-medium">shared</span> — another name also resolves to this address.
    </span>
  </span>
);

const AddressLine: React.FC<{ a: NameAddress }> = ({ a }) => (
  <li className="flex flex-wrap items-center gap-xs py-2xs">
    <span className="font-mono text-metadata">{a.ip_address}</span>
    {a.host_id != null ? (
      <Link to={`/hosts/${a.host_id}`} className="text-metadata text-primary hover:underline">
        open host
      </Link>
    ) : (
      <span className="text-caption text-muted-foreground" title="No scan has observed this address; no host row is invented.">
        not in host inventory
      </span>
    )}
    {a.shared_with > 0 && (
      <Badge variant="info-outline" title="Other names observed resolving to this address">
        +{a.shared_with} other name{a.shared_with === 1 ? '' : 's'}
      </Badge>
    )}
    <span className="ml-auto text-caption text-muted-foreground" title="First / last observed">
      {fmtTime(a.first_observed)} → {fmtTime(a.last_observed)}
    </span>
  </li>
);

// ---------------------------------------------------------------------------
// Import dialog
// ---------------------------------------------------------------------------
interface ImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The reads a change to the names inventory makes out of date. */
const NAME_READS = ['listNames', 'getNamesSummary'] as const;

const ImportDialog: React.FC<ImportDialogProps> = ({ open, onOpenChange }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  const [text, setText] = useState('');
  const [declareScope, setDeclareScope] = useState(false);
  const [includeSub, setIncludeSub] = useState(false);
  // A file that was not read; an import that failed is the mutation's.
  const [fileError, setFileError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const names = useMemo(
    () => text.split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith('#')),
    [text],
  );

  const importing = useMutation({
    mutationFn: (body: NameImportRequest) => importNames(projectId, body),
    onMutate: () => setFileError(null),
    onSuccess: (res) => {
      const parts = [`${res.names_created} new`, `${res.names_existing} already known`];
      if (res.invalid_count) parts.push(`${res.invalid_count} rejected`);
      if (res.scope_domains_added) parts.push(`${res.scope_domains_added} added to scope`);
      if (res.scope_domains_updated) parts.push(`${res.scope_domains_updated} widened to include subdomains`);
      if (res.scope_invalid?.length) parts.push(`${res.scope_invalid.length} not added to scope`);
      toast.success(`Imported: ${parts.join(', ')}`);
      void invalidateReads(queryClient, ...NAME_READS);
    },
  });
  const busy = importing.isPending;
  const result: NameImportResponse | null = importing.data ?? null;
  // Entries the import refused for the scope list.
  const scopeRefused = result?.scope_invalid ?? [];
  const error = fileError ?? queryErrorText(importing.error, 'Import failed.');

  const reset = () => {
    setText('');
    setDeclareScope(false);
    setIncludeSub(false);
    setFileError(null);
    importing.reset();
  };

  const readFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      setFileError('File is larger than 5 MB. Split it or paste a subset.');
      return;
    }
    const content = await file.text();
    setText((prev) => (prev.trim() ? `${prev.trimEnd()}\n${content}` : content));
    if (fileRef.current) fileRef.current.value = '';
  };

  const submit = () => {
    if (names.length === 0) return;
    importing.mutate({ names, declare_scope: declareScope, include_subdomains: includeSub });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import names</DialogTitle>
          <DialogDescription>
            One FQDN per line. URLs and host:port are tolerated; IP addresses are rejected — an address is a host,
            not a name. Nothing is resolved and no hosts are created. Re-importing the same list changes nothing.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-sm">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={10}
            placeholder={'portal.example.com\napi.example.com\n*.lab.example.com'}
            className="font-mono text-metadata"
            aria-label="Names to import"
          />
          <div className="flex flex-wrap items-center gap-sm">
            <Label htmlFor="names-file" className="cursor-pointer text-metadata text-primary hover:underline">
              Append from file…
            </Label>
            <input
              id="names-file"
              ref={fileRef}
              type="file"
              accept=".txt,.csv,.lst,text/plain"
              className="sr-only"
              onChange={(e) => readFile(e.target.files?.[0])}
            />
            <span className="text-caption text-muted-foreground">
              {names.length.toLocaleString()} name{names.length === 1 ? '' : 's'} ready
            </span>
          </div>
          <div className="rounded-md border border-border bg-accent/30 p-sm">
            <label className="flex items-start gap-xs text-metadata">
              <Checkbox checked={declareScope} onCheckedChange={(v) => setDeclareScope(v === true)} className="mt-2xs" />
              <span>
                <span className="font-medium">Also declare these names in scope</span>
                <span className="block text-caption text-muted-foreground">
                  A separate decision from importing. Name scope never makes the addresses they resolve to
                  subnet-in-scope.
                </span>
              </span>
            </label>
            <label className="mt-xs flex items-center gap-xs pl-lg text-metadata">
              <Checkbox
                checked={includeSub}
                disabled={!declareScope}
                onCheckedChange={(v) => setIncludeSub(v === true)}
              />
              Include subdomains of each name
            </label>
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {result && (
            <Alert>
              <AlertDescription>
                <div className="flex flex-wrap gap-xs">
                  <Badge variant="success">{result.names_created} new</Badge>
                  <Badge variant="outline">{result.names_existing} already known</Badge>
                  {result.wildcards > 0 && <Badge variant="muted">{result.wildcards} wildcard</Badge>}
                  {result.invalid_count > 0 && <Badge variant="warning">{result.invalid_count} rejected</Badge>}
                  {result.scope_domains_added > 0 && (
                    <Badge variant="info">{result.scope_domains_added} added to scope</Badge>
                  )}
                  {result.scope_domains_updated > 0 && (
                    <Badge variant="info" title="Scope entries that already existed and now also cover their subdomains">
                      {result.scope_domains_updated} widened to include subdomains
                    </Badge>
                  )}
                  {scopeRefused.length > 0 && (
                    <Badge variant="warning">{scopeRefused.length} not added to scope</Badge>
                  )}
                </div>
                {scopeRefused.length > 0 && (
                  <div className="mt-xs min-w-0 text-caption text-muted-foreground">
                    <p>Not added to scope:</p>
                    <ul className="font-mono">
                      {scopeRefused.slice(0, SCOPE_REFUSED_SHOWN).map((line) => (
                        <li key={line} className="truncate" title={line}>{line}</li>
                      ))}
                    </ul>
                    {scopeRefused.length > SCOPE_REFUSED_SHOWN && (
                      <p>+{(scopeRefused.length - SCOPE_REFUSED_SHOWN).toLocaleString()} more</p>
                    )}
                  </div>
                )}
                {result.invalid.length > 0 && (
                  <ul className="mt-xs max-h-40 overflow-y-auto font-mono text-caption text-muted-foreground">
                    {result.invalid.map((line) => (
                      <li key={line} className="truncate" title={line}>
                        {line}
                      </li>
                    ))}
                  </ul>
                )}
              </AlertDescription>
            </Alert>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {result ? 'Close' : 'Cancel'}
          </Button>
          <Button onClick={submit} disabled={busy || names.length === 0}>
            {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Upload className="size-4" aria-hidden />}
            Import {names.length > 0 ? names.length.toLocaleString() : ''}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

// ---------------------------------------------------------------------------
// Detail sheet
// ---------------------------------------------------------------------------
interface DetailSheetProps {
  nameId: number | null;
  onClose: () => void;
  onNavigate: (nameId: number) => void;
  canEdit: boolean;
}

const DetailSheet: React.FC<DetailSheetProps> = ({ nameId, onClose, onNavigate, canEdit }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [confirmDialog, confirm] = useConfirm();
  const projectId = useProjectId();

  const query = useQuery({
    queryKey: ['getName', projectId, nameId],
    queryFn: ({ signal }) => getName(projectId, nameId as number, signal),
    enabled: nameId != null,
    // Only for the sheet that is closing (no name): it keeps the name it
    // showed while it slides out.  Another name starts from nothing.
    placeholderData: keepPreviousData,
  });
  const detail: NameDetail | null = (nameId != null && query.isPlaceholderData ? null : query.data) ?? null;
  const loading = query.isFetching;
  const error = nameId != null ? queryErrorText(query.error, 'Name could not be loaded.') : null;

  const remove = useMutation({
    mutationFn: (name: NameDetail) => deleteName(projectId, name.id),
    onSuccess: (_void, name) => {
      toast.success(`Deleted ${name.fqdn}`);
      void invalidateReads(queryClient, ...NAME_READS);
      onClose();
    },
    onError: (err) => toast.error(formatApiError(err, 'Delete failed.')),
  });
  const deleting = remove.isPending;

  const handleDelete = async () => {
    if (!detail) return;
    const ok = await confirm({
      title: 'Delete name',
      body: `${detail.fqdn} and its ${detail.observations_total.toLocaleString()} observation${detail.observations_total === 1 ? '' : 's'} will be removed. Hosts are never deleted with a name.`,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    remove.mutate(detail);
  };

  return (
    <SideSheet open={nameId != null} onOpenChange={(o) => !o && onClose()}>
      <SideSheetContent width="xl">
        {confirmDialog}
        <SideSheetHeader>
          <SideSheetTitle className="break-all font-mono">{detail?.fqdn ?? (loading ? 'Loading…' : 'Name')}</SideSheetTitle>
          <SideSheetDescription asChild>
            <div className="flex flex-wrap items-center gap-xs">
              {detail?.kind === 'wildcard' && <Badge variant="muted">wildcard</Badge>}
              {detail && (
                <Badge variant={detail.in_scope ? 'success-outline' : 'outline'}>
                  {detail.in_scope ? 'in scope' : 'not in scope'}
                </Badge>
              )}
              {detail?.imported && <Badge variant="outline">imported</Badge>}
              {detail && (
                <span className="text-caption text-muted-foreground">
                  first seen {fmtTime(detail.first_seen)} · last seen {fmtTime(detail.last_seen)}
                </span>
              )}
            </div>
          </SideSheetDescription>
        </SideSheetHeader>
        <SideSheetBody className="flex flex-col gap-md">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {loading && (
            <div className="flex items-center gap-xs text-metadata text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Loading…
            </div>
          )}
          {detail && (
            <>
              <section>
                <h3 className="mb-2xs text-caption font-medium uppercase tracking-wide text-muted-foreground">
                  Currently resolves to
                </h3>
                {detail.current_addresses.length === 0 ? (
                  <p className="text-metadata text-muted-foreground">
                    No A/AAAA observation. Upload dnsx or amass output that resolves this name to bind it to an
                    address.
                  </p>
                ) : (
                  <ul className="divide-y divide-border">
                    {detail.current_addresses.map((a) => (
                      <AddressLine key={a.ip_address} a={a} />
                    ))}
                  </ul>
                )}
              </section>

              {detail.previous_addresses.length > 0 && (
                <section>
                  <h3 className="mb-2xs text-caption font-medium uppercase tracking-wide text-muted-foreground">
                    Previously observed at
                  </h3>
                  <ul className="divide-y divide-border">
                    {detail.previous_addresses.map((a) => (
                      <AddressLine key={a.ip_address} a={a} />
                    ))}
                  </ul>
                </section>
              )}

              {detail.sibling_names.length > 0 && (
                <section>
                  <h3 className="mb-2xs text-caption font-medium uppercase tracking-wide text-muted-foreground">
                    Other names at the same address{detail.current_addresses.length === 1 ? '' : 'es'}
                  </h3>
                  <ul className="flex flex-wrap gap-2xs">
                    {detail.sibling_names.map((s) => (
                      <li key={`${s.id}-${s.ip_address}`}>
                        <button
                          type="button"
                          onClick={() => onNavigate(s.id)}
                          className="max-w-[28rem] truncate rounded-chip border border-border px-xs py-2xs font-mono text-caption hover:bg-accent"
                          title={`${s.fqdn} · ${s.ip_address}`}
                        >
                          {s.fqdn}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section>
                <h3 className="mb-2xs text-caption font-medium uppercase tracking-wide text-muted-foreground">
                  Evidence
                  <span className="ml-xs normal-case tracking-normal">
                    {detail.observations.length < detail.observations_total
                      ? `latest ${detail.observations.length} of ${detail.observations_total.toLocaleString()}`
                      : detail.observations_total.toLocaleString()}
                  </span>
                </h3>
                <div className="overflow-x-auto">
                  <Table style={{ tableLayout: 'fixed' }} className="min-w-[640px]">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[14%]">Kind</TableHead>
                        <TableHead className="w-[30%]">Value</TableHead>
                        <TableHead className="w-[16%]">Resolver</TableHead>
                        <TableHead className="w-[20%]">Source</TableHead>
                        <TableHead className="w-[20%]">Observed</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {detail.observations.map((o) => (
                        <TableRow key={o.id}>
                          <TableCell>
                            <Badge variant="outline">{EVIDENCE_LABEL[o.record_type] ?? o.record_type}</Badge>
                          </TableCell>
                          <TableCell className="truncate font-mono text-metadata" title={o.value}>
                            {o.host_id != null ? (
                              <Link to={`/hosts/${o.host_id}`} className="text-primary hover:underline">
                                {o.value}
                              </Link>
                            ) : (
                              o.value
                            )}
                          </TableCell>
                          <TableCell className="truncate text-metadata text-muted-foreground" title={o.resolver_name ?? ''}>
                            {o.resolver_name ?? '—'}
                          </TableCell>
                          <TableCell
                            className="truncate text-metadata text-muted-foreground"
                            title={o.scan_filename ?? ''}
                          >
                            {o.scan_id != null ? (
                              <Link to={`/scans/${o.scan_id}`} className="hover:underline">
                                {o.scan_tool ?? 'scan'} #{o.scan_id}
                              </Link>
                            ) : (
                              'operator import'
                            )}
                          </TableCell>
                          <TableCell className="truncate text-metadata text-muted-foreground">
                            {fmtTime(o.observed_at)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </section>
            </>
          )}
        </SideSheetBody>
        {detail && canEdit && (
          <SideSheetFooter>
            <Button variant="outline" onClick={handleDelete} disabled={deleting}>
              <Trash2 className="size-4" aria-hidden /> Delete name
            </Button>
          </SideSheetFooter>
        )}
      </SideSheetContent>
    </SideSheet>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
const Names: React.FC = () => {
  const toast = useToast();
  // The PROJECT role (R32).  Data egress — the server gates at AUDITOR+ (same
  // policy as the Hosts tool-ready export); this only hides the affordance.
  const { canWrite: canEdit, canExport } = useProjectRole();
  const projectId = useProjectId();
  const [searchParams, setSearchParams] = useSearchParams();

  // The filters are the address's (UI_STYLE_GUIDE §39): read from it on every
  // render, so a link to this page with other filters, and Back, are what the
  // chips show and what the list is asked for.  `all` is left out of it.
  const state: NameStateFilter = (searchParams.get('state') as NameStateFilter) || 'all';
  const setState = useCallback((next: NameStateFilter) => {
    setSearchParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next === 'all') out.delete('state');
      else out.set('state', next);
      out.delete('page');
      return out;
    }, { replace: true });
  }, [setSearchParams]);
  // The search box: `draft` is what is being typed, `value` what is in the
  // address — and so what is asked for.
  const search = useUrlSearchDraft('search');
  const searchValue = search.value;
  const [importOpen, setImportOpen] = useState(false);

  // The page is in the address (`?page=`, left out for the first); under a
  // new filter it is the first page at once (usePagedList).
  const list = usePagedList<NameRow>(
    'listNames',
    ({ offset, limit, signal }) => listNames(projectId, { skip: offset, limit, search: searchValue, state }, signal),
    [projectId, state, searchValue],
    { pageSize: PAGE_SIZE, errorMessage: 'Failed to load names.', page: useUrlPage() },
  );
  const { page, setPage, loading, error } = list;
  // The rows on screen stay while another page or filter loads, and when it
  // fails: the last list that answered — and "updated" is when THOSE rows
  // were read.
  const shown = list.response ?? list.lastResponse;
  const rows = shown?.items ?? NO_ROWS;
  const total = shown?.total ?? 0;
  const lastLoadedAt = useRef<Date | null>(null);
  if (list.loadedAt) lastLoadedAt.current = list.loadedAt;
  const loadedAt = list.loadedAt ?? lastLoadedAt.current;

  const selectedId = useMemo(() => {
    const raw = searchParams.get('name_id');
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [searchParams]);

  const setSelectedId = useCallback(
    (id: number | null) => {
      const next = new URLSearchParams(searchParams);
      if (id == null) next.delete('name_id');
      else next.set('name_id', String(id));
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  // A failed summary is said (R34): the chips used to lose their counts with
  // nothing to tell "no counts" from "could not be loaded".
  const summaryQuery = useQuery({
    queryKey: ['getNamesSummary', projectId],
    queryFn: ({ signal }) => getNamesSummary(projectId, signal),
  });
  const summaryError = queryErrorText(summaryQuery.error, 'The counts could not be loaded.');
  // Counts that could not be re-read are not shown as if they had been.
  const summary: NamesSummary | null = summaryError ? null : summaryQuery.data ?? null;
  const reloadSummary = () => { void summaryQuery.refetch(); };

  const refreshAll = () => {
    void list.reload();
    reloadSummary();
  };

  const exportList = useMutation({
    mutationFn: (format: 'txt' | 'csv') =>
      exportNames(projectId, format, { search: searchValue || undefined, state }),
    onError: (err) => toast.error(formatApiError(err, 'Failed to export names.')),
  });
  const exporting = exportList.isPending ? exportList.variables : null;
  const handleExport = (format: 'txt' | 'csv') => exportList.mutate(format);

  const countFor = (value: NameStateFilter): number | null => {
    if (!summary) return null;
    switch (value) {
      case 'all':
        return summary.total;
      case 'unresolved':
        return summary.unresolved;
      case 'resolved':
        return summary.resolved;
      case 'in_scope':
        return summary.in_scope;
      case 'out_of_scope':
        return summary.total - summary.in_scope;
      case 'wildcard':
        return summary.wildcards;
      case 'shared':
        return summary.shared_names ?? null;
      default:
        return null;
    }
  };

  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);
  const multiPage = total > PAGE_SIZE;
  const activeLabel = state === 'all' ? 'All names' : STATE_OPTIONS.find((o) => o.value === state)?.label ?? 'Names';

  // j/k (↓/↑) move a row cursor, Enter opens the name — as on Hosts.
  const { cursorRowProps } = useListCursor(
    loading || error ? 0 : rows.length,
    (i) => setSelectedId(rows[i].id),
    { resetKey: `${page}|${state}|${searchValue}`, getId: (i) => rows[i]?.id },
  );

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Names</h1>
          <p className="text-metadata text-muted-foreground">
            Domain names and short host names, resolved or not. Addresses are what uploaded evidence
            says — BlueStick never resolves anything itself.
          </p>
        </div>
        {canEdit && (
          <Button onClick={() => setImportOpen(true)}>
            <Upload className="size-4" aria-hidden /> Import names
          </Button>
        )}
        {canExport && (
          <div className="flex items-center gap-xxs" role="group" aria-label="Export the filtered names">
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleExport('txt')}
              disabled={exporting !== null || total === 0}
              aria-label="Export names as text"
            >
              {exporting === 'txt' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Download className="size-4" aria-hidden />}
              Export .txt
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleExport('csv')}
              disabled={exporting !== null || total === 0}
              aria-label="Export names as CSV"
            >
              {exporting === 'csv' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Download className="size-4" aria-hidden />}
              Export .csv
            </Button>
          </div>
        )}
        <LastUpdated compact lastFetched={loadedAt} onRefresh={refreshAll} isLoading={loading} label="names" />
      </div>

      {/* UX review 2026-09-24 — one filter row (ListFilterBar): the search,
          then the state counts as the same sentence-case count chips as
          Ingestion Results (they were upper-case badges on a row of their own
          above the search). */}
      <ListFilterBar className="mb-md">
        <ListFilterSearch value={search.draft} onChange={search.setDraft} placeholder="Search names…" label="Search names" />
        <div className="flex min-w-0 flex-wrap items-center gap-xs" role="group" aria-label="Name state filter">
          {STATE_OPTIONS.map((opt) => {
            const active = state === opt.value;
            const count = countFor(opt.value);
            return (
              <Tooltip key={opt.value}>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={() => setState(opt.value)}
                    className={cn(
                      'inline-flex items-center gap-xs whitespace-nowrap rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      active
                        ? 'border-primary bg-primary/10 text-foreground'
                        : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
                    )}
                  >
                    <span>{opt.label}</span>
                    {count != null && <strong className="tabular-nums text-foreground">{count.toLocaleString()}</strong>}
                  </button>
                </TooltipTrigger>
                <TooltipContent>{opt.hint}</TooltipContent>
              </Tooltip>
            );
          })}
          {summaryError && (
            <span role="status" className="inline-flex min-w-0 items-center gap-xs text-caption text-muted-foreground">
              <span className="min-w-0 break-words">{summaryError}</span>
              <Button variant="link" size="sm" className="h-auto p-0" onClick={reloadSummary}>Retry</Button>
            </span>
          )}
        </div>
      </ListFilterBar>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* v5.288.0 — a section over a thin rule, not a bordered card (§7); the
          pager shows only when the list spans more than one page. */}
      <PostureSection
        title={
          <>
            {activeLabel}
            {total > 0 && (
              <SectionCount>
                {multiPage
                  ? `${from.toLocaleString()}–${to.toLocaleString()} of ${total.toLocaleString()}`
                  : total.toLocaleString()}
              </SectionCount>
            )}
          </>
        }
        description={
          <span className="inline-flex flex-wrap items-center gap-x-xs">
            Evidence chips name the kind of observation; the number beside one counts how many were recorded.
            <InfoTip label="About the evidence chips" text={<EvidenceLegend />} />
          </span>
        }
        actions={
          multiPage ? (
            <span className="flex gap-xs" role="group" aria-label="Pages">
              <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <Button size="sm" variant="outline" disabled={to >= total} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </span>
          ) : undefined
        }
      >
          {loading && rows.length === 0 ? (
            <div>
              <TableSkeleton rows={8} />
            </div>
          ) : rows.length === 0 ? (
            <div className="py-md text-metadata text-muted-foreground">
              {searchValue || state !== 'all'
                ? 'No names match the current filter.'
                : 'No names yet. Import a list, or upload dnsx / amass / subfinder / httpx output.'}
            </div>
          ) : (
            <div className="overflow-x-auto">
              {/* UX review 2026-09-24 — 960px was 44px wider than the content
                  column at a 1246px window (Last seen cut off), and "Out of
                  scope" wrapped in a 10% column. */}
              <Table style={{ tableLayout: 'fixed' }} className="min-w-[820px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[27%]">Name</TableHead>
                    <TableHead className="w-[13%]">Scope</TableHead>
                    <TableHead className="w-[26%]">Resolves to</TableHead>
                    <TableHead className="w-[22%]">Evidence</TableHead>
                    <TableHead className="w-[12%]">Last seen</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row, i) => (
                    <TableRow
                      key={row.id}
                      {...cursorRowProps(i, 'cursor-pointer hover:bg-accent/40')}
                      onClick={() => setSelectedId(row.id)}
                      aria-selected={selectedId === row.id}
                    >
                      <TableCell className="align-top font-mono">
                        {/* The row's keyboard path (data-table convention): a
                            real button in the primary cell.  A name is an
                            identifier: it wraps, never truncates. */}
                        <button
                          type="button"
                          className="max-w-full break-all rounded text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          onClick={(e) => { e.stopPropagation(); setSelectedId(row.id); }}
                        >
                          {row.fqdn}
                        </button>
                        {row.kind === 'wildcard' && (
                          <Badge variant="muted" className="ml-xs">
                            wildcard
                          </Badge>
                        )}
                        {isShortName(row) && (
                          <span
                            className="ml-xs font-sans text-caption text-muted-foreground"
                            title="A single-label name (no domain), as a scanner or SMB reported it — not a fully-qualified domain name"
                          >
                            short name
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="align-top">
                        {row.in_scope ? (
                          <Badge variant="success-outline">In scope</Badge>
                        ) : (
                          <span className="block truncate whitespace-nowrap text-metadata text-muted-foreground" title="Out of scope">Out of scope</span>
                        )}
                      </TableCell>
                      <TableCell className="align-top font-mono text-metadata">
                        {row.current_addresses.length === 0 ? (
                          <span className="font-sans text-muted-foreground">unresolved</span>
                        ) : (
                          // v5.288.0 — an address is never truncated: one per
                          // line (a long IPv6 wraps), the chips on their own
                          // line below instead of squeezing it.
                          <div
                            className="flex min-w-0 flex-col gap-2xs"
                            title={row.current_addresses.map((a) => a.ip_address).join(', ')}
                          >
                            <ul className="min-w-0">
                              {row.current_addresses.slice(0, ADDRESSES_SHOWN).map((a) => (
                                <li key={a.ip_address} className="break-all">
                                  {a.ip_address}
                                </li>
                              ))}
                              {row.current_addresses.length > ADDRESSES_SHOWN && (
                                <li className="font-sans text-caption text-muted-foreground">
                                  +{row.current_addresses.length - ADDRESSES_SHOWN} more
                                </li>
                              )}
                            </ul>
                            {(row.previous_address_count > 0 || row.current_addresses.some((a) => a.shared_with > 0)) && (
                              <span className="flex flex-wrap gap-2xs font-sans">
                                {row.previous_address_count > 0 && (
                                  <Badge
                                    variant="warning-outline"
                                    title={`This name previously resolved to ${row.previous_address_count} other address${row.previous_address_count === 1 ? '' : 'es'} — open the name to see them`}
                                  >
                                    +{row.previous_address_count} previous
                                  </Badge>
                                )}
                                {row.current_addresses.some((a) => a.shared_with > 0) && (
                                  <Badge
                                    variant="info-outline"
                                    title="Another name also resolves to this address (a load balancer or virtual host) — open the name to see which"
                                  >
                                    shared
                                  </Badge>
                                )}
                              </span>
                            )}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="align-top">
                        <EvidenceChips evidence={row.evidence} />
                      </TableCell>
                      <TableCell className="align-top text-metadata text-muted-foreground">
                        <LastSeen iso={row.last_seen} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
      </PostureSection>

      <ImportDialog open={importOpen} onOpenChange={setImportOpen} />
      <DetailSheet
        nameId={selectedId}
        onClose={() => setSelectedId(null)}
        onNavigate={(id) => setSelectedId(id)}
        canEdit={canEdit}
      />
    </div>
  );
};

export default Names;
