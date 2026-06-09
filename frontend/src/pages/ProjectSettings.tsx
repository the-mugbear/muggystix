import React, { useState, useEffect, useCallback } from 'react';
import {
  Trash2,
  Copy,
  Bot,
  RefreshCw,
  Loader2,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useProject } from '../contexts/ProjectContext';
import {
  getProjects,
  createProject,
  updateProject,
  Project,
  AgentResponse,
  AgentCreateResponse,
  getProjectAgents,
  createAgent,
  deactivateAgent,
  rotateAgentKey,
} from '../services/api';
import api from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { useConfirm } from '../hooks/useConfirm';
import { safeFallback } from '../utils/uiStyles';
import { NavigableTableRow } from '../components/NavigableTableRow';
import { formatStatusLabel, getProjectStatusChipColor } from '../utils/statusMeta';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import WebhookSettings from '../components/WebhookSettings';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Separator } from '../components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';
import { cn } from '../utils/cn';

interface Member {
  id: number;
  user_id: number;
  username: string;
  full_name: string | null;
  role: string;
  joined_at: string;
}

interface DirectoryEntry {
  id: number;
  username: string;
  full_name: string | null;
}

const MEMBER_ROLES = ['admin', 'analyst', 'auditor', 'viewer'];

const PROJECT_STATUSES = [
  { value: 'active', label: 'Active' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'archived', label: 'Archived' },
];

const formatDate = (s: string | null | undefined): string => {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleDateString();
  } catch {
    return '—';
  }
};

/** Translate MUI chip color names to v4 Badge variants. */
const STATUS_VARIANT: Record<string, 'success' | 'info' | 'warning' | 'muted' | 'destructive'> = {
  success: 'success',
  info: 'info',
  warning: 'warning',
  default: 'muted',
  error: 'destructive',
  primary: 'info',
  secondary: 'muted',
};

