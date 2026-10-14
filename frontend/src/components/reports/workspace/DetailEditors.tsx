/**
 * The two list editors of a report's details — the assessment team and the
 * distribution list — and their read-only rows.  Moved here from the old
 * `EngagementSettingsFields` for the report workspace; the draft and the
 * project's report defaults use the same ones.  The order shown is the order
 * the report prints (`ReorderableRows`).
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Plus, Users } from 'lucide-react';

import {
  getProjectReportTeam, type ProjectMember, type ReportRecipient, type ReportTester,
} from '../../../services/api';
import { useToast } from '../../../contexts/ToastContext';
import { useProjectId } from '../../../hooks/useProjectId';
import type { ProjectMembersStatus } from '../../../hooks/useProjectMembers';
import { formatApiError } from '../../../utils/apiErrors';
import MembersLoadError from '../../MembersLoadError';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../ui/select';
import ReorderableRows from '../ReorderableRows';

const memberName = (m: ProjectMember) => m.full_name || m.username || `User ${m.user_id}`;

/** A list as text: one person a line. */
export const DetailRows: React.FC<{ rows: ReadonlyArray<ReportTester | ReportRecipient>; empty: string }> = ({ rows, empty }) => (
  rows.length ? (
    <ul className="max-h-48 space-y-xxs overflow-y-auto text-body">
      {rows.map((r, i) => (
        <li key={i} className="min-w-0 [overflow-wrap:anywhere]">
          <span className="font-medium">{r.name}</span>
          {'role' in r && r.role && <span className="text-muted-foreground"> — {r.role}</span>}
          {r.email && <span className="text-muted-foreground"> · {r.email}</span>}
        </li>
      ))}
    </ul>
  ) : <p className="text-body italic text-muted-foreground">{empty}</p>
);

export const TeamEditor: React.FC<{
  value: ReportTester[];
  onChange: (next: ReportTester[]) => void;
  members: ProjectMember[];
  membersStatus?: ProjectMembersStatus;
  onRetryMembers?: () => void;
  /** Whoever is signed in: a global admin need not be a member. */
  currentUser?: { id: number; name: string } | null;
  disabled?: boolean;
}> = ({ value: testers, onChange, members, membersStatus = 'ready', onRetryMembers, currentUser, disabled = false }) => {
  const toast = useToast();
  const projectId = useProjectId();
  const available = members.filter((m) => !testers.some((t) => t.user_id === m.user_id));
  const canAddSelf = !!currentUser
    && !testers.some((t) => t.user_id === currentUser.id)
    && !members.some((m) => m.user_id === currentUser.id);
  // The project's analysts and admins as the project has them now — added
  // once each; whoever is already listed keeps what was written for them.
  const projectTeam = useMutation({
    mutationFn: () => getProjectReportTeam(projectId),
    onSuccess: (team) => {
      const fresh = team.filter((t) => !testers.some((x) => x.user_id != null && x.user_id === t.user_id));
      if (fresh.length === 0) toast.info('Everyone on the project is already listed.');
      else onChange([...testers, ...fresh]);
    },
    onError: (err) => toast.error(formatApiError(err, "Could not load the project's members.")),
  });
  const set = (i: number, patch: Partial<ReportTester>) => onChange(testers.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <div className="space-y-xs">
      {testers.length === 0 && <p className="text-caption text-muted-foreground">Nobody listed.</p>}
      <ReorderableRows items={testers} noun="team member" editable disabled={disabled} onChange={onChange}>
        {(t, i) => (
          <>
            <Input aria-label={`Team member ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
              value={t.name} disabled={disabled} onChange={(e) => set(i, { name: e.target.value })} />
            <Input aria-label={`Team member ${i + 1} role`} className="min-w-0 flex-1" maxLength={200}
              placeholder="Role" value={t.role ?? ''} disabled={disabled}
              onChange={(e) => set(i, { role: e.target.value || null })} />
            <Input aria-label={`Team member ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
              placeholder="Contact" value={t.email ?? ''} disabled={disabled}
              onChange={(e) => set(i, { email: e.target.value || null })} />
          </>
        )}
      </ReorderableRows>
      <div className="flex flex-wrap items-center gap-xs">
        {membersStatus === 'error' && onRetryMembers && <MembersLoadError onRetry={onRetryMembers} />}
        {membersStatus === 'ready' && members.length === 0 && (
          <span className="text-caption text-muted-foreground">
            Nobody is a member of this project yet — add people by name, or add members in{' '}
            <Link to="/project-settings" className="text-info hover:underline">Project settings</Link>.
          </span>
        )}
        {canAddSelf && currentUser && (
          <Button type="button" variant="outline" size="sm" disabled={disabled}
            onClick={() => onChange([...testers, { user_id: currentUser.id, name: currentUser.name, role: null, email: null }])}>
            <Plus className="size-4" aria-hidden /> Add yourself
          </Button>
        )}
        {members.length > 0 && (
          <Button type="button" variant="outline" size="sm" disabled={disabled || projectTeam.isPending}
            onClick={() => projectTeam.mutate()}>
            <Users className="size-4" aria-hidden /> Add the project&apos;s members
          </Button>
        )}
        {available.length > 0 && (
          <Select value="" disabled={disabled}
            onValueChange={(v) => {
              const m = members.find((x) => String(x.user_id) === v);
              if (m) onChange([...testers, { user_id: m.user_id, name: memberName(m), role: null, email: null }]);
            }}>
            <SelectTrigger className="h-8 w-[14rem] text-caption" aria-label="Add a project member to the team">
              <SelectValue placeholder="Add a project member…" />
            </SelectTrigger>
            <SelectContent>
              {available.map((m) => <SelectItem key={m.user_id} value={String(m.user_id)}>{memberName(m)}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Button type="button" variant="ghost" size="sm" disabled={disabled}
          onClick={() => onChange([...testers, { user_id: null, name: '', role: null, email: null }])}>
          <Plus className="size-4" aria-hidden /> Someone else
        </Button>
      </div>
    </div>
  );
};

export const DistributionEditor: React.FC<{
  value: ReportRecipient[];
  onChange: (next: ReportRecipient[]) => void;
  disabled?: boolean;
}> = ({ value: rows, onChange, disabled = false }) => {
  const set = (i: number, patch: Partial<ReportRecipient>) => onChange(rows.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <div className="space-y-xs">
      {rows.length === 0 && <p className="text-caption text-muted-foreground">Nobody listed.</p>}
      <ReorderableRows items={rows} noun="recipient" editable disabled={disabled} onChange={onChange}>
        {(r, i) => (
          <>
            <Input aria-label={`Recipient ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
              value={r.name} disabled={disabled} onChange={(e) => set(i, { name: e.target.value })} />
            <Input aria-label={`Recipient ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
              placeholder="Contact" value={r.email ?? ''} disabled={disabled}
              onChange={(e) => set(i, { email: e.target.value || null })} />
          </>
        )}
      </ReorderableRows>
      <Button type="button" variant="ghost" size="sm" disabled={disabled}
        onClick={() => onChange([...rows, { name: '', email: null }])}>
        <Plus className="size-4" aria-hidden /> Add recipient
      </Button>
    </div>
  );
};
