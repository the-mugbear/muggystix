/**
 * /reports (v5.261.0) — the project's client reports: drafts, the issued
 * history, and the report defaults every draft follows unless it overrides a detail.
 *
 * A report is a draft until a project admin issues it; issuing freezes what
 * it says and numbers it.  An addendum reports only what changed since an
 * issued report (new findings, findings on further systems, withdrawals).
 * Posture layout: a lead sentence, then sections over thin rules.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isClientTemplate } from '../utils/reportTemplates';
import { Link, useNavigate } from 'react-router-dom';
import { FilePlus2, Loader2, Pencil, RefreshCw, Trash2 } from 'lucide-react';

import {
  ClientReport,
  ClientReportKind,
  ClientReportList,
  ReportProfile,
  ReportTemplate,
  ReportTemplateProblem,
  createClientReport,
  deleteClientReport,
  downloadClientReportFile,
  getReportProfile,
  listClientReports,
  listReportTemplateProblems,
  listReportTemplates,
  saveReportProfile,
  updateClientReport,
} from '../services/api';
import { formatDate, formatTimestamp } from '../utils/relativeTime';
import TimeAgo from '../components/TimeAgo';
import { Input } from '../components/ui/input';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRoster } from '../hooks/useProjectMembers';
import { pollEvery, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import PostureLead from '../components/posture/PostureLead';
import DownloadButton from '../components/DownloadButton';
import EngagementSettingsFields, {
  DETAIL_LABELS, DETAIL_NAME, cleanSettings, isOverridden,
} from '../components/reports/EngagementSettingsFields';
import TemplateImages, { assetCountLabel, missingRequiredAssets } from '../components/reports/TemplateImages';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Label } from '../components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';
import { referencedImageIds } from '../utils/reportImages';
import { safeFallback } from '../utils/uiStyles';

const day = (iso: string | null) => formatDate(iso);

/** "started 23 Sep 2026, 14:05 · overrides 1 detail: classification ·
 *  template Executive brief" — what sets apart drafts that share a title
 *  (v5.288.0). The time is there because two drafts are often started the
 *  same day; the template only when it is not the project's default, and by
 *  its title (walkthrough 2026-10-14: every row said "template pentest"). */
export const draftMeta = (r: Pick<ClientReport, 'created_at' | 'settings'>, otherTemplate: string | null): string => {
  const started = formatTimestamp(r.created_at, null);
  // The draft's number is the row's first cell; it is not said again here.
  return [started ? `started ${started}` : null, overridesLine(r), otherTemplate ? `template ${otherTemplate}` : null]
    .filter(Boolean)
    .join(' · ');
};

/** The title of a draft's template when it is not the project's default,
 *  else null — the list and the "Continue a draft?" dialog both say it. The
 *  reads are the defaults section's, shared. */
const useOtherTemplate = (enabled: boolean): ((r: Pick<ClientReport, 'template'>) => string | null) => {
  const projectId = useProjectId();
  const profile = useQuery({
    queryKey: ['getReportProfile', projectId], queryFn: ({ signal }) => getReportProfile(projectId, signal),
    enabled,
  });
  const templatesQuery = useQuery({
    queryKey: ['listReportTemplates', projectId], queryFn: ({ signal }) => listReportTemplates(projectId, signal),
    enabled,
  });
  const defaultTemplate = profile.data?.template ?? null;
  return (r) => (defaultTemplate && r.template !== defaultTemplate
    ? (templatesQuery.data?.find((t) => t.name === r.template)?.title ?? r.template) : null);
};

/**
 * A draft's title with an in-place rename (v5.294.0, UX review): drafts start
 * under the project's default title, so several read the same; naming one
 * used to mean opening it and saving the whole form.  A shared title is
 * flagged so the operator knows why the line under it matters.
 */
