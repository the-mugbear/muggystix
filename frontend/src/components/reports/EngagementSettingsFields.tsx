/**
 * Engagement details of a client report (v5.261.0): client, author,
 * classification, engagement type, assessment team, distribution list, system
 * description (with the project's own report images, `ReportTextImages`),
 * other targets and disclaimer.  The author and the disclaimer are INHERITED
 * when empty (the project's, else the installation's): the field says what
 * the report will then print.
 * Controlled — the project's report defaults and each draft use the same
 * fields.  The team is picked from project members (name and role editable).
 * The team and the distribution list print in the order shown; their rows are
 * reordered, and removed, by `ReorderableRows`.
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Copy, Plus, Users } from 'lucide-react';

import {
  getProjectReportTeam, type EngagementSettings, type InheritedReportDetails, type ProjectMember,
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

interface Props {
  value: EngagementSettings;
  onChange: (next: EngagementSettings) => void;
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
   *  add / remove control is rendered at all.  Before, an issued report
   *  still showed "Add the project's members", "Someone else" and "Add
   *  recipient", disabled. */
  readOnly?: boolean;
  /** What the author and the disclaimer print when left empty here: the
   *  installation's values for the project's defaults (`ReportProfile.inherited`),
   *  the project's or else the installation's for a draft (`ClientReport.inherited`). */
  inherited?: InheritedReportDetails | null;
  /** Where `inherited` comes from, for the words under an empty field. */
  inheritedFrom?: 'installation' | 'project';
  idPrefix: string;
}

const memberName = (m: ProjectMember) => m.full_name || m.username || `User ${m.user_id}`;

const INHERITED_WORDS = {
  installation: 'uses the installation’s',
  project: 'uses the project’s or installation’s default',
} as const;

type TextKey = 'client_name' | 'classification' | 'engagement_type' | 'system_description' | 'author_name';

