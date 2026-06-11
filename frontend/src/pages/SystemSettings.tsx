import React, { useState, useEffect } from 'react';
import {
  Shield,
  ShieldCheck,
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
import apiClient from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
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
import UserMembershipsDialog from '../components/UserMembershipsDialog';
import { PasswordRulesChecklist, isPasswordValid } from '../components/PasswordRulesChecklist';

interface User {
  id: number;
  username: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  last_login: string | null;
  created_at: string;
  created_by_id: number | null;
}

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

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString() : 'Never');

const SystemSettings: React.FC = () => {
  const { user: currentUser, hasPermission } = useAuth();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [roleSavingUserId, setRoleSavingUserId] = useState<number | null>(null);
  const [statusSavingUserId, setStatusSavingUserId] = useState<number | null>(null);

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

  useEffect(() => {
    if (!hasPermission('admin')) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    apiClient
      .get('/users/')
      .then((r) => {
        if (!cancelled) setUsers(r.data);
      })
      .catch((err) => {
        if (!cancelled) toast.error(formatApiError(err, 'Failed to load users.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPermission]);

  const handleCreateUser = async () => {
    setSaving(true);
    try {
      // confirm_password is a client-only guard against typos — don't send it.
      const { confirm_password: _confirm, ...payload } = newUserForm;
      const response = await apiClient.post('/auth/register', payload);
      setUsers((prev) => [...prev, response.data]);
      setNewUserDialogOpen(false);
      setNewUserForm({ username: '', password: '', confirm_password: '', full_name: '', role: 'member' });
      toast.success('User created.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to create user.'));
    } finally {
      setSaving(false);
    }
  };

  // Client-only typo guard for the create-user dialog (the password is set
  // once at creation, so a mistype would otherwise lock the new account out).
  const newUserPasswordMismatch =
    newUserForm.confirm_password.length > 0 &&
    newUserForm.password !== newUserForm.confirm_password;
  // Same typo guard for the admin reset-password dialog.
  const resetPasswordMismatch =
    confirmNewPassword.length > 0 && newPassword !== confirmNewPassword;

  const handleUpdateUser = async () => {
    if (!selectedUser) return;
    setSaving(true);
    try {
      const response = await apiClient.put(`/users/${selectedUser.id}`, editUserForm);
      setUsers((prev) => prev.map((u) => (u.id === selectedUser.id ? response.data : u)));
      setEditUserDialogOpen(false);
      toast.success('User updated.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update user.'));
    } finally {
      setSaving(false);
    }
  };

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
    setRoleSavingUserId(user.id);
    try {
      const response = await apiClient.put(`/users/${user.id}`, {
        full_name: user.full_name || '',
        role,
        is_active: user.is_active,
      });
      setUsers((prev) => prev.map((u) => (u.id === user.id ? response.data : u)));
      toast.success(`Updated ${user.username}'s role to ${role}.`);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update role.'));
    } finally {
      setRoleSavingUserId(null);
    }
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
    setStatusSavingUserId(user.id);
    try {
      const response = await apiClient.put(`/users/${user.id}`, {
        full_name: user.full_name || '',
        role: user.role,
        is_active: isActive,
      });
      setUsers((prev) => prev.map((u) => (u.id === user.id ? response.data : u)));
      toast.success(`${user.username} is now ${isActive ? 'active' : 'inactive'}.`);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update status.'));
    } finally {
      setStatusSavingUserId(null);
    }
  };

  const handleResetPassword = async () => {
    if (!selectedUser) return;
    setSaving(true);
    try {
      await apiClient.post(`/users/${selectedUser.id}/reset-password`, { new_password: newPassword });
      setResetPasswordDialogOpen(false);
      setNewPassword('');
    setConfirmNewPassword('');
      toast.success('Password reset.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to reset password.'));
    } finally {
      setSaving(false);
    }
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
    try {
      await apiClient.delete(`/users/${user.id}`);
      setUsers((prev) => prev.filter((u) => u.id !== user.id));
      toast.success('User deleted.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to delete user.'));
    }
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

  if (!hasPermission('admin')) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="destructive">
          <AlertDescription>Access denied. Administrator privileges required.</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex items-center justify-between">
        <h1 className="flex items-center gap-xs text-page-title">
          <Shield className="size-6" aria-hidden /> System Settings
        </h1>
        <Button onClick={() => setNewUserDialogOpen(true)}>
          <Plus className="size-4" aria-hidden /> Add User
        </Button>
      </div>

      <Card className="mb-md">
        <CardHeader>
          <CardTitle>User Management</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <InlineLoader label="Loading users…" size="lg" centered />
          ) : (
            <div className="overflow-x-auto rounded-panel border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last Login</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="w-12 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((u) => {
                    const meta = ROLE_META[u.role as keyof typeof ROLE_META] ?? null;
                    const isCurrent = u.id === currentUser?.id;
                    return (
                      <TableRow key={u.id}>
                        <TableCell>
                          <div className="flex items-center gap-xs">
                            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-caption font-semibold">
                              {u.username.charAt(0).toUpperCase()}
                            </div>
                            <div className="min-w-0">
                              <p className="text-metadata font-medium text-foreground">
                                {u.full_name || u.username}
                              </p>
                              <p className="text-caption text-muted-foreground">@{u.username}</p>
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
                        <TableCell className="text-metadata text-foreground">{fmtDate(u.last_login)}</TableCell>
                        <TableCell className="text-metadata text-foreground">{fmtDate(u.created_at)}</TableCell>
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Role Reference</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-sm text-metadata text-muted-foreground">
            Access is decided on <strong>two levels</strong>. The <strong>account role</strong>
            below is global and binary — it only decides system-administration access. What a
            user can do <em>with project data</em> is set separately by their{' '}
            <strong>project role</strong>, assigned per project under Project Settings → Members.
            New users default to <strong>Member</strong>.
          </p>
          <p className="mb-xs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
            Account role (global)
          </p>
          <div className="mb-md overflow-x-auto rounded-panel border border-border">
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
          <div className="overflow-x-auto rounded-panel border border-border">
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
                  <TableCell>Upload scans, manage scopes and subnets, create/edit notes, follow hosts, run recon, and manage parse errors.</TableCell>
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
        </CardContent>
      </Card>

      {/* Create User Dialog */}
      <Dialog
        open={newUserDialogOpen}
        onOpenChange={(next) => !next && !saving && setNewUserDialogOpen(false)}
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
            <Button variant="outline" onClick={() => setNewUserDialogOpen(false)} disabled={saving}>
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
        onOpenChange={(next) => !next && !saving && setResetPasswordDialogOpen(false)}
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
            <Button variant="outline" onClick={() => setResetPasswordDialogOpen(false)} disabled={saving}>
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
