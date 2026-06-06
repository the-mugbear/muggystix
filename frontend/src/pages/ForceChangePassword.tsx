import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, Loader2 } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import api from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from '../components/ui/card';
import { Label } from '../components/ui/label';
import { PasswordInput } from '../components/ui/password-input';
import { Input } from '../components/ui/input';
import { Button } from '../components/ui/button';
import { Alert, AlertDescription } from '../components/ui/alert';
import { PasswordRulesChecklist, isPasswordValid } from '../components/PasswordRulesChecklist';

const ForceChangePassword: React.FC = () => {
  // navigate kept available for future redirect needs; unused here
  // because the backend revokes all sessions on password change, so
  // logout() forces the redirect to /login naturally.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _navigate = useNavigate();
  const { updateUser, logout } = useAuth();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const allRulesMet = isPasswordValid(newPassword);
  const passwordsMatch = newPassword === confirmPassword && newPassword.length > 0;
  const hasError = error.length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!passwordsMatch) {
      setError('New passwords do not match.');
      return;
    }
    if (!allRulesMet) {
      setError('Password does not meet all requirements. See the checklist below.');
      return;
    }

    setIsLoading(true);
    try {
      await api.post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      updateUser({ must_change_password: false });
      // Backend revokes all sessions on password change; force re-login.
      logout();
    } catch (err: unknown) {
      setError(formatApiError(err, 'Password change failed.'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-md">
      <div className="w-full max-w-md">
        <Card>
          <CardContent className="p-lg">
            {/* Header */}
            <div className="mb-lg flex flex-col items-center text-center">
              <div className="mb-md flex size-16 items-center justify-center rounded-full bg-warning/10">
                <ShieldAlert className="size-8 text-warning" aria-hidden />
              </div>
              <h1 className="text-section-title font-semibold text-foreground">
                Password Change Required
              </h1>
              <p className="mt-xxs text-metadata text-muted-foreground">
                Your account requires a password change before you can continue.
              </p>
            </div>

            {hasError && (
              <Alert variant="destructive" id="change-password-error" className="mb-md">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <form onSubmit={handleSubmit} className="flex flex-col gap-md" noValidate>
              <div className="flex flex-col gap-xs">
                <Label htmlFor="current-password">Current Password</Label>
                <PasswordInput
                  id="current-password"
                  name="current-password"
                  autoComplete="current-password"
                  autoFocus
                  required
                  disabled={isLoading}
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  aria-invalid={hasError || undefined}
                  aria-describedby={hasError ? 'change-password-error' : undefined}
                />
              </div>

              <div className="flex flex-col gap-xs">
                <Label htmlFor="new-password">New Password</Label>
                <PasswordInput
                  id="new-password"
                  name="new-password"
                  autoComplete="new-password"
                  required
                  disabled={isLoading}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  aria-describedby="password-rules"
                />
              </div>

              <PasswordRulesChecklist password={newPassword} id="password-rules" />

              <div className="flex flex-col gap-xs">
                <Label htmlFor="confirm-password">Confirm New Password</Label>
                <Input
                  id="confirm-password"
                  name="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  required
                  disabled={isLoading}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  aria-invalid={
                    confirmPassword.length > 0 && !passwordsMatch ? true : undefined
                  }
                  // v4.58.0 (UX·6) — when the error is visible, link it
                  // via aria-describedby so screen readers announce
                  // why the input is invalid on focus.
                  aria-describedby={
                    confirmPassword.length > 0 && !passwordsMatch
                      ? 'confirm-password-error'
                      : undefined
                  }
                />
                {confirmPassword.length > 0 && !passwordsMatch && (
                  <p
                    id="confirm-password-error"
                    role="alert"
                    className="text-caption text-destructive"
                  >
                    Passwords do not match.
                  </p>
                )}
              </div>

              <Button
                type="submit"
                size="lg"
                // Audit RSP·L3 — use the warning variant instead of
                // hand-rolling bg-warning + hover overrides.
                variant="warning"
                className="mt-xs w-full"
                disabled={
                  isLoading || !currentPassword || !newPassword || !confirmPassword || !allRulesMet || !passwordsMatch
                }
              >
                {isLoading ? (
                  <>
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                    Changing password…
                  </>
                ) : (
                  'Change Password'
                )}
              </Button>

              {/* Escape hatch for users who don't know their current
                  password (audit CRIT-5). */}
              <div className="mt-xs flex flex-col items-center gap-xxs">
                <Button
                  type="button"
                  variant="link"
                  onClick={() => logout()}
                  disabled={isLoading}
                >
                  Sign out
                </Button>
                <p className="text-caption text-muted-foreground">
                  Contact your administrator if you don't know your current password.
                </p>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default ForceChangePassword;
