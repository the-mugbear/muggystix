import React, { useState } from 'react';
import { Lock, UserCircle2, Loader2, LockKeyhole, ShieldCheck } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { PasswordInput } from '../components/ui/password-input';
import { Label } from '../components/ui/label';
import { Button } from '../components/ui/button';
import { Alert, AlertDescription } from '../components/ui/alert';
import { cn } from '../utils/cn';

const Login: React.FC = () => {
  const { login, verify2fa } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  // Non-null once the password step returns a 2FA challenge — switches the
  // card to the code-entry step.
  const [challengeToken, setChallengeToken] = useState<string | null>(null);
  const [code, setCode] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);
    try {
      const outcome = await login(username, password);
      if (outcome.twoFactorRequired) {
        // Move to the second-factor step; login() did not create a session.
        setChallengeToken(outcome.challengeToken);
        setCode('');
      }
      // Non-2FA success navigates inside login(); nothing to do here.
    } catch (err: unknown) {
      // 401 vs 403 vs network/server — surface the most actionable text.
      // Audit C5: distinguish credential failure from account-disabled
      // so locked-out users stop self-blaming.
      console.error('Login failed:', err);
      const e = asAxiosError(err);
      const status: number | undefined = e.response?.status;
      if (status === 401) {
        setError('Incorrect username or password.');
      } else if (status === 403) {
        const detailRaw = e.response?.data?.detail;
        const detail: string | undefined = typeof detailRaw === 'string' ? detailRaw : undefined;
        setError(detail ?? 'Account disabled. Contact an administrator.');
      } else if (status === 423) {
        // Audit FBK·M1: explicit lockout copy — don't pass through
        // formatApiError because the backend's raw detail is too
        // jargon-heavy for end users at this moment.
        setError('Account locked. Try again later or contact your administrator.');
      } else {
        setError(formatApiError(err, 'Login failed. Please check your credentials and try again.'));
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!challengeToken) return;
    setError('');
    setIsLoading(true);
    try {
      await verify2fa(challengeToken, code.trim());
      // verify2fa navigates on success.
    } catch (err: unknown) {
      const e2 = asAxiosError(err);
      if (e2.response?.status === 401) {
        setError('Incorrect or expired code. Try again, or start over.');
      } else {
        setError(formatApiError(err, 'Verification failed. Please try again.'));
      }
    } finally {
      setIsLoading(false);
    }
  };

  const backToPassword = () => {
    setChallengeToken(null);
    setCode('');
    setError('');
  };

  const hasError = error.length > 0;
  const twoFactorStep = challengeToken !== null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-md">
      <div className="flex w-full max-w-md flex-col items-center gap-md">
        <Card className="brand-login-card w-full">
          <CardContent className="p-lg">
            {/* Header */}
            <div className="mb-lg flex flex-col items-center text-center">
              <div
                className={cn(
                  'mb-md flex size-20 items-center justify-center rounded-full border border-border',
                  'bg-accent',
                )}
              >
                <img
                  src="/bs.svg"
                  alt=""
                  aria-hidden
                  className="size-12"
                />
              </div>
              <h1 className="brand-wordmark brand-wordmark--login text-page-title">BlueStick</h1>
            </div>

            {/* Error alert — role=alert (set by Alert variant='destructive')
                + aria-describedby on the fields so SR users hear the
                error referenced from each input.  Audit C5. */}
            {hasError && (
              <Alert variant="destructive" id="login-error" className="mb-md">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {/* Password step (hidden once a 2FA challenge is in progress) */}
            {!twoFactorStep && (
            <form onSubmit={handleSubmit} className="flex flex-col gap-md" noValidate>
              <div className="flex flex-col gap-xs">
                <Label htmlFor="login-username">Username</Label>
                <div className="relative">
                  <UserCircle2
                    className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                    aria-hidden
                  />
                  <Input
                    id="login-username"
                    name="username"
                    type="text"
                    autoComplete="username"
                    autoFocus
                    required
                    disabled={isLoading}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    aria-invalid={hasError || undefined}
                    aria-describedby={hasError ? 'login-error' : undefined}
                    className="pl-xl"
                  />
                </div>
              </div>

              <div className="flex flex-col gap-xs">
                <Label htmlFor="login-password">Password</Label>
                <div className="relative">
                  <Lock
                    className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                    aria-hidden
                  />
                  <PasswordInput
                    id="login-password"
                    name="password"
                    autoComplete="current-password"
                    required
                    disabled={isLoading}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    aria-invalid={hasError || undefined}
                    aria-describedby={hasError ? 'login-error' : undefined}
                    className="pl-xl"
                  />
                </div>
              </div>

              <Button
                type="submit"
                size="lg"
                className="mt-xs w-full"
                disabled={isLoading || !username || !password}
              >
                {isLoading ? (
                  <>
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                    Authenticating…
                  </>
                ) : (
                  'Sign In'
                )}
              </Button>
            </form>
            )}

            {/* Second-factor step — shown after the password returns a challenge */}
            {twoFactorStep && (
              <form onSubmit={handleVerify} className="flex flex-col gap-md" noValidate>
                <div className="flex flex-col items-center gap-xs text-center">
                  <ShieldCheck className="size-8 text-primary" aria-hidden />
                  <p className="text-metadata font-semibold text-foreground">Two-factor authentication</p>
                  <p className="text-caption text-muted-foreground">
                    Enter the 6-digit code from your authenticator app — or one of your recovery codes.
                  </p>
                </div>
                <div className="flex flex-col gap-xs">
                  <Label htmlFor="login-code">Authentication code</Label>
                  <Input
                    id="login-code"
                    name="otp"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    autoFocus
                    required
                    disabled={isLoading}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    aria-invalid={hasError || undefined}
                    aria-describedby={hasError ? 'login-error' : undefined}
                    placeholder="123 456"
                  />
                </div>
                <Button
                  type="submit"
                  size="lg"
                  className="mt-xs w-full"
                  disabled={isLoading || !code.trim()}
                >
                  {isLoading ? (
                    <>
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                      Verifying…
                    </>
                  ) : (
                    'Verify'
                  )}
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={backToPassword} disabled={isLoading}>
                  Back to sign in
                </Button>
              </form>
            )}

            {/* Security notice */}
            <div className="mt-lg flex items-center justify-center gap-xs rounded-panel border border-border bg-muted px-md py-sm text-caption text-muted-foreground">
              <LockKeyhole className="size-3.5" aria-hidden />
              <span>This system contains sensitive security data. All access is logged and monitored.</span>
            </div>
          </CardContent>
        </Card>

        {/* Footer */}
        <div className="flex flex-col items-center gap-xxs text-center">
          <p className="text-metadata text-muted-foreground">BlueStick</p>
          <p className="text-caption text-muted-foreground">Unauthorized access is prohibited</p>
        </div>
      </div>
    </div>
  );
};

export default Login;
