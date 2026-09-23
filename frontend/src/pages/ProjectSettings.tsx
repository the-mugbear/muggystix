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
 * Host tags, Outbound webhooks, Webhook deliveries, and a delete area at the
 * end.  What the caller may change follows their role in the project
 * (`my_role` from the API) — the server enforces the same.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, Trash2, UserPlus } from 'lucide-react';

import { useProject } from '../contexts/ProjectContext';
import { useAuth } from '../contexts/AuthContext';
import { updateProject } from '../services/api';
import api from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { useConfirm } from '../hooks/useConfirm';
import { safeFallback } from '../utils/uiStyles';
import PostureSection from '../components/posture/PostureSection';
import WebhookSettings from '../components/WebhookSettings';
import WebhookDeliveries from '../components/WebhookDeliveries';
import TagManagement from '../components/TagManagement';
import { Button } from '../components/ui/button';
import { Combobox } from '../components/ui/combobox';
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

export const PROJECT_ROLES: Array<{ value: string; label: string; can: string }> = [
  { value: 'admin', label: 'Admin', can: 'project settings and members, plus everything below' },
  { value: 'analyst', label: 'Analyst', can: 'uploads, scopes, triage, test plans, report drafts' },
  { value: 'auditor', label: 'Auditor', can: 'read everything, exports and reports' },
  { value: 'viewer', label: 'Viewer', can: 'read the inventory' },
];
const roleLabel = (r: string) => PROJECT_ROLES.find((x) => x.value === r)?.label ?? r;

export const PROJECT_STATUSES = [
  { value: 'active', label: 'Active' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'archived', label: 'Archived' },
];

const day = (s?: string | null) => (s ? new Date(s).toLocaleDateString() : '—');
const memberName = (m: { full_name: string | null; username: string }) => m.full_name || m.username;

interface Details { name: string; description: string; status: string; start: string; end: string }

