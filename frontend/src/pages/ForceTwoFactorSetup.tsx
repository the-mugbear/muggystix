import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, Loader2, Copy, Download } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import apiClient from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useToast } from '../contexts/ToastContext';
import { Card, CardContent } from '../components/ui/card';
import { Label } from '../components/ui/label';
import { Input } from '../components/ui/input';
import { Button } from '../components/ui/button';
import { Alert, AlertDescription } from '../components/ui/alert';

interface SetupData {
  secret: string;
  otpauth_uri: string;
  qr_svg: string;
  imported: boolean;
}

type Step = 'choose' | 'confirm' | 'recovery';

/**
 * Forced TOTP enrollment — shown (no sidebar) when REQUIRE_2FA is on and the
 * user hasn't enrolled.  Mandatory: the only ways out are completing setup or
 * signing out.  Mirrors ForceChangePassword.
 */
const ForceTwoFactorSetup: React.FC = () => {
  const navigate = useNavigate();
  const { logout } = useAuth();
  const toast = useToast();

  const [step, setStep] = useState<Step>('choose');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [importSecret, setImportSecret] = useState('');
  const [setupData, setSetupData] = useState<SetupData | null>(null);
  const [code, setCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  const startSetup = async () => {
    setBusy(true);
    setError('');
    try {
      const body = showImport && importSecret.trim() ? { existing_secret: importSecret.trim() } : {};
      const { data } = await apiClient.post('/auth/2fa/setup', body);
      setSetupData(data);
      setCode('');
      setStep('confirm');
    } catch (err) {
      setError(formatApiError(err, 'Could not start 2FA setup.'));
    } finally {
      setBusy(false);
    }
  };

  const confirmEnable = async () => {
    setBusy(true);
    setError('');
    try {
      const { data } = await apiClient.post('/auth/2fa/enable', { code: code.trim() });
      setRecoveryCodes(data.recovery_codes);
      setStep('recovery');
    } catch (err) {
      setError(formatApiError(err, 'That code was not accepted.'));
    } finally {
      setBusy(false);
    }
  };

  const finish = () => navigate('/', { replace: true });

  const copyCodes = () =>
    navigator.clipboard?.writeText(recoveryCodes.join('\n')).then(
      () => toast.success('Recovery codes copied.'),
      () => undefined,
    );
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
    <div className="flex min-h-screen items-center justify-center bg-background p-md">
      <div className="w-full max-w-md">
        <Card>
          <CardContent className="p-lg">
            <div className="mb-lg flex flex-col items-center text-center">
              <div className="mb-md flex size-16 items-center justify-center rounded-full bg-warning/10">
                <ShieldAlert className="size-8 text-warning" aria-hidden />
              </div>
              <h1 className="text-section-title font-semibold text-foreground">
                Two-Factor Authentication Required
              </h1>
              <p className="mt-xxs text-metadata text-muted-foreground">
                Your organization requires 2FA. Set it up to continue.
              </p>
            </div>

            {error && (
              <Alert variant="destructive" className="mb-md">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {/* STEP 1 — choose generate vs import */}
            {step === 'choose' && (
              <div className="flex flex-col gap-sm">
                {!showImport ? (
                  <>
                    <Button size="lg" onClick={startSetup} disabled={busy}>
                      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Set up with a new secret
                    </Button>
                    <Button variant="outline" onClick={() => setShowImport(true)} disabled={busy}>
                      Import an existing authenticator secret
                    </Button>
                  </>
                ) : (
                  <div className="flex flex-col gap-xs">
                    <Label htmlFor="f2fa-import">Existing base32 secret</Label>
                    <Input
                      id="f2fa-import"
                      value={importSecret}
                      onChange={(e) => setImportSecret(e.target.value)}
                      placeholder="JBSWY3DPEHPK3PXP…"
                      autoComplete="off"
                    />
                    <p className="text-caption text-muted-foreground">
                      Paste the seed your authenticator already holds (e.g. your machine-login TOTP secret) so the
                      same entry works here.
                    </p>
                    <div className="flex gap-xs">
                      <Button onClick={startSetup} disabled={busy || !importSecret.trim()}>
                        {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Continue
                      </Button>
                      <Button variant="ghost" onClick={() => { setShowImport(false); setImportSecret(''); }} disabled={busy}>
                        Back
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* STEP 2 — confirm a code */}
            {step === 'confirm' && setupData && (
              <div className="flex flex-col gap-sm">
                <p className="text-metadata text-muted-foreground">
                  {setupData.imported
                    ? 'Enter a current code from your authenticator to confirm the imported secret.'
                    : 'Scan this QR with your authenticator app (or enter the secret manually), then enter the 6-digit code.'}
                </p>
                {!setupData.imported && (
                  <div className="flex flex-col items-center gap-xs">
                    <img src={setupData.qr_svg} alt="TOTP enrollment QR code" className="size-44 rounded-control border border-border bg-white p-xs" />
                    <code className="select-all break-all rounded bg-muted px-xs py-xxs text-caption">{setupData.secret}</code>
                  </div>
                )}
                <div className="flex flex-col gap-xs">
                  <Label htmlFor="f2fa-code">Authentication code</Label>
                  <Input
                    id="f2fa-code"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="123 456"
                    autoFocus
                  />
                </div>
                <Button size="lg" onClick={confirmEnable} disabled={busy || !code.trim()}>
                  {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Verify &amp; enable
                </Button>
                <Button variant="ghost" onClick={() => { setStep('choose'); setSetupData(null); }} disabled={busy}>
                  Start over
                </Button>
              </div>
            )}

            {/* STEP 3 — recovery codes */}
            {step === 'recovery' && (
              <div className="flex flex-col gap-sm">
                <Alert variant="warning">
                  <AlertDescription>
                    Save these recovery codes somewhere safe. Each can be used once to sign in if you lose your
                    authenticator. They won't be shown again.
                  </AlertDescription>
                </Alert>
                <div className="grid grid-cols-2 gap-xxs rounded-control border border-border bg-muted/40 p-sm font-mono text-metadata">
                  {recoveryCodes.map((c) => (
                    <span key={c} className="select-all">{c}</span>
                  ))}
                </div>
                <div className="flex flex-wrap gap-xs">
                  <Button variant="outline" onClick={copyCodes}><Copy className="size-4" aria-hidden /> Copy</Button>
                  <Button variant="outline" onClick={downloadCodes}><Download className="size-4" aria-hidden /> Download</Button>
                </div>
                <Button size="lg" onClick={finish}>I've saved my recovery codes — continue</Button>
              </div>
            )}

            <div className="mt-md flex justify-center">
              <Button type="button" variant="link" onClick={() => logout()} disabled={busy}>
                Sign out
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default ForceTwoFactorSetup;
