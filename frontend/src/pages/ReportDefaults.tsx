/**
 * /reports/defaults — the project's report defaults (the report workspace,
 * owner 2026-10-14): what every draft follows unless it overrides a detail.
 * The same sections as a draft's Details (`DetailSections`), edited
 * directly, with ONE save: a change here reaches every draft that follows
 * it; issued reports keep what they were issued with.  The project's default
 * template is here too — the installation's template files are
 * /reports/templates.
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2 } from 'lucide-react';

import {
  patchReportProfile, type EngagementOverrides, type ReportProfile,
  type ReportTemplate,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { useDiscardGuard, useLinkGuard } from '../hooks/useDiscardGuard';
import { useProjectId } from '../hooks/useProjectId';
import { reportProfileQuery, reportTemplatesQuery } from '../components/reports/reportQueries';
import { useProjectRoster } from '../hooks/useProjectMembers';
import { useProjectRole } from '../hooks/useProjectRole';
import { useReportTextImages } from '../hooks/useReportTextImages';
import { invalidateReads, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { isClientTemplate } from '../utils/reportTemplates';
import DetailSections, { DETAIL_NAME } from '../components/reports/workspace/DetailSections';
import ProjectImagesPanel from '../components/reports/workspace/ProjectImagesPanel';
import { DETAIL_SECTIONS, type DetailKey, toSave } from '../components/reports/workspace/detailModel';
import { useDetailEdits } from '../components/reports/workspace/useDetailEdits';
import { DetailSkeleton } from '../components/PageSkeleton';
import SafeMarkdown from '../components/SafeMarkdown';
import { stickyFlushBelowChrome } from '../utils/uiStyles';
import { Button } from '../components/ui/button';
import { Label } from '../components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';

const NO_TEMPLATES: ReportTemplate[] = [];
const ALL_KEYS: DetailKey[] = DETAIL_SECTIONS.flatMap((s) => s.keys);

/** The stored defaults as the sections' "saved" map. */
const savedOf = (p: ReportProfile): EngagementOverrides =>
  Object.fromEntries(ALL_KEYS.map((k) => [k, p[k]])) as EngagementOverrides;

