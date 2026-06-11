import React, { useEffect, useState } from 'react';
import { ShieldCheck, ShieldOff, Loader2, KeyRound, Copy, Download } from 'lucide-react';
import apiClient from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useToast } from '../contexts/ToastContext';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { PasswordInput } from './ui/password-input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Alert, AlertDescription } from './ui/alert';

interface Status {
  enabled: boolean;
  pending: boolean;
  unused_recovery_codes: number;
}

interface SetupData {
  secret: string;
  otpauth_uri: string;
  qr_svg: string;
  imported: boolean;
}

type View = 'status' | 'setup' | 'recovery';

const TwoFactorCard: React.FC = () => {
  const toast = useToast();
  const [status, setStatus] = useState<Status | null>(null);
  const [view, setView] = useState<View>('status');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Enrollment state.
  const [importSecret, setImportSecret] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [setupData, setSetupData] = useState<SetupData | null>(null);
  const [code, setCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  // Re-auth state (disable / regenerate).
  const [password, setPassword] = useState('');
  const [pwAction, setPwAction] = useState<null | 'disable' | 'regenerate'>(null);

  const loadStatus = async () => {
    try {
      const { data } = await apiClient.get('/auth/2fa/status');
      setStatus(data);
    } catch (err) {
      setError(formatApiError(err, 'Could not load 2FA status.'));
    }
  };

  useEffect(() => {
    loadStatus();
  }, []);

  const startSetup = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = showImport && importSecret.trim() ? { existing_secret: importSecret.trim() } : {};
      const { data } = await apiClient.post('/auth/2fa/setup', body);
      setSetupData(data);
      setCode('');
      setView('setup');
    } catch (err) {
      setError(formatApiError(err, 'Could not start 2FA setup.'));
    } finally {
      setBusy(false);
    }
  };

  const confirmEnable = async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await apiClient.post('/auth/2fa/enable', { code: code.trim() });
      setRecoveryCodes(data.recovery_codes);
      setView('recovery');
      setImportSecret('');
      setShowImport(false);
      await loadStatus();
    } catch (err) {
      setError(formatApiError(err, 'That code was not accepted.'));
    } finally {
      setBusy(false);
    }
  };

  const runPwAction = async () => {
    if (!pwAction) return;
    setBusy(true);
    setError(null);
    try {
      if (pwAction === 'disable') {
        await apiClient.post('/auth/2fa/disable', { password });
        toast.success('Two-factor authentication disabled.');
        setView('status');
      } else {
        const { data } = await apiClient.post('/auth/2fa/recovery-codes', { password });
        setRecoveryCodes(data.recovery_codes);
        setView('recovery');
      }
      setPassword('');
      setPwAction(null);
      await loadStatus();
    } catch (err) {
      setError(formatApiError(err, 'Action failed — check your password.'));
    } finally {
      setBusy(false);
    }
  };

  const copyCodes = () => {
    navigator.clipboard?.writeText(recoveryCodes.join('\n')).then(
      () => toast.success('Recovery codes copied.'),
      () => undefined,
    );
  };

  const downloadCodes = () => {
    const blob = new Blob([`BlueStick recovery codes\n\n${recoveryCodes.join('\n')}\n`], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bluestick-recovery-codes.txt';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card className="md:col-span-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-xs">
          <ShieldCheck className="size-5" aria-hidden />
          Two-Factor Authentication
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-md">
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
              <Button variant="ghost" onClick={() => { setView('status'); setSetupData(null); setError(null); }} disabled={busy}>
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
      </CardContent>
    </Card>
  );
};

export default TwoFactorCard;
