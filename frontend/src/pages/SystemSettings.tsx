import { formatDate } from '../utils/relativeTime';
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  ShieldCheck,
  ShieldOff,
  Activity,
  ClipboardCheck,
  Eye,
  User,
  Users,
  Plus,
  MoreVertical,
  Edit,
  Lock,
  Trash2,
  Loader2,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import {
  deleteUser, listUsers, registerUser, resetUserPassword, resetUserTwoFactor, updateUserAccount,
  type UserAccount,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { SECRET_MUTATION, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import QueueHealthCard from '../components/QueueHealthCard';
import RemediationSettingsSection from '../components/remediation/RemediationSettingsSection';
import ReportWritingGuidanceSection from '../components/reports/ReportWritingGuidanceSection';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import { personInitials } from '../utils/people';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Switch } from '../components/ui/switch';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import { InlineLoader } from '../components/ui/inline-loader';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '../components/ui/dropdown-menu';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import UserMembershipsDialog from '../components/UserMembershipsDialog';
import { PasswordRulesChecklist, isPasswordValid } from '../components/PasswordRulesChecklist';

/** A row of the users table (the name `User` is also the icon's, as a value). */
type User = UserAccount;

interface NewUserForm {
  username: string;
  password: string;
  confirm_password: string;
  full_name: string;
  role: string;
}

interface EditUserForm {
  full_name: string;
  role: string;
  is_active: boolean;
}

// v4.8.0 — global role is binary (admin / member).  analyst/auditor/
// viewer are kept here only so a row carrying a pre-migration value
// still renders an icon instead of a blank.
const ROLE_META = {
  admin: { Icon: ShieldCheck, tone: 'destructive' as const, label: 'ADMIN' },
  member: { Icon: Eye, tone: 'success' as const, label: 'MEMBER' },
  analyst: { Icon: Activity, tone: 'warning' as const, label: 'ANALYST' },
  auditor: { Icon: ClipboardCheck, tone: 'info' as const, label: 'AUDITOR' },
  viewer: { Icon: Eye, tone: 'success' as const, label: 'VIEWER' },
} as const;

/** A timestamp as date over time, each on its own unbroken line — a single
 *  toLocaleString() wrapped mid-time in a narrow column ("8:49:37 / PM"). */
const DateTimeCell: React.FC<{ value: string | null }> = ({ value }) => {
  const d = value ? new Date(value) : null;
  if (!d || Number.isNaN(d.getTime())) {
    return <span className="text-muted-foreground">Never</span>;
  }
  return (
    <span title={d.toLocaleString()}>
      <span className="block whitespace-nowrap">{formatDate(d)}</span>
      <span className="block whitespace-nowrap text-caption text-muted-foreground">
        {d.toLocaleTimeString()}
      </span>
    </span>
  );
};

/** Every account on the installation — not a project's. */
const USERS_KEY = ['listUsers'];

const TABS = ['users', 'remediation', 'report-writing'] as const;
const DEFAULT_TAB = 'users';

const SystemSettings: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const asked = params.get('tab') ?? DEFAULT_TAB;
  const tab = (TABS as readonly string[]).includes(asked) ? asked : DEFAULT_TAB;
  const { user: currentUser, hasPermission } = useAuth();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const queryClient = useQueryClient();
  const isAdmin = hasPermission('admin');

  // Dialogs
  const [newUserDialogOpen, setNewUserDialogOpen] = useState(false);
  const [editUserDialogOpen, setEditUserDialogOpen] = useState(false);
  const [resetPasswordDialogOpen, setResetPasswordDialogOpen] = useState(false);

  // Forms
  const [newUserForm, setNewUserForm] = useState<NewUserForm>({
    username: '',
    password: '',
    confirm_password: '',
    full_name: '',
    role: 'member',
  });
  const [editUserForm, setEditUserForm] = useState<EditUserForm>({
    full_name: '',
    role: 'member',
    is_active: true,
  });

  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  // v2.59.0 — separate state from selectedUser so the memberships
  // dialog can be open without conflicting with the Edit Profile /
  // Reset Password flows that share selectedUser.
  const [membershipsUser, setMembershipsUser] = useState<User | null>(null);

  // Every account on the installation.  A failed read is said where the
  // table would be, with Retry — never a toast over an empty table.
  const usersQuery = useQuery({
    queryKey: USERS_KEY,
    queryFn: ({ signal }) => listUsers(signal),
    enabled: isAdmin,
  });
  const users: User[] = usersQuery.data ?? [];
  const loading = isAdmin && usersQuery.isPending;
  const usersError = queryErrorText(usersQuery.error, 'Failed to load users.');
  /** The accounts were never read: there is no table to show, only the failure. */
  const usersUnread = usersQuery.data == null && usersError != null;
  /** Put what a write returned (or is known to have done) into the table.
   *  With no table read yet there is nothing to put it into — a list made of
   *  the one account just written would pass for every account — so the list
   *  is asked for instead. */
  const setUsers = (update: (prev: User[]) => User[]) => {
    if (queryClient.getQueryData<User[]>(USERS_KEY) == null) {
      void queryClient.invalidateQueries({ queryKey: USERS_KEY });
      return;
    }
    queryClient.setQueryData<User[]>(USERS_KEY, (prev) => (prev ? update(prev) : prev));
  };
  const replaceUser = (updated: User) => setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));

  // Leaving the dialog ends the attempt: the password typed into it goes too
  // (the name and the role stay, as they did).
  const closeNewUserDialog = () => {
    setNewUserForm((form) => ({ ...form, password: '', confirm_password: '' }));
    setNewUserDialogOpen(false);
  };
  const closeResetPasswordDialog = () => {
    setNewPassword('');
    setConfirmNewPassword('');
    setResetPasswordDialogOpen(false);
  };

  // Creating an account and resetting a password both carry a password: the
  // library keeps neither once the request has settled (`SECRET_MUTATION`,
  // and the `reset` where each is called).  A failure is a toast, so the
  // reset hides nothing.
  const creation = useMutation({
    ...SECRET_MUTATION,
    // confirm_password is a client-only guard against typos — don't send it.
    mutationFn: ({ confirm_password: _confirm, ...payload }: NewUserForm) => registerUser(payload),
    onSuccess: (created) => {
      // The answer is the row the users list returns: it goes in as it is.
      setUsers((prev) => [...prev, created]);
      setNewUserDialogOpen(false);
      setNewUserForm({ username: '', password: '', confirm_password: '', full_name: '', role: 'member' });
      toast.success('User created.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to create user.')),
  });
  const handleCreateUser = () => creation.mutate(newUserForm, { onSettled: () => creation.reset() });

  // Client-only typo guard for the create-user dialog (the password is set
  // once at creation, so a mistype would otherwise lock the new account out).
  const newUserPasswordMismatch =
    newUserForm.confirm_password.length > 0 &&
    newUserForm.password !== newUserForm.confirm_password;
  // Same typo guard for the admin reset-password dialog.
  const resetPasswordMismatch =
    confirmNewPassword.length > 0 && newPassword !== confirmNewPassword;

  /** `PUT /users/{id}` — the edit dialog, the role select and the status
   *  select all send the whole account. */
  const putUser = (body: { userId: number } & EditUserForm): Promise<User> => {
    const { userId, ...form } = body;
    return updateUserAccount(userId, form);
  };

  const update = useMutation({
    mutationFn: putUser,
    onSuccess: (updated) => {
      replaceUser(updated);
      setEditUserDialogOpen(false);
      toast.success('User updated.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update user.')),
  });
  const handleUpdateUser = () => {
    if (!selectedUser) return;
    update.mutate({ userId: selectedUser.id, ...editUserForm });
  };

  const roleChange = useMutation({
    mutationFn: ({ user, role }: { user: User; role: string }) =>
      putUser({ userId: user.id, full_name: user.full_name || '', role, is_active: user.is_active }),
    onSuccess: (updated, { user, role }) => {
      replaceUser(updated);
      toast.success(`Updated ${user.username}'s role to ${role}.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update role.')),
  });
  const roleSavingUserId = roleChange.isPending ? roleChange.variables.user.id : null;

  const statusChange = useMutation({
    mutationFn: ({ user, isActive }: { user: User; isActive: boolean }) =>
      putUser({ userId: user.id, full_name: user.full_name || '', role: user.role, is_active: isActive }),
    onSuccess: (updated, { user, isActive }) => {
      replaceUser(updated);
      toast.success(`${user.username} is now ${isActive ? 'active' : 'inactive'}.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update status.')),
  });
  const statusSavingUserId = statusChange.isPending ? statusChange.variables.user.id : null;

  const twoFactorReset = useMutation({
    mutationFn: (user: User) => resetUserTwoFactor(user.id),
    onSuccess: (_response, user) => {
      setUsers((prev) => prev.map((u) => (u.id === user.id ? { ...u, totp_enabled: false } : u)));
      toast.success(`2FA reset for ${user.username}.`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to reset 2FA.')),
  });
  const reset2faUserId = twoFactorReset.isPending ? twoFactorReset.variables.id : null;

  const passwordReset = useMutation({
    ...SECRET_MUTATION,
    mutationFn: (body: { userId: number; new_password: string }) =>
      resetUserPassword(body.userId, body.new_password),
    onSuccess: () => {
      closeResetPasswordDialog();
      toast.success('Password reset.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to reset password.')),
  });
  const handleResetPassword = () => {
    if (!selectedUser) return;
    passwordReset.mutate(
      { userId: selectedUser.id, new_password: newPassword },
      { onSettled: () => passwordReset.reset() },
    );
  };

  const deletion = useMutation({
    mutationFn: (user: User) => deleteUser(user.id),
    onSuccess: (_response, user) => {
      setUsers((prev) => prev.filter((u) => u.id !== user.id));
      toast.success('User deleted.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to delete user.')),
  });

  // The three dialogs share one busy state, as they share one user.
  const saving = creation.isPending || update.isPending || passwordReset.isPending;

  const handleRoleChange = async (user: User, role: string) => {
    if (user.role === role) return;
    // v4.57.0 (UX·4) — confirm ONLY privilege-reducing transitions.
    // Demoting admin → member can lock the demoted user out of admin
    // surfaces immediately; promotions stay frictionless.
    const isDemotion = user.role === 'admin' && role !== 'admin';
    if (isDemotion) {
      const ok = await confirm({
        title: 'Demote administrator',
        body:
          `${user.username} will lose global admin access. They will retain any per-project memberships, ` +
          'but cannot manage users or system settings until promoted again.',
        resourceName: user.username,
        severity: 'warning',
        confirmLabel: 'Demote',
      });
      if (!ok) return;
    }
    roleChange.mutate({ user, role });
  };

  const handleStatusChange = async (user: User, isActive: boolean) => {
    if (user.is_active === isActive) return;
    // v4.57.0 (UX·4) — confirm only the privilege-reducing direction
    // (active → inactive).  Reactivating is frictionless because it
    // doesn't cut anyone off.
    if (!isActive) {
      const ok = await confirm({
        title: 'Deactivate user',
        body:
          `${user.username}'s active sessions will be revoked and they won't be able to log in. ` +
          'You can reactivate them later — their project memberships are preserved.',
        resourceName: user.username,
        severity: 'warning',
        confirmLabel: 'Deactivate',
      });
      if (!ok) return;
    }
    statusChange.mutate({ user, isActive });
  };

  const handleReset2fa = async (user: User) => {
    const ok = await confirm({
      title: 'Reset two-factor authentication?',
      body: `This clears ${user.full_name || user.username}'s 2FA enrollment and recovery codes — use it when they've lost their authenticator. With mandatory 2FA on, they'll be required to set it up again on their next login.`,
      confirmLabel: 'Reset 2FA',
      severity: 'danger',
    });
    if (!ok) return;
    twoFactorReset.mutate(user);
  };

  const handleDeleteUser = async (user: User) => {
    const ok = await confirm({
      title: 'Delete user',
      body: 'This action cannot be undone. The user will lose access and all per-project memberships will be removed.',
      resourceName: user.username,
      severity: 'danger',
      confirmLabel: 'Delete user',
      confirmTypedName: true,
    });
    if (!ok) return;
    deletion.mutate(user);
  };

  const openEditDialog = (user: User) => {
    setSelectedUser(user);
    setEditUserForm({
      full_name: user.full_name || '',
      role: user.role,
      is_active: user.is_active,
    });
    setEditUserDialogOpen(true);
  };

  const openResetPasswordDialog = (user: User) => {
    setSelectedUser(user);
    setNewPassword('');
    setConfirmNewPassword('');
    setResetPasswordDialogOpen(true);
  };

  if (!isAdmin) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="destructive">
          <AlertDescription>Access denied. Administrator privileges required.</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-lg p-md md:p-lg">
      <header className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">System Settings</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Deployment-wide administration: worker health, accounts and installation settings.
          </p>
        </div>
        <Button onClick={() => setNewUserDialogOpen(true)}>
          <Plus className="size-4" aria-hidden /> Add User
        </Button>
      </header>

      {/* Deployment worker health. First on the page because a stalled
          ingestion or report worker silently breaks every user's uploads and
          exports, and until now nothing in the UI surfaced it. */}
      <QueueHealthCard />

      {/* 5.350.0 — one job per tab, the tab in the address (`?tab=`): the
          page had grown to five unrelated sections and each new setting
          pushed the user table further down.  Worker health stays above the
          tabs: it must be seen without being looked for. */}
      <Tabs value={tab} onValueChange={(v) => setParams(v === DEFAULT_TAB ? {} : { tab: v }, { replace: true })}>
        <TabsList>
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="remediation">Remediation</TabsTrigger>
          <TabsTrigger value="report-writing">Report writing</TabsTrigger>
        </TabsList>

        {/* The two forms stay mounted while another tab shows, so text typed
            and not yet saved is still there on the way back. */}
        <TabsContent value="remediation" forceMount hidden={tab !== 'remediation'}>
          <RemediationSettingsSection />
        </TabsContent>
        <TabsContent value="report-writing" forceMount hidden={tab !== 'report-writing'}>
          <ReportWritingGuidanceSection />
        </TabsContent>

        <TabsContent value="users" className="space-y-lg">
      <PostureSection
        title={<>User management{!loading && !usersUnread && <SectionCount>{users.length}</SectionCount>}</>}
        description="Account roles are global; what a user can do with project data is set per project."
      >
          {usersError && (
            <p role="alert" className="break-words text-metadata text-destructive">
              {usersError}{' '}
              <button type="button" className="text-info hover:underline" onClick={() => { void usersQuery.refetch(); }}>Retry</button>
            </p>
          )}
          {loading ? (
            <InlineLoader label="Loading users…" size="lg" centered />
          ) : usersUnread ? null : (
            <div className="overflow-x-auto">
              {/* Fixed columns + a floor so the User column never collapses;
                  a narrow window scrolls this table, not the page. */}
              <Table className="min-w-[60rem]">
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead className="w-40">Role</TableHead>
                    <TableHead className="w-40">Status</TableHead>
                    <TableHead className="w-20">2FA</TableHead>
                    <TableHead className="w-32">Last login</TableHead>
                    <TableHead className="w-32">Created</TableHead>
                    <TableHead className="w-20 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((u) => {
                    const meta = ROLE_META[u.role as keyof typeof ROLE_META] ?? null;
                    const isCurrent = u.id === currentUser?.id;
                    return (
                      <TableRow key={u.id}>
                        <TableCell>
                          <div className="flex min-w-0 items-center gap-xs">
                            <div
                              className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-caption font-semibold"
                              aria-hidden
                            >
                              {personInitials(u.full_name, u.username)}
                            </div>
                            <div className="min-w-0">
                              <p className="truncate text-metadata font-medium text-foreground" title={u.full_name || u.username}>
                                {u.full_name || u.username}
                              </p>
                              <p className="truncate text-caption text-muted-foreground" title={`@${u.username}`}>@{u.username}</p>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-xxs">
                            <Select
                              value={u.role}
                              onValueChange={(v) => handleRoleChange(u, v)}
                              disabled={roleSavingUserId === u.id || isCurrent}
                            >
                              <SelectTrigger className="w-32">
                                <SelectValue>
                                  <span className="flex items-center gap-xs">
                                    {meta && <meta.Icon className="size-3.5" aria-hidden />}
                                    {u.role.toUpperCase()}
                                  </span>
                                </SelectValue>
                              </SelectTrigger>
                              {/* v4.8.0 — global role is binary.
                                  analyst/auditor/viewer moved to
                                  per-project membership roles
                                  (Project Settings → Members). */}
                              <SelectContent>
                                <SelectItem value="admin">Admin</SelectItem>
                                <SelectItem value="member">Member</SelectItem>
                              </SelectContent>
                            </Select>
                            {/* Inline spinner so the round-trip is
                                visible — disabling the Select alone
                                left users wondering whether the
                                click registered (audit M10). */}
                            {roleSavingUserId === u.id && (
                              <>
                                <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden />
                                <span role="status" className="sr-only">Saving role for {u.username}</span>
                              </>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-xxs">
                            <Select
                              value={u.is_active ? 'active' : 'inactive'}
                              onValueChange={(v) => handleStatusChange(u, v === 'active')}
                              disabled={statusSavingUserId === u.id || isCurrent}
                            >
                              <SelectTrigger className="w-32">
                                <SelectValue>
                                  <span className="flex items-center gap-xs">
                                    {u.is_active ? (
                                      <CheckCircle2 className="size-3.5 text-success" aria-hidden />
                                    ) : (
                                      <XCircle className="size-3.5 text-destructive" aria-hidden />
                                    )}
                                    {u.is_active ? 'Active' : 'Inactive'}
                                  </span>
                                </SelectValue>
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="active">Active</SelectItem>
                                <SelectItem value="inactive">Inactive</SelectItem>
                              </SelectContent>
                            </Select>
                            {statusSavingUserId === u.id && (
                              <>
                                <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden />
                                <span role="status" className="sr-only">Saving status for {u.username}</span>
                              </>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          {u.totp_enabled ? (
                            <Badge variant="success" className="gap-xxs">
                              <ShieldCheck className="size-3" aria-hidden /> On
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-muted-foreground">Off</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-metadata text-foreground"><DateTimeCell value={u.last_login} /></TableCell>
                        <TableCell className="text-metadata text-foreground"><DateTimeCell value={u.created_at} /></TableCell>
                        <TableCell className="text-right">
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                disabled={isCurrent && (u.role === 'admin' || !u.is_active)}
                                aria-label={`More actions for ${u.username}`}
                              >
                                <MoreVertical className="size-4" aria-hidden />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem onSelect={() => openEditDialog(u)}>
                                <Edit className="size-3.5" aria-hidden /> Edit Profile
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => setMembershipsUser(u)}>
                                <Users className="size-3.5" aria-hidden /> Manage Memberships
                              </DropdownMenuItem>
                              <DropdownMenuItem onSelect={() => openResetPasswordDialog(u)}>
                                <Lock className="size-3.5" aria-hidden /> Reset Password
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => handleReset2fa(u)}
                                disabled={!u.totp_enabled || reset2faUserId === u.id}
                              >
                                <ShieldOff className="size-3.5" aria-hidden /> Reset 2FA
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => handleDeleteUser(u)}
                                disabled={isCurrent}
                                className="text-destructive focus:bg-destructive/10 focus:text-destructive"
                              >
                                <Trash2 className="size-3.5" aria-hidden /> Delete User
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
      </PostureSection>

      {/* v5.288.0 — no section description: it restated the paragraph below. */}
      <PostureSection title="Role reference">
          <p className="mb-sm max-w-3xl text-metadata text-muted-foreground">
            The <strong>account role</strong> below is global and binary — it only decides
            system-administration access. What a user can do <em>with project data</em> is set
            separately by their <strong>project role</strong>, assigned per project under Project
            Settings → Members. New users default to <strong>Member</strong>.
          </p>
          <p className="mb-xs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
            Account role (global)
          </p>
          <div className="mb-md overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/6">Role</TableHead>
                  <TableHead className="w-2/5">Grants</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell><Badge variant="destructive">ADMIN</Badge></TableCell>
                  <TableCell>Full system access — manage users, system settings, audit log; implicitly admin on every project.</TableCell>
                  <TableCell className="text-muted-foreground">Reserve for system operators.</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell><Badge variant="success">MEMBER</Badge></TableCell>
                  <TableCell>A standard account. No inherent access to project data on its own.</TableCell>
                  <TableCell className="text-muted-foreground">Capabilities come entirely from project memberships.</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
          <p className="mb-xs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
            Project role (per project — set under Project Settings → Members)
          </p>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/6">Role</TableHead>
                  <TableHead className="w-2/5">Permissions</TableHead>
                  <TableHead>Restrictions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell><Badge variant="destructive">ADMIN</Badge></TableCell>
                  <TableCell>Everything Analyst can do, plus manage the project's membership.</TableCell>
                  <TableCell className="text-muted-foreground">Scoped to this project only.</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell><Badge variant="warning">ANALYST</Badge></TableCell>
                  <TableCell>Upload scans, manage scopes and subnets, create/edit notes, follow hosts, have their agent scan a scope, and manage parse errors.</TableCell>
                  <TableCell className="text-muted-foreground">Cannot manage project membership.</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell><Badge variant="info">AUDITOR</Badge></TableCell>
                  <TableCell>Read-only access to all scan data, hosts, vulnerabilities, and risk assessments. Can export reports.</TableCell>
                  <TableCell className="text-muted-foreground">Cannot upload, edit, delete, or modify any data.</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell><Badge variant="success">VIEWER</Badge></TableCell>
                  <TableCell>Basic read-only access to scans and host listings.</TableCell>
                  <TableCell className="text-muted-foreground">Cannot access scopes, parse errors, or export data.</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
      </PostureSection>
        </TabsContent>
      </Tabs>

      {/* Create User Dialog */}
      <Dialog
        open={newUserDialogOpen}
        onOpenChange={(next) => !next && !saving && closeNewUserDialog()}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add New User</DialogTitle>
            <DialogDescription>
              Member accounts get project access via project membership; Admin accounts get full
              system access. Role can be changed later from the users table.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="new-username">Username</Label>
              <Input
                id="new-username"
                value={newUserForm.username}
                onChange={(e) => setNewUserForm({ ...newUserForm, username: e.target.value })}
                required
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="new-fullname">Full Name</Label>
              <Input
                id="new-fullname"
                value={newUserForm.full_name}
                onChange={(e) => setNewUserForm({ ...newUserForm, full_name: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="new-password">Password</Label>
              <Input
                id="new-password"
                type="password"
                value={newUserForm.password}
                onChange={(e) => setNewUserForm({ ...newUserForm, password: e.target.value })}
                required
                aria-describedby="new-password-rules"
              />
              <PasswordRulesChecklist
                id="new-password-rules"
                password={newUserForm.password}
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="new-confirm-password">Confirm Password</Label>
              <Input
                id="new-confirm-password"
                type="password"
                value={newUserForm.confirm_password}
                onChange={(e) => setNewUserForm({ ...newUserForm, confirm_password: e.target.value })}
                required
                aria-invalid={newUserPasswordMismatch}
                aria-describedby={newUserPasswordMismatch ? 'new-password-mismatch' : undefined}
              />
              {newUserPasswordMismatch && (
                <p id="new-password-mismatch" role="alert" className="text-caption text-warning">
                  Passwords do not match
                </p>
              )}
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="new-role">Account role</Label>
              <Select
                value={newUserForm.role}
                onValueChange={(v) => setNewUserForm({ ...newUserForm, role: v })}
              >
                <SelectTrigger id="new-role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="member">Member</SelectItem>
                  <SelectItem value="admin">Admin</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-caption text-muted-foreground">
                <strong>Member</strong> — standard account; what they can do is set per project
                under Project Settings → Members. <strong>Admin</strong> — full system access:
                user management, settings, audit log.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeNewUserDialog} disabled={saving}>
              Cancel
            </Button>
            <Button
              onClick={handleCreateUser}
              disabled={
                saving ||
                !newUserForm.username ||
                !isPasswordValid(newUserForm.password) ||
                newUserForm.password !== newUserForm.confirm_password
              }
            >
              {saving ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Creating…</> : 'Create User'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit User Dialog */}
      <Dialog
        open={editUserDialogOpen}
        onOpenChange={(next) => !next && !saving && setEditUserDialogOpen(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit User: {selectedUser?.username}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="edit-fullname">Full Name</Label>
              <Input
                id="edit-fullname"
                value={editUserForm.full_name}
                onChange={(e) => setEditUserForm({ ...editUserForm, full_name: e.target.value })}
              />
            </div>
            <div className="flex items-center gap-xs">
              <Switch
                id="edit-active"
                checked={editUserForm.is_active}
                onCheckedChange={(v) => setEditUserForm({ ...editUserForm, is_active: Boolean(v) })}
                disabled={selectedUser?.id === currentUser?.id}
              />
              <Label htmlFor="edit-active">Active</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditUserDialogOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleUpdateUser} disabled={saving}>
              {saving ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Updating…</> : 'Update User'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset Password Dialog */}
      <Dialog
        open={resetPasswordDialogOpen}
        onOpenChange={(next) => !next && !saving && closeResetPasswordDialog()}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset Password: {selectedUser?.username}</DialogTitle>
            <DialogDescription>
              This bypasses the user's current password. They'll need the new one to sign in.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-xs">
            <Label htmlFor="reset-pw">New Password</Label>
            <Input
              id="reset-pw"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              autoFocus
              aria-describedby="reset-pw-rules"
            />
            <PasswordRulesChecklist id="reset-pw-rules" password={newPassword} />
          </div>
          <div className="flex flex-col gap-xs">
            <Label htmlFor="reset-confirm-pw">Confirm New Password</Label>
            <Input
              id="reset-confirm-pw"
              type="password"
              value={confirmNewPassword}
              onChange={(e) => setConfirmNewPassword(e.target.value)}
              required
              aria-invalid={resetPasswordMismatch}
              aria-describedby={resetPasswordMismatch ? 'reset-pw-mismatch' : undefined}
            />
            {resetPasswordMismatch && (
              <p id="reset-pw-mismatch" role="alert" className="text-caption text-warning">
                Passwords do not match
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeResetPasswordDialog} disabled={saving}>
              Cancel
            </Button>
            <Button
              onClick={handleResetPassword}
              disabled={saving || !isPasswordValid(newPassword) || newPassword !== confirmNewPassword}
            >
              {saving ? <><Loader2 className="size-4 animate-spin" aria-hidden /> Resetting…</> : <><Lock className="size-4" aria-hidden /> Reset Password</>}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* v2.59.0 — admin can view + edit any user's project memberships
          without leaving System Settings.  Backed by GET
          /api/v1/users/{id}/memberships + the existing
          /projects/{id}/members POST/PUT/DELETE surface. */}
      <UserMembershipsDialog
        user={membershipsUser}
        onClose={() => setMembershipsUser(null)}
      />

      {confirmEl}
    </div>
  );
};

export default SystemSettings;

// Placeholder export to keep ESLint happy if User stays unused at top level.
export type { User };
