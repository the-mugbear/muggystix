/**
 * The signed-in user's own sessions, password and two-factor enrollment.
 * Server: `backend/app/api/v1/endpoints/auth.py` and `two_factor.py` (both
 * under `/auth`).
 *
 * Signing in, verifying the token, signing out and renewing the session are
 * NOT here: `contexts/AuthContext` owns them, through the default client.
 *
 * Not one project's data: these take no `projectId`, and a query on them has
 * none in its key (`['listOwnSessions']`).
 */
import { api } from './client';

/** A row of `GET /auth/sessions`. */
export interface UserSession {
  id: number;
  ip_address: string;
  user_agent: string;
  created_at: string;
  last_activity: string;
  expires_at: string;
  /** True for the session this browser's token belongs to. */
  current?: boolean;
}

export interface TwoFactorStatus {
  enabled: boolean;
  confirmed_at?: string | null;
  /** A secret has been set up but not yet confirmed. */
  pending: boolean;
  unused_recovery_codes: number;
}

/** What `POST /auth/2fa/setup` answers: the material to enroll with. */
export interface TwoFactorSetup {
  /** base32 — shown for manual entry. */
  secret: string;
  otpauth_uri: string;
  /** A data URI of the QR code. */
  qr_svg: string;
  /** True when an existing secret was supplied. */
  imported: boolean;
}

// --- Sessions and password ---

export const listOwnSessions = async (signal?: AbortSignal): Promise<UserSession[]> => {
  const response = await api.get<UserSession[]>('/auth/sessions', { signal });
  return response.data;
};

export const revokeOwnSession = async (sessionId: number): Promise<void> => {
  await api.delete(`/auth/sessions/${sessionId}`);
};

/** The server revokes EVERY session of the account, this browser's included:
 *  the caller signs out afterwards. */
export const changeOwnPassword = async (
  body: { current_password: string; new_password: string },
): Promise<void> => {
  await api.post('/auth/change-password', body);
};

// --- Two-factor (TOTP) ---

export const getTwoFactorStatus = async (signal?: AbortSignal): Promise<TwoFactorStatus> => {
  const response = await api.get<TwoFactorStatus>('/auth/2fa/status', { signal });
  return response.data;
};

/** Begin enrollment with a new secret, or with an existing one
 *  (`existing_secret`).  Nothing is enabled until `enableTwoFactor`. */
export const startTwoFactorSetup = async (body: { existing_secret?: string }): Promise<TwoFactorSetup> => {
  const response = await api.post<TwoFactorSetup>('/auth/2fa/setup', body);
  return response.data;
};

/** Confirm the pending secret with a current code; answers the one-time
 *  recovery codes (shown once). */
export const enableTwoFactor = async (code: string): Promise<string[]> => {
  const response = await api.post<{ recovery_codes: string[] }>('/auth/2fa/enable', { code });
  return response.data.recovery_codes;
};

/** Password-gated. */
export const disableTwoFactor = async (password: string): Promise<void> => {
  await api.post('/auth/2fa/disable', { password });
};

/** Password-gated; the old set stops working. */
export const regenerateRecoveryCodes = async (password: string): Promise<string[]> => {
  const response = await api.post<{ recovery_codes: string[] }>('/auth/2fa/recovery-codes', { password });
  return response.data.recovery_codes;
};
