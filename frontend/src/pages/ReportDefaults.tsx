/**
 * /reports/defaults — the project's report defaults (the report workspace,
 * owner 2026-10-14): what every draft follows unless it overrides a detail.
 * The same sections as a draft's Details (`DetailSections`), edited
 * directly, with ONE save: a change here reaches every draft that follows
 * it; issued reports keep what they were issued with.  The project's default
 * template is here too — the installation's template files are
 * /reports/templates.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2 } from 'lucide-react';

import {
  patchReportProfile, type AcceptedDetailProposal, type EngagementOverrides, type ReportProfile,
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
import ProposedReportTextLine from '../components/reports/ProposedReportTextLine';
import { DETAIL_LABELS, DETAIL_SECTIONS, type DetailKey, toSave } from '../components/reports/workspace/detailModel';
import DetailProposalNotes, { groupSources, SharedSourceLine } from '../components/reports/DetailProposalNotes';
import { useReportDetailProposals } from '../hooks/useReportDetailProposals';
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
/** The details an agent may propose text for — the server's
 *  `report_defaults.WRITTEN_KEYS`. */
const WRITTEN_DETAILS: DetailKey[] = [
  'executive_summary', 'system_description', 'applications', 'thick_clients', 'other_targets', 'disclaimer',
];
/** Room above a detail's drafts when a link lands on them (`#review-<detail>`):
 *  this page's sticky save bar (about 3rem under the layout's chrome), and the
 *  detail's name printed above the drafts.  With the finding page's 2.25rem
 *  the heading landed under the bar (browser pass 2026-11-17). */
const DRAFTS_SCROLL_EXTRA = '6.5rem';

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
  const proposals = useReportDetailProposals();
  // A link to one detail's drafts (`#review-<detail>`, from the Proposals
  // page or a draft's checklist) lands on them once they are on the page —
  // they arrive after the defaults do.  Once per address.
  const { hash } = useLocation();
  const landedOn = useRef<string | null>(null);
  const draftsShown = proposals.byDetail.size;
  useEffect(() => {
    if (!hash.startsWith('#review-') || landedOn.current === hash || !profileQuery.data || !draftsShown) return;
    const target = document.getElementById(hash.slice(1));
    if (!target) return;
    landedOn.current = hash;
    target.scrollIntoView({ block: 'start' });
  }, [hash, draftsShown, profileQuery.data]);

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
  // 5.431.0 — under each written detail: the accepted proposal its text came
  // from, and the drafts waiting for it, reviewed where the text is.
  // A detail with drafts shows its current text ONCE — in the comparison,
  // beside the draft — so its read view leaves the text out (§43).
  const underReview: DetailKey[] = [];
  // An UNSAVED edit of a detail is what the page shows for it, and it is
  // not the stored text (browser pass 2026-11-17): it stays visible above
  // the drafts — which compare with the stored default — and the page does
  // not say of it that it "was accepted from" a proposal.
  const isEditing = (key: DetailKey) => key in edits.ctx.pending;
  // Details one agent session wrote are said once, under the lead (5.432.0).
  const sources = groupSources(WRITTEN_DETAILS.flatMap((key) => {
    const source = isEditing(key) ? undefined : profile.from_proposals?.[key];
    return source ? [[key, source] as [DetailKey, AcceptedDetailProposal]] : [];
  }));
  for (const key of WRITTEN_DETAILS) {
    const editing = isEditing(key);
    const accepted = profile.from_proposals?.[key];
    const source = sources.own.includes(key) ? accepted : undefined;
    const drafts = proposals.byDetail.get(key) ?? [];
    // Saving an edit replaces text accepted from a proposal: said while the
    // edit is unsaved (5.432.0 — a draft accepted a moment ago on this very
    // page was overwritten by the Save that followed, with no word of it).
    const replaces = editing && accepted && !accepted.changed_since ? accepted : undefined;
    if (!source && !drafts.length && !replaces) continue;
    if (drafts.length && !editing) underReview.push(key);
    notes[key] = (
      <>
        {/* "Empty: reports use the installation's…" stays above the drafts. */}
        {notes[key]}
        {replaces && (
          <p className="text-caption text-warning" data-testid={`defaults-${key}-replaces`}>
            Unsaved: saving replaces the text accepted from an agent’s proposal #{replaces.proposal_id}
            {replaces.accepted_by ? ` (accepted by ${replaces.accepted_by})` : ''}.
          </p>
        )}
        <DetailProposalNotes field={key} label={DETAIL_LABELS[key]} current={profile[key] as string | null}
          source={source} drafts={drafts} canDecide={canWrite} evidence={images.markdown.resolver}
          scrollExtra={DRAFTS_SCROLL_EXTRA} />
      </>
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
        <ProposedReportTextLine />
        {sources.shared.map((group) => (
          <SharedSourceLine key={group.sessionId} sessionId={group.sessionId} first={group.first}
            labels={group.keys.map((key) => DETAIL_LABELS[key])} />
        ))}
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

      {/* The project's summary is a full report's executive summary (an
          addendum never follows it): named as on the draft. */}
      <DetailSections edits={edits} canEdit={canWrite} idPrefix="defaults" busy={busy} notes={notes} valueInNote={underReview}
        summaryLabel="Executive summary"
        images={images.markdown}
        scopeAction={canWrite ? <ProjectImagesPanel state={images} idPrefix="defaults" /> : undefined}
        members={roster.members} membersStatus={roster.status} onRetryMembers={roster.retry}
        currentUser={currentUser} />

      {/* LAST on the page (owner, 2026-11-17: "99% of users will use the
          Penetration test report") — it was the first thing under the title,
          above the text people come here to write.  A section like the ones
          above it; the one save still carries it. */}
      <section className="min-w-0 space-y-sm border-t border-border pt-md" aria-labelledby="defaults-section-template"
        data-testid="defaults-section-template">
        <h3 id="defaults-section-template" className="text-subheading font-medium">Template</h3>
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
      </section>
      {confirmEl}
      {discardDialog}
    </div>
  );
};

export default ReportDefaults;
