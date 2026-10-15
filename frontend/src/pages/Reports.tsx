/**
 * /reports — the project's client reports: drafts in progress and the issued
 * history, and "New report" (the report workspace, owner 2026-10-14: the
 * everyday page holds the everyday work).  The project's report defaults and
 * the installation's templates have pages of their own (/reports/defaults,
 * /reports/templates), linked from here.
 *
 * A report is a draft until a project admin issues it; issuing freezes what
 * it says and numbers it.  An addendum reports only what changed since an
 * issued report (new findings, findings on further systems, withdrawals).
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { FilePlus2, Loader2, Pencil, RefreshCw } from 'lucide-react';

import {
  ClientReport,
  ClientReportKind,
  ClientReportList,
  createClientReport,
  listClientReports,
  updateClientReport,
} from '../services/api';
import { formatDate, formatTimestamp } from '../utils/relativeTime';
import TimeAgo from '../components/TimeAgo';
import { Input } from '../components/ui/input';
import { useToast } from '../contexts/ToastContext';
import { useProjectId } from '../hooks/useProjectId';
import { reportProfileQuery, reportTemplatesQuery } from '../components/reports/reportQueries';
import { FileButtons, RENDER_POLL_MS, isRendering, reportKindLabel } from '../components/reports/issuedReport';
import { pollEvery, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { isClientTemplate } from '../utils/reportTemplates';
import PostureSection from '../components/posture/PostureSection';
import PostureLead from '../components/posture/PostureLead';
import { DETAIL_LABELS, type DetailKey, savedOverride } from '../components/reports/workspace/detailModel';
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
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';
import { safeFallback } from '../utils/uiStyles';
import { useOpeningKey } from '../hooks/useOpeningKey';

const day = (iso: string | null) => formatDate(iso);

/** "started 23 Sep 2026, 14:05 · overrides 1 detail: classification ·
 *  template Executive brief" — what sets apart drafts that share a title. */
export const draftMeta = (r: Pick<ClientReport, 'created_at' | 'settings'>, otherTemplate: string | null): string => {
  const started = formatTimestamp(r.created_at, null);
  return [started ? `started ${started}` : null, overridesLine(r), otherTemplate ? `template ${otherTemplate}` : null]
    .filter(Boolean)
    .join(' · ');
};

/** The title of a draft's template when it is not the project's default,
 *  else null. */
const useOtherTemplate = (enabled: boolean): ((r: Pick<ClientReport, 'template'>) => string | null) => {
  const projectId = useProjectId();
  const profile = useQuery({
    ...reportProfileQuery(projectId),
    enabled,
  });
  const templatesQuery = useQuery({
    ...reportTemplatesQuery(projectId),
    enabled,
  });
  const defaultTemplate = profile.data?.template ?? null;
  return (r) => (defaultTemplate && r.template !== defaultTemplate
    ? (templatesQuery.data?.find((t) => t.name === r.template)?.title ?? r.template) : null);
};

