/**
 * Admin-only dialog for managing another user's project memberships.
 *
 * Opens from the System Settings users table.  Lets the admin:
 *  - See every project the target user belongs to (and their role).
 *  - Change a per-project role inline.
 *  - Remove the user from a project.
 *  - Add the user to a project they aren't already in.
 *
 * Backed by:
 *  - GET  /api/v1/users/{id}/memberships          (v2.59.0, admin-only)
 *  - POST   /api/v1/projects/{id}/members
 *  - PUT    /api/v1/projects/{id}/members/{user_id}
 *  - DELETE /api/v1/projects/{id}/members/{user_id}
 *
 * Note on global admins: the GET endpoint includes "implicit" rows for
 * every project a global-admin target user has access to (rows with no
 * underlying ProjectMembership row, surfaced as `role='admin'`,
 * `joined_at=null`).  Those are flagged with a tooltip and can't be
 * removed — they're capability rollups, not real memberships.  Demoting
 * the global role to Member converts them into normal memberships
 * that this dialog can manage.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import apiClient from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { InlineLoader } from './ui/inline-loader';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from './ui/tooltip';
import { useConfirm } from '../hooks/useConfirm';

interface MembershipRow {
  project_id: number;
  project_name: string;
  project_slug: string;
  project_status: string;
  project_is_default: boolean;
  project_is_archived: boolean;
  role: string;
  joined_at: string | null;
}

interface ProjectSummary {
  id: number;
  name: string;
  slug?: string;
  status?: string;
  is_archived?: boolean;
}

const PROJECT_ROLES: Array<{ value: string; label: string; help: string }> = [
  { value: 'admin', label: 'Admin', help: 'Manage membership; everything analyst can do.' },
  { value: 'analyst', label: 'Analyst', help: 'Read/write security data.' },
  { value: 'auditor', label: 'Auditor', help: 'Read-only with audit-log visibility.' },
  { value: 'viewer', label: 'Viewer', help: 'Read-only scans + hosts.' },
];

const roleVariant = (
  role: string,
): 'destructive' | 'warning' | 'info' | 'muted' => {
  if (role === 'admin') return 'destructive';
  if (role === 'analyst') return 'warning';
  if (role === 'auditor') return 'info';
  return 'muted';
};

export interface UserMembershipsDialogProps {
  /** When non-null, the dialog is open and targets this user. */
  user: {
    id: number;
    username: string;
    full_name?: string | null;
    role: string; // global role: admin|member
  } | null;
  onClose: () => void;
}

