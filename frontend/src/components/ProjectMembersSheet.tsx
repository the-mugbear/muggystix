/**
 * Project members side-sheet for the Portfolio / SoC-manager view
 * (SOC-P1/P2).  Views a project's roster (name + role) and — for a
 * project admin or global admin — manages it inline (add / change role /
 * remove) via the existing /projects/{id}/members endpoints.
 */
import React from 'react';
import { Loader2, RefreshCw, Trash2, UserPlus } from 'lucide-react';
import { toast } from 'sonner';

import {
  ProjectMember,
  UserDirectoryEntry,
  getProjectMembers,
  getUserDirectory,
  addProjectMember,
  updateProjectMemberRole,
  removeProjectMember,
} from '../services/api';
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

const ROLES = ['viewer', 'auditor', 'analyst', 'admin'] as const;
type RoleTone = 'destructive' | 'success' | 'info' | 'muted';
const roleTone = (role: string): RoleTone =>
  role === 'admin' ? 'destructive' : role === 'analyst' ? 'success' : role === 'auditor' ? 'info' : 'muted';

export interface ProjectMembersSheetProps {
  projectId: number | null;
  projectName: string;
  canManage: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after the roster changes so the caller can refresh counts. */
  onChanged?: () => void;
}

export const ProjectMembersSheet: React.FC<ProjectMembersSheetProps> = ({
  projectId, projectName, canManage, open, onOpenChange, onChanged,
}) => {
  const [members, setMembers] = React.useState<ProjectMember[] | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [busyUserId, setBusyUserId] = React.useState<number | null>(null);

  const [directory, setDirectory] = React.useState<UserDirectoryEntry[]>([]);
  const [addUserId, setAddUserId] = React.useState<string>('');
  const [addRole, setAddRole] = React.useState<string>('viewer');
  const [adding, setAdding] = React.useState(false);

  const load = React.useCallback(async () => {
    if (projectId == null) return;
    setLoading(true);
    setError(null);
    try {
      setMembers(await getProjectMembers(projectId));
      if (canManage) {
        try { setDirectory(await getUserDirectory()); } catch { /* picker optional */ }
      }
    } catch (err) {
      setError(formatApiError(err, 'Failed to load members.'));
      setMembers(null);
    } finally {
      setLoading(false);
    }
  }, [projectId, canManage]);

  React.useEffect(() => {
    if (open && projectId != null) load();
  }, [open, projectId, load]);

  const memberIds = new Set((members ?? []).map((m) => m.user_id));
  const available = directory.filter((u) => !memberIds.has(u.id));

  const handleAdd = async () => {
    if (projectId == null || !addUserId) return;
    setAdding(true);
    try {
      await addProjectMember(projectId, Number(addUserId), addRole);
      setAddUserId('');
      setAddRole('viewer');
      toast.success('Member added.');
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to add member.'));
    } finally {
      setAdding(false);
    }
  };

  const handleRole = async (m: ProjectMember, role: string) => {
    if (projectId == null || role === m.role) return;
    setBusyUserId(m.user_id);
    try {
      await updateProjectMemberRole(projectId, m.user_id, role);
      toast.success('Role updated.');
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update role.'));
    } finally {
      setBusyUserId(null);
    }
  };

  const handleRemove = async (m: ProjectMember) => {
    if (projectId == null) return;
    if (!window.confirm(`Remove ${m.full_name || m.username} from ${projectName}?`)) return;
    setBusyUserId(m.user_id);
    try {
      await removeProjectMember(projectId, m.user_id);
      toast.success('Member removed.');
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to remove member.'));
    } finally {
      setBusyUserId(null);
    }
  };

  return (
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
                          {ROLES.map((r) => (
                            <SelectItem key={r} value={r} className="capitalize">{r}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <Button size="sm" onClick={handleAdd} disabled={!addUserId || adding}>
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
                            onValueChange={(v) => handleRole(m, v)}
                            disabled={busyUserId === m.user_id}
                          >
                            <SelectTrigger aria-label={`Role for ${m.full_name || m.username}`}>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {ROLES.map((r) => (
                                <SelectItem key={r} value={r} className="capitalize">{r}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      ) : (
                        <Badge variant={roleTone(m.role)} className="capitalize">{m.role}</Badge>
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
  );
};

export default ProjectMembersSheet;
