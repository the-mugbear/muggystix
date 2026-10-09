/**
 * /reports/:reportId (v5.261.0) — one client report.
 *
 * A DRAFT: what it will contain (built from the live findings — counts, what
 * is left out, findings still missing text), its details (title, executive
 * summary, engagement details), previews in each format, and — for a project
 * admin — Issue, which freezes it.  An ISSUED report: its files, who issued
 * it when, the template fingerprint, and Revise (a new draft that supersedes
 * it once issued).
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { isClientTemplate } from '../utils/reportTemplates';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Download, Loader2, RefreshCw, Sparkles, Stamp, Trash2 } from 'lucide-react';

import {
  ClientReport,
  ClientReportFormat,
  ReportFileFormat,
  EngagementSettings,
  ReportJob,
  ReportSummary,
  ReportTemplate,
  deleteClientReport,
  downloadClientReportScope,
  downloadReportJob,
  getClientReport,
  getReportJob,
  issueClientReport,
  listReportTemplates,
  previewClientReport,
  rerenderClientReport,
  reviseClientReport,
  updateClientReport,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { useDiscardGuard } from '../hooks/useDiscardGuard';
import { useProjectRoster } from '../hooks/useProjectMembers';
import { pollEvery, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { formatTimestamp } from '../utils/relativeTime';
import { REPORT_FIELD_LABELS } from '../utils/reportImages';
import { safeFallback } from '../utils/uiStyles';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import EngagementSettingsFields, { cleanSettings } from '../components/reports/EngagementSettingsFields';
import TemplateImages, {
  TemplateFilesLine, assetCountLabel, missingAssetsReason, missingRequiredAssets,
} from '../components/reports/TemplateImages';
import AiDraftReportDialog from '../components/AiDraftReportDialog';
import { DetailSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import MarkdownField from '../components/MarkdownField';
import { FileButtons, reportKindLabel } from './Reports';

// `pdf` labels a file of a report issued before PDF was removed (5.293.0).
const FORMAT_LABEL: Record<ReportFileFormat, string> = {
  html: 'HTML', docx: 'Word', qmd: 'QMD source (.zip)', pdf: 'PDF',
};
const when = (iso: string | null) => formatTimestamp(iso);

/** "N test results are printed as how findings were confirmed; M were recorded
 *  by an agent." — the second clause only when an agent recorded any. */
export const evidenceRecordsNotice = (total: number, byAgent?: number | null): string => {
  const agent = byAgent ?? 0;
  const first = `${total.toLocaleString()} test result${total === 1 ? ' is' : 's are'} printed as how findings were confirmed`;
  return agent > 0
    ? `${first}; ${agent.toLocaleString()} ${agent === 1 ? 'was' : 'were'} recorded by an agent.`
    : `${first}.`;
};

/** "N further test results belong to findings this report lists without their
 *  details; they are not printed." — an addendum's already-reported findings. */
export const evidenceRecordsNotPrintedNotice = (count: number): string =>
  `${count.toLocaleString()} further test result${count === 1 ? ' belongs' : 's belong'} to findings this report lists without their details; ${count === 1 ? 'it is' : 'they are'} not printed.`;

const joinWords = (words: string[]): string =>
  words.length <= 1 ? words.join('') : `${words.slice(0, -1).join(', ')} or ${words[words.length - 1]}`;

/** Why ticked images print nowhere in this report, as the end of a sentence
 *  ("… are not printed" + this). */
const notPrintedWhy = (s: ReportSummary): string => {
  const reasons = s.images_not_printed_reasons;
  const template = s.template_images;
  const byTemplate = (reasons?.section_not_printed ?? 0) + (reasons?.no_evidence_block ?? 0);
  if (reasons && reasons.finding_not_detailed > 0 && byTemplate === 0) {
    return reasons.finding_not_detailed === 1
      ? ': its finding is listed in this report without its details'
      : ': their findings are listed in this report without their details';
  }
  if (!template) return ' by this template';
  const fields = template.fields.map((f) => (REPORT_FIELD_LABELS[f] ?? f).toLowerCase());
  if (!fields.length) {
    return template.trailing
      ? ' where they are placed: this template prints images only under Evidence'
      : ' by this template';
  }
  const also = reasons?.finding_not_detailed
    ? ', and it lists some findings without their details' : '';
  return template.trailing
    ? ` by this template: in the text it prints only images placed in the ${joinWords(fields)}${also}`
    : ` by this template: it prints only images placed in the ${joinWords(fields)}${also}`;
};

