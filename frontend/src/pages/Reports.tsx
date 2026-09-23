/**
 * /reports (v5.261.0) — the project's client reports: drafts, the issued
 * history, and the defaults every new report starts from.
 *
 * A report is a draft until a project admin issues it; issuing freezes what
 * it says and numbers it.  An addendum reports only what changed since an
 * issued report (new findings, findings on further systems, withdrawals).
 * Posture layout: a lead sentence, then sections over thin rules.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Download, FilePlus2, Loader2, Pencil, RefreshCw } from 'lucide-react';

import {
  ClientReport,
  ClientReportKind,
  ClientReportList,
  ProjectMember,
  ReportProfile,
  ReportTemplate,
  createClientReport,
  downloadClientReportFile,
  getReportProfile,
  listClientReports,
  listProjectMembers,
  listReportTemplates,
  saveReportProfile,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import PostureLead from '../components/posture/PostureLead';
import EngagementSettingsFields, { cleanSettings } from '../components/reports/EngagementSettingsFields';
import TemplateImages, { missingAssetCount } from '../components/reports/TemplateImages';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Label } from '../components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { safeFallback } from '../utils/uiStyles';

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : '—');

export const reportKindLabel = (r: Pick<ClientReport, 'kind' | 'baseline' | 'revision_of'>): string => {
  const parts = [r.kind === 'addendum' ? `Addendum to #${r.baseline?.number ?? '?'}` : 'Full report'];
  if (r.revision_of) parts.push(`revises #${r.revision_of.number ?? '?'}`);
  return parts.join(' · ');
};

export const FileButtons: React.FC<{ report: ClientReport }> = ({ report }) => {
  const toast = useToast();
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
        <Button key={f.format} variant="outline" size="sm" className="h-7 px-xs text-caption"
          aria-label={`Download ${f.filename}`}
          onClick={() => void downloadClientReportFile(report.id, f).catch((err) =>
            toast.error(formatApiError(err, 'Could not download the file.')))}>
          <Download className="size-3.5" aria-hidden /> {f.format.toUpperCase()}
        </Button>
      ))}
    </span>
  );
};

const Reports: React.FC = () => {
  const toast = useToast();
  const navigate = useNavigate();
  const [data, setData] = useState<ClientReportList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<ClientReportKind | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await listClientReports());
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Could not load the reports.'));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const create = async (kind: ClientReportKind) => {
    setCreating(kind);
    try {
      const report = await createClientReport({ kind });
      navigate(`/reports/${report.id}`);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not start the report.'));
    } finally {
      setCreating(null);
    }
  };

  const drafts = data?.items.filter((r) => r.status === 'draft') ?? [];
  const issued = data?.items.filter((r) => r.status !== 'draft') ?? [];
  const latest = issued.find((r) => r.id === data?.latest_issued_id) ?? null;

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
          <div className="flex shrink-0 flex-wrap gap-xs">
            <Button onClick={() => void create('full')} disabled={creating !== null}>
              {creating === 'full' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <FilePlus2 className="size-4" aria-hidden />}
              New report
            </Button>
            <Button variant="outline" onClick={() => void create('addendum')}
              disabled={creating !== null || !latest}
              title={latest ? `Report what changed since #${latest.number}` : 'An addendum needs an issued report to compare against'}>
              {creating === 'addendum' && <Loader2 className="size-4 animate-spin" aria-hidden />}
              New addendum{latest ? ` to #${latest.number}` : ''}
            </Button>
          </div>
        )}
      </header>

      {error && (
        <div className="flex flex-wrap items-center gap-sm">
          <p className="text-caption text-destructive">{error}</p>
          <Button variant="outline" size="sm" onClick={() => void load()}>
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
                <colgroup><col /><col style={{ width: '14rem' }} /><col style={{ width: '10rem' }} /><col style={{ width: '8rem' }} /></colgroup>
                <TableHeader>
                  <TableRow>
                    <TableHead>Title</TableHead><TableHead>Kind</TableHead>
                    <TableHead>Started by</TableHead><TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {drafts.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="truncate">
                        <Link to={`/reports/${r.id}`} className="text-info hover:underline" title={r.title}>{r.title}</Link>
                      </TableCell>
                      <TableCell className="truncate text-caption">{reportKindLabel(r)}</TableCell>
                      <TableCell className="truncate text-caption">{safeFallback(r.created_by_name, '—')}</TableCell>
                      <TableCell className="text-caption">{day(r.updated_at ?? r.created_at)}</TableCell>
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
                        {r.status === 'superseded'
                          ? <Badge variant="muted">Superseded</Badge>
                          : <Badge variant="success">Current</Badge>}
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
    </div>
  );
};

const ProfileSection: React.FC<{ canEdit: boolean }> = ({ canEdit }) => {
  const toast = useToast();
  const [profile, setProfile] = useState<ReportProfile | null>(null);
  const [draft, setDraft] = useState<ReportProfile | null>(null);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getReportProfile(), listReportTemplates()])
      .then(([p, t]) => { if (!cancelled) { setProfile(p); setTemplates(t); } })
      .catch((err) => { if (!cancelled) setError(formatApiError(err, 'Could not load the report defaults.')); });
    if (canEdit) listProjectMembers().then((m) => { if (!cancelled) setMembers(m); }).catch(() => {});
    return () => { cancelled = true; };
  }, [canEdit]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const saved = await saveReportProfile({ ...cleanSettings(draft), template: draft.template });
      setProfile(saved);
      setDraft(null);
      toast.success('Report defaults saved. Existing drafts keep their own details.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not save the report defaults.'));
    } finally {
      setSaving(false);
    }
  };

  const templateTitle = (name: string | null) => templates.find((t) => t.name === name)?.title ?? name ?? '—';
  // The images of the template new reports use — the one being chosen while editing.
  const imagesFor = (draft ? draft.template : profile?.template) ?? null;
  const imagesTemplate = templates.find((t) => t.name === imagesFor);
  const imagesMissing = missingAssetCount(imagesTemplate);

  return (
    <>
    <PostureSection
      title="Defaults for new reports"
      description="Every new draft starts from these; each report can change its own copy."
      actions={canEdit && profile && !draft ? (
        <Button variant="ghost" size="sm" onClick={() => setDraft(profile)}>
          <Pencil className="size-4" aria-hidden /> Edit
        </Button>
      ) : undefined}
    >
      {error && <p className="text-caption text-destructive">{error}</p>}
      {profile && !draft && (
        <dl className="grid gap-x-lg gap-y-xs text-body sm:grid-cols-[10rem_minmax(0,1fr)]">
          <dt className="text-muted-foreground">Client</dt><dd className="truncate">{safeFallback(profile.client_name, 'Not set')}</dd>
          <dt className="text-muted-foreground">Classification</dt><dd className="truncate">{safeFallback(profile.classification, 'Not set')}</dd>
          <dt className="text-muted-foreground">Engagement type</dt><dd className="truncate">{safeFallback(profile.engagement_type, 'Not set')}</dd>
          <dt className="text-muted-foreground">Team</dt>
          <dd className="break-words">
            {profile.testers.length ? profile.testers.map((t) => t.name).join(', ') : 'Nobody listed'}
            {profile.testers_from_project && profile.testers.length > 0 && (
              <span className="text-caption text-muted-foreground"> — the project&apos;s analysts and admins, until a team is saved</span>
            )}
          </dd>
          <dt className="text-muted-foreground">Distribution</dt>
          <dd className="break-words">{profile.distribution.length ? profile.distribution.map((r) => r.name).join(', ') : 'Nobody listed'}</dd>
          <dt className="text-muted-foreground">Template</dt><dd className="truncate">{templateTitle(profile.template)}</dd>
        </dl>
      )}
      {draft && (
        <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <EngagementSettingsFields idPrefix="profile" value={draft} members={members} disabled={saving}
            onChange={(next) => setDraft({ ...draft, ...next })} />
          <div className="w-72 max-w-full space-y-xxs">
            <Label htmlFor="profile-template">Template</Label>
            <Select value={draft.template ?? ''} onValueChange={(v) => setDraft({ ...draft, template: v })} disabled={saving}>
              <SelectTrigger id="profile-template"><SelectValue placeholder="Choose a template" /></SelectTrigger>
              <SelectContent>
                {templates.map((t) => <SelectItem key={t.name} value={t.name}>{t.title}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="flex gap-xs">
            <Button type="submit" size="sm" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save defaults
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setDraft(null)} disabled={saving}>Cancel</Button>
          </div>
        </form>
      )}
    </PostureSection>
    {(profile || draft) && (
      <PostureSection
        title={<>Template files{imagesMissing > 0 && <SectionCount>{imagesMissing} missing</SectionCount>}</>}
        description={`The logo, Word styles and other files ${imagesTemplate ? `“${imagesTemplate.title}”` : 'the template'} uses, besides the findings' evidence — install them before generating a report.`}
      >
        <TemplateImages template={imagesTemplate} templateName={imagesFor} />
      </PostureSection>
    )}
    </>
  );
};

export default Reports;
