/**
 * /reports/:reportId — one client report, as the report WORKSPACE (owner,
 * 2026-10-14: the workflow "seems cumbersome and the design feels like bad
 * UX"; planning/PROPOSAL_report_workspace_2026-10-14.md).
 *
 * A header (title, kind, state) and, on a draft, an action bar that stays on
 * screen: save status, Discard changes, Save draft, Preview (Save & preview
 * with unsaved changes), and the readiness line.  Below, three sections in
 * the address (`?section=details|review|issue`), freely reachable:
 *
 * - Details: the title and template, then the report's details in the order
 *   the report reads (`DetailSections`): each follows the project's report
 *   defaults unless this report customises it.  An edit is pending until
 *   Save draft, across sections and tabs; nothing here writes the project's
 *   defaults except "Update project defaults…" and the project images panel.
 * - Review: what the report holds and what it still lacks (`ReportChecklist`).
 * - Preview & issue: previews and Issue (a draft); files and Revise (issued).
 *
 * An issued report opens on Preview & issue and its details read as issued.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Loader2, RefreshCw, Sparkles, Stamp, Trash2 } from 'lucide-react';

import {
  ClientReport,
  ClientReportFormat,
  ReportJob,
  ReportSummary,
  ReportTemplate,
  deleteClientReport,
  downloadClientReportScope,
  downloadReportJob,
  getClientReport,
  getClientReportInputs,
  getReportJob,
  issueClientReport,
  previewClientReport,
  rerenderClientReport,
  reviseClientReport,
  updateClientReport,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { useDiscardGuard, useLinkGuard } from '../hooks/useDiscardGuard';
import { useOpeningKey } from '../hooks/useOpeningKey';
import { reportTemplatesQuery } from '../components/reports/reportQueries';
import { useJobPolls } from '../hooks/useJobPoll';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRoster } from '../hooks/useProjectMembers';
import { useProjectRole } from '../hooks/useProjectRole';
import { useReportTextImages } from '../hooks/useReportTextImages';
import { pollEvery, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { NEED_SECTION } from '../utils/findingNeeds';
import { formatTimestamp } from '../utils/relativeTime';
import { REPORT_FIELD_LABELS } from '../utils/reportImages';
import { REPORT_FORMAT_LABEL, isClientTemplate } from '../utils/reportTemplates';
import { safeFallback, stickyFlushBelowChrome } from '../utils/uiStyles';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import DetailSections, { DETAIL_NAME, focusDetailField } from '../components/reports/workspace/DetailSections';
import ProjectImagesPanel from '../components/reports/workspace/ProjectImagesPanel';
import UpdateDefaultsDialog from '../components/reports/workspace/UpdateDefaultsDialog';
import {
  DETAIL_LABELS, DETAIL_SECTIONS, type DetailKey, customised, sectionOf, toSave,
} from '../components/reports/workspace/detailModel';
import { useDetailEdits } from '../components/reports/workspace/useDetailEdits';
import TemplateImages, {
  TemplateFilesLine, assetCountLabel, missingAssetsReason, missingRequiredAssets,
} from '../components/reports/TemplateImages';
import ReportChecklist from '../components/reports/ReportChecklist';
import OpenPreviewButton from '../components/reports/OpenPreviewButton';
import { REPORT_CHECKLIST_ANCHOR } from '../components/findings/ReportDraftBar';
import AiDraftReportDialog from '../components/AiDraftReportDialog';
import DownloadButton from '../components/DownloadButton';
import { DetailSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { InfoTip } from '../components/ui/info-tip';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import { FileButtons, RENDER_POLL_MS, isRendering, reportKindLabel } from '../components/reports/issuedReport';

const FORMAT_LABEL = REPORT_FORMAT_LABEL;
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

/** "N notes are printed as written before the image attached to them, under
 *  Evidence." — an image no section places is preceded by its note's text. */
export const evidenceNotesNotice = (count: number): string =>
  `${count.toLocaleString()} note${count === 1 ? ' is' : 's are'} printed as written before the `
  + `image${count === 1 ? '' : 's'} attached to ${count === 1 ? 'it' : 'them'}, under Evidence.`;

/** Images the report prints under their file's name, nobody having written a
 *  caption — the issue dialog's line; the page lists the findings. */
export const uncaptionedImagesSentence = (rows: ReadonlyArray<{ count: number }>): string => {
  const images = rows.reduce((n, r) => n + r.count, 0);
  return `${images.toLocaleString()} image${images === 1 ? ' has' : 's have'} no caption and `
    + `${images === 1 ? 'is' : 'are'} printed under ${images === 1 ? 'its' : 'their'} file name.`;
};

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
 * (review 2026-10-01 S2).  The server measures it; with no measurement (a
 * report issued before this, or a template that cannot be read) the line
 * claims nothing.
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
    parts.push(template && !template.trailing && trailing === 0
      ? `${inText.toLocaleString()} in the text`
      : `${inText.toLocaleString()} in the text, ${trailing.toLocaleString()} under Evidence`);
  }
  if (unprinted > 0) {
    parts.push(`${unprinted.toLocaleString()} ticked image${unprinted === 1 ? ' is' : 's are'} not printed${notPrintedWhy(s)}`);
  }
  return parts.join(' · ');
};

export type WorkspaceSection = 'details' | 'review' | 'issue';
const SECTIONS: Array<{ id: WorkspaceSection; label: string }> = [
  { id: 'details', label: 'Details' },
  { id: 'review', label: 'Review' },
  { id: 'issue', label: 'Preview & issue' },
];
const isSection = (v: string | null): v is WorkspaceSection => v === 'details' || v === 'review' || v === 'issue';

