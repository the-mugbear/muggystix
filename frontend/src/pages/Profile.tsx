import React, { useCallback, useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  FolderOpen,
  Loader2,
  Lock,
  RefreshCw,
  Save,
  Shield,
  SquareArrowOutUpRight,
  Trash2,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useProject } from '../contexts/ProjectContext';
import apiClient from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { PasswordInput } from '../components/ui/password-input';
import { Label } from '../components/ui/label';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Separator } from '../components/ui/separator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { cn } from '../utils/cn';
import { DetailSkeleton } from '../components/PageSkeleton';
import { PasswordRulesChecklist, isPasswordValid } from '../components/PasswordRulesChecklist';

interface UserSession {
  id: number;
  ip_address: string;
  user_agent: string;
  created_at: string;
  last_activity: string;
  expires_at: string;
}

interface MyProjectMembership {
  project_id: number;
  project_name: string;
  project_slug: string;
  project_status: string;
  project_is_default: boolean;
  project_is_archived: boolean;
  role: string;
  joined_at: string | null;
}

const roleVariant = (
  role: string,
): 'destructive' | 'warning' | 'info' | 'success' | 'muted' => {
  switch (role) {
    case 'admin':
      return 'destructive';
    case 'member':
      return 'success';
    // Pre-2.46.0 global roles — kept so a stale value still renders.
    case 'analyst':
      return 'warning';
    case 'auditor':
      return 'info';
    case 'viewer':
      return 'success';
    default:
      return 'muted';
  }
};

const formatDate = (s: string | null | undefined) => (s ? new Date(s).toLocaleString() : '—');