const EngagementSettingsFields: React.FC<Props> = ({
  value, onChange, members, membersStatus = 'ready', onRetryMembers, currentUser, disabled: busy, readOnly = false,
  inherited, inheritedFrom = 'installation', idPrefix,
}) => {
  const disabled = busy || readOnly;
  const set = <K extends keyof EngagementSettings>(key: K, v: EngagementSettings[K]) =>
    onChange({ ...value, [key]: v });
  const text = (key: TextKey) => value[key] ?? '';
  const onText = (key: TextKey) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => set(key, e.target.value || null);

  // The project's own report images: what the System description may place.
  // One load for the field's picker, its preview and the list under it.
  const reportImages = useReportTextImages({ enabled: !readOnly });
  // Inherited when empty — said under the field, never in an issued report
  // (it shows what it holds).
  const inheritedAuthor = !readOnly && !text('author_name').trim() ? (inherited?.author_name ?? '').trim() : '';
  const disclaimerEmpty = !(value.disclaimer ?? '').trim();
  const inheritedDisclaimer = !readOnly && disclaimerEmpty ? (inherited?.disclaimer ?? '').trim() : '';

  const available = members.filter((m) => !value.testers.some((t) => t.user_id === m.user_id));
  // Offered only when the member picker cannot add them (not a member).
  const canAddSelf = !!currentUser
    && !value.testers.some((t) => t.user_id === currentUser.id)
    && !members.some((m) => m.user_id === currentUser.id);

  // The project's analysts and admins (name, role line, email) — added once
  // each; whoever is already listed keeps what was written for them.
  const toast = useToast();
  const projectId = useProjectId();
  // Asked for by the button, each time: the team as the project has it now.
  const projectTeam = useMutation({
    mutationFn: () => getProjectReportTeam(projectId),
    onSuccess: (team) => {
      const fresh = team.filter((t) => !value.testers.some((x) => x.user_id != null && x.user_id === t.user_id));
      if (fresh.length === 0) toast.info('Everyone on the project is already listed.');
      else set('testers', [...value.testers, ...fresh]);
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
            onChange={onText('client_name')} disabled={disabled} />
        </div>
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-author`}>Author</Label>
          <Input id={`${idPrefix}-author`} maxLength={255} placeholder="e.g. Example Security Ltd"
            title="Who produced the report"
            aria-describedby={inheritedAuthor ? `${idPrefix}-author-inherited` : undefined}
            // As issued (readOnly): what the report printed — its own, else
            // the value it inherited when it was issued.
            value={readOnly ? (text('author_name') || (inherited?.author_name ?? '')) : text('author_name')}
            onChange={onText('author_name')} disabled={disabled} />
          {inheritedAuthor && (
            <p id={`${idPrefix}-author-inherited`} title={inheritedAuthor}
              className="line-clamp-2 text-caption text-muted-foreground [overflow-wrap:anywhere]">
              Empty: {INHERITED_WORDS[inheritedFrom]} — “{inheritedAuthor}”
            </p>
          )}
        </div>
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-classification`}>Classification</Label>
          <Input id={`${idPrefix}-classification`} maxLength={100} placeholder="e.g. Confidential"
            value={text('classification')} onChange={onText('classification')} disabled={disabled} />
        </div>
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-type`}>Engagement type</Label>
          <Input id={`${idPrefix}-type`} maxLength={100} placeholder="e.g. Internal network penetration test"
            value={text('engagement_type')} onChange={onText('engagement_type')} disabled={disabled} />
        </div>
      </div>

      <fieldset className="min-w-0 space-y-xs">
        <legend className="text-body font-medium">Assessment team</legend>
        {value.testers.length === 0 && (
          <p className="text-caption text-muted-foreground">Nobody listed yet.</p>
        )}
        {/* The order here is the order the report prints. */}
        <ReorderableRows items={value.testers} noun="team member" editable={!readOnly} disabled={disabled}
          onChange={(next) => set('testers', next)}>
          {(t, i) => (
            <>
              <Input aria-label={`Team member ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
                value={t.name} disabled={disabled}
                onChange={(e) => set('testers', value.testers.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <Input aria-label={`Team member ${i + 1} role`} className="min-w-0 flex-1" maxLength={200}
                placeholder="Role" value={t.role ?? ''} disabled={disabled}
                onChange={(e) => set('testers', value.testers.map((x, j) => (j === i ? { ...x, role: e.target.value || null } : x)))} />
              <Input aria-label={`Team member ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
                placeholder="Contact" value={t.email ?? ''} disabled={disabled}
                onChange={(e) => set('testers', value.testers.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
            </>
          )}
        </ReorderableRows>
        {!readOnly && (
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
              onClick={() => set('testers', [...value.testers, { user_id: currentUser.id, name: currentUser.name, role: null, email: null }])}>
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
                if (m) set('testers', [...value.testers, { user_id: m.user_id, name: memberName(m), role: null, email: null }]);
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
            onClick={() => set('testers', [...value.testers, { user_id: null, name: '', role: null, email: null }])}>
            <Plus className="size-4" aria-hidden /> Someone else
          </Button>
        </div>
        )}
      </fieldset>

      <fieldset className="min-w-0 space-y-xs">
        <legend className="text-body font-medium">Distribution</legend>
        {value.distribution.length === 0 && (
          <p className="text-caption text-muted-foreground">Nobody listed yet.</p>
        )}
        <ReorderableRows items={value.distribution} noun="recipient" editable={!readOnly} disabled={disabled}
          onChange={(next) => set('distribution', next)}>
          {(r, i) => (
            <>
              <Input aria-label={`Recipient ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
                value={r.name} disabled={disabled}
                onChange={(e) => set('distribution', value.distribution.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <Input aria-label={`Recipient ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
                placeholder="Contact" value={r.email ?? ''} disabled={disabled}
                onChange={(e) => set('distribution', value.distribution.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
            </>
          )}
        </ReorderableRows>
        {!readOnly && (
          <Button type="button" variant="ghost" size="sm" disabled={disabled}
            onClick={() => set('distribution', [...value.distribution, { name: '', email: null }])}>
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
          value={text('system_description')} onChange={(v) => set('system_description', v || null)} disabled={disabled}
          images={readOnly ? undefined : reportImages.markdown} />
        {!readOnly && <ReportTextImages idPrefix={idPrefix} state={reportImages} disabled={disabled} />}
      </div>

      <fieldset className="min-w-0 space-y-xs">
        <legend className="text-body font-medium">Other targets</legend>
        <p className="text-caption text-muted-foreground">
          If applicable — each list appears in the report only when written. Markdown; one target per line works well.
        </p>
        <div className="grid gap-md md:grid-cols-3">
          {([
            ['applications', 'Application URLs and API endpoints'],
            ['thick_clients', 'Thick client applications'],
            ['other_targets', 'Other targets'],
          ] as const).map(([key, label]) => (
            <div key={key} className="min-w-0 space-y-xxs">
              <Label htmlFor={`${idPrefix}-${key}`}>{label}</Label>
              <Textarea id={`${idPrefix}-${key}`} rows={3} maxLength={32768} value={value[key] ?? ''}
                onChange={(e) => set(key, e.target.value || null)} disabled={disabled} />
            </div>
          ))}
        </div>
      </fieldset>

      <div className="min-w-0 space-y-xxs">
        <Label htmlFor={`${idPrefix}-disclaimer`}>Disclaimer</Label>
        <MarkdownField id={`${idPrefix}-disclaimer`} label="Disclaimer" rows={4} maxLength={32768}
          value={readOnly ? (value.disclaimer || (inherited?.disclaimer ?? '')) : (value.disclaimer ?? '')}
          onChange={(v) => set('disclaimer', v || null)} disabled={disabled} />
        {inheritedDisclaimer && (
          // Any length: closed, it is one line; open, it scrolls inside itself.
          <details className="text-caption text-muted-foreground" data-testid={`${idPrefix}-disclaimer-inherited`}>
            <summary className="cursor-pointer">
              Empty: {INHERITED_WORDS[inheritedFrom]} disclaimer. Show it
            </summary>
            <div className="mt-xxs max-h-64 overflow-y-auto rounded-control border border-border px-sm py-xs">
              <SafeMarkdown text={inheritedDisclaimer} />
            </div>
          </details>
        )}
        {inheritedDisclaimer && (
          <Button type="button" variant="ghost" size="sm" className="h-7 px-xs text-caption" disabled={disabled}
            onClick={() => set('disclaimer', inheritedDisclaimer)}>
            <Copy className="size-3.5" aria-hidden /> Edit a copy
          </Button>
        )}
        {!readOnly && disclaimerEmpty && !inheritedDisclaimer && (
          <p className="text-caption text-muted-foreground">Empty: the template’s own disclaimer is printed.</p>
        )}
      </div>
    </div>
  );
};

/** Rows with no name would fail validation; drop them before saving. */
export const cleanSettings = (s: EngagementSettings): EngagementSettings => ({
  ...s,
  testers: s.testers.filter((t) => t.name.trim()).map((t) => ({ ...t, name: t.name.trim() })),
  distribution: s.distribution.filter((r) => r.name.trim()).map((r) => ({ ...r, name: r.name.trim() })),
});

export default EngagementSettingsFields;