const NO_TEMPLATES: ReportTemplate[] = [];
/** How often a preview job still on the worker is asked about. */
const PREVIEW_POLL_MS = 2000;
const isRunning = (job: ReportJob): boolean => job.status === 'queued' || job.status === 'processing';
/** A finished preview against what it would render now.  "Inputs changed",
 *  not "what prints changed": a template may ignore a changed field. */
type Freshness = 'current' | 'out-of-date' | 'unknown' | 'checking';
const FRESHNESS_TEXT: Record<Freshness, string> = {
  current: 'current — nothing it was built from has changed.',
  'out-of-date': 'out of date — the findings, details, images or template changed after it was rendered. Preview again to see them.',
  unknown: 'could not verify whether this preview is current.',
  checking: 'checking whether it is current…',
};

const ReportDetailView: React.FC<{ id: number }> = ({ id }) => {
  const toast = useToast();
  // Revise is the analyst's, Render again the project admin's (the routes'
  // floors): write controls are hidden, not refused (third review
  // 2026-10-15, H5 — an auditor pressed Revise and got a 403).
  const { canWrite, isProjectAdmin } = useProjectRole();
  const navigate = useNavigate();
  const [confirmDialog, confirm] = useConfirm();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const currentUser = user ? { id: user.id, name: user.full_name || user.username } : null;
  const queryClient = useQueryClient();
  const roster = useProjectRoster();
  const [aiOpen, setAiOpen] = useState(false);
  const [promoteOpen, setPromoteOpen] = useState(false);
  // Each opening starts with every detail ticked (review 2026-10-14, R13: an
  // untick survived Cancel).
  const promoteKey = useOpeningKey(promoteOpen);

  const projectId = useProjectId();
  const reportKey = ['getClientReport', projectId, id];
  const query = useQuery({
    queryKey: reportKey,
    queryFn: ({ signal }) => getClientReport(projectId, id, signal),
    ...pollEvery((q) => (isRendering(q.state.data) ? RENDER_POLL_MS : null)),
  });
  const report: ClientReport | null = query.data ?? null;
  const reportId = report?.id ?? id;
  const error = queryErrorText(query.error, 'Could not load the report.');
  const putReport = (next: ClientReport) => queryClient.setQueryData(reportKey, next);

  const templatesQuery = useQuery({
    ...reportTemplatesQuery(projectId),
  });
  const templates: ReportTemplate[] = templatesQuery.data ?? NO_TEMPLATES;

  const isDraft = report?.status === 'draft';
  const editable = !!report && isDraft && report.can_edit;

  // ---- the section in the address ---------------------------------------
  const [params, setParams] = useSearchParams();
  const asked = params.get('section');
  const section: WorkspaceSection = isSection(asked) ? asked : (report && !isDraft ? 'issue' : 'details');
  const goTo = (next: WorkspaceSection) => setParams((prev) => {
    const p = new URLSearchParams(prev);
    p.set('section', next);
    return p;
  });
  // "Back to the report" from a finding the checklist opened
  // (`#report-checklist`) lands on Review.
  const { hash } = useLocation();
  const loaded = report != null;
  useEffect(() => {
    if (!loaded || hash !== `#${REPORT_CHECKLIST_ANCHOR}`) return;
    if (section !== 'review') {
      // The hash stays, so the checklist is scrolled to once it is shown.
      navigate({ search: '?section=review', hash: REPORT_CHECKLIST_ANCHOR }, { replace: true });
    } else {
      document.getElementById(REPORT_CHECKLIST_ANCHOR)?.scrollIntoView?.({ block: 'start' });
    }
  }, [loaded, hash, section, navigate]);

  // ---- pending edits (held here: they survive switching sections) --------
  const base = useMemo(() => ({
    saved: (editable ? report?.settings : report?.effective) ?? {},
    followed: editable ? (report?.followed ?? null) : null,
    sources: editable ? (report?.followed_from ?? null) : null,
    ownOnly: (report?.kind === 'addendum' ? ['executive_summary'] : []) as DetailKey[],
  }), [editable, report]);
  const edits = useDetailEdits(base);
  const [title, setTitle] = useState<string | null>(null);
  const [template, setTemplate] = useState<string | null>(null);
  const titleChanged = report != null && title !== null && title !== report.title;
  const templateChanged = report != null && template !== null && template !== report.template;
  const dirty = edits.dirty || titleChanged || templateChanged;
  const changedParts = [
    ...(titleChanged || templateChanged ? ['Report'] : []),
    ...DETAIL_SECTIONS.filter((s) => edits.dirtySections.includes(s.id))
      .map((s) => (s.id === 'summary' && report?.kind === 'addendum' ? 'Summary of changes' : s.title)),
  ];
  const discardAll = () => { edits.discardAll(); setTitle(null); setTemplate(null); };
  // The action bar's Discard asks first, as leaving does: what is typed is
  // lost (walkthrough 2026-10-14 — one press beside Save draft threw it away).
  const confirmDiscardAll = async () => {
    const ok = await confirm({
      title: 'Discard your unsaved changes?',
      body: `What you changed in ${changedParts.join(', ')} goes back to the last save.`,
      severity: 'danger', confirmLabel: 'Discard changes',
    });
    if (ok) discardAll();
  };

  const { confirmLeave, confirmEl: leaveDialog } = useDiscardGuard(
    () => dirty,
    'This draft has unsaved changes. Leave anyway?',
  );
  const guardLinks = useLinkGuard(confirmLeave);

  // The project's report images: the system description's picker, paste and
  // preview; managed in the project images panel.
  const reportImages = useReportTextImages({ enabled: editable });

  // ---- previews -----------------------------------------------------------
  const [started, setStarted] = useState<Partial<Record<ClientReportFormat, ReportJob>>>({});
  const startedJobs = Object.entries(started) as Array<[ClientReportFormat, ReportJob]>;
  const followedJobs = useJobPolls({
    jobs: startedJobs.map(([, job]) => job),
    queryKey: (job) => ['getReportJob', projectId, job.id],
    queryFn: (job, signal) => getReportJob(projectId, job.id, signal),
    interval: PREVIEW_POLL_MS,
    isDone: (job) => !isRunning(job),
  });
  const previews: Partial<Record<ClientReportFormat, ReportJob>> = Object.fromEntries(
    startedJobs.map(([fmt, job], i) => [fmt, followedJobs[i]?.job ?? job]),
  );
  const previewStale = followedJobs.find((poll) => poll.running && poll.error != null)?.error;
  // A finished preview is said once per job.
  const readyPreviews = (Object.entries(previews) as Array<[ClientReportFormat, ReportJob]>)
    .filter(([, job]) => job.status === 'completed')
    .map(([fmt, job]) => `${fmt}:${job.id}`)
    .join(',');
  const announcedPreviews = useRef(new Set<string>());
  useEffect(() => {
    for (const ready of readyPreviews ? readyPreviews.split(',') : []) {
      if (announcedPreviews.current.has(ready)) continue;
      announcedPreviews.current.add(ready);
      const fmt = ready.split(':')[0] as ClientReportFormat;
      toast.success(`The ${FORMAT_LABEL[fmt]} preview is ready.`, { id: `preview-ready-${ready}` });
    }
  }, [readyPreviews, toast]);

  // What a preview would render NOW, compared with what each finished
  // preview rendered (the proposal's "Preview freshness").  Its own
  // lifecycle, on purpose: the findings, the project's defaults, its images
  // and the template change on other pages and in other tabs, so it is read
  // again on return to the window, as well as on entering Preview & issue,
  // after a save, when a preview finishes and just before the issue's
  // confirmation.
  const inputs = useQuery({
    queryKey: ['getClientReportInputs', projectId, reportId],
    queryFn: ({ signal }) => getClientReportInputs(projectId, reportId, signal),
    enabled: isDraft && section === 'issue' && readyPreviews !== '',
    refetchOnWindowFocus: true,
  });
  // A preview that finishes asks again — but not the FIRST one, whose
  // finishing turns the query on (and so reads it once already; review
  // 2026-10-14, R2: it was read twice).
  const previousReady = useRef(readyPreviews);
  useEffect(() => {
    const before = previousReady.current;
    previousReady.current = readyPreviews;
    if (readyPreviews && before && readyPreviews !== before) {
      void queryClient.invalidateQueries({ queryKey: ['getClientReportInputs', projectId, reportId] });
    }
  }, [readyPreviews, queryClient, projectId, reportId]);
  /** A finished preview against the inputs now; never "current" unless both
   *  fingerprints are known and equal. */
  const freshnessOf = (job: ReportJob, now: { fingerprint: string | null } | undefined, failed: boolean): Freshness => {
    if (failed) return 'unknown';
    if (now === undefined) return 'checking';
    if (!job.inputs_fingerprint || !now.fingerprint) return 'unknown';
    return job.inputs_fingerprint === now.fingerprint ? 'current' : 'out-of-date';
  };
  const finishedPreviews = (Object.entries(previews) as Array<[ClientReportFormat, ReportJob]>)
    .filter(([, job]) => job.status === 'completed');

  // An issue's render, announced once when this page SAW it running.
  const sawRendering = useRef(false);
  const renderStatus = report?.status !== 'draft' ? report?.render_status ?? null : null;
  useEffect(() => {
    if (renderStatus === 'pending') { sawRendering.current = true; return; }
    if (!sawRendering.current || !report) return;
    sawRendering.current = false;
    if (renderStatus === 'done') {
      toast.success(`Report #${report.number} is issued and its files are ready to download.`,
        { id: `report-files-ready-${report.id}` });
    } else if (renderStatus === 'failed') {
      toast.error(`Report #${report.number} is issued, but rendering its files failed — see Files.`,
        { id: `report-files-failed-${report.id}` });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderStatus]);

  const startPreview = useMutation({
    mutationFn: (fmt: ClientReportFormat) => previewClientReport(projectId, reportId, fmt),
    onSuccess: (job, fmt) => setStarted((prev) => ({ ...prev, [fmt]: job })),
    onError: (err) => toast.error(formatApiError(err, 'Could not start the preview.')),
  });

  // ---- save draft ---------------------------------------------------------
  const saveDraft = useMutation({
    mutationFn: () => {
      return updateClientReport(projectId, reportId, {
        ...(titleChanged ? { title: (title ?? '').trim() } : {}),
        ...(templateChanged ? { template: template ?? undefined } : {}),
        settings: toSave(edits.ctx),
      });
    },
    onSuccess: (updated) => {
      putReport(updated);
      if (templateChanged) setStarted({});
      discardAll();
      void queryClient.invalidateQueries({ queryKey: ['listClientReports', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['getClientReportInputs', projectId, reportId] });
      toast.success('Draft saved.');
    },
    // A failed save keeps every edit, and previews nothing.
    onError: (err) => toast.error(formatApiError(err, 'Could not save the draft.')),
  });
  const saving = saveDraft.isPending;
  const save = async (): Promise<boolean> => {
    if (titleChanged && !(title ?? '').trim()) { toast.error('A report needs a title.'); return false; }
    try {
      await saveDraft.mutateAsync();
      return true;
    } catch {
      return false;
    }
  };
  const preview = async (fmt: ClientReportFormat) => {
    if (!report) return;
    if (dirty && !(await save())) return;
    startPreview.mutate(fmt);
    if (section !== 'issue') goTo('issue');
  };

  // ---- issue, revise, render again, discard -------------------------------
  const issuing = useMutation({
    mutationFn: () => issueClientReport(projectId, reportId),
    onSuccess: (issued) => {
      putReport(issued);
      discardAll();
      void queryClient.invalidateQueries({ queryKey: ['listClientReports', projectId] });
      toast.success(`Issued as report #${issued.number}. Rendering its files…`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not issue the report.')),
  });
  const issue = async () => {
    if (!report) return;
    const s = report.summary;
    const issueTemplate = templates.find((t) => t.name === report.template);
    // Checked again now: something may have changed since the page last asked.
    let previewsNow: Freshness[] = [];
    if (finishedPreviews.length) {
      const now = await inputs.refetch();
      previewsNow = finishedPreviews.map(([, job]) => freshnessOf(job, now.data, now.isError));
    }
    const ok = await confirm({
      title: 'Issue this report?',
      body: (
        <>
          <p>
            It takes the project&apos;s next report number and is frozen: what it says about each finding
            is kept as it is now, and it can no longer be edited or deleted. A correction is a revision
            that supersedes it.
          </p>
          <p className="mt-xs">
            It is built from the findings and details as they stand at this moment, not from an earlier preview.
          </p>
          {previewsNow.includes('out-of-date') && (
            <p className="mt-xs" data-testid="issue-preview-out-of-date">
              Your preview is out of date: the findings, details, images or template changed after it was rendered.
            </p>
          )}
          {!previewsNow.includes('out-of-date') && previewsNow.some((f) => f === 'unknown' || f === 'checking') && (
            <p className="mt-xs" data-testid="issue-preview-unverified">
              Whether your preview is still current could not be verified.
            </p>
          )}
          <p className="mt-xs break-words" data-testid="issue-template">
            It is issued with the <strong>{issueTemplate?.title ?? report.template}</strong> template
            {issueTemplate?.formats.length
              ? <>, as {issueTemplate.formats.map((f) => FORMAT_LABEL[f]).join(', ')}</> : null}.
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
          {!!s?.uncaptioned_images?.length && (
            <p className="mt-xs">{uncaptionedImagesSentence(s.uncaptioned_images)}</p>
          )}
          {!!s?.agent_images && (
            <p className="mt-xs">{s.agent_images} image{s.agent_images === 1 ? ' comes' : 's come'} from notes an agent wrote.</p>
          )}
          {!!s?.images_not_printed && (
            <p className="mt-xs break-words">Evidence images: {evidenceImagesLine(s)}.</p>
          )}
          {!!s?.evidence_records && (
            <p className="mt-xs">{evidenceRecordsNotice(s.evidence_records, s.agent_evidence_records)}</p>
          )}
          {!!s?.evidence_records_not_printed && (
            <p className="mt-xs">{evidenceRecordsNotPrintedNotice(s.evidence_records_not_printed)}</p>
          )}
          {!!s?.evidence_notes && (
            <p className="mt-xs">{evidenceNotesNotice(s.evidence_notes)}</p>
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
    mutationFn: () => reviseClientReport(projectId, reportId),
    onSuccess: (draft) => navigate(`/reports/${draft.id}`),
    onError: (err) => toast.error(formatApiError(err, 'Could not start a revision.')),
  });
  const rerendering = useMutation({
    mutationFn: () => rerenderClientReport(projectId, reportId),
    onSuccess: (next) => { putReport(next); },
    onError: (err) => toast.error(formatApiError(err, 'Could not restart the rendering.')),
  });
  const discarding = useMutation({
    mutationFn: () => deleteClientReport(projectId, reportId),
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
  const busy: 'issue' | 'revise' | 'render' | 'delete' | null = issuing.isPending ? 'issue'
    : revising.isPending ? 'revise' : rerendering.isPending ? 'render'
      : discarding.isPending || discarding.isSuccess ? 'delete' : null;

  // A checklist line about a detail opens its section on Details and puts
  // the caret in its field.
  const openDetail = (key: string) => {
    if (!(key in DETAIL_LABELS)) return;
    const detail = key as DetailKey;
    goTo('details');
    edits.edit(sectionOf(detail).id);
    // Once the section has rendered open.
    window.setTimeout(() => { focusDetailField('report', detail); }, 0);
  };

  if (!report && !error) return <DetailSkeleton />;
  if (!report) {
    return (
      <div className="p-md md:p-lg">
        <Button variant="ghost" size="sm" onClick={() => navigate('/reports')}><ArrowLeft className="size-4" aria-hidden /> Reports</Button>
        <p className="mt-md text-destructive">{error}</p>
        <Button variant="outline" size="sm" className="mt-sm" onClick={() => void query.refetch()}><RefreshCw className="size-4" aria-hidden /> Retry</Button>
      </div>
    );
  }

  const s = report.summary ?? {};
  const scopeFile = s.scope_external?.file;
  const counts = s.counts;
  const tpl = templates.find((t) => t.name === (template ?? report.template));
  const formats: ClientReportFormat[] = tpl?.formats ?? ['html', 'docx'];
  const assetsBlock = missingAssetsReason(tpl);
  const missingText = s.missing_text ?? [];
  const missingDetails = s.missing_details ?? [];
  const toFix = missingText.length + missingDetails.length;
  const warnings = (s.pending_proposals?.length ?? 0) + (s.uncaptioned_images?.length ?? 0)
    + (s.internal_references?.length ?? 0) + (s.unresolved_images?.length ?? 0);
  const ready = !toFix;
  // What refuses the issue — the server refuses these too.  The ONE list:
  // Review lists it and the Issue button is held by it (review 2026-10-14,
  // R10: the button had its own copy of the conditions).
  const blockers = [
    ...(dirty ? ['Unsaved changes: save the draft first.'] : []),
    ...(assetsBlock ? [`${assetsBlock}.`] : []),
    ...(s.error ? [`The report could not be built: ${s.error}`] : []),
  ];
  const anyRunning = Object.values(previews).some((j) => j && isRunning(j));
  const summaryLabel = report.kind === 'addendum' ? 'Summary of changes' : 'Executive summary';
  // What "Update project defaults…" offers: the details this report has as
  // its own, saved or pending (never an addendum's summary).
  const customisedKeys = DETAIL_SECTIONS.flatMap((x) => x.keys).filter((k) => customised(edits.ctx, k));

  let lead: React.ReactNode;
  if (s.error) {
    lead = <span className="text-destructive">{s.error}</span>;
  } else if (report.kind === 'addendum' && s.delta) {
    const reRated = s.delta.findings_with_changed_severity ?? 0;
    lead = <>Compared with report #{report.baseline?.number}: <strong>{s.delta.new_findings}</strong> new finding{s.delta.new_findings === 1 ? '' : 's'}, <strong>{s.delta.findings_with_new_endpoints}</strong> reported finding{s.delta.findings_with_new_endpoints === 1 ? '' : 's'} on further systems, <strong>{s.delta.withdrawn}</strong> withdrawal{s.delta.withdrawn === 1 ? '' : 's'}{reRated > 0 && <>, <strong>{reRated}</strong> reported finding{reRated === 1 ? '' : 's'} with a changed severity</>}.</>;
  } else {
    lead = isDraft
      ? <>Covers <strong>{counts?.total ?? 0}</strong> finding{counts?.total === 1 ? '' : 's'}, as they stand now.</>
      : <>Covered <strong>{counts?.total ?? 0}</strong> finding{counts?.total === 1 ? '' : 's'} when issued.</>;
  }

  // ---- Details -------------------------------------------------------------
  // What the template is for (with several to choose from) and how its
  // files stand are on the (i); what BLOCKS preview and issue is on the page.
  const onlyTemplate = editable && templates.length === 1;
  const describeTemplate = !!tpl?.description && templates.length > 1;
  const filesLine = !assetsBlock && !!tpl?.assets?.length;
  const templateTip = (onlyTemplate || describeTemplate || filesLine) ? (
    <div className="space-y-xxs">
      {describeTemplate && <p className="break-words" data-testid="template-description">{tpl?.description}</p>}
      {onlyTemplate && <p>The only template installed.</p>}
      {filesLine && <TemplateFilesLine template={tpl} />}
    </div>
  ) : null;
  const details = (
    <div className="space-y-lg">
      <section className="min-w-0 space-y-sm" aria-labelledby="report-section-report" data-testid="report-section-report">
        <h3 id="report-section-report" className="text-subheading font-medium">Report</h3>
        <div className="grid gap-md md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="min-w-0 space-y-xxs">
            <Label htmlFor="report-title" className={DETAIL_NAME}>Title</Label>
            {editable ? (
              <Input id="report-title" maxLength={255} value={title ?? report.title} disabled={saving}
                onChange={(e) => setTitle(e.target.value)} />
            ) : <p className="break-words text-body font-medium">{report.title}</p>}
          </div>
          <div className="min-w-0 space-y-xxs" data-testid="report-template-picker">
            <span className="flex items-center gap-xxs">
              {editable
                ? <Label htmlFor="report-template" className={DETAIL_NAME}>Template</Label>
                : <span className={DETAIL_NAME}>Template</span>}
              {templateTip && <InfoTip label="About this template" text={templateTip} />}
            </span>
            {editable ? (
              <Select value={template ?? report.template} onValueChange={setTemplate} disabled={saving}>
                <SelectTrigger id="report-template"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {templates.filter(isClientTemplate).map((t) => <SelectItem key={t.name} value={t.name}>{t.title}</SelectItem>)}
                  {!templates.some((t) => t.name === report.template) && (
                    <SelectItem value={report.template}>{report.template}</SelectItem>
                  )}
                </SelectContent>
              </Select>
            ) : <p className="break-words text-body">{tpl?.title ?? report.template}</p>}
          </div>
        </div>
        {assetsBlock ? (
          <div className="space-y-xxs">
            <p className="text-caption text-destructive">
              {assetCountLabel(tpl)} — preview and issue are unavailable until {missingRequiredAssets(tpl).length === 1 ? 'it is' : 'they are'} installed:
            </p>
            <TemplateImages template={tpl} templateName={template ?? report.template} showServerPaths={isAdmin} />
          </div>
        ) : !tpl && templates.length > 0 && (
          <p className="break-words text-caption text-destructive">
            The template “{template ?? report.template}” is not installed on this server{editable ? ' — choose another' : ''}.
          </p>
        )}
      </section>

      <DetailSections edits={edits} canEdit={editable} idPrefix="report" busy={saving}
        summaryLabel={summaryLabel}
        summaryAction={editable ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => setAiOpen(true)}>
            <Sparkles className="size-4" aria-hidden /> Draft with AI…
          </Button>
        ) : undefined}
        images={editable ? reportImages.markdown : undefined}
        scopeAction={editable ? <ProjectImagesPanel state={reportImages} idPrefix="report" /> : undefined}
        members={roster.members} membersStatus={roster.status} onRetryMembers={roster.retry}
        currentUser={currentUser} />

      {editable && customisedKeys.length > 0 && (
        <div className="flex flex-wrap items-center gap-xs border-t border-border pt-md">
          <Button type="button" variant="outline" size="sm" onClick={() => setPromoteOpen(true)} disabled={saving}>
            Update project defaults…
          </Button>
          <span className="text-caption text-muted-foreground">
            Make this report’s customised details the project’s, for every draft that follows them.
          </span>
        </div>
      )}
    </div>
  );

  // ---- Review --------------------------------------------------------------
  const review = (
    <div className="space-y-lg">
      {counts && (
        <div className="grid grid-cols-1 gap-y-md md:grid-cols-3 md:divide-x md:divide-border" data-testid="report-measures">
          <PostureMeasure label="Findings in the report" value={counts.total}
            info="Confirmed, risk-accepted and remediated findings (an addendum: only new ones). False-positive systems are left out.">
            {counts.critical} critical · {counts.high} high · {counts.medium} medium · {counts.low} low · {counts.info} info
          </PostureMeasure>
          <PostureMeasure label="Still under investigation" value={s.under_investigation ?? 0}
            info="Open findings. They are not in a report until they are confirmed, accepted or remediated."
            to={isDraft && s.under_investigation ? '/findings?status=open' : undefined} toLabel="Open findings">
            {isDraft ? 'Left out of this report' : 'Left out when issued'}
          </PostureMeasure>
          <PostureMeasure label="Evidence images" value={s.images ?? 0}
            info="Images are opt-in: tick “In report” on an image attached to a finding's evidence or comments. Where one prints depends on the report's template. WebP images cannot be placed in every format and are skipped.">
            {s.images ? (
              <span className="break-words" data-testid="report-images-line">
                {evidenceImagesLine(s)}
                {s.images_skipped ? ` · ${s.images_skipped} skipped (WebP)` : ''}
              </span>
            ) : s.images_skipped ? `${s.images_skipped} skipped (WebP)`
              : 'Tick “In report” on a finding’s image to include it'}
          </PostureMeasure>
        </div>
      )}
      {isDraft && (
        // What really refuses the issue, apart from what prints as TODO or is
        // a warning (the checklist below): no new blocking rule (proposal §4.2).
        // A section like "Before issuing" under it (walkthrough 2026-10-14:
        // a plain heading read as a lesser rank).
        <div data-testid="review-blockers">
          <PostureSection title="What blocks issuing">
            {blockers.length ? (
              <ul className="list-disc space-y-xxs pl-lg text-body text-destructive">
                {blockers.map((b) => <li key={b} className="break-words">{b}</li>)}
              </ul>
            ) : (
              <p className="text-body text-muted-foreground">Nothing blocks issuing. What is left below prints as TODO or is a warning.</p>
            )}
          </PostureSection>
        </div>
      )}
      {isDraft && (
        <ReportChecklist reportId={reportId} summary={s} isAdmin={isAdmin} canEdit={report.can_edit}
          onDetail={openDetail} />
      )}
      {isDraft && !toFix && !warnings && (
        <p className="text-body text-muted-foreground" data-testid="review-clear">Nothing left to fix before issuing.</p>
      )}
      {!!s.evidence_records && (
        <p className="break-words text-caption text-muted-foreground" data-testid="report-evidence-notice">
          {evidenceRecordsNotice(s.evidence_records, s.agent_evidence_records)}
        </p>
      )}
      {!!s.evidence_records_not_printed && (
        <p className="break-words text-caption text-muted-foreground" data-testid="report-evidence-not-printed">
          {evidenceRecordsNotPrintedNotice(s.evidence_records_not_printed)}
        </p>
      )}
      {!!s.evidence_notes && !s.evidence_note_findings?.length && (
        <p className="break-words text-caption text-muted-foreground" data-testid="report-evidence-notes">
          {evidenceNotesNotice(s.evidence_notes)}
        </p>
      )}
      {!isDraft && !!s.evidence_note_findings?.length && (
        <PostureSection title="Notes printed in this report"
          description="Each was printed word for word under Evidence, before the image attached to it.">
          <ul className="space-y-xs" data-testid="report-evidence-notes">
            {s.evidence_note_findings.map((m) => (
              <li key={m.id} className="min-w-0 text-body">
                <span className="flex min-w-0 flex-wrap items-baseline gap-x-xs">
                  <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                  <Link to={`/findings/${m.id}#${NEED_SECTION.uncaptioned_images}`}
                    className="min-w-0 truncate text-info hover:underline" title={m.title}>{m.title}</Link>
                </span>
                <ul className="mt-xxs space-y-xxs">
                  {m.notes.map((note, i) => (
                    <li key={i} className="line-clamp-3 whitespace-pre-wrap border-l-2 border-border pl-xs text-caption text-muted-foreground [overflow-wrap:anywhere]">
                      {note}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </PostureSection>
      )}
      {s.scope_external && scopeFile && (
        <PostureSection title="Scope file — send it with the report"
          description={isDraft
            ? 'The scope is over this template’s limit, so the report summarises it and names this file instead of listing it. The file is today’s scope; issuing freezes it with the report.'
            : 'The report names this file instead of listing its scope. It is the frozen scope, so its SHA-256 is the one the report prints.'}>
          <p className="mb-xs break-words text-body">
            <strong>{s.scope_external.networks.toLocaleString()}</strong> network{s.scope_external.networks === 1 ? '' : 's'}
            {' '}and <strong>{s.scope_external.domains.toLocaleString()}</strong> domain{s.scope_external.domains === 1 ? '' : 's'}
            {s.scope_external.inline_max != null && <> (this template lists up to {s.scope_external.inline_max} networks)</>}.
            {' '}The client needs <span className="font-medium">{scopeFile.name}</span> to see what was in scope.
          </p>
          <dl className="mb-sm grid gap-x-lg gap-y-xxs text-caption sm:grid-cols-[10rem_minmax(0,1fr)]">
            <dt className="text-muted-foreground">SHA-256</dt>
            <dd className="break-all font-mono">{scopeFile.sha256}</dd>
          </dl>
          <DownloadButton size="sm" variant="outline" label="Download scope file (CSV)"
            filename={scopeFile.name} errorText="Could not download the scope file." disabled={busy !== null}
            saveFn={() => downloadClientReportScope(projectId, reportId, scopeFile.name)} />
        </PostureSection>
      )}
    </div>
  );

  // ---- Preview & issue -----------------------------------------------------
  const issuePanel = isDraft ? (
    <div className="space-y-lg">
      <PostureSection title="Preview"
        description="Rendered from the live findings and this draft’s saved details, with its template. Previews expire after a day.">
        {dirty && (
          <p className="mb-xs text-caption text-warning" data-testid="preview-pending">
            A preview shows the saved draft, not your unsaved changes — “Save & preview” saves them first.
          </p>
        )}
        <div className="flex flex-wrap gap-sm">
          {formats.map((fmt) => {
            const job = previews[fmt];
            const running = job != null && isRunning(job);
            return (
              <div key={fmt} className="flex min-w-0 items-center gap-xs">
                {/* A write (it renders a job): hidden for whoever may not
                    edit the draft, never disabled (second review 2026-10-14). */}
                {report.can_edit && (
                  <Button variant="outline" size="sm" onClick={() => void preview(fmt)}
                    disabled={!!running || saving || !!assetsBlock}
                    title={assetsBlock}>
                    {running && <Loader2 className="size-4 animate-spin" aria-hidden />}
                    {running ? `Rendering ${FORMAT_LABEL[fmt]}…` : `${dirty ? 'Save & preview' : 'Preview'} ${FORMAT_LABEL[fmt]}`}
                  </Button>
                )}
                {job?.status === 'completed' && fmt === 'html' && (
                  <>
                    <OpenPreviewButton key={`open-${job.id}`} size="sm" projectId={projectId} jobId={job.id}
                      label="Open HTML preview" />
                    <DownloadButton key={job.id} size="sm" variant="ghost" label="Download"
                      name="Download HTML preview" errorText="Could not download the preview."
                      saveFn={() => downloadReportJob(projectId, job.id)} />
                  </>
                )}
                {job?.status === 'completed' && fmt !== 'html' && (
                  <DownloadButton key={job.id} size="sm" label={`Download ${FORMAT_LABEL[fmt]} preview`}
                    errorText="Could not download the preview."
                    saveFn={() => downloadReportJob(projectId, job.id)} />
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
        {finishedPreviews.length > 0 && (
          <ul className="mt-xs space-y-xxs text-caption" data-testid="preview-freshness">
            {finishedPreviews.map(([fmt, job]) => {
              const freshness = freshnessOf(job, inputs.data, inputs.isError);
              return (
                <li key={fmt} data-testid={`preview-freshness-${fmt}`} data-freshness={freshness}
                  className={freshness === 'current' ? 'text-muted-foreground' : 'text-warning'}>
                  {FORMAT_LABEL[fmt]} preview: {FRESHNESS_TEXT[freshness]}
                  {freshness === 'unknown' && inputs.isError && (
                    <Button variant="link" size="sm" className="ml-xs h-auto p-0" onClick={() => void inputs.refetch()}>
                      Check again
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {previewStale != null && (
          <p role="status" className="mt-xs break-words text-caption text-warning">
            Status may be stale — {formatApiError(previewStale, 'Could not refresh the status.')}
          </p>
        )}
        {formats.includes('docx') && (
          <p className="mt-xs text-caption text-muted-foreground">
            For a PDF, open the Word report and export it (File › Save as PDF): the PDF keeps the Word design.
          </p>
        )}
      </PostureSection>

      <PostureSection title="Issue"
        description="Issuing numbers the report and freezes it: it can no longer be edited or deleted. A correction is a revision.">
        <div className="flex flex-wrap items-center gap-xs">
          {report.can_issue && (
            <Button variant={ready ? 'default' : 'outline'} onClick={() => void issue()}
              disabled={busy !== null || blockers.length > 0}
              title={blockers[0]}>
              {busy === 'issue' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Stamp className="size-4" aria-hidden />}
              Issue report
            </Button>
          )}
          {report.can_discard && (
            <Button variant="ghost" className="text-destructive" onClick={() => void discard()} disabled={busy !== null}>
              <Trash2 className="size-4" aria-hidden /> Discard draft
            </Button>
          )}
        </div>
        {report.can_issue && dirty && (
          <p className="mt-xs text-caption text-muted-foreground">Save your changes before issuing.</p>
        )}
        {report.can_issue && !dirty && !ready && (
          <p className="mt-xs text-caption text-muted-foreground">
            Parts of this report still print as TODO — see <button type="button" className="text-info hover:underline" onClick={() => goTo('review')}>Review</button>.
          </p>
        )}
        {!report.can_issue && (
          <p className="mt-xs text-caption text-muted-foreground">A project administrator issues the report.</p>
        )}
      </PostureSection>
    </div>
  ) : (
    <PostureSection title="Files" description="Rendered from the frozen report; the checksum identifies each file.">
      <div className="space-y-xs">
        <FileButtons report={report} />
        {report.render_status === 'failed' && (
          <div className="space-y-xxs">
            <p className="whitespace-pre-wrap break-words font-mono text-caption text-destructive">{report.render_error}</p>
            {isProjectAdmin && (
              <Button size="sm" variant="outline" onClick={() => rerendering.mutate()} disabled={busy !== null}>
                <RefreshCw className="size-4" aria-hidden /> Render again
              </Button>
            )}
          </div>
        )}
        <dl className="grid gap-x-lg gap-y-xxs text-caption sm:grid-cols-[10rem_minmax(0,1fr)]">
          <dt className="text-muted-foreground">Issued</dt>
          <dd>{when(report.issued_at)} by {safeFallback(report.issued_by_name, 'unknown')}</dd>
          <dt className="text-muted-foreground">Template</dt>
          <dd className="break-all">{tpl?.title ?? report.template} · <span className="font-mono">{report.template_fingerprint?.slice(0, 12) ?? '—'}</span></dd>
          <dt className="text-muted-foreground">Quarto</dt><dd>{safeFallback(report.quarto_version, '—')}</dd>
          {report.files.map((f) => (
            <React.Fragment key={f.format}>
              <dt className="text-muted-foreground">{FORMAT_LABEL[f.format]} SHA-256</dt>
              <dd className="break-all font-mono">{f.sha256}</dd>
            </React.Fragment>
          ))}
        </dl>
        {report.status === 'issued' && canWrite && (
          <Button variant="outline" size="sm" onClick={() => revising.mutate()} disabled={busy !== null}>
            {busy === 'revise' && <Loader2 className="size-4 animate-spin" aria-hidden />} Revise
          </Button>
        )}
      </div>
    </PostureSection>
  );

  return (
    <div className="space-y-md p-md md:p-lg" onClickCapture={guardLinks}>
      <div>
        <Button variant="ghost" size="sm" className="mb-sm"
          onClick={async () => { if (await confirmLeave()) navigate('/reports'); }}>
          <ArrowLeft className="size-4" aria-hidden /> Reports
        </Button>
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

      {isDraft && (
        // The action bar stays on screen in every section, under the app's
        // header and tab strip (`top-0` hid it behind them).
        <div style={stickyFlushBelowChrome} className="sticky z-10 -mx-md flex flex-wrap items-center justify-between gap-xs border-y border-border bg-background/95 px-md py-xs backdrop-blur md:-mx-lg md:px-lg"
          data-testid="report-action-bar">
          <div className="flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs text-caption">
            <span data-testid="report-save-status" className={dirty ? 'text-warning' : 'text-muted-foreground'}>
              {saving ? 'Saving…' : dirty ? `Unsaved changes — ${changedParts.join(', ')}` : 'All changes saved'}
            </span>
            <button type="button" className="text-info hover:underline" onClick={() => goTo('review')}
              data-testid="report-readiness">
              {toFix ? `${toFix} to fix` : 'Nothing to fix'}{warnings ? ` · ${warnings} warning${warnings === 1 ? '' : 's'}` : ''}
            </button>
          </div>
          {editable && (
            <div className="flex flex-wrap gap-xs">
              <Button variant="ghost" size="sm" disabled={!dirty || saving} onClick={() => void confirmDiscardAll()}>Discard changes</Button>
              <Button variant="outline" size="sm" disabled={!dirty || saving} onClick={() => void save()}>
                {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save draft
              </Button>
              <Button size="sm" disabled={saving || anyRunning || !!assetsBlock} title={assetsBlock}
                // The template's own format: HTML when it renders it, else its
                // first (a Word-only template only failed here — review
                // 2026-10-14, suspected).
                onClick={() => void preview(formats.includes('html') ? 'html' : formats[0])}>
                {dirty ? 'Save & preview' : 'Preview'}
              </Button>
            </div>
          )}
        </div>
      )}

      <div role="tablist" aria-label="Report sections" className="flex gap-xxs border-b border-border">
        {SECTIONS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={section === t.id}
            id={`report-tab-${t.id}`} aria-controls={`report-panel-${t.id}`}
            onClick={() => goTo(t.id)}
            className={`-mb-px border-b-2 px-sm py-xs text-body ${section === t.id ? 'border-primary font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
            {t.label}
            {t.id === 'review' && isDraft && toFix > 0 && <span className="ml-xxs text-caption text-warning">{toFix}</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`report-panel-${section}`} aria-labelledby={`report-tab-${section}`} className="pt-xs">
        {section === 'details' ? details : section === 'review' ? review : issuePanel}
      </div>

      {editable && (
        <>
          <AiDraftReportDialog reportId={reportId} open={aiOpen} onClose={() => setAiOpen(false)}
            useLabel="Use as the summary"
            // The draft's own summary from then on (pending until Save draft).
            onUse={(text) => { edits.edit('summary'); edits.set('executive_summary', text); }} />
          <UpdateDefaultsDialog key={promoteKey} open={promoteOpen} onClose={() => setPromoteOpen(false)}
            report={report} ctx={edits.ctx} keys={customisedKeys}
            onPromoted={(updated, keys) => { putReport(updated); edits.forget(keys); }} />
        </>
      )}
      {confirmDialog}
      {leaveDialog}
    </div>
  );
};

/** Keyed on the report: "Revise" navigates to the new draft's id. */
const ReportDetail: React.FC = () => {
  const { reportId } = useParams<{ reportId: string }>();
  return <ReportDetailView key={reportId} id={Number(reportId)} />;
};

export default ReportDetail;