const Profile: React.FC = () => {
  const { user, updateUser } = useAuth();
  const { selectProject, projects } = useProject();
  const navigate = useNavigate();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();

  const [saving, setSaving] = useState(false);
  const [profileForm, setProfileForm] = useState({ full_name: user?.full_name || '' });

  const [passwordForm, setPasswordForm] = useState({
    current_password: '',
    new_password: '',
    confirm_password: '',
  });
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordError, setPasswordError] = useState<string>('');
  // Block submit + announce when the confirmation diverges (a11y: associated
  // with the field via role=alert + aria-describedby).
  const passwordMismatch =
    passwordForm.confirm_password.length > 0 &&
    passwordForm.new_password !== passwordForm.confirm_password;

  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);

  // Project associations — the projects this user is a member of, with
  // their per-project role. Loaded once on mount; refreshable via the
  // Refresh button on the card so a freshly-added project shows up
  // without a full page reload.
  const [memberships, setMemberships] = useState<MyProjectMembership[] | null>(null);
  const [membershipsLoading, setMembershipsLoading] = useState(true);
  const [membershipsError, setMembershipsError] = useState<string | null>(null);

  const fetchMemberships = useCallback(() => {
    let cancelled = false;
    setMembershipsLoading(true);
    setMembershipsError(null);
    apiClient
      .get<MyProjectMembership[]>('/users/profile/projects')
      .then((r) => {
        if (!cancelled) setMemberships(r.data);
      })
      .catch((err) => {
        if (cancelled) return;
        setMemberships(null);
        setMembershipsError(formatApiError(err, 'Failed to load project associations.'));
      })
      .finally(() => {
        if (!cancelled) setMembershipsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => fetchMemberships(), [fetchMemberships]);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get('/auth/sessions')
      .then((r) => {
        if (!cancelled) setSessions(r.data);
      })
      .catch((err) => {
        if (!cancelled) toast.error(formatApiError(err, 'Failed to load sessions.'));
      })
      .finally(() => {
        if (!cancelled) setSessionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleProfileSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      await apiClient.put('/users/profile', profileForm);
      if (user) updateUser({ ...user, full_name: profileForm.full_name });
      toast.success('Profile updated.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update profile.'));
    } finally {
      setSaving(false);
    }
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError('');
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      setPasswordError('New passwords do not match.');
      return;
    }
    setPasswordSaving(true);
    try {
      await apiClient.post('/auth/change-password', {
        current_password: passwordForm.current_password,
        new_password: passwordForm.new_password,
      });
      setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
      setPasswordDialogOpen(false);
      toast.success('Password changed.');
    } catch (err: unknown) {
      setPasswordError(formatApiError(err, 'Failed to change password.'));
    } finally {
      setPasswordSaving(false);
    }
  };

  const handleRevokeSession = async (session: UserSession) => {
    const ok = await confirm({
      title: 'Revoke session?',
      body: `This will sign out the session on ${session.ip_address}. If it's this browser, you'll be redirected to login on the next request.`,
      severity: 'danger',
      confirmLabel: 'Revoke',
    });
    if (!ok) return;
    try {
      await apiClient.delete(`/auth/sessions/${session.id}`);
      setSessions((prev) => prev.filter((s) => s.id !== session.id));
      toast.success('Session revoked.');
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to revoke session.'));
    }
  };

  if (!user) {
    return <DetailSkeleton />;
  }

  return (
    <div className="mx-auto max-w-5xl p-md md:p-lg">
      <h1 className="mb-md text-page-title">User Profile</h1>

      <div className="grid grid-cols-1 gap-md md:grid-cols-3">
        {/* User Info Card */}
        <Card className="md:col-span-1">
          <CardContent className="flex flex-col items-center p-lg text-center">
            <div className="mb-md flex size-20 items-center justify-center rounded-full bg-primary text-primary-foreground text-section-title font-semibold">
              {user.username.charAt(0).toUpperCase()}
            </div>
            <p className="text-subheading font-semibold text-foreground">
              {user.full_name || user.username}
            </p>
            <Badge variant={roleVariant(user.role)} className="mt-xs">
              {user.role.toUpperCase()}
            </Badge>
            <p className="mt-sm text-caption text-muted-foreground">
              Member since {formatDate(user.created_at)}
            </p>
            {user.last_login && (
              <p className="text-caption text-muted-foreground">
                Last login: {formatDate(user.last_login)}
              </p>
            )}
          </CardContent>
        </Card>

        {/* Profile Form */}
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Profile Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleProfileSubmit} className="flex flex-col gap-md">
              <div className="flex flex-col gap-xs">
                <Label htmlFor="profile-username">Username</Label>
                {/* readOnly (not disabled) so NVDA browse-mode users can
                    still navigate to the field and read its value
                    (audit a11y·L4). */}
                <Input
                  id="profile-username"
                  value={user.username}
                  readOnly
                  aria-readonly
                  aria-describedby="profile-username-help"
                />
                <p
                  id="profile-username-help"
                  className="text-caption text-muted-foreground"
                >
                  Username cannot be changed.
                </p>
              </div>
              <div className="flex flex-col gap-xs">
                <Label htmlFor="profile-fullname">Full Name</Label>
                <Input
                  id="profile-fullname"
                  value={profileForm.full_name}
                  onChange={(e) => setProfileForm({ ...profileForm, full_name: e.target.value })}
                />
              </div>
            </form>
          </CardContent>
          <CardFooter className="flex flex-wrap gap-xs">
            <Button type="submit" onClick={handleProfileSubmit} disabled={saving}>
              {saving ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden /> Saving…
                </>
              ) : (
                <>
                  <Save className="size-4" aria-hidden /> Save Changes
                </>
              )}
            </Button>
            <Button variant="outline" onClick={() => setPasswordDialogOpen(true)}>
              <Lock className="size-4" aria-hidden /> Change Password
            </Button>
          </CardFooter>
        </Card>

        {/* Project Associations — every project the user is a member
            of, with their per-project role. Switching project from
            here uses the same selectProject path as the topbar
            ProjectSelector so the route-safe redirect (CRIT-1) fires
            if needed. */}
        <Card className="md:col-span-3">
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-xs">
            <CardTitle className="flex items-center gap-xs">
              <FolderOpen className="size-4" aria-hidden /> Project Associations
              {memberships && (
                <span className="text-caption font-normal text-muted-foreground">
                  ({memberships.length})
                </span>
              )}
            </CardTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={fetchMemberships}
              disabled={membershipsLoading}
              aria-label="Refresh project associations"
            >
              <RefreshCw
                className={cn('size-3.5', membershipsLoading && 'animate-spin')}
                aria-hidden
              />
              Refresh
            </Button>
          </CardHeader>
          <CardContent>
            {membershipsLoading && !memberships ? (
              <div className="flex justify-center py-md">
                <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
              </div>
            ) : membershipsError ? (
              <p className="text-metadata text-destructive">{membershipsError}</p>
            ) : memberships && memberships.length > 0 ? (
              <ul className="flex flex-col divide-y divide-border">
                {memberships.map((m) => {
                  // selectProject expects the full Project shape from
                  // the ProjectContext; look it up rather than
                  // constructing a partial Project.
                  const projectRow = projects.find((p) => p.id === m.project_id);
                  return (
                    <li
                      key={m.project_id}
                      className="flex flex-wrap items-center justify-between gap-sm py-sm"
                    >
                      <div className="flex min-w-0 flex-1 flex-col gap-xxs">
                        <div className="flex flex-wrap items-center gap-xs">
                          <p className="truncate text-metadata font-medium text-foreground">
                            {m.project_name}
                          </p>
                          {m.project_is_archived && (
                            <Badge variant="muted">Archived</Badge>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-md text-caption text-muted-foreground">
                          <span>Status: {m.project_status}</span>
                          {m.joined_at ? (
                            <span>Member since {formatDate(m.joined_at)}</span>
                          ) : (
                            <span>Global admin access</span>
                          )}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-xs">
                        <Badge variant={roleVariant(m.role)}>{m.role.toUpperCase()}</Badge>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            if (projectRow) {
                              selectProject(projectRow);
                              navigate('/operations');
                            }
                          }}
                          // Switch is only meaningful when the project
                          // appears in the user-visible projects list
                          // (admins viewing an archived/foreign project
                          // they've never selected may not have it
                          // hydrated in context yet).
                          disabled={!projectRow}
                          aria-label={`Switch to ${m.project_name}`}
                        >
                          <SquareArrowOutUpRight className="size-3.5" aria-hidden />
                          Switch
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-metadata text-muted-foreground">
                You aren't a member of any projects yet. Ask an administrator to add you.
              </p>
            )}
          </CardContent>
        </Card>

        {/* Active Sessions */}
        <Card className="md:col-span-3">
          <CardHeader>
            <CardTitle className="flex items-center gap-xs">
              <Shield className="size-4" aria-hidden /> Active Sessions
            </CardTitle>
          </CardHeader>
          <CardContent>
            {sessionsLoading ? (
              <div className="flex justify-center py-md">
                <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
              </div>
            ) : sessions.length === 0 ? (
              <p className="text-metadata text-muted-foreground">No active sessions found.</p>
            ) : (
              <ul className="flex flex-col">
                {sessions.map((session, index) => (
                  <li key={session.id}>
                    {index > 0 && <Separator className="my-sm" />}
                    <div className="flex items-start gap-sm">
                      <div className="min-w-0 flex-1">
                        <p className="text-metadata font-medium text-foreground">{session.ip_address}</p>
                        <p className="text-caption text-muted-foreground line-clamp-2 break-all">
                          {session.user_agent}
                        </p>
                        <div className="mt-xxs flex flex-wrap gap-md text-caption text-muted-foreground">
                          <span>Created: {formatDate(session.created_at)}</span>
                          <span>Last active: {formatDate(session.last_activity)}</span>
                          <span>Expires: {formatDate(session.expires_at)}</span>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleRevokeSession(session)}
                        aria-label={`Revoke session from ${session.ip_address}`}
                        className={cn('text-muted-foreground hover:text-destructive')}
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Password Change Dialog */}
      <Dialog
        open={passwordDialogOpen}
        onOpenChange={(next) => !next && !passwordSaving && setPasswordDialogOpen(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change Password</DialogTitle>
            <DialogDescription>
              Enter your current password and a new one. You'll stay signed in.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handlePasswordSubmit} className="flex flex-col gap-md">
            {passwordError && (
              <p className="text-caption text-destructive" role="alert">
                {passwordError}
              </p>
            )}
            <div className="flex flex-col gap-xs">
              <Label htmlFor="profile-current-pw">Current Password</Label>
              <PasswordInput
                id="profile-current-pw"
                value={passwordForm.current_password}
                onChange={(e) =>
                  setPasswordForm({ ...passwordForm, current_password: e.target.value })
                }
                required
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="profile-new-pw">New Password</Label>
              <PasswordInput
                id="profile-new-pw"
                value={passwordForm.new_password}
                onChange={(e) =>
                  setPasswordForm({ ...passwordForm, new_password: e.target.value })
                }
                aria-describedby="profile-pw-rules"
                required
              />
              {/* Pre-audit (H9): Profile only learned the password
                  policy when the server rejected.  The shared
                  checklist matches the rules ForceChangePassword
                  already shows, so users see requirements live. */}
              <PasswordRulesChecklist
                id="profile-pw-rules"
                password={passwordForm.new_password}
              />
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="profile-confirm-pw">Confirm New Password</Label>
              <Input
                id="profile-confirm-pw"
                type="password"
                value={passwordForm.confirm_password}
                onChange={(e) =>
                  setPasswordForm({ ...passwordForm, confirm_password: e.target.value })
                }
                aria-invalid={passwordMismatch}
                aria-describedby={passwordMismatch ? 'profile-pw-mismatch' : undefined}
                required
              />
              {passwordMismatch && (
                <p id="profile-pw-mismatch" role="alert" className="text-caption text-warning">
                  Passwords do not match
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setPasswordDialogOpen(false)}
                disabled={passwordSaving}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={passwordSaving || passwordMismatch}>
                {passwordSaving ? (
                  <>
                    <Loader2 className="size-4 animate-spin" aria-hidden /> Changing…
                  </>
                ) : (
                  <>
                    <Lock className="size-4" aria-hidden /> Change Password
                  </>
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {confirmEl}
    </div>
  );
};

export default Profile;
