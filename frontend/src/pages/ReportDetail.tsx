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
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Download, Loader2, RefreshCw, Sparkles, Stamp, Trash2 } from 'lucide-react';

import {
  ClientReport,
  ClientReportFormat,
  ReportFileFormat,
  EngagementSettings,
  ProjectMember,
  ReportJob,
  ReportTemplate,
  deleteClientReport,
  downloadReportJob,
  getClientReport,
  getReportJob,
  issueClientReport,
  listProjectMembers,
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
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import { formatApiError } from '../utils/apiErrors';
import { formatTimestamp } from '../utils/relativeTime';
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

const ReportDetailView: React.FC<{ id: number }> = ({ id }) => {
  const toast = useToast();
  const navigate = useNavigate();
  const [confirmDialog, confirm] = useConfirm();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const currentUser = user ? { id: user.id, name: user.full_name || user.username } : null;
  const [showAllMissing, setShowAllMissing] = useState(false);

  const [report, setReport] = useState<ClientReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<'issue' | 'revise' | 'render' | 'delete' | null>(null);
  const [aiOpen, setAiOpen] = useState(false);
  const [previews, setPreviews] = useState<Partial<Record<ClientReportFormat, ReportJob>>>({});

  const load = useCallback(async () => {
    try {
      const r = await getClientReport(id);
      setReport(r);
      setForm(toForm(r));
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Could not load the report.'));
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    listReportTemplates().then(setTemplates).catch(() => {});
    listProjectMembers().then(setMembers).catch(() => {});
  }, []);

  // An issued report renders its files on the worker: follow it until done.
  const rendering = report?.status !== 'draft' && report?.render_status === 'pending';
  useVisibilityPoll(async () => {
    const r = await getClientReport(id);
    setReport(r);
  }, 3000, rendering);

  // Draft previews are report jobs: follow the ones still running.
  const activePreviews = Object.values(previews).filter(
    (j): j is ReportJob => !!j && (j.status === 'queued' || j.status === 'processing'),
  );
  useVisibilityPoll(async () => {
    const updated = await Promise.all(activePreviews.map((j) => getReportJob(j.id)));
    setPreviews((prev) => {
      const next = { ...prev };
      for (const job of updated) {
        const fmt = (job.format.replace('report-', '') as ClientReportFormat);
        next[fmt] = job;
      }
      return next;
    });
  }, 2000, activePreviews.length > 0);

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

  const save = async () => {
    if (!report || !form) return;
    if (!form.title.trim()) { toast.error('A report needs a title.'); return; }
    setSaving(true);
    try {
      const updated = await updateClientReport(report.id, {
        title: form.title.trim(),
        template: form.template,
        executive_summary: form.executive_summary.trim() || null,
        settings: cleanSettings(form.settings),
      });
      setReport(updated);
      setForm(toForm(updated));
      toast.success('Report saved.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not save the report.'));
    } finally {
      setSaving(false);
    }
  };

  // The template is a rendering choice beside the Preview buttons, so it is
  // saved on its own at once — other unsaved edits stay unsaved.
  const [savingTemplate, setSavingTemplate] = useState(false);
  const changeTemplate = async (name: string) => {
    if (!report || name === report.template) return;
    setSavingTemplate(true);
    try {
      const updated = await updateClientReport(report.id, { template: name });
      setReport(updated);
      setForm((f) => (f ? { ...f, template: updated.template } : f));
      setPreviews({});
    } catch (err) {
      toast.error(formatApiError(err, 'Could not change the template.'));
    } finally {
      setSavingTemplate(false);
    }
  };

  const preview = async (fmt: ClientReportFormat) => {
    if (!report) return;
    try {
      const job = await previewClientReport(report.id, fmt);
      setPreviews((prev) => ({ ...prev, [fmt]: job }));
    } catch (err) {
      toast.error(formatApiError(err, 'Could not start the preview.'));
    }
  };

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
        </>
      ),
      confirmLabel: 'Issue report',
    });
    if (!ok) return;
    setBusy('issue');
    try {
      const issued = await issueClientReport(report.id);
      setReport(issued);
      setForm(toForm(issued));
      toast.success(`Issued as report #${issued.number}. Rendering its files…`);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not issue the report.'));
    } finally {
      setBusy(null);
    }
  };

  const revise = async () => {
    if (!report) return;
    setBusy('revise');
    try {
      const draft = await reviseClientReport(report.id);
      navigate(`/reports/${draft.id}`);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not start a revision.'));
    } finally {
      setBusy(null);
    }
  };

  const rerender = async () => {
    if (!report) return;
    setBusy('render');
    try {
      setReport(await rerenderClientReport(report.id));
    } catch (err) {
      toast.error(formatApiError(err, 'Could not restart the rendering.'));
    } finally {
      setBusy(null);
    }
  };

  const discard = async () => {
    if (!report) return;
    const ok = await confirm({
      title: 'Discard this draft?', body: `"${report.title}" is deleted. Findings are not affected.`,
      severity: 'danger', confirmLabel: 'Discard draft',
    });
    if (!ok) return;
    setBusy('delete');
    try {
      await deleteClientReport(report.id);
      navigate('/reports');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not discard the draft.'));
      setBusy(null);
    }
  };

  if (!report && !error) return <DetailSkeleton />;
  if (!report || !form) {
    return (
      <div className="p-md md:p-lg">
        <Button variant="ghost" size="sm" onClick={() => navigate('/reports')}><ArrowLeft className="size-4" aria-hidden /> Reports</Button>
        <p className="mt-md text-destructive">{error}</p>
        <Button variant="outline" size="sm" className="mt-sm" onClick={() => void load()}><RefreshCw className="size-4" aria-hidden /> Retry</Button>
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
  // Issue is the main action only once nothing prints as TODO; until then
  // the page leads to the gaps and to previewing, not to freezing them.
  const ready = !missingText.length && !missingDetails.length;
  const notReadyReason = ready ? undefined
    : 'Parts of this report still print as TODO — see Before issuing';

  let lead: React.ReactNode;
  if (s.error) {
    lead = <span className="text-destructive">{s.error}</span>;
  } else if (report.kind === 'addendum' && s.delta) {
    lead = <>Compared with report #{report.baseline?.number}: <strong>{s.delta.new_findings}</strong> new finding{s.delta.new_findings === 1 ? '' : 's'}, <strong>{s.delta.findings_with_new_endpoints}</strong> reported finding{s.delta.findings_with_new_endpoints === 1 ? '' : 's'} on further systems, <strong>{s.delta.withdrawn}</strong> withdrawal{s.delta.withdrawn === 1 ? '' : 's'}.</>;
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
              <Button variant="outline" onClick={() => void revise()} disabled={busy !== null}>
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
            info="Images are opt-in: tick “In report” on an image attached to a finding's evidence or comments. WebP images cannot be placed in every format and are skipped.">
            {s.images_skipped ? `${s.images_skipped} skipped (WebP)`
              : s.images ? 'Ticked “In report” on the findings'
                : 'Tick “In report” on a finding’s image to include it'}
          </PostureMeasure>
        </div>
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

      {isDraft ? (
        <PostureSection title="Preview"
          description="Rendered from the live findings on the report worker, with the template chosen here. Previews expire after a day.">
          <div className="mb-sm space-y-xs">
            <div className="flex min-w-0 flex-wrap items-center gap-xs">
              <Label htmlFor="report-template" className="shrink-0">Template</Label>
              <Select value={report.template} onValueChange={(v) => void changeTemplate(v)}
                disabled={!editable || savingTemplate}>
                <SelectTrigger id="report-template" className="h-8 w-[18rem] max-w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {templates.map((t) => <SelectItem key={t.name} value={t.name}>{t.title}</SelectItem>)}
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
                <TemplateImages template={template} templateName={report.template} showServerPaths={isAdmin} />
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
                  <Button variant="outline" size="sm" onClick={() => void preview(fmt)}
                    disabled={!!running || !report.can_edit || dirty || !!assetsBlock}
                    title={dirty ? 'Save your changes first' : assetsBlock}>
                    {running && <Loader2 className="size-4 animate-spin" aria-hidden />}
                    {running ? `Rendering ${FORMAT_LABEL[fmt]}…` : `Preview ${FORMAT_LABEL[fmt]}`}
                  </Button>
                  {job?.status === 'completed' && (
                    <Button size="sm" variant="ghost" aria-label={`Download the ${FORMAT_LABEL[fmt]} preview`}
                      onClick={() => void downloadReportJob(job.id).catch((err) => toast.error(formatApiError(err, 'Could not download the preview.')))}>
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
                <Button size="sm" variant="outline" onClick={() => void rerender()} disabled={busy !== null}>
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
        <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <div className="min-w-0 space-y-xxs">
            <Label htmlFor="report-title">Title</Label>
            <Input id="report-title" maxLength={255} value={form.title} disabled={!editable || saving}
              onChange={(e) => setForm({ ...form, title: e.target.value })} />
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
              onChange={(v) => setForm((f) => (f ? { ...f, executive_summary: v } : f))} />
          </div>

          <EngagementSettingsFields idPrefix="report" value={form.settings} members={members} currentUser={currentUser}
            readOnly={!editable} disabled={saving} onChange={(settings) => setForm({ ...form, settings })} />

          {editable && (
            <div className="flex flex-wrap items-center gap-xs">
              <Button type="submit" size="sm" disabled={saving || !dirty}>
                {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save
              </Button>
              <Button type="button" variant="ghost" size="sm" disabled={saving || !dirty} onClick={() => setForm(toForm(report))}>
                Undo changes
              </Button>
              {dirty && <span className="text-caption text-muted-foreground">Unsaved changes — save before previewing or issuing.</span>}
            </div>
          )}
        </form>
      </PostureSection>

      <AiDraftReportDialog open={aiOpen} onClose={() => setAiOpen(false)}
        useLabel="Use as the summary"
        onUse={(text) => setForm((f) => (f ? { ...f, executive_summary: text } : f))} />
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
