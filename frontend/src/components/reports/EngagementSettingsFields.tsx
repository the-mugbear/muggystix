/**
 * Engagement details of a client report (v5.261.0): client, author,
 * classification, engagement type, assessment team, distribution list, system
 * description (with the project's own report images, `ReportTextImages`),
 * other targets and disclaimer.  Controlled; three uses:
 *
 * - The project's report defaults (Reports page): plain editing.  An empty
 *   author or disclaimer uses the installation's (`inherited`), said under it.
 * - A draft (`followed` given): every detail FOLLOWS the project unless the
 *   report overrides it (owner, 2026-10-14).  `value` is the override map — a
 *   key present is overridden (a list even when empty), a key absent follows.
 *   A followed detail shows the project's value, greyed, "From the project's
 *   defaults", with "Override for this report", which makes it an editable
 *   copy; an overridden one says so, with "Revert to the project's".  Lists
 *   are overridden as a whole.
 * - `readOnly` (an issued report, or someone who may not edit): the values as
 *   they stand (an issued report's as issued), no action.
 *
 * The team and the distribution list print in the order shown; their rows are
 * reordered, and removed, by `ReorderableRows`.
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Copy, Plus, Users } from 'lucide-react';

import {
  getProjectReportTeam, type EngagementOverrides, type EngagementSettings, type InheritedReportDetails,
  type ProjectMember,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import type { ProjectMembersStatus } from '../../hooks/useProjectMembers';
import { useReportTextImages } from '../../hooks/useReportTextImages';
import MembersLoadError from '../MembersLoadError';
import { formatApiError } from '../../utils/apiErrors';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../ui/select';
import { Textarea } from '../ui/textarea';
import MarkdownField from '../MarkdownField';
import SafeMarkdown from '../SafeMarkdown';
import ReorderableRows from './ReorderableRows';
import ReportTextImages from './ReportTextImages';

interface Props<T extends EngagementOverrides> {
  value: T;
  onChange: (next: T) => void;
  members: ProjectMember[];
  /** How the member list stands (`useProjectRoster`): a failed load is said,
   *  never shown as a project with no members. */
  membersStatus?: ProjectMembersStatus;
  onRetryMembers?: () => void;
  /** Whoever is signed in. A global admin can work on a project without being
   *  a member of it, so the member list alone left them nobody to pick. */
  currentUser?: { id: number; name: string } | null;
  /** Momentarily unavailable (saving): every control stays, greyed. */
  disabled?: boolean;
  /** v5.290.0 — the details as they stand, not a form (an issued report,
   *  or someone who may not edit): the fields show their values and no
   *  add / remove / override control is rendered at all. */
  readOnly?: boolean;
  /** A DRAFT: what each detail prints when the report does not override it
   *  (`ClientReport.followed`).  Given, `value` is the report's overrides. */
  followed?: EngagementSettings | null;
  /** The project's defaults only: the installation's author and disclaimer,
   *  which an empty one there uses (`ReportProfile.inherited`). */
  inherited?: InheritedReportDetails | null;
  idPrefix: string;
}

const memberName = (m: ProjectMember) => m.full_name || m.username || `User ${m.user_id}`;

type DetailKey = keyof EngagementSettings;
type TextKey = Exclude<DetailKey, 'testers' | 'distribution'>;

const LABELS: Record<DetailKey, string> = {
  client_name: 'Client', author_name: 'Author', classification: 'Classification', engagement_type: 'Engagement type',
  testers: 'Assessment team', distribution: 'Distribution', system_description: 'System description',
  applications: 'Application URLs and API endpoints', thick_clients: 'Thick client applications',
  other_targets: 'Other targets', disclaimer: 'Disclaimer',
};

/** Is this detail overridden in a draft's override map?  Present = yes (a
 *  list even when empty, a text even while it is being emptied). */
export const isOverridden = (overrides: EngagementOverrides, key: DetailKey): boolean =>
  overrides[key] !== undefined && overrides[key] !== null;

