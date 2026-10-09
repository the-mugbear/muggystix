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
import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import apiClient, {
  addProjectMember, getProjectMembers, getProjects, removeProjectMember, updateProjectMemberRole,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { GLOBAL, queryErrorText } from '../lib/query';
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
import {
  PROJECT_ROLES, allowMemberChange, countProjectAdmins, memberName, projectRoleLabel,
  removalDecision, roleChangeDecision, type MemberChange,
} from '../utils/projectMembers';

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

/** Key name of `GET /users/{id}/memberships` (no barrel function). */
const USER_MEMBERSHIPS = '/users/{id}/memberships';

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
  const { user: me } = useAuth();
  const [confirmEl, confirm] = useConfirm();
  const queryClient = useQueryClient();
  const [addPickerProjectId, setAddPickerProjectId] = useState<string>('');
  const [addPickerRole, setAddPickerRole] = useState<string>('viewer');
  const userId = user?.id ?? null;

  // Both lists belong to the open dialog: asked for when it opens on a user,
  // gone when it closes.
  const membershipsQuery = useQuery({
    queryKey: [GLOBAL, USER_MEMBERSHIPS, userId],
    queryFn: async () => (await apiClient.get<MembershipRow[]>(`/users/${userId}/memberships`)).data,
    enabled: userId != null,
  });
  const projectsQuery = useQuery({
    queryKey: [GLOBAL, 'getProjects'],
    queryFn: () => getProjects(),
    enabled: userId != null,
  });
  const failure = membershipsQuery.error ?? projectsQuery.error;
  const error = queryErrorText(failure, "Failed to load this user's memberships.");
  const loading = membershipsQuery.isFetching || projectsQuery.isFetching;
  // Nothing is listed unless both answered; a failure is said above an empty list.
  const memberships: MembershipRow[] | null = useMemo(() => {
    if (failure) return [];
    return projectsQuery.data ? membershipsQuery.data ?? null : null;
  }, [failure, projectsQuery.data, membershipsQuery.data]);
  const projects: ProjectSummary[] = useMemo(
    () => (failure ? [] : projectsQuery.data ?? []), [failure, projectsQuery.data],
  );

  // The next open starts with an empty picker.
  useEffect(() => {
    if (userId == null) {
      setAddPickerProjectId('');
      setAddPickerRole('viewer');
    }
  }, [userId]);

  // Projects the target user is NOT already a member of (and that aren't
  // archived).  Drives the "Add to project" picker.
  const addablProjects = useMemo(() => {
    if (!memberships) return [];
    const memberIds = new Set(memberships.map((m) => m.project_id));
    return projects
      .filter((p) => !memberIds.has(p.id) && !p.is_archived)
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [memberships, projects]);

  // A membership is shown here, on that project's roster and in its pickers.
  // The projects are read again with it, as they always were.
  const membershipsChanged = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: [GLOBAL, USER_MEMBERSHIPS] }),
    queryClient.invalidateQueries({ queryKey: [GLOBAL, 'getProjects'] }),
    queryClient.invalidateQueries({ queryKey: [GLOBAL, 'getProjectMembers'] }),
    queryClient.invalidateQueries({ queryKey: ['listProjectMembers'] }),
  ]);

  const changingRole = useMutation({
    mutationFn: ({ row, role }: { row: MembershipRow; role: string }) =>
      updateProjectMemberRole(row.project_id, userId as number, role),
    onSuccess: (_updated, { row, role }) => {
      toast.success(`${user?.username} is now ${role} on ${row.project_name}.`);
      return membershipsChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update role.')),
  });
  // What is asked or refused first is `utils/projectMembers` — the same rules
  // and words as Project settings and Portfolio's members sheet.  The server
  // gives a global administrator nothing more than a project admin on these
  // routes: the only project admin cannot be removed, and can be demoted.
  // This dialog lists one person's projects, not a project's roster, so
  // whether they are a project's ONLY admin is read when it matters — before
  // an admin row is demoted or removed.  `null`: it could not be read (said).
  const changeOf = async (row: MembershipRow): Promise<MemberChange | null> => {
    if (!user) return null;
    let adminCount = 0;
    if (row.role === 'admin') {
      try {
        const roster = await queryClient.fetchQuery({
          queryKey: [GLOBAL, 'getProjectMembers', row.project_id],
          queryFn: () => getProjectMembers(row.project_id),
        });
        adminCount = countProjectAdmins(roster);
      } catch (err) {
        toast.error(formatApiError(err, `Could not check the admins of ${row.project_name}.`));
        return null;
      }
    }
    return {
      name: memberName(user),
      role: row.role,
      projectName: row.project_name,
      isSelf: user.id === me?.id,
      adminCount,
    };
  };
  const refuse = (reason: string) => { toast.error(reason); };
  // The row whose roster is being read, so it cannot be acted on twice.
  const [checkingProjectId, setCheckingProjectId] = useState<number | null>(null);
  const checked = async (row: MembershipRow): Promise<MemberChange | null> => {
    setCheckingProjectId(row.project_id);
    try {
      return await changeOf(row);
    } finally {
      setCheckingProjectId(null);
    }
  };

  const handleRoleChange = async (row: MembershipRow, newRole: string) => {
    if (!user) return;
    if (row.role === newRole) return;
    const change = await checked(row);
    if (!change) return;
    if (!(await allowMemberChange(roleChangeDecision(change, newRole), confirm, refuse))) return;
    changingRole.mutate({ row, role: newRole });
  };

  const removing = useMutation({
    mutationFn: (row: MembershipRow) => removeProjectMember(row.project_id, userId as number),
    onSuccess: (_void, row) => {
      toast.success(`Removed ${user?.username} from ${row.project_name}.`);
      return membershipsChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to remove user from project.')),
  });

  const adding = useMutation({
    mutationFn: ({ projectId, role }: { projectId: number; role: string }) =>
      addProjectMember(projectId, userId as number, role),
    onSuccess: (_added, { projectId, role }) => {
      const project = projects.find((p) => p.id === projectId);
      toast.success(
        `Added ${user?.username} to ${project?.name ?? `project ${projectId}`} as ${role}.`,
      );
      setAddPickerProjectId('');
      setAddPickerRole('viewer');
      return membershipsChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to add user to project.')),
  });
  const handleAdd = () => {
    if (!user || !addPickerProjectId) return;
    adding.mutate({ projectId: Number(addPickerProjectId), role: addPickerRole });
  };

  // One change at a time; the row it belongs to shows the spinner.
  const savingProjectId: number | null = checkingProjectId ?? (changingRole.isPending
    ? changingRole.variables.row.project_id
    : removing.isPending
      ? removing.variables.project_id
      : adding.isPending ? adding.variables.projectId : null);

  const handleRemove = async (row: MembershipRow) => {
    if (!user) return;
    const change = await checked(row);
    if (!change) return;
    if (!(await allowMemberChange(removalDecision(change), confirm, refuse))) return;
    removing.mutate(row);
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
                                    {projectRoleLabel(row.role)}
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
                                onValueChange={(v) => void handleRoleChange(row, v)}
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