/**
 * Where the report's ticked images print — in THIS report, with ITS template
 * (review 2026-10-01 S2).  The line used to read "N placed in text, M under
 * Evidence" whatever the template: the Executive brief prints no image, the
 * Remediation worklist only those placed in a recommendation, and an addendum
 * lists a finding the client already has in one line.  The server measures
 * it; with no measurement (a report issued before this, or a template that
 * cannot be read) the line claims nothing.
 */
export const evidenceImagesLine = (s: ReportSummary): string => {
  const total = s.images ?? 0;
  const inText = s.images_printed;
  const trailing = s.images_trailing;
  const unprinted = s.images_not_printed;
  if (!total) return '';
  if (inText == null || trailing == null || unprinted == null) return 'Ticked “In report” on the findings';
  const template = s.template_images;
  if (inText + trailing === 0 && template && !template.fields.length && !template.trailing) {
    return `This template prints no evidence images (${total.toLocaleString()} ticked)`;
  }
  const parts: string[] = [];
  if (inText + trailing > 0) {
    // "under Evidence" is left out for a template with no such block.
    parts.push(template && !template.trailing && trailing === 0
      ? `${inText.toLocaleString()} in the text`
      : `${inText.toLocaleString()} in the text, ${trailing.toLocaleString()} under Evidence`);
  }
  if (unprinted > 0) {
    parts.push(`${unprinted.toLocaleString()} ticked image${unprinted === 1 ? ' is' : 's are'} not printed${notPrintedWhy(s)}`);
  }
  return parts.join(' · ');
};

interface Form {
  title: string;
  template: string;
  executive_summary: string;
  settings: EngagementSettings;
}

const toForm = (r: ClientReport): Form => ({
  title: r.title,
  template: r.template,
  executive_summary: r.executive_summary ?? '',
  settings: r.settings,
});

/** "Before issuing" lists this many findings missing text, then "Show all". */
const MISSING_PREVIEW = 10;

const NO_TEMPLATES: ReportTemplate[] = [];
/** How often an issued report still rendering is read again. */
const RENDER_POLL_MS = 3000;
const isRendering = (r: ClientReport | undefined): boolean =>
  r != null && r.status !== 'draft' && r.render_status === 'pending';
/** How often a preview job still on the worker is asked about. */
const PREVIEW_POLL_MS = 2000;
const isRunning = (job: ReportJob | undefined): boolean =>
  job != null && (job.status === 'queued' || job.status === 'processing');

