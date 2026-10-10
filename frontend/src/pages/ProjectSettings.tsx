/**
 * /project-settings (v5.265.0) — settings for ONE project: the one chosen at
 * the top of the page.
 *
 * It used to list every project and manage the members of whichever row was
 * clicked, while tags, webhooks and deliveries below followed the top-bar
 * project — two projects on one page, silently.  Now every section is about
 * the current project, the header says which, and the cross-project list
 * (create, open another project's settings) is its own page, All projects,
 * for global administrators.
 *
 * Sections over thin rules, not cards (UI_STYLE_GUIDE §7): Details, Members,
 * Imports, Host tags, Outbound webhooks, Webhook deliveries, and a delete area
 * at the end.  What the caller may change follows their role in the project
 * (`my_role` from the API) — the server enforces the same.
 */
import { formatDate } from '../utils/relativeTime';
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Loader2, Trash2, UserPlus } from 'lucide-react';

import { useProject } from '../contexts/ProjectContext';
import { useAuth } from '../contexts/AuthContext';
import {
  addProjectMember, deleteProject as deleteProjectRequest, getUserDirectory, listProjectMembers,
  removeProjectMember, updateProject, updateProjectMemberRole,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { invalidateReads, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { useConfirm } from '../hooks/useConfirm';
import {
  DEFAULT_MEMBER_ROLE, PROJECT_ROLES, allowMemberChange, countProjectAdmins, memberName, projectRoleLabel,
  removalDecision, roleChangeDecision, type MemberChange,
} from '../utils/projectMembers';
import { safeFallback } from '../utils/uiStyles';
import PostureSection from '../components/posture/PostureSection';
import WebhookSettings from '../components/WebhookSettings';
import WebhookDeliveries from '../components/WebhookDeliveries';
import TagManagement from '../components/TagManagement';
import ProjectIngestSettings from '../components/scans/ProjectIngestSettings';
import { Button } from '../components/ui/button';
import { Combobox } from '../components/ui/combobox';
import { CharacterCount } from '../components/ui/character-count';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';

interface Member {
  id: number;
  user_id: number;
  username: string;
  full_name: string | null;
  role: string;
  joined_at?: string | null;
  created_at?: string | null;
}

interface DirectoryEntry {
  id: number;
  username: string;
  full_name: string | null;
}

const roleLabel = projectRoleLabel;

/** The API's `max_length` for a project name (ProjectCreate / ProjectUpdate). */
export const PROJECT_NAME_MAX = 100;

export const PROJECT_STATUSES = [
  { value: 'active', label: 'Active' },
  { value: 'completed', label: 'Completed' },
  { value: 'archived', label: 'Archived' },
];

const day = (s?: string | null) => formatDate(s);

interface Details { name: string; description: string; status: string; start: string; end: string }

const ProjectSettings: React.FC = () => {
  const { currentProject, projects } = useProject();
  const { user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [confirmEl, confirm] = useConfirm();

  const isGlobalAdmin = user?.role === 'admin';
  const canAdmin = currentProject?.my_role === 'admin' || isGlobalAdmin;
  // A role that has not loaded leaves the decision to the server (§40).
  const canSeeWebhooks = canAdmin || currentProject?.my_role == null;

  const queryClient = useQueryClient();
  // The project list is the context's (`['getProjects']`).  After a
  // write that changed it, it is read again in place — this page stays on
  // screen — and what the write says waits for that read.
  const projectsChanged = () => invalidateReads(queryClient, 'getProjects');

  // --- Details -----------------------------------------------------------
  // What is saved comes from the project; the form holds only what the reader
  // changed, laid over it — so a refresh that hands back the project again
  // cannot reset what is being typed, and nothing is copied into state.
  const p = currentProject;
  const saved: Details = {
    name: p?.name ?? '',
    description: p?.description ?? '',
    status: p?.status ?? 'active',
    start: p?.start_date ? p.start_date.split('T')[0] : '',
    end: p?.end_date ? p.end_date.split('T')[0] : '',
  };
  const [edits, setEdits] = useState<Partial<Details>>({});
  const details: Details = { ...saved, ...edits };
  const setDetails = (next: Details) => setEdits(next);
  const detailsDirty = JSON.stringify(details) !== JSON.stringify(saved);

  const detailsSave = useMutation({
    mutationFn: (body: { projectId: number; details: Details }) => updateProject(body.projectId, {
      name: body.details.name.trim(),
      description: body.details.description.trim(),
      status: body.details.status,
      start_date: body.details.start ? new Date(body.details.start).toISOString() : null,
      end_date: body.details.end ? new Date(body.details.end).toISOString() : null,
    }),
    onSuccess: async (updated) => {
      // The form shows the project list's row: put the server's answer there
      // first, so a failed re-read cannot show the old values under "saved".
      queryClient.setQueryData<Array<{ id: number }>>(['getProjects'], (list) => (
        list?.map((p) => (p.id === updated.id ? { ...p, ...updated } : p))
      ));
      await projectsChanged();
      setEdits({});
      toast.success('Project details saved.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the project details.')),
  });
  const savingDetails = detailsSave.isPending;
  const saveDetails = () => {
    if (!currentProject) return;
    if (!details.name.trim()) { toast.error('A project needs a name.'); return; }
    if (details.start && details.end && details.end < details.start) {
      toast.error('The end date is before the start date.');
      return;
    }
    detailsSave.mutate({ projectId: currentProject.id, details });
  };

  // --- Members -----------------------------------------------------------
  // One roster per project wherever it is shown: this page, Portfolio's sheet,
  // the admin's memberships dialog and the pickers (`hooks/useProjectMembers`)
  // read the same key, so a change made here is what the pickers show.
  const projectId = currentProject?.id;
  const membersKey = ['listProjectMembers', projectId];
  const membersQuery = useQuery({
    queryKey: membersKey,
    queryFn: async ({ signal }) => (await listProjectMembers(projectId as number, signal)) as unknown as Member[],
    enabled: !!projectId,
  });
  const members: Member[] | null = membersQuery.data ?? null;
  const membersError = queryErrorText(membersQuery.error, 'Could not load the members.');
  const loadMembers = () => { void membersQuery.refetch(); };
  /** Patch the roster on screen with what a write is known to have done. */
  const setMembers = (update: (prev: Member[]) => Member[]) => {
    queryClient.setQueryData<Member[]>(membersKey, (prev) => update(prev ?? []));
  };
  // (The pickers of this project — owner, assignee, @mention — read this same
  // entry, so a patch here is what they show: nothing more to tell them.)

  const [addOpen, setAddOpen] = useState(false);
  const [newUser, setNewUser] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<string>(DEFAULT_MEMBER_ROLE);
  // The people who could be added: asked for each time the dialog opens.
  const directoryQuery = useQuery({
    queryKey: ['getUserDirectory'],
    queryFn: ({ signal }) => getUserDirectory(signal),
    enabled: addOpen,
  });
  // Said as a failure: an empty list read "Everyone is already a member".
  const directoryFailed = directoryQuery.isError && !directoryQuery.isFetching;
  const directory: DirectoryEntry[] | null = useMemo(() => {
    if (directoryQuery.isError) return directoryQuery.isFetching ? null : [];
    return directoryQuery.data
      ? directoryQuery.data.map((u) => ({ id: u.id, username: u.username, full_name: u.full_name ?? null }))
      : null;
  }, [directoryQuery.data, directoryQuery.isError, directoryQuery.isFetching]);
  const openAdd = () => {
    setNewUser(null);
    setNewRole(DEFAULT_MEMBER_ROLE);
    setAddOpen(true);
  };
  const candidates = useMemo(
    () => (directory ?? []).filter((u) => !(members ?? []).some((m) => m.user_id === u.id)),
    [directory, members],
  );
  const addition = useMutation({
    mutationFn: (body: { projectId: number; userId: number; role: string }) =>
      addProjectMember(body.projectId, body.userId, body.role),
    onSuccess: async () => {
      setAddOpen(false);
      await queryClient.invalidateQueries({ queryKey: ['listProjectMembers'] });
      await projectsChanged();
      toast.success('Member added.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not add the member.')),
  });
  const adding = addition.isPending;
  const addMember = () => {
    if (!currentProject || !newUser) return;
    addition.mutate({ projectId: currentProject.id, userId: Number(newUser), role: newRole });
  };

  const roleChange = useMutation({
    mutationFn: (body: { projectId: number; member: Member; role: string; self: boolean }) =>
      updateProjectMemberRole(body.projectId, body.member.user_id, body.role),
    onSuccess: async (_updated, { member, role, self }) => {
      setMembers((prev) => prev.map((x) => (x.user_id === member.user_id ? { ...x, role } : x)));
      if (self) await projectsChanged();
      toast.success(`${memberName(member)} is now ${roleLabel(role)}.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not change the role.')),
  });
  const removal = useMutation({
    mutationFn: (body: { projectId: number; member: Member }) =>
      removeProjectMember(body.projectId, body.member.user_id),
    onSuccess: async (_void, { member }) => {
      setMembers((prev) => prev.filter((x) => x.user_id !== member.user_id));
      await projectsChanged();
      toast.success('Member removed.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not remove the member.')),
  });
  const deletion = useMutation({
    mutationFn: (project: { id: number; name: string }) => deleteProjectRequest(project.id),
    onSuccess: async (_void, project) => {
      await projectsChanged();
      toast.success(`Project "${project.name}" deleted.`);
      navigate('/operations');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not delete the project.')),
  });

  // What is asked or refused first is `utils/projectMembers` — the same rules
  // and words as Portfolio's members sheet and the administrator's dialog.
  const changeOf = (m: Member, projectName: string): MemberChange => ({
    name: memberName(m),
    role: m.role,
    projectName,
    isSelf: m.user_id === user?.id,
    adminCount: countProjectAdmins(members ?? []),
  });
  const refuse = (reason: string) => { toast.error(reason); };
  const changeRole = async (m: Member, role: string) => {
    if (!currentProject || role === m.role) return;
    const change = changeOf(m, currentProject.name);
    if (!(await allowMemberChange(roleChangeDecision(change, role), confirm, refuse))) return;
    roleChange.mutate({ projectId: currentProject.id, member: m, role, self: change.isSelf });
  };

  const removeMember = async (m: Member) => {
    if (!currentProject) return;
    if (!(await allowMemberChange(removalDecision(changeOf(m, currentProject.name)), confirm, refuse))) return;
    removal.mutate({ projectId: currentProject.id, member: m });
  };

  // --- Delete ------------------------------------------------------------
  const deleteProject = async () => {
    if (!currentProject) return;
    const ok = await confirm({
      title: `Delete project "${currentProject.name}"?`,
      body: (
        <>
          <p>
            This deletes the project and <strong>all</strong> data in it: scans, hosts, scopes, findings, reports,
            host tests, evidence and agent sessions. This cannot be undone.
          </p>
          <p className="mt-xs">Type the project name exactly to confirm.</p>
        </>
      ),
      resourceName: currentProject.name,
      severity: 'danger',
      confirmLabel: 'Delete project',
      confirmTypedName: true,
    });
    if (!ok) return;
    deletion.mutate({ id: currentProject.id, name: currentProject.name });
  };

  if (!currentProject) {
    return (
      <div className="p-md md:p-lg">
        <h1 className="text-page-title">Project settings</h1>
        <p className="mt-xs text-metadata text-muted-foreground">Choose a project at the top of the page to see its settings.</p>
      </div>
    );
  }

  return (
    // Full width like the other hub pages (see Reference.tsx); the forms keep
    // their own widths (the dates grid is capped at md:max-w-lg).
    <div className="space-y-lg p-md md:p-lg">
      <header className="flex flex-wrap items-start justify-between gap-md">
        <div className="min-w-0">
          <h1 className="text-page-title">Project settings</h1>
          <p className="mt-xxs max-w-3xl break-words text-metadata text-muted-foreground">
            For <span className="font-medium text-foreground">{currentProject.name}</span> — the project chosen at the top
            of the page. Switch project there to change another one.
            {!canAdmin && ' Only a project admin can change these settings.'}
          </p>
        </div>
        {/* v5.288.0 — no "All projects" button here: the Settings hub's own
            "All projects" tab is the one way there. */}
      </header>

      <PostureSection title="Details" description="The dates are the engagement window: they appear on client reports and scope the Oversight filters.">
        {/* Capped so the inputs stay a readable length on the full-width page. */}
        <form className="max-w-4xl space-y-md" onSubmit={(e) => { e.preventDefault(); saveDetails(); }}>
          <div className="grid gap-md md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <div className="min-w-0 space-y-xxs">
              <Label htmlFor="ps-name">Name</Label>
              <Input id="ps-name" maxLength={PROJECT_NAME_MAX} value={details.name} disabled={!canAdmin || savingDetails}
                aria-describedby="ps-name-count"
                onChange={(e) => setDetails({ ...details, name: e.target.value })} />
              {canAdmin && <CharacterCount id="ps-name-count" value={details.name} max={PROJECT_NAME_MAX} />}
            </div>
            <div className="min-w-0 space-y-xxs">
              <Label htmlFor="ps-status">Status</Label>
              <Select value={details.status} onValueChange={(v) => setDetails({ ...details, status: v })}
                disabled={!canAdmin || savingDetails}>
                <SelectTrigger id="ps-status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PROJECT_STATUSES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="min-w-0 space-y-xxs">
            <Label htmlFor="ps-desc">Description</Label>
            <Textarea id="ps-desc" rows={2} value={details.description} disabled={!canAdmin || savingDetails}
              onChange={(e) => setDetails({ ...details, description: e.target.value })} />
          </div>
          <div className="grid gap-md sm:grid-cols-2 md:max-w-lg">
            <div className="space-y-xxs">
              <Label htmlFor="ps-start">Start date</Label>
              <Input id="ps-start" type="date" value={details.start} disabled={!canAdmin || savingDetails}
                onChange={(e) => setDetails({ ...details, start: e.target.value })} />
            </div>
            <div className="space-y-xxs">
              <Label htmlFor="ps-end">End date</Label>
              <Input id="ps-end" type="date" value={details.end} disabled={!canAdmin || savingDetails}
                onChange={(e) => setDetails({ ...details, end: e.target.value })} />
            </div>
          </div>
          {canAdmin && (
            <div className="flex items-center gap-xs">
              <Button type="submit" size="sm" disabled={!detailsDirty || savingDetails}>
                {savingDetails && <Loader2 className="size-4 animate-spin" aria-hidden />} Save details
              </Button>
              <Button type="button" variant="ghost" size="sm" disabled={!detailsDirty || savingDetails}
                onClick={() => setEdits({})}>
                Undo changes
              </Button>
            </div>
          )}
        </form>
      </PostureSection>

      <PostureSection
        title={`Members${members ? ` (${members.length})` : ''}`}
        description={<>What each role may do: {PROJECT_ROLES.map((r, i) => (
          <React.Fragment key={r.value}>{i > 0 && ' · '}<span className="font-medium text-foreground">{r.label}</span> — {r.can}</React.Fragment>
        ))}. Global administrators may do everything in every project.</>}
        actions={canAdmin ? (
          <Button size="sm" variant="outline" onClick={openAdd}>
            <UserPlus className="size-4" aria-hidden /> Add member
          </Button>
        ) : undefined}
      >
        {membersError ? (
          <div className="flex flex-wrap items-center gap-sm">
            <p className="text-caption text-destructive">{membersError}</p>
            <Button size="sm" variant="outline" onClick={loadMembers}>Retry</Button>
          </div>
        ) : members === null ? (
          <p className="inline-flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading members…
          </p>
        ) : members.length === 0 ? (
          <p className="text-metadata text-muted-foreground">Nobody is a member yet — only global administrators can open this project.</p>
        ) : (
          <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }} aria-label="Project members">
            <thead>
              <tr className="text-left text-caption text-muted-foreground">
                <th className="pb-xxs pr-md font-medium">Member</th>
                <th className="w-44 pb-xxs pr-md font-medium">Role</th>
                <th className="w-28 pb-xxs pr-md font-medium">Joined</th>
                <th className="w-12 pb-xxs" aria-label="Remove" />
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.user_id} className="border-t border-border/60 align-middle">
                  <td className="py-xs pr-md">
                    <span className="block truncate font-medium text-foreground" title={memberName(m)}>
                      {memberName(m)}{m.user_id === user?.id && <span className="font-normal text-muted-foreground"> (you)</span>}
                    </span>
                    {m.full_name && <span className="block truncate text-caption text-muted-foreground">{m.username}</span>}
                  </td>
                  <td className="py-xs pr-md">
                    {canAdmin ? (
                      <Select value={m.role} onValueChange={(v) => void changeRole(m, v)}>
                        <SelectTrigger className="h-8 w-36 text-caption" aria-label={`Role of ${memberName(m)}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PROJECT_ROLES.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    ) : roleLabel(m.role)}
                  </td>
                  <td className="py-xs pr-md text-caption text-muted-foreground">{day(m.joined_at ?? m.created_at)}</td>
                  <td className="py-xs text-right">
                    {canAdmin && (
                      <Button variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive"
                        onClick={() => void removeMember(m)} aria-label={`Remove ${memberName(m)} from the project`}>
                        <Trash2 className="size-4" aria-hidden />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </PostureSection>

      {/* UX review 2026-09-24 — was a switch in the upload dialog. Analysts
          may change it (the ingest-settings route), as they upload. */}
      <ProjectIngestSettings
        canEdit={isGlobalAdmin || currentProject?.my_role === 'admin' || currentProject?.my_role === 'analyst'}
      />

      <TagManagement />
      {/* Webhooks are a project admin's, reads included (the server refuses
          the list to everyone else): below that role the sections are not
          offered — they used to answer "Failed to load webhooks". */}
      {canSeeWebhooks && (
        <>
          <WebhookSettings />
          <WebhookDeliveries />
        </>
      )}

      {isGlobalAdmin && (
        <PostureSection title="Delete this project"
          description="Removes the project and everything in it, for everyone. Global administrators only.">
          <div className="flex flex-wrap items-center gap-sm">
            {/* v5.288.0 — an outline destructive button, not a filled red one
                always on screen; the typed-name confirmation is the gate. */}
            <Button variant="outline" size="sm"
              className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => void deleteProject()} disabled={projects.length <= 1}>
              <Trash2 className="size-4" aria-hidden /> Delete {safeFallback(currentProject.name, 'project')}
            </Button>
            {projects.length <= 1 && (
              <span className="text-caption text-muted-foreground">The only project cannot be deleted.</span>
            )}
          </div>
        </PostureSection>
      )}

      <Dialog open={addOpen} onOpenChange={(v) => !v && !adding && setAddOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add a member</DialogTitle>
            <DialogDescription>They can open {currentProject.name} with the role you choose.</DialogDescription>
          </DialogHeader>
          <div className="space-y-md">
            <div className="space-y-xxs">
              <Label id="ps-new-user-label" htmlFor="ps-new-user">Person</Label>
              <Combobox
                id="ps-new-user"
                options={candidates.map((u) => ({
                  value: String(u.id), label: memberName(u), description: u.full_name ? u.username : undefined,
                  keywords: [u.username, u.full_name ?? ''],
                }))}
                value={newUser}
                onChange={setNewUser}
                placeholder={
                  directory === null ? 'Loading people…'
                    : directoryFailed ? 'Could not load the user directory — close and try again'
                      : candidates.length ? 'Search people…' : 'Everyone is already a member'
                }
                searchPlaceholder="Name or username"
                disabled={directory === null || candidates.length === 0}
              />
            </div>
            <div className="space-y-xxs">
              <Label htmlFor="ps-new-role">Role</Label>
              <Select value={newRole} onValueChange={setNewRole}>
                <SelectTrigger id="ps-new-role"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PROJECT_ROLES.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-caption text-muted-foreground">{PROJECT_ROLES.find((r) => r.value === newRole)?.can}</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)} disabled={adding}>Cancel</Button>
            <Button onClick={addMember} disabled={adding || !newUser}>
              {adding && <Loader2 className="size-4 animate-spin" aria-hidden />} Add member
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {confirmEl}
    </div>
  );
};

export default ProjectSettings;