const DraftTitle: React.FC<{
  report: ClientReport;
  duplicate: boolean;
}> = ({ report, duplicate }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  const [value, setValue] = useState<string | null>(null);
  const renaming = useMutation({
    mutationFn: (title: string) => updateClientReport(projectId, report.id, { title }),
    onSuccess: (updated) => {
      // The new title goes where the list is read; the row keeps its place.
      queryClient.setQueryData<ClientReportList>(['listClientReports', projectId], (d) => (d
        ? { ...d, items: d.items.map((x) => (x.id === updated.id ? { ...x, title: updated.title } : x)) }
        : d));
      setValue(null);
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not rename the draft.')),
  });
  const saving = renaming.isPending;

  const save = () => {
    const title = (value ?? '').trim();
    if (!title) { toast.error('A report needs a title.'); return; }
    if (title === report.title) { setValue(null); return; }
    renaming.mutate(title);
  };

  if (value !== null) {
    return (
      <form className="flex min-w-0 items-center gap-xs" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <Input
          autoFocus value={value} maxLength={255} disabled={saving}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') setValue(null); }}
          aria-label={`New title for draft #${report.id}`}
          className="h-7 min-w-0 flex-1 text-metadata"
        />
        <Button type="submit" size="sm" className="h-7" disabled={saving}>
          {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Save
        </Button>
        <Button type="button" variant="ghost" size="sm" className="h-7" onClick={() => setValue(null)} disabled={saving}>
          Cancel
        </Button>
      </form>
    );
  }
  return (
    <span className="flex min-w-0 items-center gap-xxs">
      <Link to={`/reports/${report.id}`} className="min-w-0 truncate text-info hover:underline" title={report.title}>
        {report.title}
      </Link>
      {duplicate && (
        <span className="shrink-0 text-caption text-muted-foreground" title="Another draft has this title">(same title)</span>
      )}
      {report.can_edit && (
        <Button variant="ghost" size="icon" className="size-6 shrink-0" onClick={() => setValue(report.title)}
          aria-label={`Rename draft #${report.id}`} title="Rename">
          <Pencil className="size-3.5" aria-hidden />
        </Button>
      )}
    </span>
  );
};

const written = (text: string | null | undefined): boolean => !!text?.trim();

/** One line each for the defaults' long texts — whether one is written, and
 *  what a report prints when it is not — for the read view, which shows no
 *  text of theirs.  Images are counted from the description's own references
 *  (`referencedImageIds`), so nothing is fetched to say it. */
export const profileWritten = (
  profile: Pick<ReportProfile, 'executive_summary' | 'system_description' | 'applications' | 'thick_clients' | 'other_targets' | 'disclaimer' | 'inherited'>,
): { summary: string; system: string; targets: string; disclaimer: string } => {
  const images = referencedImageIds(profile.system_description).length;
  const lists = ([
    ['applications', 'Applications'], ['thick_clients', 'Thick clients'], ['other_targets', 'Other'],
  ] as const).filter(([key]) => written(profile[key])).map(([, label]) => label);
  return {
    // Every full report prints it unless it overrides it (owner, 2026-10-14).
    summary: written(profile.executive_summary) ? 'Written — every full report prints it unless it overrides it' : 'Not written',
    system: !written(profile.system_description) ? 'Not written'
      : images ? `Written · ${images.toLocaleString()} image${images === 1 ? '' : 's'} placed` : 'Written',
    targets: lists.length ? `Written: ${lists.join(', ')}` : 'None written',
    disclaimer: written(profile.disclaimer) ? 'Written for this project'
      : written(profile.inherited?.disclaimer) ? 'The installation’s default'
        : 'The template’s own',
  };
};

/** An issued report's State cell: the newest issued report is "Latest", a
 *  revised one "Superseded", any other a dash. */
export const issuedState = (r: Pick<ClientReport, 'id' | 'status'>, latestId: number | null | undefined): React.ReactNode => {
  if (r.status === 'superseded') return <Badge variant="muted">Superseded</Badge>;
  if (r.id === latestId) return <Badge variant="success">Latest</Badge>;
  return <span className="text-muted-foreground">—</span>;
};

/** How often a page showing an issued report whose files are still rendering
 *  reads it again — the report's page and the list alike. */
export const RENDER_POLL_MS = 3000;
export const isRendering = (r: ClientReport | undefined): boolean =>
  r != null && r.status !== 'draft' && r.render_status === 'pending';

export const reportKindLabel = (r: Pick<ClientReport, 'kind' | 'baseline' | 'revision_of'>): string => {
  const parts = [r.kind === 'addendum' ? `Addendum to #${r.baseline?.number ?? '?'}` : 'Full report'];
  if (r.revision_of) parts.push(`revises #${r.revision_of.number ?? '?'}`);
  return parts.join(' · ');
};

/** An issued report's files.  Each button is its own download (`DownloadButton`):
 *  it says "Downloading…" while its file is fetched, cannot be pressed twice,
 *  and keeps a check once the file is saved — the other formats stay usable. */
export const FileButtons: React.FC<{ report: ClientReport }> = ({ report }) => {
  const projectId = useProjectId();
  if (report.render_status === 'pending') {
    return (
      <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" aria-hidden /> Rendering files…
      </span>
    );
  }
  if (report.render_status === 'failed') {
    return <span className="text-caption text-destructive">Rendering failed</span>;
  }
  if (report.files.length === 0) return <span className="text-caption text-muted-foreground">No files</span>;
  return (
    <span className="inline-flex flex-wrap gap-xxs">
      {report.files.map((f) => (
        <DownloadButton key={f.format} compact variant="outline" size="sm" className="h-7 px-xs text-caption"
          label={f.format.toUpperCase()} name={`Download ${f.filename}`} filename={f.filename}
          errorText="Could not download the file."
          saveFn={() => downloadClientReportFile(projectId, report.id, f)} />
      ))}
    </span>
  );
};

const Reports: React.FC = () => {
  const toast = useToast();
  const navigate = useNavigate();
  const projectId = useProjectId();
  // Read again while an issued report's files render, and not otherwise: the
  // row said "Rendering files…" until the page was reloaded (owner,
  // 2026-10-14) — only the report's own page followed the render.
  const query = useQuery({
    queryKey: ['listClientReports', projectId], queryFn: ({ signal }) => listClientReports(projectId, signal),
    ...pollEvery((q) => ((q.state.data as ClientReportList | undefined)?.items.some(isRendering) ? RENDER_POLL_MS : null)),
  });
  const data: ClientReportList | null = query.data ?? null;
  const error = queryErrorText(query.error, 'Could not load the reports.');

  // The new draft's own page is where it goes; this list is read again when
  // the reader comes back to it.
  const starting = useMutation({
    mutationFn: (kind: ClientReportKind) => createClientReport(projectId, { kind }),
    onSuccess: (report) => navigate(`/reports/${report.id}`),
    onError: (err) => toast.error(formatApiError(err, 'Could not start the report.')),
  });
  const creating: ClientReportKind | null = starting.isPending ? starting.variables : null;

  const drafts = data?.items.filter((r) => r.status === 'draft') ?? [];
  const issued = data?.items.filter((r) => r.status !== 'draft') ?? [];
  const titleCounts = new Map<string, number>();
  for (const r of drafts) titleCounts.set(r.title, (titleCounts.get(r.title) ?? 0) + 1);
  const latest = issued.find((r) => r.id === data?.latest_issued_id) ?? null;
  const otherTemplate = useOtherTemplate(drafts.length > 0);

  // "New report" with drafts already there offers to continue one first
  // (walkthrough 2026-10-14: three drafts under one title, each started by
  // a click meant to go on with the last).  An addendum: only drafts of an
  // addendum to the same report.
  const [offering, setOffering] = useState<ClientReportKind | null>(null);
  const draftsOf = (kind: ClientReportKind) => continuableDrafts(drafts, kind, latest?.id ?? null);
  const newReport = (kind: ClientReportKind) => {
    if (draftsOf(kind).length > 0) setOffering(kind);
    else starting.mutate(kind);
  };

  // A failed load is said: the lead read "Loading…" forever.
  let lead: React.ReactNode = error && !data ? 'The report list could not be loaded.' : 'Loading…';
  if (data) {
    const current = issued.filter((r) => r.status === 'issued').length;
    lead = latest
      ? <>{current} issued report{current === 1 ? '' : 's'}; the latest is <strong>#{latest.number}</strong>, issued {day(latest.issued_at)}.{drafts.length ? ` ${drafts.length} draft${drafts.length === 1 ? '' : 's'} in progress.` : ''}</>
      : <>No report has been issued for this project yet.{drafts.length ? ` ${drafts.length} draft${drafts.length === 1 ? '' : 's'} in progress.` : ''}</>;
  }

  return (
    <div className="space-y-lg p-md md:p-lg">
      <header className="flex flex-wrap items-start justify-between gap-md">
        <div className="min-w-0">
          <h1 className="text-page-title">Reports</h1>
          <PostureLead tone="info" className="mt-xs max-w-3xl">{lead}</PostureLead>
        </div>
        {data?.can_create && (
          <div className="flex shrink-0 flex-col items-end gap-xxs">
            <div className="flex flex-wrap gap-xs">
              <Button onClick={() => newReport('full')} disabled={creating !== null}>
                {creating === 'full' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <FilePlus2 className="size-4" aria-hidden />}
                New report
              </Button>
              <Button variant="outline" onClick={() => newReport('addendum')}
                disabled={creating !== null || !latest}
                aria-describedby={latest ? undefined : 'addendum-why'}
                title={latest ? `Report what changed since #${latest.number}` : undefined}>
                {creating === 'addendum' && <Loader2 className="size-4 animate-spin" aria-hidden />}
                New addendum{latest ? ` to #${latest.number}` : ''}
              </Button>
            </div>
            {/* A disabled button shows no tooltip — say why in text
                (UX review 2026-09-24). */}
            {!latest && (
              <p id="addendum-why" className="text-caption text-muted-foreground">
                Issue a report first — an addendum reports what changed since one.
              </p>
            )}
          </div>
        )}
      </header>

      {error && (
        <div className="flex flex-wrap items-center gap-sm">
          <p className="text-caption text-destructive">{error}</p>
          <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
            <RefreshCw className="size-4" aria-hidden /> Retry
          </Button>
        </div>
      )}

      {data && (
        <>
          <PostureSection title="Drafts" description="Built from the live findings each time they are previewed.">
            {drafts.length === 0 ? (
              <p className="text-caption text-muted-foreground">No drafts.</p>
            ) : (
              <Table className="table-fixed" aria-label="Draft reports">
                {/* The draft number leads the row in a column of its own (UX
                    walkthrough U8): several drafts share one title, and the
                    number is what tells them apart — it is never truncated. */}
                <colgroup><col style={{ width: '4.5rem' }} /><col /><col style={{ width: '14rem' }} /><col style={{ width: '10rem' }} /><col style={{ width: '8rem' }} /></colgroup>
                <TableHeader>
                  <TableRow>
                    <TableHead>Draft</TableHead><TableHead>Title</TableHead><TableHead>Kind</TableHead>
                    <TableHead>Started by</TableHead><TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {drafts.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="whitespace-nowrap align-top font-medium tabular-nums" data-testid={`draft-number-${r.id}`}>
                        <Link to={`/reports/${r.id}`} className="text-foreground hover:underline" aria-label={`Open draft #${r.id}`}>
                          #{r.id}
                        </Link>
                      </TableCell>
                      <TableCell className="min-w-0">
                        <DraftTitle report={r} duplicate={(titleCounts.get(r.title) ?? 0) > 1} />
                        {/* Drafts often share the default title; this line
                            tells them apart from what the list already
                            carries (v5.288.0) — no per-draft request. */}
                        <span
                          className="block break-words text-caption text-muted-foreground"
                          data-testid={`draft-meta-${r.id}`}
                          title={draftMeta(r, otherTemplate(r))}
                        >
                          {draftMeta(r, otherTemplate(r))}
                        </span>
                      </TableCell>
                      <TableCell className="truncate text-caption" title={reportKindLabel(r)}>{reportKindLabel(r)}</TableCell>
                      <TableCell className="truncate text-caption" title={r.created_by_name ?? undefined}>{safeFallback(r.created_by_name, '—')}</TableCell>
                      <TableCell className="text-caption">
                        <TimeAgo value={r.updated_at ?? r.created_at} absoluteAfterDays={30} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </PostureSection>

          <PostureSection title="Issued" description="Frozen when issued; a correction is a revision that supersedes the original.">
            {issued.length === 0 ? (
              <p className="text-caption text-muted-foreground">Nothing issued yet.</p>
            ) : (
              <Table className="table-fixed" aria-label="Issued reports">
                <colgroup>
                  <col style={{ width: '4rem' }} /><col /><col style={{ width: '13rem' }} />
                  <col style={{ width: '9rem' }} /><col style={{ width: '8rem' }} /><col style={{ width: '15rem' }} />
                </colgroup>
                <TableHeader>
                  <TableRow>
                    <TableHead>No.</TableHead><TableHead>Title</TableHead><TableHead>Kind</TableHead>
                    <TableHead>Issued</TableHead><TableHead>State</TableHead><TableHead>Files</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {issued.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="tabular-nums">#{r.number}</TableCell>
                      <TableCell className="truncate">
                        <Link to={`/reports/${r.id}`} className="text-info hover:underline" title={r.title}>{r.title}</Link>
                      </TableCell>
                      <TableCell className="truncate text-caption">{reportKindLabel(r)}</TableCell>
                      <TableCell className="truncate text-caption" title={r.issued_by_name ?? undefined}>
                        {day(r.issued_at)}
                      </TableCell>
                      <TableCell>
                        {/* "Current" was on every report not superseded, so
                            three separate full reports all said it (walkthrough
                            2026-10-14). The newest is the one marked; a
                            revised one says so; the rest are a dash. */}
                        {issuedState(r, data.latest_issued_id)}
                      </TableCell>
                      <TableCell><FileButtons report={r} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </PostureSection>

          <ProfileSection canEdit={data.can_create} />
        </>
      )}

      <ContinueDraftDialog
        kind={offering}
        drafts={offering ? draftsOf(offering) : []}
        onClose={() => setOffering(null)}
        onContinue={(id) => { setOffering(null); navigate(`/reports/${id}`); }}
        onStartNew={(kind) => { setOffering(null); starting.mutate(kind); }}
      />
    </div>
  );
};

/** The drafts "New report" (`full`) or "New addendum" offers to continue,
 *  the most recently updated first: a full report's drafts (revisions
 *  among them), or the drafts of an addendum to `baselineId`. */
export const continuableDrafts = (
  drafts: ClientReport[], kind: ClientReportKind, baselineId: number | null,
): ClientReport[] => drafts
  .filter((r) => r.status === 'draft'
    && (kind === 'addendum' ? r.kind === 'addendum' && r.baseline?.id === baselineId : r.kind !== 'addendum'))
  .sort((a, b) => Date.parse(b.updated_at ?? b.created_at ?? '') - Date.parse(a.updated_at ?? a.created_at ?? ''));

/** What a draft overrides, in words: "follows the project's details", or
 *  "overrides 2 details: client, distribution" (its `settings` are its
 *  overrides only). */
export const overridesLine = (r: Pick<ClientReport, 'settings'>): string => {
  const keys = (Object.keys(DETAIL_LABELS) as Array<keyof typeof DETAIL_LABELS>)
    .filter((key) => isOverridden(r.settings ?? {}, key));
  if (!keys.length) return 'follows the project’s details';
  return `overrides ${keys.length} detail${keys.length === 1 ? '' : 's'}: `
    + keys.map((key) => DETAIL_LABELS[key].toLowerCase()).join(', ');
};

/** One draft the dialog offers: continue it, read what sets it apart
 *  (owner, 2026-10-14: three drafts under one title showed nothing to choose
 *  by), or discard it — whoever may (`can_discard`), after a second press. */
const DraftChoice: React.FC<{
  report: ClientReport;
  primary: boolean;
  /** The template's title when it is not the project's default; else null. */
  otherTemplate: string | null;
  onContinue: (id: number) => void;
}> = ({ report: r, primary, otherTemplate, onContinue }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  const [confirming, setConfirming] = useState(false);
  const discarding = useMutation({
    mutationFn: () => deleteClientReport(projectId, r.id),
    onSuccess: () => {
      // Off the list at once; then read again.
      queryClient.setQueryData<ClientReportList>(['listClientReports', projectId], (d) => (d
        ? { ...d, items: d.items.filter((x) => x.id !== r.id) } : d));
      void queryClient.invalidateQueries({ queryKey: ['listClientReports', projectId] });
      toast.success(`Draft #${r.id} is discarded.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not discard the draft.')),
  });
  const started = formatTimestamp(r.created_at, null);
  return (
    <li className="min-w-0 space-y-xxs border-b border-border pb-xs last:border-b-0" data-testid={`draft-choice-${r.id}`}>
      <div className="flex min-w-0 items-start gap-xs">
        <Button variant={primary ? 'default' : 'link'} size="sm"
          className={primary ? 'h-auto min-w-0 flex-1 justify-start py-xxs text-left' : 'h-auto min-w-0 flex-1 justify-start p-0 text-left'}
          onClick={() => onContinue(r.id)} aria-label={`Continue draft #${r.id}`} title={r.title}>
          <span className="min-w-0 truncate">{primary ? 'Continue draft ' : ''}#{r.id} — {r.title}</span>
        </Button>
        {r.can_discard && !confirming && (
          <Button variant="ghost" size="sm" className="h-7 shrink-0 px-xs text-destructive"
            aria-label={`Discard draft #${r.id}`} onClick={() => setConfirming(true)} disabled={discarding.isPending}>
            <Trash2 className="size-3.5" aria-hidden /> Discard
          </Button>
        )}
      </div>
      <p className="break-words text-caption text-muted-foreground [overflow-wrap:anywhere]" data-testid={`draft-choice-meta-${r.id}`}>
        started by {safeFallback(r.created_by_name, 'someone')}{started ? ` ${started}` : ''}
        {' · '}last changed <TimeAgo value={r.updated_at ?? r.created_at} absoluteAfterDays={30} />
        {' · '}{overridesLine(r)}
        {otherTemplate ? ` · template ${otherTemplate}` : ''}
      </p>
      {confirming && (
        <div className="flex flex-wrap items-center gap-xs text-caption" role="group" aria-label={`Discard draft #${r.id}?`}>
          <span>Discard draft #{r.id}? It is deleted; findings are not affected.</span>
          <Button variant="destructive" size="sm" className="h-7 px-xs" disabled={discarding.isPending}
            onClick={() => discarding.mutate()}>
            {discarding.isPending && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Discard draft
          </Button>
          <Button variant="ghost" size="sm" className="h-7 px-xs" disabled={discarding.isPending}
            onClick={() => setConfirming(false)}>
            Keep it
          </Button>
        </div>
      )}
    </li>
  );
};

const ContinueDraftDialog: React.FC<{
  kind: ClientReportKind | null;
  drafts: ClientReport[];
  onClose: () => void;
  onContinue: (id: number) => void;
  onStartNew: (kind: ClientReportKind) => void;
}> = ({ kind, drafts, onClose, onContinue, onStartNew }) => {
  const open = kind !== null;
  const otherTemplate = useOtherTemplate(open);
  // Discarding the last one leaves "Start a new draft" — the dialog stays.
  const none = drafts.length === 0;
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{none ? 'Start a new draft?' : 'Continue a draft?'}</DialogTitle>
          <DialogDescription>
            {none ? 'No draft is in progress any more.'
              : kind === 'addendum'
                ? `${drafts.length === 1 ? 'A draft addendum to the same report is' : `${drafts.length} draft addenda to the same report are`} already in progress.`
                : `${drafts.length === 1 ? 'A draft report is' : `${drafts.length} draft reports are`} already in progress.`}
          </DialogDescription>
        </DialogHeader>
        {!none && (
          <ul className="max-h-[50vh] min-w-0 space-y-xs overflow-y-auto" aria-label="Drafts in progress" data-testid="draft-choices">
            {drafts.map((r, i) => (
              <DraftChoice key={r.id} report={r} primary={i === 0} otherTemplate={otherTemplate(r)} onContinue={onContinue} />
            ))}
          </ul>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant={none ? 'default' : 'outline'} onClick={() => { if (kind) onStartNew(kind); }}>
            Start a new draft
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const FORMAT_LABEL: Record<string, string> = { html: 'HTML', docx: 'Word', qmd: 'QMD source' };
const NO_TEMPLATES: ReportTemplate[] = [];
const NO_PROBLEMS: ReportTemplateProblem[] = [];

const ProfileSection: React.FC<{ canEdit: boolean }> = ({ canEdit }) => {
  const toast = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const currentUser = user ? { id: user.id, name: user.full_name || user.username } : null;
  const queryClient = useQueryClient();
  // The form while it is open (null = closed) holds only what the reader
  // changed, laid over the stored defaults — never a copy of them: a field
  // that was not touched follows the profile, and nothing is copied out of
  // the query into state.
  const [edits, setEdits] = useState<Partial<ReportProfile> | null>(null);
  const roster = useProjectRoster({ enabled: canEdit });

  const projectId = useProjectId();
  const profileQuery = useQuery({
    queryKey: ['getReportProfile', projectId], queryFn: ({ signal }) => getReportProfile(projectId, signal),
  });
  const templatesQuery = useQuery({
    queryKey: ['listReportTemplates', projectId], queryFn: ({ signal }) => listReportTemplates(projectId, signal),
  });
  // Only an administrator can put a folder on the server, so only they are
  // told which ones could not be offered.
  const problemsQuery = useQuery({
    queryKey: ['listReportTemplateProblems', projectId],
    queryFn: ({ signal }) => listReportTemplateProblems(projectId, signal),
    enabled: isAdmin,
  });
  // The defaults name a template: they are shown once both are known.
  const templates: ReportTemplate[] = templatesQuery.data ?? NO_TEMPLATES;
  const profile: ReportProfile | null = (templatesQuery.data && profileQuery.data) ?? null;
  const problems: ReportTemplateProblem[] = problemsQuery.data ?? NO_PROBLEMS;
  const error = queryErrorText(profileQuery.error ?? templatesQuery.error, 'Could not load the report defaults.');
  const draft: ReportProfile | null = profile && edits ? { ...profile, ...edits } : null;
  // Only the fields that differ from what is stored are edits (lists by
  // their content).
  const setDraft = (next: ReportProfile) => {
    if (!profile) return;
    setEdits(Object.fromEntries(
      (Object.keys(next) as Array<keyof ReportProfile>)
        .filter((k) => JSON.stringify(next[k]) !== JSON.stringify(profile[k]))
        .map((k) => [k, next[k]]),
    ) as Partial<ReportProfile>);
  };

  const savingDefaults = useMutation({
    mutationFn: (next: ReportProfile) => saveReportProfile(projectId, { ...cleanSettings(next), template: next.template }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['getReportProfile', projectId], saved);
      setEdits(null);
      toast.success('Report defaults saved. Drafts that follow them now use them.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the report defaults.')),
  });
  const saving = savingDefaults.isPending;
  const save = () => { if (draft) savingDefaults.mutate(draft); };

  const templateTitle = (name: string | null) => templates.find((t) => t.name === name)?.title ?? name ?? '—';

  return (
    <>
    <PostureSection
      title="Report defaults"
      description="Every draft follows these until it is issued, except a detail it overrides — so a change here reaches every such draft. Issued reports keep what they were issued with."
      actions={canEdit && profile && !draft ? (
        <Button variant="ghost" size="sm" onClick={() => setEdits({})}>
          <Pencil className="size-4" aria-hidden /> Edit
        </Button>
      ) : undefined}
    >
      {error && <p className="text-caption text-destructive">{error}</p>}
      {profile && !draft && (
        <dl className="grid items-baseline gap-x-lg gap-y-xs text-body text-foreground sm:grid-cols-[10rem_minmax(0,1fr)]">
          <dt className={DETAIL_NAME}>Client</dt><dd className="truncate">{safeFallback(profile.client_name, 'Not set')}</dd>
          <dt className={DETAIL_NAME}>Author</dt>
          <dd className="truncate" data-testid="profile-author"
            title={profile.author_name?.trim() || profile.inherited?.author_name?.trim() || undefined}>
            {profile.author_name?.trim()
              ? profile.author_name
              : profile.inherited?.author_name?.trim()
                ? <>{profile.inherited.author_name} <span className="text-caption text-muted-foreground">(installation default)</span></>
                : 'Not set'}
          </dd>
          <dt className={DETAIL_NAME}>Classification</dt><dd className="truncate">{safeFallback(profile.classification, 'Not set')}</dd>
          <dt className={DETAIL_NAME}>Engagement type</dt><dd className="truncate">{safeFallback(profile.engagement_type, 'Not set')}</dd>
          <dt className={DETAIL_NAME}>Team</dt>
          <dd className="break-words">
            {profile.testers.length ? profile.testers.map((t) => t.name).join(', ') : 'Nobody listed'}
            {profile.testers_from_project && profile.testers.length > 0 && (
              <span className="text-caption text-muted-foreground"> — the project&apos;s analysts and admins, until a team is saved</span>
            )}
          </dd>
          <dt className={DETAIL_NAME}>Distribution</dt>
          <dd className="break-words">{profile.distribution.length ? profile.distribution.map((r) => r.name).join(', ') : 'Nobody listed'}</dd>
          {/* The long texts are SAID, not shown: this view is one line per
              detail, and the text itself is one click away (Edit).  Before,
              nothing here told a reader a description, a target list or a
              disclaimer had been written at all (walkthrough 2026-10-13). */}
          <dt className={DETAIL_NAME}>Executive summary</dt>
          <dd className="break-words" data-testid="profile-summary">{profileWritten(profile).summary}</dd>
          <dt className={DETAIL_NAME}>System description</dt>
          <dd className="break-words" data-testid="profile-system">{profileWritten(profile).system}</dd>
          <dt className={DETAIL_NAME}>Other targets</dt>
          <dd className="break-words" data-testid="profile-targets">{profileWritten(profile).targets}</dd>
          <dt className={DETAIL_NAME}>Disclaimer</dt>
          <dd className="break-words" data-testid="profile-disclaimer">{profileWritten(profile).disclaimer}</dd>
          <dt className={DETAIL_NAME}>Template</dt><dd className="truncate">{templateTitle(profile.template)}</dd>
        </dl>
      )}
      {draft && (
        <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <EngagementSettingsFields idPrefix="profile" value={draft} members={roster.members}
            membersStatus={roster.status} onRetryMembers={roster.retry} currentUser={currentUser}
            inherited={profile?.inherited}
            disabled={saving} onChange={(next) => setDraft({ ...draft, ...next })} />
          <div className="w-72 max-w-full space-y-xxs">
            <Label htmlFor="profile-template">Template</Label>
            <Select value={draft.template ?? ''} onValueChange={(v) => setDraft({ ...draft, template: v })} disabled={saving}>
              <SelectTrigger id="profile-template"><SelectValue placeholder="Choose a template" /></SelectTrigger>
              <SelectContent>
                {templates.filter(isClientTemplate).map((t) => <SelectItem key={t.name} value={t.name}>{t.title}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="flex gap-xs">
            <Button type="submit" size="sm" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save defaults
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setEdits(null)} disabled={saving}>Cancel</Button>
          </div>
        </form>
      )}
    </PostureSection>
    {profile && (
      <TemplatesSection
        templates={templates}
        problems={problems}
        defaultName={profile.template}
        isAdmin={isAdmin}
      />
    )}
    </>
  );
};

/**
 * Every template installed on the server, the default first, each with the
 * files it uses besides the findings' evidence. A template is a folder under
 * `report-templates/`, read on each request — templates are not uploaded, but
 * a global admin uploads a template's own files here (v5.311.0).
 */
const TemplatesSection: React.FC<{
  templates: ReportTemplate[]; problems: ReportTemplateProblem[]; defaultName: string | null; isAdmin: boolean;
}> = ({
  templates, problems, defaultName, isAdmin,
}) => {
  const ordered = [...templates].sort((a, b) => Number(b.name === defaultName) - Number(a.name === defaultName));
  return (
    <PostureSection
      title={<>Templates<SectionCount>{templates.length} installed</SectionCount></>}
      description="Open a template for its own files (logo, Word styles…)."
    >
      {templates.length === 0 ? (
        <p className="text-caption text-destructive">No report templates are installed, so no report can be rendered.</p>
      ) : (
        <div className="divide-y divide-border">
          {ordered.map((t) => {
            const count = assetCountLabel(t);
            return (
              // Closed unless something is missing that blocks a report: the
              // asset guidance is set-up reading, and this page is for reports.
              <details key={t.name} open={missingRequiredAssets(t).length > 0} className="group py-sm first:pt-0">
                <summary className="flex min-w-0 cursor-pointer flex-wrap items-baseline gap-x-xs gap-y-xxs">
                  <span className="break-words font-medium">{t.title}</span>
                  {t.name === defaultName && <Badge variant="info">Default</Badge>}
                  <span className="text-caption text-muted-foreground">
                    {t.formats.map((f) => FORMAT_LABEL[f] ?? f).join(', ')}
                    {count && <> · {count}</>}
                  </span>
                </summary>
                <div className="mt-xs space-y-xs">
                  {t.description && <p className="max-w-3xl break-words text-caption text-muted-foreground">{t.description}</p>}
                  <TemplateImages
                    template={t}
                    templateName={t.name}
                    showServerPaths={isAdmin}
                  />
                </div>
              </details>
            );
          })}
        </div>
      )}
      {/* A folder that is there but not offered used to vanish without a
          word (v5.295.0): the author had no way to learn why. */}
      {isAdmin && problems.length > 0 && (
        <div className="mt-sm border-l-4 border-l-warning py-xxs pl-sm" role="status">
          <p className="text-caption font-medium text-foreground">
            {problems.length === 1 ? '1 folder is' : `${problems.length} folders are`} in{' '}
            <span className="font-mono">report-templates/</span> but not offered:
          </p>
          <ul className="mt-xxs space-y-xxs">
            {problems.map((p) => (
              <li key={p.name} className="min-w-0 break-words text-caption text-muted-foreground">
                <span className="font-mono text-foreground">{p.name}</span> — {p.error}
              </li>
            ))}
          </ul>
        </div>
      )}
      {isAdmin && (
        <details className="mt-sm max-w-3xl text-caption text-muted-foreground">
          <summary className="cursor-pointer font-medium text-foreground">Add a template</summary>
          <ol className="mt-xs list-decimal space-y-xxs pl-lg">
            <li>
              Copy an existing template&apos;s folder on the server. <span className="font-mono">remediation-worklist</span>{' '}
              is the starter: the smallest <span className="font-mono">template.json</span>, no images, and a{' '}
              <span className="font-mono">report.qmd</span> whose comments explain each part.
            </li>
            <li>
              Rename the folder (lower-case letters, digits, <span className="font-mono">-</span> or{' '}
              <span className="font-mono">_</span>) and give <span className="font-mono">template.json</span> its own title and
              description — they are what people choose by here and on each draft.
            </li>
            <li>
              Edit <span className="font-mono">report.qmd</span> and render it with its{' '}
              <span className="font-mono">sample-data.json</span> (<span className="font-mono">make</span> in the folder)
              until it reads right.
            </li>
            <li>
              Put the folder in <span className="font-mono">report-templates/</span>. It is listed here on the next reload — no
              rebuild or restart. A folder with a mistake is listed above with what to fix.
            </li>
          </ol>
          <p className="mt-xs">
            The full guide — every <span className="font-mono">template.json</span> key, the data a template receives and the
            rules that keep written text from becoming template code — is{' '}
            <span className="font-mono">report-templates/README.md</span> in the BlueStick source.
          </p>
        </details>
      )}
    </PostureSection>
  );
};

export default Reports;