const ProjectSettings: React.FC = () => {
  const { currentProject, projects, refreshProjects } = useProject();
  const { user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [confirmEl, confirm] = useConfirm();

  const isGlobalAdmin = user?.role === 'admin';
  const canAdmin = currentProject?.my_role === 'admin' || isGlobalAdmin;

  // --- Details -----------------------------------------------------------
  // Keyed on the values, not the object's identity: a refresh that hands
  // back an equal project must not reset what is being typed.
  const p = currentProject;
  const fromProject = useCallback((): Details => ({
    name: p?.name ?? '',
    description: p?.description ?? '',
    status: p?.status ?? 'active',
    start: p?.start_date ? p.start_date.split('T')[0] : '',
    end: p?.end_date ? p.end_date.split('T')[0] : '',
  }), [p?.name, p?.description, p?.status, p?.start_date, p?.end_date]); // eslint-disable-line react-hooks/exhaustive-deps
  const [details, setDetails] = useState<Details>(fromProject);
  const [savingDetails, setSavingDetails] = useState(false);
  useEffect(() => { setDetails(fromProject()); }, [fromProject]);
  const detailsDirty = JSON.stringify(details) !== JSON.stringify(fromProject());

  const saveDetails = async () => {
    if (!currentProject) return;
    if (!details.name.trim()) { toast.error('A project needs a name.'); return; }
    if (details.start && details.end && details.end < details.start) {
      toast.error('The end date is before the start date.');
      return;
    }
    setSavingDetails(true);
    try {
      await updateProject(currentProject.id, {
        name: details.name.trim(),
        description: details.description.trim(),
        status: details.status,
        start_date: details.start ? new Date(details.start).toISOString() : null,
        end_date: details.end ? new Date(details.end).toISOString() : null,
      });
      await refreshProjects();
      toast.success('Project details saved.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not save the project details.'));
    } finally {
      setSavingDetails(false);
    }
  };

  // --- Members -----------------------------------------------------------
  const [members, setMembers] = useState<Member[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);
  const projectId = currentProject?.id;
  const loadMembers = useCallback(async () => {
    if (!projectId) return;
    try {
      const res = await api.get(`/projects/${projectId}/members`);
      setMembers(res.data);
      setMembersError(null);
    } catch (err) {
      setMembersError(formatApiError(err, 'Could not load the members.'));
    }
  }, [projectId]);
  useEffect(() => { setMembers(null); void loadMembers(); }, [loadMembers]);

  const [addOpen, setAddOpen] = useState(false);
  const [directory, setDirectory] = useState<DirectoryEntry[] | null>(null);
  const [directoryFailed, setDirectoryFailed] = useState(false);
  const [newUser, setNewUser] = useState<string | null>(null);
  const [newRole, setNewRole] = useState('analyst');
  const [adding, setAdding] = useState(false);
  const openAdd = async () => {
    setNewUser(null);
    setNewRole('analyst');
    setAddOpen(true);
    setDirectoryFailed(false);
    try {
      setDirectory((await api.get('/users/directory')).data);
    } catch {
      // Said as a failure: an empty list read "Everyone is already a member".
      setDirectory([]);
      setDirectoryFailed(true);
    }
  };
  const candidates = useMemo(
    () => (directory ?? []).filter((u) => !(members ?? []).some((m) => m.user_id === u.id)),
    [directory, members],
  );
  const addMember = async () => {
    if (!currentProject || !newUser) return;
    setAdding(true);
    try {
      await api.post(`/projects/${currentProject.id}/members`, { user_id: Number(newUser), role: newRole });
      setAddOpen(false);
      await loadMembers();
      await refreshProjects();
      toast.success('Member added.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not add the member.'));
    } finally {
      setAdding(false);
    }
  };

  const admins = (members ?? []).filter((m) => m.role === 'admin');
  const changeRole = async (m: Member, role: string) => {
    if (!currentProject || role === m.role) return;
    const self = m.user_id === user?.id;
    const lastAdmin = m.role === 'admin' && admins.length === 1;
    if (self || lastAdmin) {
      const ok = await confirm({
        title: self ? 'Change your own role?' : `${memberName(m)} is the only project admin`,
        body: self
          ? `You will be a ${roleLabel(role)} on this project. ${role === 'admin' ? '' : 'Unless you are a global administrator, you will no longer be able to change settings or members here.'}`
          : `Making them a ${roleLabel(role)} leaves the project with no project admin; only a global administrator could then manage its members.`,
        severity: 'danger',
        confirmLabel: `Make ${self ? 'me' : 'them'} ${roleLabel(role)}`,
      });
      if (!ok) return;
    }
    try {
      await api.put(`/projects/${currentProject.id}/members/${m.user_id}`, { role });
      setMembers((prev) => (prev ?? []).map((x) => (x.user_id === m.user_id ? { ...x, role } : x)));
      if (self) await refreshProjects();
      toast.success(`${memberName(m)} is now ${roleLabel(role)}.`);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not change the role.'));
    }
  };

  const removeMember = async (m: Member) => {
    if (!currentProject) return;
    const ok = await confirm({
      title: `Remove ${memberName(m)}?`,
      body: `${memberName(m)} loses access to ${currentProject.name}. Their notes, findings and reviews stay. They can be added again later.`,
      severity: 'danger',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    try {
      await api.delete(`/projects/${currentProject.id}/members/${m.user_id}`);
      setMembers((prev) => (prev ?? []).filter((x) => x.user_id !== m.user_id));
      await refreshProjects();
      toast.success('Member removed.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not remove the member.'));
    }
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
            test plans, execution sessions and recon runs. This cannot be undone.
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
    try {
      await api.delete(`/projects/${currentProject.id}`);
      await refreshProjects();
      toast.success(`Project "${currentProject.name}" deleted.`);
      navigate('/operations');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not delete the project.'));
    }
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
    <div className="mx-auto max-w-6xl space-y-lg p-md md:p-lg">
      <header className="flex flex-wrap items-start justify-between gap-md">
        <div className="min-w-0">
          <h1 className="text-page-title">Project settings</h1>
          <p className="mt-xxs max-w-3xl break-words text-metadata text-muted-foreground">
            For <span className="font-medium text-foreground">{currentProject.name}</span> — the project chosen at the top
            of the page. Switch project there to change another one.
            {!canAdmin && ' Only a project admin can change these settings.'}
          </p>
        </div>
        {isGlobalAdmin && (
          <Button asChild variant="outline" size="sm"><Link to="/settings/projects">All projects</Link></Button>
        )}
      </header>

      <PostureSection title="Details" description="The dates are the engagement window: they appear on client reports and scope the Oversight filters.">
        <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); void saveDetails(); }}>
          <div className="grid gap-md md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <div className="min-w-0 space-y-xxs">
              <Label htmlFor="ps-name">Name</Label>
              <Input id="ps-name" maxLength={100} value={details.name} disabled={!canAdmin || savingDetails}
                onChange={(e) => setDetails({ ...details, name: e.target.value })} />
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
                onClick={() => setDetails(fromProject())}>
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
          <Button size="sm" variant="outline" onClick={() => void openAdd()}>
            <UserPlus className="size-4" aria-hidden /> Add member
          </Button>
        ) : undefined}
      >
        {membersError ? (
          <div className="flex flex-wrap items-center gap-sm">
            <p className="text-caption text-destructive">{membersError}</p>
            <Button size="sm" variant="outline" onClick={() => void loadMembers()}>Retry</Button>
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

      <TagManagement />
      <WebhookSettings />
      <WebhookDeliveries />

      {isGlobalAdmin && (
        <PostureSection title="Delete this project"
          description="Removes the project and everything in it, for everyone. Global administrators only.">
          <div className="flex flex-wrap items-center gap-sm">
            <Button variant="destructive" size="sm" onClick={() => void deleteProject()} disabled={projects.length <= 1}>
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
            <Button onClick={() => void addMember()} disabled={adding || !newUser}>
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
