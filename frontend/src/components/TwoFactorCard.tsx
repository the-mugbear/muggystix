import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldOff, Loader2, KeyRound, Copy, Download } from 'lucide-react';
import { disableTwoFactor, getTwoFactorStatus, regenerateRecoveryCodes } from '../services/api';
import { SECRET_MUTATION, invalidateReads, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { useToast } from '../contexts/ToastContext';
import { useTwoFactorEnrolment } from '../hooks/useTwoFactorEnrolment';
import PostureSection from './posture/PostureSection';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { PasswordInput } from './ui/password-input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Alert, AlertDescription } from './ui/alert';

const TwoFactorCard: React.FC = () => {
  const toast = useToast();
  const queryClient = useQueryClient();

  // The signed-in user's own 2FA state — not a project's.
  const statusQuery = useQuery({
    queryKey: ['getTwoFactorStatus'],
    queryFn: ({ signal }) => getTwoFactorStatus(signal),
  });
  const status = statusQuery.data ?? null;
  // Each step ends by reading the status again, and is busy until it has.
  const rereadStatus = () => invalidateReads(queryClient, 'getTwoFactorStatus');

  // Enrolling — start, confirm a code, the recovery codes once — is the one
  // implementation shared with the forced page (`hooks/useTwoFactorEnrolment`,
  // which also says how its secrets are kept out of the library's cache).
  // This section adds what only an enrolled account has: the status, turning
  // 2FA off, and new recovery codes.  The status is shown on the `start` step.
  const enrol = useTwoFactorEnrolment({ onEnabled: rereadStatus });
  const {
    step, setError, showImport, importSecret, setImportSecret, setup: setupData, code, setCode, recoveryCodes,
  } = enrol;
  // Why the last action failed; a status that could not be read is the query's.
  const error = enrol.error ?? queryErrorText(statusQuery.error, 'Could not load 2FA status.');

  // Re-auth state (disable / regenerate).
  const [password, setPassword] = useState('');
  const [pwAction, setPwAction] = useState<null | 'disable' | 'regenerate'>(null);

  // Disable, or new recovery codes: both ask for the password again.  It
  // carries the password and may return recovery codes, so the library keeps
  // neither once it has settled (`SECRET_MUTATION`, and the `reset` below).
  const reauthenticated = useMutation({
    ...SECRET_MUTATION,
    mutationFn: async (body: { action: 'disable' | 'regenerate'; password: string }) => {
      if (body.action === 'disable') {
        await disableTwoFactor(body.password);
        return null;
      }
      return regenerateRecoveryCodes(body.password);
    },
    onMutate: () => setError(null),
    onSuccess: (codes) => {
      if (codes === null) {
        toast.success('Two-factor authentication disabled.');
      } else {
        enrol.showRecoveryCodes(codes);
      }
      setPassword('');
      setPwAction(null);
      return rereadStatus();
    },
    onError: (err) => setError(formatApiError(err, 'Action failed — check your password.')),
  });
  const runPwAction = () => {
    if (!pwAction) return;
    reauthenticated.mutate({ action: pwAction, password }, { onSettled: () => reauthenticated.reset() });
  };
  const busy = enrol.busy || reauthenticated.isPending;

  return (
    // A section of the Profile page (§7), not a card — the name is historical.
    <PostureSection title="Two-factor authentication">
      <div className="max-w-3xl space-y-md">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* STATUS VIEW */}
        {step === 'start' && status && (
          <div className="space-y-sm">
            <div className="flex flex-wrap items-center gap-xs">
              {status.enabled ? (
                <Badge variant="success">Enabled</Badge>
              ) : (
                <Badge variant="outline">Not enabled</Badge>
              )}
              {status.enabled && (
                <span className="text-caption text-muted-foreground">
                  {status.unused_recovery_codes} recovery code{status.unused_recovery_codes === 1 ? '' : 's'} remaining
                </span>
              )}
            </div>
            <p className="text-metadata text-muted-foreground">
              Protect your account with a time-based one-time code (TOTP). You can scan a new QR code or
              import an existing authenticator secret so the same app entry you already use works here too.
            </p>

            {!status.enabled && (
              <div className="space-y-xs">
                {!showImport ? (
                  <div className="flex flex-wrap gap-xs">
                    <Button onClick={enrol.start} disabled={busy}>
                      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <KeyRound className="size-4" aria-hidden />}
                      Set up with a new secret
                    </Button>
                    <Button variant="outline" onClick={enrol.openImport} disabled={busy}>
                      Import existing secret
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-xs rounded-control border border-border p-sm">
                    <Label htmlFor="tfa-import">Existing base32 secret</Label>
                    <Input
                      id="tfa-import"
                      value={importSecret}
                      onChange={(e) => setImportSecret(e.target.value)}
                      placeholder="JBSWY3DPEHPK3PXP…"
                      autoComplete="off"
                    />
                    <p className="text-caption text-muted-foreground">
                      Paste the seed your authenticator already holds (e.g. your machine-login TOTP secret).
                    </p>
                    <div className="flex gap-xs">
                      <Button onClick={enrol.start} disabled={busy || !importSecret.trim()}>
                        {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Continue
                      </Button>
                      <Button variant="ghost" onClick={enrol.closeImport} disabled={busy}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {status.enabled && (
              <div className="flex flex-wrap gap-xs">
                <Button variant="outline" onClick={() => { setPwAction('regenerate'); setError(null); }} disabled={busy}>
                  Regenerate recovery codes
                </Button>
                <Button variant="outline" className="border-destructive/40 text-destructive" onClick={() => { setPwAction('disable'); setError(null); }} disabled={busy}>
                  <ShieldOff className="size-4" aria-hidden /> Disable 2FA
                </Button>
              </div>
            )}

            {/* Password re-auth prompt for disable/regenerate */}
            {pwAction && (
              <div className="space-y-xs rounded-control border border-border p-sm">
                <Label htmlFor="tfa-pw">
                  Confirm your password to {pwAction === 'disable' ? 'disable 2FA' : 'regenerate recovery codes'}
                </Label>
                <PasswordInput id="tfa-pw" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
                <div className="flex gap-xs">
                  <Button onClick={runPwAction} disabled={busy || !password}>
                    {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Confirm
                  </Button>
                  <Button variant="ghost" onClick={() => { setPwAction(null); setPassword(''); }} disabled={busy}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* SETUP VIEW */}
        {step === 'confirm' && setupData && (
          <div className="space-y-sm">
            <p className="text-metadata text-muted-foreground">
              {setupData.imported
                ? 'Confirm your imported secret by entering a current code from your authenticator.'
                : 'Scan this QR code with your authenticator app (or enter the secret manually), then enter the 6-digit code to confirm.'}
            </p>
            {!setupData.imported && (
              <div className="flex flex-col items-center gap-xs">
                <img src={setupData.qr_svg} alt="TOTP enrollment QR code" className="size-44 rounded-control border border-border bg-white p-xs" />
                <code className="select-all break-all rounded bg-muted px-xs py-xxs text-caption">{setupData.secret}</code>
              </div>
            )}
            <div className="flex flex-col gap-xs">
              <Label htmlFor="tfa-code">Authentication code</Label>
              <Input
                id="tfa-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123 456"
                autoFocus
              />
            </div>
            <div className="flex gap-xs">
              <Button onClick={enrol.confirm} disabled={busy || !code.trim()}>
                {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Verify &amp; enable
              </Button>
              <Button variant="ghost" onClick={() => { enrol.startOver(); setError(null); }} disabled={busy}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* RECOVERY CODES VIEW */}
        {step === 'recovery' && (
          <div className="space-y-sm">
            <Alert variant="warning">
              <AlertDescription>
                Save these recovery codes somewhere safe. Each can be used once to sign in if you lose your
                authenticator. They won't be shown again.
              </AlertDescription>
            </Alert>
            <div className="grid grid-cols-2 gap-xxs rounded-control border border-border bg-muted/40 p-sm font-mono text-metadata sm:grid-cols-2">
              {recoveryCodes.map((c) => (
                <span key={c} className="select-all">{c}</span>
              ))}
            </div>
            <div className="flex flex-wrap gap-xs">
              <Button variant="outline" onClick={enrol.copyCodes}><Copy className="size-4" aria-hidden /> Copy</Button>
              <Button variant="outline" onClick={enrol.downloadCodes}><Download className="size-4" aria-hidden /> Download</Button>
              <Button onClick={enrol.dismissRecoveryCodes}>
                Done
              </Button>
            </div>
          </div>
        )}
      </div>
    </PostureSection>
  );
};

export default TwoFactorCard;