export const UserMembershipsDialog: React.FC<UserMembershipsDialogProps> = ({
  user,
  onClose,
}) => {
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [memberships, setMemberships] = useState<MembershipRow[] | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Granular saving flags keyed by project_id so the row UI can show a
  // spinner per row without blocking other rows.
  const [savingProjectId, setSavingProjectId] = useState<number | null>(null);
  const [addPickerProjectId, setAddPickerProjectId] = useState<string>('');
  const [addPickerRole, setAddPickerRole] = useState<string>('viewer');

  const reload = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    setError(null);
    try {
      const [memberRes, projectRes] = await Promise.all([
        apiClient.get<MembershipRow[]>(`/users/${user.id}/memberships`),
        apiClient.get<ProjectSummary[]>('/projects/'),
      ]);
      setMemberships(memberRes.data);
      setProjects(projectRes.data);
    } catch (err) {
      setError(formatApiError(err, "Failed to load this user's memberships."));
      setMemberships([]);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (user) {
      void reload();
    } else {
      // Clear state on close so the next open starts fresh.
      setMemberships(null);
      setProjects([]);
      setError(null);
      setAddPickerProjectId('');
      setAddPickerRole('viewer');
    }
  }, [user, reload]);

  // Projects the target user is NOT already a member of (and that aren't
  // archived).  Drives the "Add to project" picker.
  const addablProjects = useMemo(() => {
    if (!memberships) return [];
    const memberIds = new Set(memberships.map((m) => m.project_id));
    return projects
      .filter((p) => !memberIds.has(p.id) && !p.is_archived)
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [memberships, projects]);

  const handleRoleChange = async (row: MembershipRow, newRole: string) => {
    if (!user) return;
    if (row.role === newRole) return;
    setSavingProjectId(row.project_id);
    try {
      await apiClient.put(
        `/projects/${row.project_id}/members/${user.id}`,
        { role: newRole },
      );
      toast.success(`${user.username} is now ${newRole} on ${row.project_name}.`);
      await reload();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update role.'));
    } finally {
      setSavingProjectId(null);
    }
  };

  const handleRemove = async (row: MembershipRow) => {
    if (!user) return;
    // v4.57.0 (UX·4) — membership removal cuts the user's access to
    // every host / scan / plan in that project until a project admin
    // re-adds them.  Confirm before performing.  Promotions /
    // role changes within the project don't go through this handler.
    const ok = await confirm({
      title: 'Remove user from project',
      body:
        `${user.username} will lose access to ${row.project_name} immediately. ` +
        'They can be re-added later, but any active sessions tied to this project will be revoked.',
      resourceName: `${user.username} → ${row.project_name}`,
      severity: 'warning',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    setSavingProjectId(row.project_id);
    try {
      await apiClient.delete(`/projects/${row.project_id}/members/${user.id}`);
      toast.success(`Removed ${user.username} from ${row.project_name}.`);
      await reload();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to remove user from project.'));
    } finally {
      setSavingProjectId(null);
    }
  };

  const handleAdd = async () => {
    if (!user || !addPickerProjectId) return;
    const projectId = Number(addPickerProjectId);
    setSavingProjectId(projectId);
    try {
      await apiClient.post(`/projects/${projectId}/members`, {
        user_id: user.id,
        role: addPickerRole,
      });
      const project = projects.find((p) => p.id === projectId);
      toast.success(
        `Added ${user.username} to ${project?.name ?? `project ${projectId}`} as ${addPickerRole}.`,
      );
      setAddPickerProjectId('');
      setAddPickerRole('viewer');
      await reload();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to add user to project.'));
    } finally {
      setSavingProjectId(null);
    }
  };

  return (
    <Dialog open={user !== null} onOpenChange={(open) => !open && onClose()}>
      {confirmEl}
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            Manage project memberships
            {user && (
              <span className="ml-xs text-muted-foreground">
                · {user.full_name || user.username}
              </span>
            )}
          </DialogTitle>
          <DialogDescription>
            Add or remove this user from projects, or change their per-project
            role.  Global admins implicitly have access to every project;
            those rows show below but can&apos;t be removed individually —
            change their account role under the user&apos;s row instead.
          </DialogDescription>
        </DialogHeader>

        {/* v4.56.0 (UX·2) — wrap the variable-length content in
            DialogBody so it scrolls instead of getting clipped by
            DialogContent's max-h-[85vh] + overflow-hidden.  Pre-fix
            the membership table and the add-to-project row could
            push the Close button off-screen on short viewports or
            for users with many memberships, blocking the task. */}
        <DialogBody>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {loading && memberships === null ? (
          <InlineLoader label="Loading memberships…" centered />
        ) : (
          <>
            {memberships && memberships.length === 0 ? (
              <p className="py-md text-center text-metadata text-muted-foreground">
                This user has no project memberships yet.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Project</TableHead>
                      <TableHead className="w-40">Role</TableHead>
                      <TableHead className="w-12 text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {memberships?.map((row) => {
                      const isImplicit = row.joined_at === null;
                      const isSaving = savingProjectId === row.project_id;
                      return (
                        <TableRow key={row.project_id}>
                          <TableCell>
                            <div className="min-w-0">
                              <p className="text-metadata font-medium">
                                {row.project_name}
                              </p>
                              <p className="text-caption text-muted-foreground">
                                {row.project_status}
                                {row.project_is_archived && ' · archived'}
                                {isImplicit && ' · implicit (global admin)'}
                              </p>
                            </div>
                          </TableCell>
                          <TableCell>
                            {isImplicit ? (
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Badge
                                    variant={roleVariant(row.role)}
                                    className="whitespace-nowrap"
                                  >
                                    {row.role}
                                  </Badge>
                                </TooltipTrigger>
                                <TooltipContent>
                                  Global-admin reach — can&apos;t be changed
                                  per project.
                                </TooltipContent>
                              </Tooltip>
                            ) : (
                              <Select
                                value={row.role}
                                onValueChange={(v) => handleRoleChange(row, v)}
                                disabled={isSaving}
                              >
                                <SelectTrigger>
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {PROJECT_ROLES.map((opt) => (
                                    <SelectItem key={opt.value} value={opt.value}>
                                      {opt.label}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {isImplicit ? (
                              <span className="text-caption text-muted-foreground">
                                —
                              </span>
                            ) : (
                              <Button
                                size="icon"
                                variant="ghost"
                                onClick={() => handleRemove(row)}
                                disabled={isSaving}
                                aria-label={`Remove from ${row.project_name}`}
                              >
                                {isSaving ? (
                                  <Loader2 className="size-4 animate-spin" aria-hidden />
                                ) : (
                                  <Trash2 className="size-4 text-destructive" aria-hidden />
                                )}
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}

            {/* Add-to-project row.  Hidden when there's no addable project
                (e.g. user is already a member of everything not archived). */}
            {addablProjects.length > 0 && (
              <div className="mt-md rounded-control border border-dashed border-border p-sm">
                <p className="mb-xs text-metadata font-semibold">
                  Add to a project
                </p>
                <div className="flex flex-wrap items-end gap-xs">
                  <div className="min-w-[200px] flex-1">
                    <Select
                      value={addPickerProjectId}
                      onValueChange={setAddPickerProjectId}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Pick a project…" />
                      </SelectTrigger>
                      <SelectContent>
                        {addablProjects.map((p) => (
                          <SelectItem key={p.id} value={String(p.id)}>
                            {p.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="w-40">
                    <Select
                      value={addPickerRole}
                      onValueChange={setAddPickerRole}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {PROJECT_ROLES.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    onClick={handleAdd}
                    disabled={!addPickerProjectId || savingProjectId !== null}
                  >
                    {savingProjectId !== null &&
                    savingProjectId === Number(addPickerProjectId) ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <Plus className="size-4" aria-hidden />
                    )}
                    Add
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default UserMembershipsDialog;