const ReportDetailView: React.FC<{ id: number }> = ({ id }) => {
  const toast = useToast();
  const navigate = useNavigate();
  const [confirmDialog, confirm] = useConfirm();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const currentUser = user ? { id: user.id, name: user.full_name || user.username } : null;
  const [showAllMissing, setShowAllMissing] = useState(false);

  const queryClient = useQueryClient();
  const roster = useProjectRoster();
  const [aiOpen, setAiOpen] = useState(false);

  // An issued report renders its files on the worker: it is read again every
  // three seconds until they are done, and not at all otherwise.
  const reportKey = ['getClientReport', id];
  const query = useQuery({
    queryKey: reportKey,
    queryFn: () => getClientReport(id),
    ...pollEvery((q) => (isRendering(q.state.data) ? RENDER_POLL_MS : null)),
  });
  const report: ClientReport | null = query.data ?? null;
  // Every write names the report by the id it was loaded with.
  const reportId = report?.id ?? id;
  const error = queryErrorText(query.error, 'Could not load the report.');
  const putReport = (next: ClientReport) => queryClient.setQueryData(reportKey, next);

  const templatesQuery = useQuery({ queryKey: ['listReportTemplates'], queryFn: () => listReportTemplates() });
  const templates: ReportTemplate[] = templatesQuery.data ?? NO_TEMPLATES;

  // What the reader changed, over the report as stored: a save, an issue or a
  // re-read cannot leave the form and the report out of step.
  const [edits, setEdits] = useState<Partial<Form>>({});
  const form: Form | null = useMemo(() => (report ? { ...toForm(report), ...edits } : null), [report, edits]);
  const edit = (changed: Partial<Form>) => setEdits((e) => ({ ...e, ...changed }));

  // Draft previews are report jobs.  `started` is each format's job as the
  // worker took it; the ones still running are asked about every two seconds,
  // from that answer on.
  const [started, setStarted] = useState<Partial<Record<ClientReportFormat, ReportJob>>>({});
  const startedJobs = Object.entries(started) as Array<[ClientReportFormat, ReportJob]>;
  const followed = useQueries({
    queries: startedJobs.map(([, job]) => ({
      queryKey: ['getReportJob', job.id],
      queryFn: () => getReportJob(job.id),
      initialData: job,
      staleTime: PREVIEW_POLL_MS,
      enabled: (q: { state: { data?: ReportJob } }) => isRunning(q.state.data),
      ...pollEvery(PREVIEW_POLL_MS),
    })),
  });
  const previews: Partial<Record<ClientReportFormat, ReportJob>> = Object.fromEntries(
    startedJobs.map(([fmt, job], i) => [fmt, followed[i]?.data ?? job]),
  );

  const dirty = useMemo(() => {
    if (!report || !form) return false;
    return JSON.stringify(toForm(report)) !== JSON.stringify(form);
  }, [report, form]);
  // The Back button and a tab close used to discard an unsaved narrative
  // without asking, although `dirty` was right here (review B-UI-7).
  const { confirmLeave, confirmEl: leaveDialog } = useDiscardGuard(
    () => dirty,
    'This draft has unsaved changes — the summary, details or engagement fields you edited. Leave anyway?',
  );

  const saveDetails = useMutation({
    mutationFn: (next: Form) => updateClientReport(reportId, {
      title: next.title.trim(),
      template: next.template,
      executive_summary: next.executive_summary.trim() || null,
      settings: cleanSettings(next.settings),
    }),
    onSuccess: (updated) => {
      putReport(updated);
      setEdits({});
      toast.success('Report saved.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the report.')),
  });
  const saving = saveDetails.isPending;
  const save = () => {
    if (!report || !form) return;
    if (!form.title.trim()) { toast.error('A report needs a title.'); return; }
    saveDetails.mutate(form);
  };

  // The template is a rendering choice beside the Preview buttons, so it is
  // saved on its own at once — other unsaved edits stay unsaved.
  const templateChange = useMutation({
    mutationFn: (name: string) => updateClientReport(reportId, { template: name }),
    onSuccess: (updated) => {
      putReport(updated);
      setStarted({});
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not change the template.')),
  });
  const savingTemplate = templateChange.isPending;
  const changeTemplate = (name: string) => {
    if (!report || name === report.template) return;
    templateChange.mutate(name);
  };

  const startPreview = useMutation({
    mutationFn: (fmt: ClientReportFormat) => previewClientReport(reportId, fmt),
    onSuccess: (job, fmt) => setStarted((prev) => ({ ...prev, [fmt]: job })),
    onError: (err) => toast.error(formatApiError(err, 'Could not start the preview.')),
  });
  const preview = (fmt: ClientReportFormat) => { if (report) startPreview.mutate(fmt); };
  const downloadPreview = useMutation({
    mutationFn: (jobId: number) => downloadReportJob(jobId),
    onError: (err) => toast.error(formatApiError(err, 'Could not download the preview.')),
  });

  const issuing = useMutation({
    mutationFn: () => issueClientReport(reportId),
    onSuccess: (issued) => {
      putReport(issued);
      setEdits({});
      // The project's reports gained a number; "start the clock from a
      // report" and the Reports page read that list.
      void queryClient.invalidateQueries({ queryKey: ['listClientReports'] });
      toast.success(`Issued as report #${issued.number}. Rendering its files…`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not issue the report.')),
  });
  const issue = async () => {
    if (!report) return;
    const s = report.summary;
    const ok = await confirm({
      title: 'Issue this report?',
      body: (
        <>
          <p>
            It takes the project&apos;s next report number and is frozen: what it says about each finding
            is kept as it is now, and it can no longer be edited or deleted. A correction is a revision
            that supersedes it.
          </p>
          {!!s?.under_investigation && (
            <p className="mt-xs">{s.under_investigation} finding{s.under_investigation === 1 ? ' is' : 's are'} still under investigation and will not be in it.</p>
          )}
          {!!s?.missing_text?.length && (
            <p className="mt-xs">{s.missing_text.length} finding{s.missing_text.length === 1 ? ' is' : 's are'} missing report text.</p>
          )}
          {!!s?.missing_details?.length && (
            <p className="mt-xs">Still empty, and issued as TODO: {s.missing_details.join(', ')}.</p>
          )}
          {!!s?.pending_proposals?.length && (
            <p className="mt-xs">{s.pending_proposals.length} finding{s.pending_proposals.length === 1 ? ' has' : 's have'} proposed changes nobody has decided — it is issued as the findings stand now.</p>
          )}
          {!!s?.internal_references?.length && (
            <p className="mt-xs">{s.internal_references.length} finding{s.internal_references.length === 1 ? ' names' : 's name'} a BlueStick record in its text, which the reader cannot look up.</p>
          )}
          {!!s?.agent_images && (
            <p className="mt-xs">{s.agent_images} image{s.agent_images === 1 ? ' comes' : 's come'} from notes an agent wrote.</p>
          )}
          {!!s?.images_not_printed && (
            // Said before the sign-off: a ticked image this report will not show.
            <p className="mt-xs break-words">Evidence images: {evidenceImagesLine(s)}.</p>
          )}
          {!!s?.evidence_records && (
            <p className="mt-xs">{evidenceRecordsNotice(s.evidence_records, s.agent_evidence_records)}</p>
          )}
          {!!s?.evidence_records_not_printed && (
            <p className="mt-xs">{evidenceRecordsNotPrintedNotice(s.evidence_records_not_printed)}</p>
          )}
          {!!s?.scope_external?.file && (
            <p className="mt-xs">Its scope ({s.scope_external.networks.toLocaleString()} networks) is over the template&apos;s limit: the report names <span className="font-medium">{s.scope_external.file.name}</span> instead of listing it — send that file with the report.</p>
          )}
        </>
      ),
      confirmLabel: 'Issue report',
    });
    if (ok) issuing.mutate();
  };

  const revising = useMutation({
    mutationFn: () => reviseClientReport(reportId),
    onSuccess: (draft) => navigate(`/reports/${draft.id}`),
    onError: (err) => toast.error(formatApiError(err, 'Could not start a revision.')),
  });

  const rerendering = useMutation({
    mutationFn: () => rerenderClientReport(reportId),
    onSuccess: (next) => { putReport(next); },
    onError: (err) => toast.error(formatApiError(err, 'Could not restart the rendering.')),
  });

  // 5.319.0 — the scope file an over-cutoff report names.
  const scopeDownload = useMutation({
    mutationFn: (name: string) => downloadClientReportScope(reportId, name),
    onError: (err) => toast.error(formatApiError(err, 'Could not download the scope file.')),
  });
  const downloadScope = () => {
    const file = report?.summary?.scope_external?.file;
    if (file) scopeDownload.mutate(file.name);
  };

  const discarding = useMutation({
    mutationFn: () => deleteClientReport(reportId),
    onSuccess: () => navigate('/reports'),
    onError: (err) => toast.error(formatApiError(err, 'Could not discard the draft.')),
  });
  const discard = async () => {
    if (!report) return;
    const ok = await confirm({
      title: 'Discard this draft?', body: `"${report.title}" is deleted. Findings are not affected.`,
      severity: 'danger', confirmLabel: 'Discard draft',
    });
    if (ok) discarding.mutate();
  };

  // One of these at a time; the buttons wait for it.  A discarded draft's
  // page stays busy until the list replaces it.
  const busy: 'issue' | 'revise' | 'render' | 'delete' | null = issuing.isPending ? 'issue'
    : revising.isPending ? 'revise' : rerendering.isPending ? 'render'
      : discarding.isPending || discarding.isSuccess ? 'delete' : null;

  if (!report && !error) return <DetailSkeleton />;
  if (!report || !form) {
    return (
      <div className="p-md md:p-lg">
        <Button variant="ghost" size="sm" onClick={() => navigate('/reports')}><ArrowLeft className="size-4" aria-hidden /> Reports</Button>
        <p className="mt-md text-destructive">{error}</p>
        <Button variant="outline" size="sm" className="mt-sm" onClick={() => void query.refetch()}><RefreshCw className="size-4" aria-hidden /> Retry</Button>
      </div>
    );
  }

  const isDraft = report.status === 'draft';
  const s = report.summary ?? {};
  const counts = s.counts;
  const editable = isDraft && report.can_edit;
  const template = templates.find((t) => t.name === report.template);
  const formats: ClientReportFormat[] = template?.formats ?? ['html', 'docx'];
  // A required template image that is not installed blocks every render (the
  // server refuses too); say so on the buttons instead of failing a job.
  const assetsBlock = missingAssetsReason(template);
  const missingText = s.missing_text ?? [];
  const missingDetails = s.missing_details ?? [];
  const pendingProposals = s.pending_proposals ?? [];
  const internalReferences = s.internal_references ?? [];
  // Issue is the main action only once nothing prints as TODO; until then
  // the page leads to the gaps and to previewing, not to freezing them.
  const ready = !missingText.length && !missingDetails.length;
  const notReadyReason = ready ? undefined
    : 'Parts of this report still print as TODO — see Before issuing';

  let lead: React.ReactNode;
  if (s.error) {
    lead = <span className="text-destructive">{s.error}</span>;
  } else if (report.kind === 'addendum' && s.delta) {
    // Reports issued before severity changes were counted lack the field.
    const reRated = s.delta.findings_with_changed_severity ?? 0;
    lead = <>Compared with report #{report.baseline?.number}: <strong>{s.delta.new_findings}</strong> new finding{s.delta.new_findings === 1 ? '' : 's'}, <strong>{s.delta.findings_with_new_endpoints}</strong> reported finding{s.delta.findings_with_new_endpoints === 1 ? '' : 's'} on further systems, <strong>{s.delta.withdrawn}</strong> withdrawal{s.delta.withdrawn === 1 ? '' : 's'}{reRated > 0 && <>, <strong>{reRated}</strong> reported finding{reRated === 1 ? '' : 's'} with a changed severity</>}.</>;
  } else {
    lead = isDraft
      ? <>Covers <strong>{counts?.total ?? 0}</strong> finding{counts?.total === 1 ? '' : 's'}, as they stand now.</>
      : <>Covered <strong>{counts?.total ?? 0}</strong> finding{counts?.total === 1 ? '' : 's'} when issued.</>;
  }

  return (
    <div className="space-y-lg p-md md:p-lg">
      <div>
        <Button
          variant="ghost" size="sm" className="mb-sm"
          onClick={async () => { if (await confirmLeave()) navigate('/reports'); }}
        >
          <ArrowLeft className="size-4" aria-hidden /> Reports
        </Button>
        <header className="flex flex-wrap items-start justify-between gap-md">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-xs">
              {isDraft ? <Badge variant="warning">Draft</Badge>
                : report.status === 'superseded' ? <Badge variant="muted">Superseded</Badge>
                  : <Badge variant="success">Issued #{report.number}</Badge>}
              <span className="text-caption text-muted-foreground">{reportKindLabel(report)}</span>
            </div>
            <h1 className="mt-xxs break-words text-page-title">{report.title}</h1>
            <p className="mt-xxs max-w-3xl text-body text-muted-foreground">{lead}</p>
            {report.superseded_by && (
              <p className="text-caption">
                Superseded by <Link className="text-info hover:underline" to={`/reports/${report.superseded_by.id}`}>report #{report.superseded_by.number}</Link>.
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap gap-xs">
            {isDraft && report.can_issue && (
              <Button variant={ready ? 'default' : 'outline'} onClick={() => void issue()}
                disabled={busy !== null || dirty || !!s.error || !!assetsBlock}
                title={dirty ? 'Save your changes first' : (assetsBlock ?? notReadyReason)}>
                {busy === 'issue' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Stamp className="size-4" aria-hidden />}
                Issue report
              </Button>
            )}
            {isDraft && report.can_edit && (
              <Button variant="ghost" className="text-destructive" onClick={() => void discard()} disabled={busy !== null}>
                <Trash2 className="size-4" aria-hidden /> Discard draft
              </Button>
            )}
            {report.status === 'issued' && (
              <Button variant="outline" onClick={() => revising.mutate()} disabled={busy !== null}>
                {busy === 'revise' && <Loader2 className="size-4 animate-spin" aria-hidden />} Revise
              </Button>
            )}
            {isDraft && dirty && (
              <p className="w-full text-right text-caption text-muted-foreground">
                Save your changes below before issuing.
              </p>
            )}
            {isDraft && !dirty && assetsBlock && (
              <p className="w-full text-right text-caption text-destructive">
                {missingRequiredAssets(template).length === 1 ? 'A required template file is' : 'Required template files are'} not installed — see Preview.
              </p>
            )}
          </div>
        </header>
      </div>

      {counts && (
        <div className="grid grid-cols-2 gap-y-md lg:grid-cols-4 lg:divide-x lg:divide-border">
          <PostureMeasure label="Findings in the report" value={counts.total}
            info="Confirmed, risk-accepted and remediated findings (an addendum: only new ones). False-positive systems are left out.">
            {counts.critical} critical · {counts.high} high · {counts.medium} medium · {counts.low} low · {counts.info} info
          </PostureMeasure>
          <PostureMeasure label="Still under investigation" value={s.under_investigation ?? 0}
            info="Open or retest findings. They are not in a report until they are confirmed, accepted or remediated."
            to={isDraft && s.under_investigation ? '/findings?status=open' : undefined} toLabel="Open findings">
            {isDraft ? 'Left out of this report' : 'Left out when issued'}
          </PostureMeasure>
          <PostureMeasure label="Missing report text" value={s.missing_text?.length ?? 0}
            info="Findings in the report with no description, impact or recommendation written.">
            {s.missing_text?.length ? 'Listed below' : 'Every finding has its text'}
          </PostureMeasure>
          <PostureMeasure label="Evidence images" value={s.images ?? 0}
            info="Images are opt-in: tick “In report” on an image attached to a finding's evidence or comments. Where one prints depends on the report's template: the Penetration test report prints an image its author placed in a written section (Insert image, in the report text editor) there and the rest under the finding's Evidence; the Remediation worklist prints only images placed in a recommendation; the Executive brief prints none. The line below says what this report does. WebP images cannot be placed in every format and are skipped.">
            {s.images ? (
              // What THIS report's template prints (`evidenceImagesLine`); a
              // report issued before that was measured claims nothing.
              <span className="break-words" data-testid="report-images-line">
                {evidenceImagesLine(s)}
                {s.images_skipped ? ` · ${s.images_skipped} skipped (WebP)` : ''}
              </span>
            ) : s.images_skipped ? `${s.images_skipped} skipped (WebP)`
              : 'Tick “In report” on a finding’s image to include it'}
          </PostureMeasure>
        </div>
      )}

      {isDraft && pendingProposals.length > 0 && (
        <PostureSection title="Proposals to review"
          description="Changes an agent or an AI draft proposed for findings in this report. The report prints the findings as they stand; review these first if they should be in it.">
          <ul className="space-y-xxs">
            {pendingProposals.map((m) => (
              <li key={m.id} className="flex min-w-0 flex-wrap items-baseline gap-x-xs text-body">
                <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                <Link to={`/findings/${m.id}#proposals`} className="min-w-0 truncate text-info hover:underline"
                  title={m.title}>{m.title}</Link>
                <span className="text-caption text-muted-foreground">{m.count} proposal{m.count === 1 ? '' : 's'}</span>
              </li>
            ))}
          </ul>
        </PostureSection>
      )}

      {isDraft && internalReferences.length > 0 && (
        <PostureSection title="Text the reader cannot follow"
          description="These sections name a BlueStick record. The report’s reader has never seen BlueStick: name a finding by its title and a system by its address.">
          <ul className="space-y-xxs">
            {internalReferences.map((m) => (
              <li key={m.id} className="flex min-w-0 flex-wrap items-baseline gap-x-xs text-body">
                <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                <Link to={`/findings/${m.id}?edit=report-text`} className="min-w-0 truncate text-info hover:underline"
                  title={m.title}>{m.title}</Link>
                <span className="min-w-0 break-words text-caption text-muted-foreground">
                  {m.fields.map((f) => `${REPORT_FIELD_LABELS[f.field] ?? f.field}: ${f.phrases.map((p) => `“${p}”`).join(', ')}`).join(' · ')}
                </span>
              </li>
            ))}
          </ul>
        </PostureSection>
      )}

      {isDraft && !ready && (
        <PostureSection title="Before issuing"
          description="Anything empty prints in the report as a highlighted TODO — search the preview for TODO to find each one.">
          {!!missingDetails.length && (
            <p className="mb-sm break-words text-body">
              <span className="mr-xs rounded bg-warning/20 px-xxs text-caption font-semibold text-foreground">TODO</span>
              Report details still empty: <span className="font-medium">{missingDetails.join(', ')}</span>
              {missingDetails.includes('project dates') && (
                <> (project dates are set in <Link to="/project-settings" className="text-info hover:underline">Project settings</Link>)</>
              )}.
            </p>
          )}
          {!!missingText.length && (
            <p className="mb-xs text-body">
              <span className="mr-xs rounded bg-warning/20 px-xxs text-caption font-semibold text-foreground">TODO</span>
              {missingText.length} finding{missingText.length === 1 ? ' has' : 's have'} report text still to write — open one to write or draft it:
            </p>
          )}
          <ul className="space-y-xxs">
            {(showAllMissing ? missingText : missingText.slice(0, MISSING_PREVIEW)).map((m) => (
              <li key={m.id} className="flex min-w-0 flex-wrap items-baseline gap-x-xs text-body">
                <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                <Link to={`/findings/${m.id}?edit=report-text`} className="min-w-0 truncate text-info hover:underline"
                  title={`${m.title} — open its report text to write or draft it`}>{m.title}</Link>
                <span className="text-caption text-muted-foreground">missing {m.missing.join(', ')}</span>
              </li>
            ))}
          </ul>
          {missingText.length > MISSING_PREVIEW && (
            <Button variant="ghost" size="sm" className="mt-xxs" onClick={() => setShowAllMissing((v) => !v)}>
              {showAllMissing ? 'Show fewer' : `Show all ${missingText.length}`}
            </Button>
          )}
        </PostureSection>
      )}

      {!!s.evidence_records && (
        // A notice, never a block: what the report prints as its proof, and
        // how much of it a person did not run.
        <p className="break-words text-caption text-muted-foreground" data-testid="report-evidence-notice">
          {evidenceRecordsNotice(s.evidence_records, s.agent_evidence_records)}
        </p>
      )}
      {!!s.evidence_records_not_printed && (
        <p className="break-words text-caption text-muted-foreground" data-testid="report-evidence-not-printed">
          {evidenceRecordsNotPrintedNotice(s.evidence_records_not_printed)}
        </p>
      )}

      {s.scope_external?.file && (
        <PostureSection title="Scope file — send it with the report"
          description={isDraft
            ? 'The scope is over this template’s limit, so the report summarises it and names this file instead of listing it. The file is today’s scope; issuing freezes it with the report.'
            : 'The report names this file instead of listing its scope. It is the frozen scope, so its SHA-256 is the one the report prints.'}>
          <p className="mb-xs break-words text-body">
            <strong>{s.scope_external.networks.toLocaleString()}</strong> network{s.scope_external.networks === 1 ? '' : 's'}
            {' '}and <strong>{s.scope_external.domains.toLocaleString()}</strong> domain{s.scope_external.domains === 1 ? '' : 's'}
            {s.scope_external.inline_max != null && <> (this template lists up to {s.scope_external.inline_max} networks)</>}.
            {' '}The client needs <span className="font-medium">{s.scope_external.file.name}</span> to see what was in scope.
          </p>
          <dl className="mb-sm grid gap-x-lg gap-y-xxs text-caption sm:grid-cols-[10rem_minmax(0,1fr)]">
            <dt className="text-muted-foreground">SHA-256</dt>
            <dd className="break-all font-mono">{s.scope_external.file.sha256}</dd>
          </dl>
          <Button size="sm" variant="outline" onClick={downloadScope} disabled={busy !== null}>
            <Download className="size-4" aria-hidden /> Download scope file (CSV)
          </Button>
        </PostureSection>
      )}

      {isDraft ? (
        <PostureSection title="Preview"
          description="Rendered from the live findings on the report worker, with the template chosen here. Previews expire after a day.">
          <div className="mb-sm space-y-xs">
            <div className="flex min-w-0 flex-wrap items-center gap-xs">
              <Label htmlFor="report-template" className="shrink-0">Template</Label>
              <Select value={report.template} onValueChange={changeTemplate}
                disabled={!editable || savingTemplate}>
                <SelectTrigger id="report-template" className="h-8 w-[18rem] max-w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {templates.filter(isClientTemplate).map((t) => <SelectItem key={t.name} value={t.name}>{t.title}</SelectItem>)}
                  {!templates.some((t) => t.name === report.template) && (
                    <SelectItem value={report.template}>{report.template}</SelectItem>
                  )}
                </SelectContent>
              </Select>
              {savingTemplate && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-label="Saving the template" />}
              {templates.length === 1 && (
                <span className="text-caption text-muted-foreground">The only template installed.</span>
              )}
            </div>
            {/* What the chosen template is for (v5.295.0) — with several
                installed, the title alone does not say which reader it serves. */}
            {template?.description && templates.length > 1 && (
              <p className="max-w-3xl break-words text-caption text-muted-foreground" data-testid="template-description">
                {template.description}
              </p>
            )}
            {assetsBlock ? (
              <div className="space-y-xxs">
                <p className="text-caption text-destructive">
                  {assetCountLabel(template)} — preview and issue are unavailable until {missingRequiredAssets(template).length === 1 ? 'it is' : 'they are'} installed:
                </p>
                <TemplateImages
                  template={template}
                  templateName={report.template}
                  showServerPaths={isAdmin}
                />
              </div>
            ) : template ? (
              <TemplateFilesLine template={template} />
            ) : templates.length > 0 && (
              <p className="break-words text-caption text-destructive">
                The template “{report.template}” is not installed on this server — choose another.
              </p>
            )}
          </div>
          {dirty && (
            <p className="mb-xs text-caption text-muted-foreground">
              The report details below have unsaved changes — save them to preview them.
            </p>
          )}
          <div className="flex flex-wrap gap-sm">
            {formats.map((fmt) => {
              const job = previews[fmt];
              const running = job && (job.status === 'queued' || job.status === 'processing');
              return (
                <div key={fmt} className="flex min-w-0 items-center gap-xs">
                  <Button variant="outline" size="sm" onClick={() => preview(fmt)}
                    disabled={!!running || !report.can_edit || dirty || !!assetsBlock}
                    title={dirty ? 'Save your changes first' : assetsBlock}>
                    {running && <Loader2 className="size-4 animate-spin" aria-hidden />}
                    {running ? `Rendering ${FORMAT_LABEL[fmt]}…` : `Preview ${FORMAT_LABEL[fmt]}`}
                  </Button>
                  {job?.status === 'completed' && (
                    <Button size="sm" variant="ghost" aria-label={`Download the ${FORMAT_LABEL[fmt]} preview`}
                      onClick={() => downloadPreview.mutate(job.id)}>
                      <Download className="size-4" aria-hidden /> Download
                    </Button>
                  )}
                  {job?.status === 'failed' && (
                    <span className="max-w-md truncate text-caption text-destructive" title={job.error_message ?? undefined}>
                      Failed: {safeFallback(job.error_message, 'see the report worker log')}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          {formats.includes('docx') && (
            <p className="mt-xs text-caption text-muted-foreground">
              For a PDF, open the Word report and export it (File › Save as PDF): the PDF keeps the Word design.
            </p>
          )}
        </PostureSection>
      ) : (
        <PostureSection title="Files" description="Rendered from the frozen report; the checksum identifies each file.">
          <div className="space-y-xs">
            <FileButtons report={report} />
            {report.render_status === 'failed' && (
              <div className="space-y-xxs">
                <p className="whitespace-pre-wrap break-words font-mono text-caption text-destructive">{report.render_error}</p>
                <Button size="sm" variant="outline" onClick={() => rerendering.mutate()} disabled={busy !== null}>
                  <RefreshCw className="size-4" aria-hidden /> Render again
                </Button>
              </div>
            )}
            <dl className="grid gap-x-lg gap-y-xxs text-caption sm:grid-cols-[10rem_minmax(0,1fr)]">
              <dt className="text-muted-foreground">Issued</dt>
              <dd>{when(report.issued_at)} by {safeFallback(report.issued_by_name, 'unknown')}</dd>
              <dt className="text-muted-foreground">Template</dt>
              <dd className="break-all">{template?.title ?? report.template} · <span className="font-mono">{report.template_fingerprint?.slice(0, 12) ?? '—'}</span></dd>
              <dt className="text-muted-foreground">Quarto</dt><dd>{safeFallback(report.quarto_version, '—')}</dd>
              {report.files.map((f) => (
                <React.Fragment key={f.format}>
                  <dt className="text-muted-foreground">{FORMAT_LABEL[f.format]} SHA-256</dt>
                  <dd className="break-all font-mono">{f.sha256}</dd>
                </React.Fragment>
              ))}
            </dl>
          </div>
        </PostureSection>
      )}

      <PostureSection title="Report details"
        description={editable ? 'Saved with this report only. The defaults for new reports are on the Reports page.' : 'As issued.'}>
        <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <div className="min-w-0 space-y-xxs">
            <Label htmlFor="report-title">Title</Label>
            <Input id="report-title" maxLength={255} value={form.title} disabled={!editable || saving}
              onChange={(e) => edit({ title: e.target.value })} />
          </div>

          <div className="min-w-0 space-y-xxs">
            <div className="flex flex-wrap items-end justify-between gap-xs">
              <Label htmlFor="report-summary">{report.kind === 'addendum' ? 'Summary of changes' : 'Executive summary'}</Label>
              {editable && (
                <Button type="button" variant="ghost" size="sm" onClick={() => setAiOpen(true)}>
                  <Sparkles className="size-4" aria-hidden /> Draft with AI…
                </Button>
              )}
            </div>
            <p className="text-caption text-muted-foreground">Markdown. Written for this report only.</p>
            <MarkdownField id="report-summary" label={report.kind === 'addendum' ? 'Summary of changes' : 'Executive summary'}
              rows={8} maxLength={65536} value={form.executive_summary} disabled={!editable || saving}
              onChange={(v) => edit({ executive_summary: v })} />
          </div>

          <EngagementSettingsFields idPrefix="report" value={form.settings} members={roster.members}
            membersStatus={roster.status} onRetryMembers={roster.retry} currentUser={currentUser}
            readOnly={!editable} disabled={saving} onChange={(settings) => edit({ settings })} />

          {editable && (
            <div className="flex flex-wrap items-center gap-xs">
              <Button type="submit" size="sm" disabled={saving || !dirty}>
                {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save
              </Button>
              <Button type="button" variant="ghost" size="sm" disabled={saving || !dirty} onClick={() => setEdits({})}>
                Undo changes
              </Button>
              {dirty && <span className="text-caption text-muted-foreground">Unsaved changes — save before previewing or issuing.</span>}
            </div>
          )}
        </form>
      </PostureSection>

      <AiDraftReportDialog open={aiOpen} onClose={() => setAiOpen(false)}
        useLabel="Use as the summary"
        onUse={(text) => edit({ executive_summary: text })} />
      {confirmDialog}
      {leaveDialog}
    </div>
  );
};

/** Keyed on the report: "Revise" navigates to the new draft's id, and the
 *  same component instance kept the previous report's previews, AI dialog
 *  and form until the new one loaded (review 2026-09-23). */
const ReportDetail: React.FC = () => {
  const { reportId } = useParams<{ reportId: string }>();
  return <ReportDetailView key={reportId} id={Number(reportId)} />;
};

export default ReportDetail;
