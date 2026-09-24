/**
 * Engagement details of a client report (v5.261.0): client, classification,
 * engagement type, assessment team, distribution list and system description.
 * Controlled — the project's report defaults and each draft use the same
 * fields.  The team is picked from project members (name and role editable).
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { Plus, Trash2, Users } from 'lucide-react';

import { getProjectReportTeam, type EngagementSettings, type ProjectMember } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../ui/select';
import { Textarea } from '../ui/textarea';

export const emptySettings = (): EngagementSettings => ({
  client_name: null, classification: null, engagement_type: null,
  testers: [], distribution: [], system_description: null,
  applications: null, thick_clients: null, other_targets: null,
});

interface Props {
  value: EngagementSettings;
  onChange: (next: EngagementSettings) => void;
  members: ProjectMember[];
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
  idPrefix: string;
}

const memberName = (m: ProjectMember) => m.full_name || m.username || `User ${m.user_id}`;

const EngagementSettingsFields: React.FC<Props> = ({
  value, onChange, members, currentUser, disabled: busy, readOnly = false, idPrefix,
}) => {
  const disabled = busy || readOnly;
  const set = <K extends keyof EngagementSettings>(key: K, v: EngagementSettings[K]) =>
    onChange({ ...value, [key]: v });
  const text = (key: 'client_name' | 'classification' | 'engagement_type' | 'system_description') =>
    value[key] ?? '';
  const onText = (key: 'client_name' | 'classification' | 'engagement_type' | 'system_description') =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => set(key, e.target.value || null);

  const available = members.filter((m) => !value.testers.some((t) => t.user_id === m.user_id));
  // Offered only when the member picker cannot add them (not a member).
  const canAddSelf = !!currentUser
    && !value.testers.some((t) => t.user_id === currentUser.id)
    && !members.some((m) => m.user_id === currentUser.id);

  // The project's analysts and admins (name, role line, email) — added once
  // each; whoever is already listed keeps what was written for them.
  const toast = useToast();
  const [addingTeam, setAddingTeam] = React.useState(false);
  const addProjectTeam = async () => {
    setAddingTeam(true);
    try {
      const team = await getProjectReportTeam();
      const fresh = team.filter((t) => !value.testers.some((x) => x.user_id != null && x.user_id === t.user_id));
      if (fresh.length === 0) toast.info('Everyone on the project is already listed.');
      else set('testers', [...value.testers, ...fresh]);
    } catch (err) {
      toast.error(formatApiError(err, "Could not load the project's members."));
    } finally {
      setAddingTeam(false);
    }
  };

  return (
    <div className="space-y-md">
      <div className="grid gap-md md:grid-cols-3">
        <div className="min-w-0 space-y-xxs">
          <Label htmlFor={`${idPrefix}-client`}>Client</Label>
          <Input id={`${idPrefix}-client`} maxLength={255} value={text('client_name')}
            onChange={onText('client_name')} disabled={disabled} />
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
        {value.testers.map((t, i) => (
          <div key={`${t.user_id ?? 'x'}-${i}`} className="flex flex-wrap items-center gap-xs">
            <Input aria-label={`Team member ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
              value={t.name} disabled={disabled}
              onChange={(e) => set('testers', value.testers.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
            <Input aria-label={`Team member ${i + 1} role`} className="min-w-0 flex-1" maxLength={200}
              placeholder="Role" value={t.role ?? ''} disabled={disabled}
              onChange={(e) => set('testers', value.testers.map((x, j) => (j === i ? { ...x, role: e.target.value || null } : x)))} />
            <Input aria-label={`Team member ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
              placeholder="Contact" value={t.email ?? ''} disabled={disabled}
              onChange={(e) => set('testers', value.testers.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
            {!readOnly && (
              <Button type="button" variant="ghost" size="icon" disabled={disabled}
                aria-label={`Remove ${t.name || 'team member'}`}
                onClick={() => set('testers', value.testers.filter((_, j) => j !== i))}>
                <Trash2 className="size-4" aria-hidden />
              </Button>
            )}
          </div>
        ))}
        {!readOnly && (
        <div className="flex flex-wrap items-center gap-xs">
          {members.length === 0 && !disabled && (
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
              onClick={() => void addProjectTeam()}>
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
        {value.distribution.map((r, i) => (
          <div key={i} className="flex flex-wrap items-center gap-xs">
            <Input aria-label={`Recipient ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
              value={r.name} disabled={disabled}
              onChange={(e) => set('distribution', value.distribution.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
            <Input aria-label={`Recipient ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
              placeholder="Contact" value={r.email ?? ''} disabled={disabled}
              onChange={(e) => set('distribution', value.distribution.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
            {!readOnly && (
              <Button type="button" variant="ghost" size="icon" disabled={disabled}
                aria-label={`Remove ${r.name || 'recipient'}`}
                onClick={() => set('distribution', value.distribution.filter((_, j) => j !== i))}>
                <Trash2 className="size-4" aria-hidden />
              </Button>
            )}
          </div>
        ))}
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
        <Textarea id={`${idPrefix}-system`} rows={5} maxLength={32768} value={text('system_description')}
          onChange={onText('system_description')} disabled={disabled} />
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
