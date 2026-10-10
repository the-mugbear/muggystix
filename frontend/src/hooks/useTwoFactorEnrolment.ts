import { useCallback, useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { enableTwoFactor, startTwoFactorSetup, type TwoFactorSetup } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { SECRET_MUTATION } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { copyToClipboard } from '../utils/clipboard';
import { downloadTextFile } from '../utils/download';

/**
 * Enrolling in two-factor authentication — the ONE implementation behind the
 * Profile page's section (`components/TwoFactorCard`) and the forced page
 * (`pages/ForceTwoFactorSetup`), which wrote the same three steps twice:
 *
 *   start      a new secret, or one the reader's authenticator already holds
 *   confirm    the secret and its QR shown once; a current code proves it
 *   recovery   the recovery codes, shown once
 *
 * Both mutations carry or return a secret — the TOTP secret, a one-time code,
 * the recovery codes — so neither is kept by the library once it has settled
 * (`SECRET_MUTATION`, and the `reset` where each is called).  What the reader
 * is SHOWN once (the secret and QR, the recovery codes) is this hook's own
 * state, copied in `onSuccess` and cleared when they leave that step; why a
 * step failed is `error`.
 *
 * Each caller keeps what is its own: its layout and words, what happens after
 * the codes are saved, and — on Profile — the status read, disabling and new
 * recovery codes (which show their codes through `showRecoveryCodes`).
 */
export type EnrolmentStep = 'start' | 'confirm' | 'recovery';

export interface TwoFactorEnrolment {
  step: EnrolmentStep;
  /** Why the last step failed; cleared when a step is tried again. */
  error: string | null;
  setError: (error: string | null) => void;

  /** The reader chose to bring a secret their authenticator already holds. */
  showImport: boolean;
  importSecret: string;
  setImportSecret: (secret: string) => void;
  openImport: () => void;
  /** Back out of importing: the box closes and what was pasted goes. */
  closeImport: () => void;

  /** The secret being confirmed (`confirm` step only). */
  setup: TwoFactorSetup | null;
  code: string;
  setCode: (code: string) => void;

  /** Shown once (`recovery` step only). */
  recoveryCodes: string[];
  copyCodes: () => void;
  downloadCodes: () => void;

  /** Ask the server for a secret (the pasted one, when importing). */
  start: () => void;
  /** Prove the secret with the typed code. */
  confirm: () => void;
  /** Leave `confirm`: the secret and the code go.  `error` is the caller's. */
  startOver: () => void;
  /** Show codes that came from elsewhere (regenerated ones), once. */
  showRecoveryCodes: (codes: string[]) => void;
  /** The reader has saved the codes: they go, back to `start`. */
  dismissRecoveryCodes: () => void;

  /** A step's request is in flight. */
  busy: boolean;
}

export function useTwoFactorEnrolment(
  /** After the server enabled 2FA — the step stays busy until this settles
   *  (Profile reads the status again). */
  { onEnabled }: { onEnabled?: () => Promise<unknown> | void } = {},
): TwoFactorEnrolment {
  const toast = useToast();
  const [step, setStep] = useState<EnrolmentStep>('start');
  const [error, setError] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [importSecret, setImportSecret] = useState('');
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [code, setCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  const starting = useMutation({
    ...SECRET_MUTATION,
    mutationFn: (body: { existing_secret?: string }) => startTwoFactorSetup(body),
    onMutate: () => setError(null),
    onSuccess: (data) => {
      setSetup(data);
      setCode('');
      setStep('confirm');
    },
    onError: (err) => setError(formatApiError(err, 'Could not start 2FA setup.')),
  });
  const start = () => {
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
      setStep('recovery');
      // Enrollment is over: the secret, its QR and the code that confirmed it go.
      setSetup(null);
      setCode('');
      setImportSecret('');
      setShowImport(false);
      return onEnabled?.();
    },
    onError: (err) => setError(formatApiError(err, 'That code was not accepted.')),
  });
  const confirm = () => enabling.mutate(code.trim(), { onSettled: () => enabling.reset() });

  const openImport = useCallback(() => setShowImport(true), []);
  const closeImport = useCallback(() => { setShowImport(false); setImportSecret(''); }, []);
  const startOver = useCallback(() => { setStep('start'); setSetup(null); setCode(''); }, []);
  const showRecoveryCodes = useCallback((codes: string[]) => { setRecoveryCodes(codes); setStep('recovery'); }, []);
  const dismissRecoveryCodes = useCallback(() => { setStep('start'); setRecoveryCodes([]); }, []);

  const copyCodes = () => {
    void copyToClipboard(recoveryCodes.join('\n')).then((ok) => {
      if (ok) toast.success('Recovery codes copied.');
    });
  };
  const downloadCodes = () => {
    downloadTextFile('bluestick-recovery-codes.txt', `BlueStick recovery codes\n\n${recoveryCodes.join('\n')}\n`);
  };

  return {
    step, error, setError,
    showImport, importSecret, setImportSecret, openImport, closeImport,
    setup, code, setCode,
    recoveryCodes, copyCodes, downloadCodes,
    start, confirm, startOver, showRecoveryCodes, dismissRecoveryCodes,
    busy: starting.isPending || enabling.isPending,
  };
}

export default useTwoFactorEnrolment;