const ProjectSettings: React.FC = () => {
  const { user } = useAuth();
  const { refreshProjects, currentProject } = useProject();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();

  // Projects list
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);

  // Create project
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Members
  const [members, setMembers] = useState<Member[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);

  // Edit project
  const [editOpen, setEditOpen] = useState(false);
  const [editProject, setEditProject] = useState<Project | null>(null);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editStatus, setEditStatus] = useState('active');
  const [editStartDate, setEditStartDate] = useState('');
  const [editEndDate, setEditEndDate] = useState('');
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Add member
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [newMemberUserId, setNewMemberUserId] = useState<string>('');
  const [newMemberRole, setNewMemberRole] = useState('viewer');
  const [addingMember, setAddingMember] = useState(false);
  const [addMemberError, setAddMemberError] = useState<string | null>(null);
  const [userDirectory, setUserDirectory] = useState<DirectoryEntry[]>([]);
  const [directoryLoading, setDirectoryLoading] = useState(false);

  // Agent
  const [agent, setAgent] = useState<AgentResponse | null>(null);
  const [allAgents, setAllAgents] = useState<AgentResponse[]>([]);
  const [agentLoading, setAgentLoading] = useState(false);
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  const [agentName, setAgentName] = useState('');
  const [agentDescription, setAgentDescription] = useState('');
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [newApiKey, setNewApiKey] = useState<string | null>(null);
  const [apiKeyCopied, setApiKeyCopied] = useState(false);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getProjects();
      setProjects(data);
    } catch (err: unknown) {
      setError(formatApiError(err, 'Failed to load projects.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  // Auto-select a project on load so the Members section is visible
  // without requiring the user to click a row first.  Prefer the
  // currently-active project (from the topbar selector), then fall
  // through to "the only project" when there's just one — the common
  // single-tenant case where requiring a click was a discoverability
  // dead-end ("created a new admin but can't add them to a project").
  useEffect(() => {
    if (selectedProject || projects.length === 0) return;
    const initial =
      (currentProject && projects.find((p) => p.id === currentProject.id)) ||
      (projects.length === 1 ? projects[0] : null);
    if (initial) {
      setSelectedProject(initial);
      loadMembers(initial.id);
      loadAgent(initial.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, currentProject?.id]);

  const loadMembers = useCallback(async (projectId: number) => {
    setMembersLoading(true);
    try {
      const res = await api.get(`/projects/${projectId}/members`);
      setMembers(res.data);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to load members.'));
      setMembers([]);
    } finally {
      setMembersLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadAgent = useCallback(
    async (_projectId: number) => {
      setAgentLoading(true);
      try {
        const agents = await getProjectAgents();
        setAllAgents(agents);
        setAgent(agents.find((a) => a.owner_id === user?.id) || null);
      } catch (err: unknown) {
        toast.error(formatApiError(err, 'Failed to load agent.'));
        setAgent(null);
        setAllAgents([]);
      } finally {
        setAgentLoading(false);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [user?.id],
  );

  const handleSelectProject = (project: Project) => {
    setSelectedProject(project);
    loadMembers(project.id);
    loadAgent(project.id);
  };

  // Agent management
  const handleCreateAgent = async () => {
    if (!agentName.trim()) return;
    setCreatingAgent(true);
    try {
      const result: AgentCreateResponse = await createAgent({
        name: agentName.trim(),
        description: agentDescription.trim() || undefined,
      });
      setAgent(result);
      setNewApiKey(result.api_key);
      setAgentName('');
      setAgentDescription('');
      setCreateAgentOpen(false);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to create agent.'));
    } finally {
      setCreatingAgent(false);
    }
  };

  const handleDeactivateAgent = async () => {
    if (!agent) return;
    const ok = await confirm({
      title: 'Deactivate agent',
      body: "The agent's API key will be revoked immediately. Any test plans or recon runs still using it will stop working.",
      resourceName: agent.name,
      severity: 'danger',
      confirmLabel: 'Deactivate',
    });
    if (!ok) return;
    try {
      await deactivateAgent(agent.id);
      setAgent(null);
      toast.success('Agent deactivated.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to deactivate agent.'));
    }
  };

  // Project admins (and global admins) can stop another member's agent —
  // the backend DELETE /agents/{id} already authorises owner-or-project-admin;
  // this surfaces that kill-switch in the UI so a runaway agent can be
  // stopped without a DB poke.
  const isProjectAdmin =
    user?.role === 'admin' ||
    members.some((m) => m.user_id === user?.id && m.role === 'admin');

  const handleDeactivateTeamAgent = async (a: AgentResponse) => {
    const ok = await confirm({
      title: 'Deactivate agent',
      body: "This revokes the agent's API key immediately. Any test plans or recon runs still using it will stop working. As a project admin you can stop another member's agent.",
      resourceName: a.name,
      severity: 'danger',
      confirmLabel: 'Deactivate',
    });
    if (!ok) return;
    try {
      await deactivateAgent(a.id);
      setAllAgents((prev) =>
        prev.map((x) => (x.id === a.id ? { ...x, is_active: false } : x)),
      );
      toast.success(`Agent "${a.name}" deactivated.`);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to deactivate agent.'));
    }
  };

  const handleRotateKey = async () => {
    if (!agent) return;
    const ok = await confirm({
      title: 'Rotate agent API key',
      body: 'A new key will be generated and the current one will be revoked immediately. Any running agents using the old key will stop working and must be given the new key.',
      resourceName: agent.name,
      severity: 'warning',
      confirmLabel: 'Generate new key',
    });
    if (!ok) return;
    try {
      const result = await rotateAgentKey(agent.id);
      setNewApiKey(result.api_key);
      toast.success('New API key generated.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to rotate key.'));
    }
  };

  const handleCopyApiKey = async () => {
    if (!newApiKey) return;
    try {
      await navigator.clipboard.writeText(newApiKey);
      setApiKeyCopied(true);
      setTimeout(() => setApiKeyCopied(false), 2000);
    } catch {
      // ignore — fallback for non-secure context
    }
  };

  // Create project
  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      await createProject(newName.trim(), newDescription.trim() || undefined);
      setCreateOpen(false);
      setNewName('');
      setNewDescription('');
      await loadProjects();
      await refreshProjects();
      toast.success('Project created.');
    } catch (err: unknown) {
      setCreateError(formatApiError(err, 'Failed to create project.'));
    } finally {
      setCreating(false);
    }
  };

  // Edit project
  const handleOpenEdit = (project: Project) => {
    setEditProject(project);
    setEditName(project.name);
    setEditDescription(project.description || '');
    setEditStatus(project.status || 'active');
    setEditStartDate(project.start_date ? project.start_date.split('T')[0] : '');
    setEditEndDate(project.end_date ? project.end_date.split('T')[0] : '');
    setEditError(null);
    setEditOpen(true);
  };

  const handleSaveEdit = async () => {
    if (!editProject) return;
    setSaving(true);
    setEditError(null);
    try {
      await updateProject(editProject.id, {
        name: editName.trim() || undefined,
        description: editDescription.trim() || undefined,
        status: editStatus,
        start_date: editStartDate ? new Date(editStartDate).toISOString() : null,
        end_date: editEndDate ? new Date(editEndDate).toISOString() : null,
      });
      setEditOpen(false);
      await loadProjects();
      await refreshProjects();
      toast.success('Project updated.');
    } catch (err: unknown) {
      setEditError(formatApiError(err, 'Failed to update project.'));
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteProject = async (project: Project) => {
    // Typed-name confirmation since deletion drops every scan, host,
    // scope, finding, plan, and execution session under this project.
    // Mirrors the test-plan delete pattern (TestPlanLayout) — heavy
    // destructive operations require the user to type the exact name.
    const ok = await confirm({
      title: `Delete project "${project.name}"?`,
      body: (
        <>
          <p>
            This deletes the project and <strong>all</strong> data scoped to it: scans, hosts,
            scopes, findings, test plans, execution sessions, and recon runs. This cannot be
            undone.
          </p>
          <p className="mt-xs">
            Type the project name exactly to confirm.
          </p>
        </>
      ),
      resourceName: project.name,
      severity: 'danger',
      confirmLabel: 'Delete project',
      confirmTypedName: true,
    });
    if (!ok) return;
    try {
      await api.delete(`/projects/${project.id}`);
      // If we just deleted the currently-selected one, clear local
      // selection so the next refreshProjects() picks a new one via
      // the MRU ring (the deleted id will be dropped from the picker).
      if (selectedProject?.id === project.id) setSelectedProject(null);
      await loadProjects();
      await refreshProjects();
      toast.success(`Project "${project.name}" deleted.`);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to delete project.'));
    }
  };

  // Add member
  const loadUserDirectory = useCallback(async () => {
    setDirectoryLoading(true);
    try {
      const res = await api.get('/users/directory');
      setUserDirectory(res.data);
    } catch {
      setUserDirectory([]);
    } finally {
      setDirectoryLoading(false);
    }
  }, []);

  const openAddMemberDialog = () => {
    setNewMemberUserId('');
    setNewMemberRole('viewer');
    setAddMemberError(null);
    setAddMemberOpen(true);
    loadUserDirectory();
  };

  const handleAddMember = async () => {
    if (!selectedProject || !newMemberUserId) return;
    setAddingMember(true);
    setAddMemberError(null);
    try {
      await api.post(`/projects/${selectedProject.id}/members`, {
        user_id: Number(newMemberUserId),
        role: newMemberRole,
      });
      setAddMemberOpen(false);
      setNewMemberUserId('');
      setNewMemberRole('viewer');
      await loadMembers(selectedProject.id);
      toast.success('Member added.');
    } catch (err: unknown) {
      setAddMemberError(formatApiError(err, 'Failed to add member.'));
    } finally {
      setAddingMember(false);
    }
  };

  const handleRoleChange = async (member: Member, newRole: string) => {
    if (!selectedProject) return;
    try {
      // Backend exposes PUT for this resource (full role replacement);
      // a PATCH here returns 405 Method Not Allowed.  Caught during
      // 4.1.0 regression — the old code path predated the backend
      // route shape but was never wired up to anything that actually
      // round-tripped.
      await api.put(`/projects/${selectedProject.id}/members/${member.user_id}`, {
        role: newRole,
      });
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, role: newRole } : m)),
      );
      toast.success(`Role updated to ${newRole}.`);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update role.'));
    }
  };

  const handleRemoveMember = async (member: Member) => {
    if (!selectedProject) return;
    const ok = await confirm({
      title: 'Remove member?',
      body: `${member.username} will lose access to this project. They can be re-added later.`,
      severity: 'danger',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    try {
      await api.delete(`/projects/${selectedProject.id}/members/${member.user_id}`);
      setMembers((prev) => prev.filter((m) => m.user_id !== member.user_id));
      toast.success('Member removed.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to remove member.'));
    }
  };

  const availableDirectoryUsers = userDirectory.filter(
    (u) => !members.some((m) => m.user_id === u.id),
  );

  return (
    <div className="mx-auto max-w-6xl p-md md:p-lg">
      <h1 className="mb-md text-page-title">Project Settings</h1>

      {/* Projects table */}
      <Card className="mb-md">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Projects</CardTitle>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            Create Project
          </Button>
        </CardHeader>
        <CardContent>
          {error && (
            <Alert variant="destructive" className="mb-sm">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {loading ? (
            <div className="flex justify-center py-lg">
              <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
            </div>
          ) : projects.length === 0 ? (
            <p className="py-md text-center text-metadata text-muted-foreground">
              No projects yet. Create one to get started.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-panel border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="text-center">Members</TableHead>
                    <TableHead>Start</TableHead>
                    <TableHead>End</TableHead>
                    <TableHead className="text-center">Status</TableHead>
                    <TableHead className="text-center">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {projects.map((project) => {
                    const statusLabel =
                      PROJECT_STATUSES.find((s) => s.value === project.status)?.label ||
                      formatStatusLabel(project.status, 'Active');
                    const statusVariant =
                      STATUS_VARIANT[getProjectStatusChipColor(project.status)] || 'muted';
                    const isSelected = selectedProject?.id === project.id;
                    // v2.43.0 — UX review #2: NavigableTableRow + explicit
                    // button in the primary cell.  Selection is an in-page
                    // action (not nav), so a <button> drives it.
                    return (
                      <NavigableTableRow
                        key={project.id}
                        selected={isSelected}
                        data-state={isSelected ? 'selected' : undefined}
                      >
                        <TableCell className="font-medium p-0">
                          <button
                            type="button"
                            onClick={() => handleSelectProject(project)}
                            className="block w-full px-md py-xs text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            aria-pressed={isSelected}
                            aria-label={`Select project ${project.name}`}
                          >
                            {project.name}
                          </button>
                        </TableCell>
                        <TableCell>
                          <p className="line-clamp-2 text-metadata text-muted-foreground">
                            {safeFallback(project.description)}
                          </p>
                        </TableCell>
                        <TableCell className="text-center">{project.member_count ?? '—'}</TableCell>
                        <TableCell>{formatDate(project.start_date)}</TableCell>
                        <TableCell>{formatDate(project.end_date)}</TableCell>
                        <TableCell className="text-center">
                          <Badge variant={statusVariant}>{statusLabel}</Badge>
                        </TableCell>
                        <TableCell className="text-center">
                          <div className="flex flex-wrap justify-center gap-xs">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleOpenEdit(project);
                              }}
                            >
                              Edit
                            </Button>
                            <Button
                              size="sm"
                              variant="destructive"
                              // Delete is gated server-side too — the
                              // last remaining project cannot be
                              // deleted. Surfaced as a button on every
                              // row; the typed-name confirm in
                              // handleDeleteProject is the friction
                              // that matters.
                              disabled={projects.length <= 1}
                              title={
                                projects.length <= 1
                                  ? 'Cannot delete the only project'
                                  : undefined
                              }
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDeleteProject(project);
                              }}
                            >
                              Delete
                            </Button>
                          </div>
                        </TableCell>
                      </NavigableTableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Members */}
      {!selectedProject && projects.length > 1 && !loading && (
        <Alert variant="info" className="mb-md">
          <AlertDescription>
            Click a row in the Projects table above to manage its members.
          </AlertDescription>
        </Alert>
      )}
      {selectedProject && (
        <Card className="mb-md">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Members — {selectedProject.name}</CardTitle>
            <Button size="sm" variant="outline" onClick={openAddMemberDialog}>
              Add Member
            </Button>
          </CardHeader>
          <CardContent>
            {membersLoading ? (
              <div className="flex justify-center py-lg">
                <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
              </div>
            ) : members.length === 0 ? (
              <p className="py-md text-center text-metadata text-muted-foreground">
                No members in this project.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-panel border border-border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Username</TableHead>
                      <TableHead>Full Name</TableHead>
                      <TableHead>Role</TableHead>
                      <TableHead>Joined</TableHead>
                      <TableHead className="text-center">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {members.map((m) => (
                      <TableRow key={m.user_id}>
                        <TableCell className="font-medium">{m.username}</TableCell>
                        <TableCell>{safeFallback(m.full_name)}</TableCell>
                        <TableCell>
                          <Select value={m.role} onValueChange={(v) => handleRoleChange(m, v)}>
                            <SelectTrigger className="w-32">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {MEMBER_ROLES.map((role) => (
                                <SelectItem key={role} value={role}>
                                  {role.charAt(0).toUpperCase() + role.slice(1)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </TableCell>
                        <TableCell>{formatDate(m.joined_at)}</TableCell>
                        <TableCell className="text-center">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => handleRemoveMember(m)}
                                aria-label={`Remove ${m.username} from project`}
                                className="text-muted-foreground hover:text-destructive"
                              >
                                <Trash2 className="size-4" aria-hidden />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Remove member</TooltipContent>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Agent section */}
      {selectedProject && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-xs">
              <Bot className="size-5" aria-hidden /> AI Agents
              {allAgents.length > 0 && <Badge variant="muted">{allAgents.length}</Badge>}
            </CardTitle>
            {!agent && (
              <Button size="sm" onClick={() => setCreateAgentOpen(true)}>
                Create My Agent
              </Button>
            )}
          </CardHeader>
          <CardContent>
            {newApiKey && (
              <Alert variant="success" className="mb-md">
                <AlertDescription>
                  <div className="flex flex-col gap-xs">
                    <p className="font-semibold">API Key (shown once only):</p>
                    <p className="break-all font-mono text-caption">{newApiKey}</p>
                    <div>
                      <Button size="sm" variant="outline" onClick={handleCopyApiKey}>
                        <Copy className="size-3.5" aria-hidden />
                        {apiKeyCopied ? 'Copied!' : 'Copy'}
                      </Button>
                    </div>
                  </div>
                </AlertDescription>
              </Alert>
            )}
            {agentLoading ? (
              <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
            ) : agent ? (
              <div>
                <div className="mb-md grid grid-cols-1 gap-sm sm:grid-cols-2">
                  <div>
                    <p className="text-caption text-muted-foreground">Name</p>
                    <p className="text-metadata font-medium text-foreground">{agent.name}</p>
                  </div>
                  <div>
                    <p className="text-caption text-muted-foreground">Status</p>
                    <Badge variant={agent.is_active ? 'success' : 'muted'}>
                      {agent.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  </div>
                  <div>
                    <p className="text-caption text-muted-foreground">Key Prefix</p>
                    <p className="font-mono text-metadata text-foreground">
                      {safeFallback(agent.api_key_prefix)}
                    </p>
                  </div>
                  <div>
                    <p className="text-caption text-muted-foreground">Last Active</p>
                    <p className="text-metadata text-foreground">
                      {agent.last_activity_at
                        ? new Date(agent.last_activity_at).toLocaleString()
                        : 'Never'}
                    </p>
                  </div>
                  {agent.description && (
                    <div className="sm:col-span-2">
                      <p className="text-caption text-muted-foreground">Description</p>
                      <p className="text-metadata text-foreground">{agent.description}</p>
                    </div>
                  )}
                </div>
                <div className="flex gap-xs">
                  <Button variant="outline" size="sm" onClick={handleRotateKey}>
                    <RefreshCw className="size-3.5" aria-hidden /> Rotate Key
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleDeactivateAgent}
                    className="border-destructive/40 text-destructive hover:bg-destructive/10"
                  >
                    Deactivate
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-metadata text-muted-foreground">
                You don't have an agent for this project yet.
              </p>
            )}

            {allAgents.filter((a) => a.owner_id !== user?.id).length > 0 && (
              <div className="mt-md border-t border-border pt-md">
                <p className="mb-xs text-caption font-semibold text-muted-foreground">Team Agents</p>
                <div className="flex flex-col gap-xxs">
                  {allAgents
                    .filter((a) => a.owner_id !== user?.id)
                    .map((a) => (
                      <div key={a.id} className="flex items-center gap-sm text-metadata">
                        <span className="min-w-0 truncate">{a.name}</span>
                        <Badge variant={a.is_active ? 'success' : 'muted'}>
                          {a.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                        <span className="text-caption text-muted-foreground">
                          Last active:{' '}
                          {a.last_activity_at
                            ? new Date(a.last_activity_at).toLocaleDateString()
                            : 'Never'}
                        </span>
                        {isProjectAdmin && a.is_active && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="ml-auto text-destructive"
                            onClick={() => handleDeactivateTeamAgent(a)}
                          >
                            Deactivate
                          </Button>
                        )}
                      </div>
                    ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Create Agent Dialog */}
      <Dialog
        open={createAgentOpen}
        onOpenChange={(next) => !next && !creatingAgent && setCreateAgentOpen(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create AI Agent</DialogTitle>
            <DialogDescription>
              Create an AI agent for this project. The agent will receive an API key for
              programmatic access to project data.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="agent-name">Agent Name</Label>
              <Input
                id="agent-name"
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="agent-desc">Description</Label>
              <Textarea
                id="agent-desc"
                rows={2}
                value={agentDescription}
                onChange={(e) => setAgentDescription(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateAgentOpen(false)} disabled={creatingAgent}>
              Cancel
            </Button>
            <Button onClick={handleCreateAgent} disabled={creatingAgent || !agentName.trim()}>
              {creatingAgent ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Creating…</> : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Create Project Dialog */}
      <Dialog open={createOpen} onOpenChange={(next) => !next && !creating && setCreateOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Project</DialogTitle>
          </DialogHeader>
          {createError && (
            <Alert variant="destructive">
              <AlertDescription>{createError}</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="proj-name">Project Name</Label>
              <Input
                id="proj-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="proj-desc">Description</Label>
              <Textarea
                id="proj-desc"
                rows={3}
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={creating}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={creating || !newName.trim()}>
              {creating ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Creating…</> : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add Member Dialog */}
      <Dialog
        open={addMemberOpen}
        onOpenChange={(next) => !next && !addingMember && setAddMemberOpen(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Member</DialogTitle>
          </DialogHeader>
          {addMemberError && (
            <Alert variant="destructive">
              <AlertDescription>{addMemberError}</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="member-user">User</Label>
              <Select value={newMemberUserId} onValueChange={setNewMemberUserId}>
                <SelectTrigger
                  id="member-user"
                  disabled={directoryLoading || availableDirectoryUsers.length === 0}
                >
                  <SelectValue
                    placeholder={
                      directoryLoading
                        ? 'Loading…'
                        : availableDirectoryUsers.length === 0
                          ? 'No available users'
                          : 'Pick a user'
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {availableDirectoryUsers.map((u) => (
                    <SelectItem key={u.id} value={String(u.id)}>
                      {u.full_name ? `${u.username} — ${u.full_name}` : u.username}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="member-role">Role</Label>
              <Select value={newMemberRole} onValueChange={setNewMemberRole}>
                <SelectTrigger id="member-role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MEMBER_ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {role.charAt(0).toUpperCase() + role.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddMemberOpen(false)} disabled={addingMember}>
              Cancel
            </Button>
            <Button onClick={handleAddMember} disabled={addingMember || !newMemberUserId}>
              {addingMember ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Adding…</> : 'Add'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Project Dialog */}
      <Dialog open={editOpen} onOpenChange={(next) => !next && !saving && setEditOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Project</DialogTitle>
          </DialogHeader>
          {editError && (
            <Alert variant="destructive">
              <AlertDescription>{editError}</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="edit-proj-name">Project Name</Label>
              <Input
                id="edit-proj-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="edit-proj-desc">Description</Label>
              <Textarea
                id="edit-proj-desc"
                rows={2}
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="edit-proj-status">Status</Label>
              <Select value={editStatus} onValueChange={setEditStatus}>
                <SelectTrigger id="edit-proj-status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROJECT_STATUSES.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-1 gap-md sm:grid-cols-2">
              <div className="flex flex-col gap-xs">
                <Label htmlFor="edit-proj-start">Start Date</Label>
                <Input
                  id="edit-proj-start"
                  type="date"
                  value={editStartDate}
                  onChange={(e) => setEditStartDate(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-xs">
                <Label htmlFor="edit-proj-end">End Date</Label>
                <Input
                  id="edit-proj-end"
                  type="date"
                  value={editEndDate}
                  onChange={(e) => setEditEndDate(e.target.value)}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleSaveEdit} disabled={saving}>
              {saving ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Saving…</> : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Outbound webhooks — scoped to the active project (v2.73.0). */}
      {currentProject && <WebhookSettings />}

      {confirmEl}
      {/* prevent ESLint unused on the cn import — kept for future variants */}
      <span className={cn('hidden')} />
    </div>
  );
};

export default ProjectSettings;