/** Where a draft's detail comes from, and the one action that changes it. */
const DetailSource: React.FC<{
  id: string; label: string; overridden: boolean; blank: boolean; disabled?: boolean;
  onOverride: () => void; onRevert: () => void;
}> = ({ id, label, overridden, blank, disabled, onOverride, onRevert }) => (
  <p id={id} className="flex min-w-0 flex-wrap items-center gap-x-xs text-caption text-muted-foreground">
    <span>
      {overridden
        ? (blank ? 'Overridden for this report — empty: saved, it follows the project’s again' : 'Overridden for this report')
        : 'From the project’s defaults'}
    </span>
    <span aria-hidden>·</span>
    {overridden ? (
      <Button type="button" variant="link" size="sm" className="h-auto p-0 text-caption" disabled={disabled}
        aria-label={`Revert ${label} to the project’s`} onClick={onRevert}>
        Revert to the project’s
      </Button>
    ) : (
      <Button type="button" variant="link" size="sm" className="h-auto p-0 text-caption" disabled={disabled}
        aria-label={`Override ${label} for this report`} onClick={onOverride}>
        Override for this report
      </Button>
    )}
  </p>
);

function EngagementSettingsFields<T extends EngagementOverrides>({
  value, onChange, members, membersStatus = 'ready', onRetryMembers, currentUser, disabled: busy, readOnly = false,
  followed, inherited, idPrefix,
}: Props<T>): React.ReactElement {
  const disabled = busy || readOnly;
  // A draft: each detail is the report's override or the project's.
  const drafting = !readOnly && followed != null;
  const overridden = (key: DetailKey) => !drafting || isOverridden(value, key);
  const set = <K extends DetailKey>(key: K, v: EngagementSettings[K]) => onChange({ ...value, [key]: v });
  const override = (key: DetailKey) => {
    const from = followed?.[key];
    set(key, (Array.isArray(from) ? from.map((e) => ({ ...e })) : (from ?? '')) as EngagementSettings[typeof key]);
  };
  const revert = (key: DetailKey) => {
    const next = { ...value };
    delete next[key];
    onChange(next);
  };
  // What a field shows: the report's own, else (a draft) what it follows.
  const shown = <K extends DetailKey>(key: K): EngagementSettings[K] | undefined =>
    (overridden(key) ? value[key] : followed?.[key]) as EngagementSettings[K] | undefined;
  const text = (key: TextKey) => shown(key) ?? '';
  // A followed detail is the project's: shown, not edited here.
  const locked = (key: DetailKey) => disabled || !overridden(key);
  // In a draft an emptied override stays one until saved (the server then
  // reads it as following); in the defaults an empty text is null.
  const typed = (key: TextKey, v: string) => set(key, (drafting ? v : (v || null)) as EngagementSettings[TextKey]);
  const onText = (key: TextKey) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => typed(key, e.target.value);
  const sourceId = (key: DetailKey) => `${idPrefix}-${key}-source`;
  const source = (key: DetailKey) => drafting && (
    <DetailSource id={sourceId(key)} label={LABELS[key]} overridden={overridden(key)} disabled={busy}
      blank={overridden(key) && typeof value[key] === 'string' && !(value[key] as string).trim()}
      onOverride={() => override(key)} onRevert={() => revert(key)} />
  );
  const describedBy = (key: DetailKey, extra?: string) =>
    [drafting ? sourceId(key) : '', extra ?? ''].filter(Boolean).join(' ') || undefined;
  // Followed and empty: the project sets none.
  const placeholder = (key: TextKey, example?: string) =>
    (!overridden(key) ? 'Not set in the project’s defaults' : (readOnly ? undefined : example));

  // The project's own report images: what the System description may place.
  // One load for the field's picker, its preview and the list under it.
  const reportImages = useReportTextImages({ enabled: !readOnly });
  // The project's defaults: an empty author or disclaimer uses the
  // installation's, said under the field.
  const installationAuthor = !readOnly && !drafting && !text('author_name').trim()
    ? (inherited?.author_name ?? '').trim() : '';
  const disclaimerEmpty = !text('disclaimer').trim();
  const installationDisclaimer = !readOnly && !drafting && disclaimerEmpty ? (inherited?.disclaimer ?? '').trim() : '';

  const testers = shown('testers') ?? [];
  const distribution = shown('distribution') ?? [];
  const ownTesters = value.testers ?? [];
  const ownDistribution = value.distribution ?? [];
  const editTeam = !readOnly && overridden('testers');
  const editDistribution = !readOnly && overridden('distribution');
  const available = members.filter((m) => !ownTesters.some((t) => t.user_id === m.user_id));
  // Offered only when the member picker cannot add them (not a member).
  const canAddSelf = !!currentUser
    && !ownTesters.some((t) => t.user_id === currentUser.id)
    && !members.some((m) => m.user_id === currentUser.id);

  // The project's analysts and admins (name, role line, email) — added once
  // each; whoever is already listed keeps what was written for them.
  const toast = useToast();
  const projectId = useProjectId();
  // Asked for by the button, each time: the team as the project has it now.
  const projectTeam = useMutation({
    mutationFn: () => getProjectReportTeam(projectId),
    onSuccess: (team) => {
      const fresh = team.filter((t) => !ownTesters.some((x) => x.user_id != null && x.user_id === t.user_id));
      if (fresh.length === 0) toast.info('Everyone on the project is already listed.');
      else set('testers', [...ownTesters, ...fresh]);
    },
    onError: (err) => toast.error(formatApiError(err, "Could not load the project's members.")),
  });
  const addingTeam = projectTeam.isPending;

  return (
    <div className="space-y-md">
      <div className="grid gap-md md:grid-cols-2 xl:grid-cols-4">
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-client`}>Client</Label>
          <Input id={`${idPrefix}-client`} maxLength={255} value={text('client_name')}
            placeholder={placeholder('client_name')} aria-describedby={describedBy('client_name')}
            onChange={onText('client_name')} disabled={locked('client_name')} />
          {source('client_name')}
        </div>
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-author`}>Author</Label>
          <Input id={`${idPrefix}-author`} maxLength={255}
            // No example on a report that cannot be edited: in a disabled
            // field it reads as the author the report was issued with.
            placeholder={placeholder('author_name', 'e.g. Example Security Ltd')}
            title="Who produced the report"
            aria-describedby={describedBy('author_name', installationAuthor ? `${idPrefix}-author-inherited` : undefined)}
            value={text('author_name')}
            onChange={onText('author_name')} disabled={locked('author_name')} />
          {source('author_name')}
          {installationAuthor && (
            <p id={`${idPrefix}-author-inherited`} title={installationAuthor}
              className="line-clamp-2 text-caption text-muted-foreground [overflow-wrap:anywhere]">
              Empty: uses the installation’s — “{installationAuthor}”
            </p>
          )}
        </div>
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-classification`}>Classification</Label>
          <Input id={`${idPrefix}-classification`} maxLength={100}
            placeholder={placeholder('classification', 'e.g. Confidential')} aria-describedby={describedBy('classification')}
            value={text('classification')} onChange={onText('classification')} disabled={locked('classification')} />
          {source('classification')}
        </div>
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-type`}>Engagement type</Label>
          <Input id={`${idPrefix}-type`} maxLength={100}
            placeholder={placeholder('engagement_type', 'e.g. Internal network penetration test')}
            aria-describedby={describedBy('engagement_type')}
            value={text('engagement_type')} onChange={onText('engagement_type')} disabled={locked('engagement_type')} />
          {source('engagement_type')}
        </div>
      </div>

      <fieldset id={`${idPrefix}-team`} className="min-w-0 space-y-xs" aria-describedby={describedBy('testers')}>
        <legend className="text-body font-medium">Assessment team</legend>
        {source('testers')}
        {testers.length === 0 && (
          <p className="text-caption text-muted-foreground">Nobody listed yet.</p>
        )}
        {/* The order here is the order the report prints. */}
        <ReorderableRows items={testers} noun="team member" editable={editTeam} disabled={locked('testers')}
          onChange={(next) => set('testers', next)}>
          {(t, i) => (
            <>
              <Input aria-label={`Team member ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
                value={t.name} disabled={locked('testers')}
                onChange={(e) => set('testers', ownTesters.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <Input aria-label={`Team member ${i + 1} role`} className="min-w-0 flex-1" maxLength={200}
                placeholder="Role" value={t.role ?? ''} disabled={locked('testers')}
                onChange={(e) => set('testers', ownTesters.map((x, j) => (j === i ? { ...x, role: e.target.value || null } : x)))} />
              <Input aria-label={`Team member ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
                placeholder="Contact" value={t.email ?? ''} disabled={locked('testers')}
                onChange={(e) => set('testers', ownTesters.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
            </>
          )}
        </ReorderableRows>
        {editTeam && (
        <div className="flex flex-wrap items-center gap-xs">
          {membersStatus === 'error' && onRetryMembers && <MembersLoadError onRetry={onRetryMembers} />}
          {membersStatus === 'ready' && members.length === 0 && !disabled && (
            <span className="text-caption text-muted-foreground">
              Nobody is a member of this project yet, so there is no one to pick — add people by name, or add members in{' '}
              <Link to="/project-settings" className="text-info hover:underline">Project settings</Link>.
            </span>
          )}
          {canAddSelf && currentUser && (
            <Button type="button" variant="outline" size="sm" disabled={disabled}
              onClick={() => set('testers', [...ownTesters, { user_id: currentUser.id, name: currentUser.name, role: null, email: null }])}>
              <Plus className="size-4" aria-hidden /> Add yourself
            </Button>
          )}
          {members.length > 0 && (
            <Button type="button" variant="outline" size="sm" disabled={disabled || addingTeam}
              onClick={() => projectTeam.mutate()}>
              <Users className="size-4" aria-hidden /> Add the project&apos;s members
            </Button>
          )}
          {available.length > 0 && (
            <Select value="" disabled={disabled}
              onValueChange={(v) => {
                const m = members.find((x) => String(x.user_id) === v);
                if (m) set('testers', [...ownTesters, { user_id: m.user_id, name: memberName(m), role: null, email: null }]);
              }}>
              <SelectTrigger className="h-8 w-[14rem] text-caption" aria-label="Add a project member to the team">
                <SelectValue placeholder="Add a project member…" />
              </SelectTrigger>
              <SelectContent>
                {available.map((m) => (
                  <SelectItem key={m.user_id} value={String(m.user_id)}>{memberName(m)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button type="button" variant="ghost" size="sm" disabled={disabled}
            onClick={() => set('testers', [...ownTesters, { user_id: null, name: '', role: null, email: null }])}>
            <Plus className="size-4" aria-hidden /> Someone else
          </Button>
        </div>
        )}
      </fieldset>

      <fieldset id={`${idPrefix}-distribution`} className="min-w-0 space-y-xs" aria-describedby={describedBy('distribution')}>
        <legend className="text-body font-medium">Distribution</legend>
        {source('distribution')}
        {distribution.length === 0 && (
          <p className="text-caption text-muted-foreground">Nobody listed yet.</p>
        )}
        <ReorderableRows items={distribution} noun="recipient" editable={editDistribution}
          disabled={locked('distribution')} onChange={(next) => set('distribution', next)}>
          {(r, i) => (
            <>
              <Input aria-label={`Recipient ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
                value={r.name} disabled={locked('distribution')}
                onChange={(e) => set('distribution', ownDistribution.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <Input aria-label={`Recipient ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
                placeholder="Contact" value={r.email ?? ''} disabled={locked('distribution')}
                onChange={(e) => set('distribution', ownDistribution.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
            </>
          )}
        </ReorderableRows>
        {editDistribution && (
          <Button type="button" variant="ghost" size="sm" disabled={disabled}
            onClick={() => set('distribution', [...ownDistribution, { name: '', email: null }])}>
            <Plus className="size-4" aria-hidden /> Add recipient
          </Button>
        )}
      </fieldset>

      <div className="min-w-0 space-y-xxs">
        <Label htmlFor={`${idPrefix}-system`}>System description</Label>
        <p className="text-caption text-muted-foreground">
          What was assessed, in the client&apos;s terms. Markdown. The scope&apos;s networks and domains are listed after it.
        </p>
        <MarkdownField id={`${idPrefix}-system`} label="System description" rows={5} maxLength={32768}
          value={text('system_description')} onChange={(v) => typed('system_description', v)}
          disabled={locked('system_description')}
          images={locked('system_description') ? undefined : reportImages.markdown} />
        {source('system_description')}
        {!readOnly && <ReportTextImages idPrefix={idPrefix} state={reportImages} disabled={disabled} />}
      </div>

      <fieldset className="min-w-0 space-y-xs">
        <legend className="text-body font-medium">Other targets</legend>
        <p className="text-caption text-muted-foreground">
          If applicable — each list appears in the report only when written. Markdown; one target per line works well.
        </p>
        <div className="grid gap-md md:grid-cols-3">
          {(['applications', 'thick_clients', 'other_targets'] as const).map((key) => (
            <div key={key} className="min-w-0 space-y-xxs">
              <Label htmlFor={`${idPrefix}-${key}`}>{LABELS[key]}</Label>
              <Textarea id={`${idPrefix}-${key}`} rows={3} maxLength={32768} value={text(key)}
                placeholder={placeholder(key)} aria-describedby={describedBy(key)}
                onChange={onText(key)} disabled={locked(key)} />
              {source(key)}
            </div>
          ))}
        </div>
      </fieldset>

      <div className="min-w-0 space-y-xxs">
        <Label htmlFor={`${idPrefix}-disclaimer`}>Disclaimer</Label>
        <MarkdownField id={`${idPrefix}-disclaimer`} label="Disclaimer" rows={4} maxLength={32768}
          value={text('disclaimer')} onChange={(v) => typed('disclaimer', v)} disabled={locked('disclaimer')} />
        {source('disclaimer')}
        {installationDisclaimer && (
          // Any length: closed, it is one line; open, it scrolls inside itself.
          <details className="text-caption text-muted-foreground" data-testid={`${idPrefix}-disclaimer-inherited`}>
            <summary className="cursor-pointer">Empty: uses the installation’s disclaimer. Show it</summary>
            <div className="mt-xxs max-h-64 overflow-y-auto rounded-control border border-border px-sm py-xs">
              <SafeMarkdown text={installationDisclaimer} />
            </div>
          </details>
        )}
        {installationDisclaimer && (
          <Button type="button" variant="ghost" size="sm" className="h-7 px-xs text-caption" disabled={disabled}
            onClick={() => set('disclaimer', installationDisclaimer)}>
            <Copy className="size-3.5" aria-hidden /> Edit a copy
          </Button>
        )}
        {readOnly && disclaimerEmpty && (
          <p className="text-caption text-muted-foreground">None was written: the template’s own disclaimer was printed.</p>
        )}
        {!readOnly && disclaimerEmpty && !installationDisclaimer && !overridden('disclaimer') && (
          <p className="text-caption text-muted-foreground">The project sets none: the template’s own disclaimer is printed.</p>
        )}
        {!readOnly && !drafting && disclaimerEmpty && !installationDisclaimer && (
          <p className="text-caption text-muted-foreground">Empty: the template’s own disclaimer is printed.</p>
        )}
      </div>
    </div>
  );
}

/** Rows with no name would fail validation; drop them before saving.  A list
 *  a draft does not override stays absent (it follows the project). */
export const cleanSettings = <T extends EngagementOverrides>(s: T): T => ({
  ...s,
  ...(s.testers ? { testers: s.testers.filter((t) => t.name.trim()).map((t) => ({ ...t, name: t.name.trim() })) } : {}),
  ...(s.distribution
    ? { distribution: s.distribution.filter((r) => r.name.trim()).map((r) => ({ ...r, name: r.name.trim() })) }
    : {}),
});

export default EngagementSettingsFields;
