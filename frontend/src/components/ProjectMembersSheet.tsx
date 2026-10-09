/**
 * Project members side-sheet for the Portfolio / SoC-manager view
 * (SOC-P1/P2).  Views a project's roster (name + role) and — for a
 * project admin or global admin — manages it inline (add / change role /
 * remove) via the existing /projects/{id}/members endpoints.
 */
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, RefreshCw, Trash2, UserPlus } from 'lucide-react';
import { toast } from 'sonner';

import {
  ProjectMember,
  listProjectMembers,
  getUserDirectory,
  addProjectMember,
  updateProjectMemberRole,
  removeProjectMember,
} from '../services/api';
import { queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import {
  SideSheet,
  SideSheetContent,
  SideSheetHeader,
  SideSheetTitle,
  SideSheetBody,
} from './ui/side-sheet';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from './ui/select';
import { useConfirm } from '../hooks/useConfirm';
import { useAuth } from '../contexts/AuthContext';
import {
  PROJECT_ROLES, allowMemberChange, countProjectAdmins, memberName, projectRoleLabel,
  removalDecision, roleChangeDecision, type MemberChange,
} from '../utils/projectMembers';

type RoleTone = 'destructive' | 'success' | 'info' | 'muted';
const roleTone = (role: string): RoleTone =>
  role === 'admin' ? 'destructive' : role === 'analyst' ? 'success' : role === 'auditor' ? 'info' : 'muted';

export interface ProjectMembersSheetProps {
  projectId: number | null;
  projectName: string;
  canManage: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const ProjectMembersSheet: React.FC<ProjectMembersSheetProps> = ({
  projectId, projectName, canManage, open, onOpenChange,
}) => {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [confirmDialog, confirm] = useConfirm();
  const [addUserId, setAddUserId] = React.useState<string>('');
  const [addRole, setAddRole] = React.useState<string>('viewer');

  // The project is this sheet's argument (any project on Portfolio), not the
  // one the app is in: the key names the project it was given.
  const roster = useQuery({
    queryKey: ['listProjectMembers', projectId],
    queryFn: ({ signal }) => listProjectMembers(projectId as number, signal),
    enabled: open && projectId != null,
  });
  // The picker is optional: asked for once the roster is there, and a failure
  // leaves it empty.
  const users = useQuery({
    queryKey: ['getUserDirectory'],
    queryFn: ({ signal }) => getUserDirectory(signal),
    enabled: open && projectId != null && canManage && roster.isSuccess,
  });
  const members = roster.data ?? null;
  const loading = roster.isFetching || users.isFetching;
  const error = queryErrorText(roster.error, 'Failed to load members.');
  const load = () => { void roster.refetch(); };

  const memberIds = new Set((members ?? []).map((m) => m.user_id));
  const available = (users.data ?? []).filter((u) => !memberIds.has(u.id));

  // Who is on a project is shown here, in the pickers of that project
  // (`listProjectMembers`, the roster hook's key) and as Portfolio's count.
  const rosterChanged = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['listProjectMembers'] }),
    queryClient.invalidateQueries({ queryKey: ['getPortfolioDashboard'] }),
  ]);

  const adding = useMutation({
    mutationFn: (body: { userId: number; role: string }) =>
      addProjectMember(projectId as number, body.userId, body.role),
    onSuccess: () => {
      setAddUserId('');
      setAddRole('viewer');
      toast.success('Member added.');
      return rosterChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to add member.')),
  });
  const handleAdd = () => {
    if (projectId == null || !addUserId) return;
    adding.mutate({ userId: Number(addUserId), role: addRole });
  };

  const changingRole = useMutation({
    mutationFn: (body: { userId: number; role: string }) =>
      updateProjectMemberRole(projectId as number, body.userId, body.role),
    onSuccess: () => {
      toast.success('Role updated.');
      return rosterChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update role.')),
  });
  // What is asked or refused first is `utils/projectMembers` — the same rules
  // and words as Project settings and the administrator's dialog.
  const changeOf = (m: ProjectMember): MemberChange => ({
    name: memberName(m),
    role: m.role,
    projectName,
    isSelf: m.user_id === user?.id,
    adminCount: countProjectAdmins(members ?? []),
  });
  const refuse = (reason: string) => { toast.error(reason); };
  const handleRole = async (m: ProjectMember, role: string) => {
    if (projectId == null || role === m.role) return;
    if (!(await allowMemberChange(roleChangeDecision(changeOf(m), role), confirm, refuse))) return;
    changingRole.mutate({ userId: m.user_id, role });
  };

  const removing = useMutation({
    mutationFn: (userId: number) => removeProjectMember(projectId as number, userId),
    onSuccess: () => {
      toast.success('Member removed.');
      return rosterChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to remove member.')),
  });
  const handleRemove = async (m: ProjectMember) => {
    if (projectId == null) return;
    if (!(await allowMemberChange(removalDecision(changeOf(m)), confirm, refuse))) return;
    removing.mutate(m.user_id);
  };
  const busyUserId = changingRole.isPending
    ? changingRole.variables.userId
    : removing.isPending ? removing.variables : null;

  return (
    <>
    <SideSheet open={open} onOpenChange={onOpenChange}>
      <SideSheetContent>
        <SideSheetHeader>
          <SideSheetTitle>
            Members — <span className="font-normal text-muted-foreground">{projectName}</span>
          </SideSheetTitle>
        </SideSheetHeader>
        <SideSheetBody>
          {loading ? (
            <div className="flex items-center gap-xs text-metadata text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Loading members…
            </div>
          ) : error ? (
            <Alert variant="destructive">
              <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
                <span className="break-words">{error}</span>
                <Button size="sm" variant="outline" onClick={load}>
                  <RefreshCw className="size-3.5" aria-hidden /> Retry
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <div className="flex flex-col gap-sm">
              {canManage && (
                <div className="rounded-panel border border-border p-sm">
                  <p className="mb-xs text-metadata font-semibold">Add member</p>
                  <div className="flex flex-wrap items-center gap-xs">
                    <div className="min-w-[12rem] flex-1">
                      <Select value={addUserId} onValueChange={setAddUserId}>
                        <SelectTrigger aria-label="Select a user to add">
                          <SelectValue placeholder={available.length ? 'Select user…' : 'No available users'} />
                        </SelectTrigger>
                        <SelectContent>
                          {available.map((u) => (
                            <SelectItem key={u.id} value={String(u.id)}>
                              {u.full_name || u.username}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="w-[8rem]">
                      <Select value={addRole} onValueChange={setAddRole}>
                        <SelectTrigger aria-label="Role for the new member">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PROJECT_ROLES.map((r) => (
                            <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <Button size="sm" onClick={handleAdd} disabled={!addUserId || adding.isPending}>
                      <UserPlus className="size-4" aria-hidden /> Add
                    </Button>
                  </div>
                </div>
              )}

              {(members ?? []).length === 0 ? (
                <p className="text-metadata text-muted-foreground">No members yet.</p>
              ) : (
                <ul className="flex flex-col divide-y divide-border">
                  {(members ?? []).map((m) => (
                    <li key={m.id} className="flex flex-wrap items-center gap-xs py-sm">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-metadata font-medium text-foreground">
                          {m.full_name || m.username}
                        </p>
                        {m.full_name && m.username && (
                          <p className="truncate text-caption text-muted-foreground">@{m.username}</p>
                        )}
                      </div>
                      {canManage ? (
                        <div className="w-[8rem]">
                          <Select
                            value={m.role}
                            onValueChange={(v) => void handleRole(m, v)}
                            disabled={busyUserId === m.user_id}
                          >
                            <SelectTrigger aria-label={`Role for ${m.full_name || m.username}`}>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {PROJECT_ROLES.map((r) => (
                                <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      ) : (
                        <Badge variant={roleTone(m.role)}>{projectRoleLabel(m.role)}</Badge>
                      )}
                      {canManage && (
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Remove ${m.full_name || m.username}`}
                          disabled={busyUserId === m.user_id}
                          onClick={() => handleRemove(m)}
                        >
                          <Trash2 className="size-4" aria-hidden />
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </SideSheetBody>
      </SideSheetContent>
    </SideSheet>
    {confirmDialog}
    </>
  );
};

export default ProjectMembersSheet;
