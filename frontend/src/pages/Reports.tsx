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
  updateClientReport,
} from '../services/api';
import { formatDate, formatTimestamp } from '../utils/relativeTime';
import TimeAgo from '../components/TimeAgo';
import { Input } from '../components/ui/input';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import PostureLead from '../components/posture/PostureLead';
import EngagementSettingsFields, { cleanSettings } from '../components/reports/EngagementSettingsFields';
import TemplateImages, { assetCountLabel } from '../components/reports/TemplateImages';
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

const day = (iso: string | null) => formatDate(iso);

/** "Draft #12 · started 23 Sep 2026, 14:05 · template default" — what sets
 *  apart drafts that share a title (v5.288.0). The time is there because two
 *  drafts are often started the same day. */
export const draftMeta = (r: Pick<ClientReport, 'id' | 'created_at' | 'template'>): string => {
  const started = formatTimestamp(r.created_at, null);
  return [`Draft #${r.id}`, started ? `started ${started}` : null, r.template ? `template ${r.template}` : null]
    .filter(Boolean)
    .join(' · ');
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
  onRenamed: (r: ClientReport) => void;
}> = ({ report, duplicate, onRenamed }) => {
  const toast = useToast();
  const [value, setValue] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const title = (value ?? '').trim();
    if (!title) { toast.error('A report needs a title.'); return; }
    if (title === report.title) { setValue(null); return; }
    setSaving(true);
    try {
      onRenamed(await updateClientReport(report.id, { title }));
      setValue(null);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not rename the draft.'));
    } finally {
      setSaving(false);
    }
  };

  if (value !== null) {
    return (
      <form className="flex min-w-0 items-center gap-xs" onSubmit={(e) => { e.preventDefault(); void save(); }}>
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
  const titleCounts = new Map<string, number>();
  for (const r of drafts) titleCounts.set(r.title, (titleCounts.get(r.title) ?? 0) + 1);
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
          <div className="flex shrink-0 flex-col items-end gap-xxs">
            <div className="flex flex-wrap gap-xs">
              <Button onClick={() => void create('full')} disabled={creating !== null}>
                {creating === 'full' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <FilePlus2 className="size-4" aria-hidden />}
                New report
              </Button>
              <Button variant="outline" onClick={() => void create('addendum')}
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
                      <TableCell className="min-w-0">
                        <DraftTitle report={r} duplicate={(titleCounts.get(r.title) ?? 0) > 1}
                          onRenamed={(updated) => setData((d) => (d
                            ? { ...d, items: d.items.map((x) => (x.id === updated.id ? { ...x, title: updated.title } : x)) }
                            : d))} />
                        {/* Drafts often share the default title; this line
                            tells them apart from what the list already
                            carries (v5.288.0) — no per-draft request. */}
                        <span className="block truncate text-caption text-muted-foreground" data-testid={`draft-meta-${r.id}`}>
                          {draftMeta(r)}
                        </span>
                      </TableCell>
                      <TableCell className="truncate text-caption">{reportKindLabel(r)}</TableCell>
                      <TableCell className="truncate text-caption">{safeFallback(r.created_by_name, '—')}</TableCell>
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

const FORMAT_LABEL: Record<string, string> = { html: 'HTML', docx: 'Word', qmd: 'QMD source' };

const ProfileSection: React.FC<{ canEdit: boolean }> = ({ canEdit }) => {
  const toast = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const currentUser = user ? { id: user.id, name: user.full_name || user.username } : null;
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
          <EngagementSettingsFields idPrefix="profile" value={draft} members={members} currentUser={currentUser}
            disabled={saving} onChange={(next) => setDraft({ ...draft, ...next })} />
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
    {profile && <TemplatesSection templates={templates} defaultName={profile.template} isAdmin={isAdmin} />}
    </>
  );
};

/**
 * Every template installed on the server, the default first, each with the
 * files it uses besides the findings' evidence. A template is a folder under
 * `report-templates/`, read on each request — there is no upload.
 */
const TemplatesSection: React.FC<{ templates: ReportTemplate[]; defaultName: string | null; isAdmin: boolean }> = ({
  templates, defaultName, isAdmin,
}) => {
  const ordered = [...templates].sort((a, b) => Number(b.name === defaultName) - Number(a.name === defaultName));
  return (
    <PostureSection
      title={<>Templates<SectionCount>{templates.length} installed</SectionCount></>}
      description="New reports use the default, set above; each draft can choose another. A template's own files (logo, Word styles…) are listed with it."
    >
      {templates.length === 0 ? (
        <p className="text-caption text-destructive">No report templates are installed, so no report can be rendered.</p>
      ) : (
        <div className="divide-y divide-border">
          {ordered.map((t) => {
            const count = assetCountLabel(t);
            return (
              <details key={t.name} open={t.name === defaultName || templates.length === 1} className="group py-sm first:pt-0">
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
                  <TemplateImages template={t} templateName={t.name} showServerPaths={isAdmin} />
                </div>
              </details>
            );
          })}
        </div>
      )}
      {isAdmin && (
        <p className="mt-sm max-w-3xl text-caption text-muted-foreground">
          To add a template, copy its folder, with its <span className="font-mono">template.json</span>, into{' '}
          <span className="font-mono">report-templates/</span> on the server. It is listed here on the next reload; no rebuild
          is needed.
        </p>
      )}
    </PostureSection>
  );
};

export default Reports;
