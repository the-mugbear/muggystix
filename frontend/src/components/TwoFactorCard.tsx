import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { copyToClipboard } from '../utils/clipboard';
import { downloadTextFile } from '../utils/download';
import { ShieldOff, Loader2, KeyRound, Copy, Download } from 'lucide-react';
import {
  disableTwoFactor, enableTwoFactor, getTwoFactorStatus, regenerateRecoveryCodes, startTwoFactorSetup,
  type TwoFactorSetup,
} from '../services/api';
import { SECRET_MUTATION, invalidateReads, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { useToast } from '../contexts/ToastContext';
import PostureSection from './posture/PostureSection';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { PasswordInput } from './ui/password-input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Alert, AlertDescription } from './ui/alert';

type View = 'status' | 'setup' | 'recovery';

const TwoFactorCard: React.FC = () => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [view, setView] = useState<View>('status');
  // Why the last action failed; a status that could not be read is the query's.
  const [actionError, setError] = useState<string | null>(null);

  // Enrollment state.
  const [importSecret, setImportSecret] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [setupData, setSetupData] = useState<TwoFactorSetup | null>(null);
  const [code, setCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  // Re-auth state (disable / regenerate).
  const [password, setPassword] = useState('');
  const [pwAction, setPwAction] = useState<null | 'disable' | 'regenerate'>(null);

  // The signed-in user's own 2FA state — not a project's.
  const statusQuery = useQuery({
    queryKey: ['getTwoFactorStatus'],
    queryFn: ({ signal }) => getTwoFactorStatus(signal),
  });
  const status = statusQuery.data ?? null;
  const error = actionError ?? queryErrorText(statusQuery.error, 'Could not load 2FA status.');
  // Each step ends by reading the status again, and is busy until it has.
  const rereadStatus = () => invalidateReads(queryClient, 'getTwoFactorStatus');

  // The three mutations below carry or return a secret — the TOTP secret, a
  // one-time code, the password, the recovery codes — so none is kept by the
  // library once it has settled (`SECRET_MUTATION`, and the `reset` where each
  // is called).  What the reader is SHOWN once (the secret and QR, the
  // recovery codes) is this component's own state, copied in `onSuccess` and
  // cleared when they leave that step; why a step failed is `actionError`.
  const starting = useMutation({
    ...SECRET_MUTATION,
    mutationFn: (body: { existing_secret?: string }) => startTwoFactorSetup(body),
    onMutate: () => setError(null),
    onSuccess: (data) => {
      setSetupData(data);
      setCode('');
      setView('setup');
    },
    onError: (err) => setError(formatApiError(err, 'Could not start 2FA setup.')),
  });
  const startSetup = () => {
    starting.mutate(
      showImport && importSecret.trim() ? { existing_secret: importSecret.trim() } : {},
      { onSettled: () => starting.reset() },
    );
  };

  const enabling = useMutation({
    ...SECRET_MUTATION,
    mutationFn: (enteredCode: string) => enableTwoFactor(enteredCode),
    onMutate: () => setError(null),
    onSuccess: (codes) => {
      setRecoveryCodes(codes);
      setView('recovery');
      // Enrollment is over: the secret, its QR and the code that confirmed it go.
      setSetupData(null);
      setCode('');
      setImportSecret('');
      setShowImport(false);
      return rereadStatus();
    },
    onError: (err) => setError(formatApiError(err, 'That code was not accepted.')),
  });
  const confirmEnable = () => enabling.mutate(code.trim(), { onSettled: () => enabling.reset() });

  // Disable, or new recovery codes: both ask for the password again.
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
        setView('status');
      } else {
        setRecoveryCodes(codes);
        setView('recovery');
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
  const busy = starting.isPending || enabling.isPending || reauthenticated.isPending;

  const copyCodes = () => {
    copyToClipboard(recoveryCodes.join('\n')).then((ok) => {
      if (ok) toast.success('Recovery codes copied.');
    });
  };

  const downloadCodes = () => {
    downloadTextFile('bluestick-recovery-codes.txt', `BlueStick recovery codes\n\n${recoveryCodes.join('\n')}\n`);
  };

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
        {view === 'status' && status && (
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
                    <Button onClick={startSetup} disabled={busy}>
                      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <KeyRound className="size-4" aria-hidden />}
                      Set up with a new secret
                    </Button>
                    <Button variant="outline" onClick={() => setShowImport(true)} disabled={busy}>
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
                      <Button onClick={startSetup} disabled={busy || !importSecret.trim()}>
                        {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Continue
                      </Button>
                      <Button variant="ghost" onClick={() => { setShowImport(false); setImportSecret(''); }} disabled={busy}>
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
        {view === 'setup' && setupData && (
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
              <Button onClick={confirmEnable} disabled={busy || !code.trim()}>
                {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Verify &amp; enable
              </Button>
              <Button variant="ghost" onClick={() => { setView('status'); setSetupData(null); setCode(''); setError(null); }} disabled={busy}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* RECOVERY CODES VIEW */}
        {view === 'recovery' && (
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
              <Button variant="outline" onClick={copyCodes}><Copy className="size-4" aria-hidden /> Copy</Button>
              <Button variant="outline" onClick={downloadCodes}><Download className="size-4" aria-hidden /> Download</Button>
              <Button onClick={() => { setView('status'); setRecoveryCodes([]); }}>
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