const ReportDefaults: React.FC = () => {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { canWrite } = useProjectRole();
  const roster = useProjectRoster({ enabled: canWrite });
  const currentUser = user ? { id: user.id, name: user.full_name || user.username } : null;

  const profileQuery = useQuery({
    ...reportProfileQuery(projectId),
  });
  const templatesQuery = useQuery({
    ...reportTemplatesQuery(projectId),
  });
  const profile = profileQuery.data ?? null;
  const templates = templatesQuery.data ?? NO_TEMPLATES;
  const error = queryErrorText(profileQuery.error, 'Could not load the report defaults.');

  const base = useMemo(() => ({
    saved: profile ? savedOf(profile) : {}, followed: null, sources: null, ownOnly: [],
  }), [profile]);
  const edits = useDetailEdits(base);
  const [template, setTemplate] = useState<string | null>(null);
  const templateChanged = template !== null && template !== profile?.template;
  const dirty = edits.dirty || templateChanged;
  // Read for every reader: one who may not edit sees the diagram, not a text
  // marker (third review 2026-10-15, N4).
  const images = useReportTextImages();

  const { confirmLeave, confirmEl } = useDiscardGuard(() => dirty, 'The report defaults have unsaved changes. Leave anyway?');
  const guardLinks = useLinkGuard(confirmLeave);
  const [discardDialog, confirmDiscard] = useConfirm();

  const saving = useMutation({
    mutationFn: () => {
      // Only what changed: a stale read never overwrites another person's
      // change to a detail this page did not touch.
      const body: Record<string, unknown> = { ...toSave(edits.ctx) };
      if (templateChanged) body.template = template;
      return patchReportProfile(projectId, body as EngagementOverrides);
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(['getReportProfile', projectId], saved);
      edits.discardAll();
      setTemplate(null);
      // Every draft that follows the defaults prints the new values.
      void invalidateReads(queryClient, 'getClientReport');
      void invalidateReads(queryClient, 'listClientReports');
      toast.success('Report defaults saved. Every draft that follows them now prints them.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the report defaults.')),
  });
  const busy = saving.isPending;

  if (!profile && !error) return <DetailSkeleton />;
  if (!profile) return <p className="p-md text-destructive md:p-lg">{error}</p>;

  const installationAuthor = profile.inherited?.author_name?.trim();
  const installationDisclaimer = profile.inherited?.disclaimer?.trim();
  const notes: Partial<Record<DetailKey, React.ReactNode>> = {};
  if (!profile.author_name?.trim() && installationAuthor) {
    notes.author_name = <p className="text-caption text-muted-foreground [overflow-wrap:anywhere]">Empty: reports use the installation’s — “{installationAuthor}”</p>;
  }
  if (!profile.disclaimer?.trim() && installationDisclaimer && !('disclaimer' in edits.ctx.pending)) {
    // Shown on request, rendered, with a copy to edit as the project's own.
    notes.disclaimer = (
      <details className="text-caption text-muted-foreground" data-testid="defaults-disclaimer-inherited">
        <summary className="cursor-pointer">Empty: reports use the installation’s disclaimer. Show it</summary>
        <div className="mt-xxs max-w-4xl rounded-control border border-border px-sm py-xs text-body text-foreground">
          <SafeMarkdown text={installationDisclaimer} />
        </div>
        {canWrite && (
          <Button type="button" variant="link" size="sm" className="h-auto p-0 text-caption"
            onClick={() => { edits.edit('disclaimer'); edits.set('disclaimer', installationDisclaimer); }}>
            Copy it here to edit
          </Button>
        )}
      </details>
    );
  }
  if (profile.testers_from_project && profile.testers.length) {
    notes.testers = <p className="text-caption text-muted-foreground">The project’s analysts and admins, until a team is saved.</p>;
  }
  const templateValue = template ?? profile.template ?? '';

  return (
    <div className="space-y-lg p-md md:p-lg" onClickCapture={guardLinks}>
      <div>
        <Button variant="ghost" size="sm" className="mb-sm"
          onClick={async () => { if (await confirmLeave()) navigate('/reports'); }}>
          <ArrowLeft className="size-4" aria-hidden /> Reports
        </Button>
        <h1 className="text-page-title">Report defaults</h1>
        <p className="mt-xxs max-w-3xl text-metadata text-muted-foreground">
          Every draft follows these unless it overrides a detail, so a change here reaches every such draft. Issued
          reports keep what they were issued with.
        </p>
      </div>

      {canWrite && (
        <div style={stickyFlushBelowChrome} className="sticky z-10 -mx-md flex flex-wrap items-center justify-between gap-xs border-y border-border bg-background/95 px-md py-xs backdrop-blur md:-mx-lg md:px-lg"
          data-testid="defaults-action-bar">
          <span className="text-caption text-muted-foreground">
            {dirty ? <span className="text-warning">Unsaved changes</span> : 'All changes saved'}
          </span>
          <div className="flex gap-xs">
            <Button variant="ghost" size="sm" disabled={!dirty || busy}
              onClick={async () => {
                // Asks first, as leaving does (walkthrough 2026-10-14).
                const ok = await confirmDiscard({
                  title: 'Discard your unsaved changes?', body: 'The defaults go back to the last save.',
                  severity: 'danger', confirmLabel: 'Discard changes',
                });
                if (ok) { edits.discardAll(); setTemplate(null); }
              }}>
              Discard changes
            </Button>
            <Button size="sm" disabled={!dirty || busy || images.uploading}
              title={images.uploading ? 'Waiting for the pasted image to upload' : undefined}
              onClick={() => saving.mutate()}>
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Save defaults
            </Button>
          </div>
        </div>
      )}

      <div className="max-w-sm space-y-xxs">
        {canWrite ? (
          <>
            <Label htmlFor="defaults-template" className={DETAIL_NAME}>Default template</Label>
            <Select value={templateValue} onValueChange={setTemplate} disabled={busy}>
              <SelectTrigger id="defaults-template"><SelectValue placeholder="Choose a template" /></SelectTrigger>
              <SelectContent>
                {templates.filter(isClientTemplate).map((t) => <SelectItem key={t.name} value={t.name}>{t.title}</SelectItem>)}
              </SelectContent>
            </Select>
          </>
        ) : (
          <>
            <span className={DETAIL_NAME}>Default template</span>
            <p className="text-body">{templates.find((t) => t.name === profile.template)?.title ?? profile.template ?? '—'}</p>
          </>
        )}
        <p className="text-caption text-muted-foreground">
          A new report starts with it. The installation’s templates and their files are on{' '}
          <Link to="/reports/templates" className="text-info hover:underline">Templates</Link>.
        </p>
      </div>

      {/* The project's summary is a full report's executive summary (an
          addendum never follows it): named as on the draft. */}
      <DetailSections edits={edits} canEdit={canWrite} idPrefix="defaults" busy={busy} notes={notes}
        summaryLabel="Executive summary"
        images={images.markdown}
        scopeAction={canWrite ? <ProjectImagesPanel state={images} idPrefix="defaults" /> : undefined}
        members={roster.members} membersStatus={roster.status} onRetryMembers={roster.retry}
        currentUser={currentUser} />
      {confirmEl}
      {discardDialog}
    </div>
  );
};

export default ReportDefaults;