/** A draft's title with an in-place rename (v5.294.0). */
const DraftTitle: React.FC<{ report: ClientReport; duplicate: boolean }> = ({ report, duplicate }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  const [value, setValue] = useState<string | null>(null);
  const renaming = useMutation({
    mutationFn: (title: string) => updateClientReport(projectId, report.id, { title }),
    onSuccess: (updated) => {
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
        <Input autoFocus value={value} maxLength={255} disabled={saving}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') setValue(null); }}
          aria-label={`New title for draft #${report.id}`} className="h-7 min-w-0 flex-1 text-metadata" />
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

/** An issued report's State cell: the newest issued report is "Latest", a
 *  revised one "Superseded", any other a dash. */
export const issuedState = (r: Pick<ClientReport, 'id' | 'status'>, latestId: number | null | undefined): React.ReactNode => {
  if (r.status === 'superseded') return <Badge variant="muted">Superseded</Badge>;
  if (r.id === latestId) return <Badge variant="success">Latest</Badge>;
  return <span className="text-muted-foreground">—</span>;
};

/** The drafts a new report of `kind` might continue instead, the most
 *  recently updated first: a full report's drafts (revisions among them), or
 *  the drafts of an addendum to `baselineId`. */
export const continuableDrafts = (
  drafts: ClientReport[], kind: ClientReportKind, baselineId: number | null,
): ClientReport[] => drafts
  .filter((r) => r.status === 'draft'
    && (kind === 'addendum' ? r.kind === 'addendum' && r.baseline?.id === baselineId : r.kind !== 'addendum'))
  .sort((a, b) => Date.parse(b.updated_at ?? b.created_at ?? '') - Date.parse(a.updated_at ?? a.created_at ?? ''));

/** What a draft overrides, in words: "follows the project's details", or
 *  "overrides 2 details: client, distribution". */
export const overridesLine = (r: Pick<ClientReport, 'settings'>): string => {
  const keys = (Object.keys(DETAIL_LABELS) as DetailKey[]).filter((key) => savedOverride(r.settings ?? {}, key));
  if (!keys.length) return 'follows the project’s details';
  return `overrides ${keys.length} detail${keys.length === 1 ? '' : 's'}: `
    + keys.map((key) => DETAIL_LABELS[key].toLowerCase()).join(', ');
};

const Reports: React.FC = () => {
  const toast = useToast();
  const navigate = useNavigate();
  const projectId = useProjectId();
  const query = useQuery({
    queryKey: ['listClientReports', projectId], queryFn: ({ signal }) => listClientReports(projectId, signal),
    ...pollEvery((q) => ((q.state.data as ClientReportList | undefined)?.items.some(isRendering) ? RENDER_POLL_MS : null)),
  });
  const data: ClientReportList | null = query.data ?? null;
  const error = queryErrorText(query.error, 'Could not load the reports.');

  const drafts = data?.items.filter((r) => r.status === 'draft') ?? [];
  const issued = data?.items.filter((r) => r.status !== 'draft') ?? [];
  const titleCounts = new Map<string, number>();
  for (const r of drafts) titleCounts.set(r.title, (titleCounts.get(r.title) ?? 0) + 1);
  const latest = issued.find((r) => r.id === data?.latest_issued_id) ?? null;
  const otherTemplate = useOtherTemplate(drafts.length > 0);
  const [creatingKind, setCreatingKind] = useState<ClientReportKind | null>(null);
  // Every opening of New report starts clean (review 2026-10-14, R13: a
  // title typed before a create was there again the next time).
  const newReportKey = useOpeningKey(creatingKind);

  const starting = useMutation({
    mutationFn: (body: { kind: ClientReportKind; title?: string; template?: string }) => createClientReport(projectId, body),
    onSuccess: (report) => navigate(`/reports/${report.id}`),
    onError: (err) => toast.error(formatApiError(err, 'Could not start the report.')),
  });

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
          <p className="mt-xs text-caption text-muted-foreground" data-testid="reports-config-links">
            <Link to="/reports/defaults" className="text-info hover:underline">Report defaults</Link>
            {' — what every draft follows · '}
            <Link to="/reports/templates" className="text-info hover:underline">Templates</Link>
          </p>
        </div>
        {data?.can_create && (
          <div className="flex shrink-0 flex-col items-end gap-xxs">
            <div className="flex flex-wrap gap-xs">
              <Button onClick={() => setCreatingKind('full')} disabled={starting.isPending}>
                <FilePlus2 className="size-4" aria-hidden /> New report
              </Button>
              <Button variant="outline" onClick={() => setCreatingKind('addendum')} disabled={starting.isPending || !latest}
                aria-describedby={latest ? undefined : 'addendum-why'}
                title={latest ? `Report what changed since #${latest.number}` : undefined}>
                New addendum{latest ? ` to #${latest.number}` : ''}
              </Button>
            </div>
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
            <div id="report-drafts" className="scroll-mt-24" />
            {drafts.length === 0 ? (
              <p className="text-caption text-muted-foreground">No drafts.</p>
            ) : (
              <Table className="table-fixed" aria-label="Draft reports">
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
                        <span className="block break-words text-caption text-muted-foreground"
                          data-testid={`draft-meta-${r.id}`} title={draftMeta(r, otherTemplate(r))}>
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
                      <TableCell className="truncate text-caption" title={r.issued_by_name ?? undefined}>{day(r.issued_at)}</TableCell>
                      <TableCell>{issuedState(r, data.latest_issued_id)}</TableCell>
                      <TableCell><FileButtons report={r} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </PostureSection>
        </>
      )}

      <NewReportDialog
        key={newReportKey}
        kind={creatingKind}
        latest={latest}
        drafts={creatingKind ? continuableDrafts(drafts, creatingKind, latest?.id ?? null) : []}
        busy={starting.isPending}
        onClose={() => setCreatingKind(null)}
        onCreate={(body) => starting.mutate(body)}
        onContinue={(id) => { setCreatingKind(null); navigate(`/reports/${id}`); }}
        onViewDrafts={() => {
          setCreatingKind(null);
          document.getElementById('report-drafts')?.scrollIntoView?.({ block: 'start' });
        }}
      />
    </div>
  );
};

/**
 * "New report" / "New addendum" (the report workspace, owner 2026-10-14): a
 * small dialog — title (empty = the usual one), template (the project's
 * default first) — with a one-line note when a draft of the same kind (an
 * addendum: to the same report) is already in progress: one is named with
 * "Continue it", several are counted with "View drafts".  Not a
 * continue-or-start screen of its own any more.
 */
const NewReportDialog: React.FC<{
  kind: ClientReportKind | null;
  latest: ClientReport | null;
  drafts: ClientReport[];
  busy: boolean;
  onClose: () => void;
  onCreate: (body: { kind: ClientReportKind; title?: string; template?: string }) => void;
  onContinue: (id: number) => void;
  onViewDrafts: () => void;
}> = ({ kind, latest, drafts, busy, onClose, onCreate, onContinue, onViewDrafts }) => {
  const projectId = useProjectId();
  const open = kind !== null;
  const profile = useQuery({
    ...reportProfileQuery(projectId),
    enabled: open,
  });
  const templatesQuery = useQuery({
    ...reportTemplatesQuery(projectId),
    enabled: open,
  });
  const [title, setTitle] = useState('');
  const [template, setTemplate] = useState<string | null>(null);
  const templates = (templatesQuery.data ?? []).filter(isClientTemplate);
  const chosen = template ?? profile.data?.template ?? templates[0]?.name ?? '';
  const addendum = kind === 'addendum';
  // Until the project's default is known, "the template chosen" is not: the
  // first template would be sent in its place (review 2026-10-14, R13).
  const settling = open && (profile.isPending || templatesQuery.isPending);
  const close = () => onClose();
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !busy) close(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{addendum ? `New addendum to #${latest?.number ?? '?'}` : 'New report'}</DialogTitle>
          <DialogDescription>
            {addendum
              ? 'An addendum reports what changed since the report it is compared with.'
              : 'A new draft follows the project’s report defaults; you can customise any detail.'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-sm">
          {drafts.length === 1 && (
            <p className="break-words rounded-control border border-border px-sm py-xs text-caption [overflow-wrap:anywhere]"
              data-testid="new-report-in-progress">
              A draft {addendum ? 'addendum to the same report' : 'report'} is in progress: <strong>#{drafts[0].id} {drafts[0].title}</strong>.{' '}
              <button type="button" className="text-info hover:underline" onClick={() => onContinue(drafts[0].id)}>Continue it</button>
            </p>
          )}
          {drafts.length > 1 && (
            <p className="rounded-control border border-border px-sm py-xs text-caption" data-testid="new-report-in-progress">
              {drafts.length} draft {addendum ? 'addenda to the same report are' : 'reports are'} in progress.{' '}
              <button type="button" className="text-info hover:underline" onClick={onViewDrafts}>View drafts</button>
            </p>
          )}
          <div className="space-y-xxs">
            <Label htmlFor="new-report-title">Title</Label>
            <Input id="new-report-title" maxLength={255} value={title} disabled={busy}
              placeholder={addendum ? `Addendum to report #${latest?.number ?? '?'}` : 'The project’s name — security assessment report'}
              onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-xxs">
            <Label htmlFor="new-report-template">Template</Label>
            <Select value={chosen} onValueChange={setTemplate} disabled={busy || !templates.length}>
              <SelectTrigger id="new-report-template"><SelectValue placeholder="Choose a template" /></SelectTrigger>
              <SelectContent>
                {templates.map((t) => (
                  <SelectItem key={t.name} value={t.name}>
                    {t.title}{t.name === profile.data?.template ? ' (project default)' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={close} disabled={busy}>Cancel</Button>
          <Button disabled={busy || !kind || settling}
            onClick={() => kind && onCreate({ kind, ...(title.trim() ? { title: title.trim() } : {}), ...(chosen ? { template: chosen } : {}) })}>
            {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {addendum ? 'Start the addendum' : 'Start the draft'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default Reports;
