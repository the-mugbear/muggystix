/**
 * When the signed-in session ends, read from the token itself.
 *
 * A session ends a fixed time (the server's `ACCESS_TOKEN_EXPIRE_MINUTES`)
 * after the reader last did something: a key press or a click renews it
 * (`hooks/useSessionRenewal`), and each renewal stores a token with a later
 * expiry.  Left alone, at the end the next request answers 401 and the reader
 * is sent to sign in, losing anything typed and unsaved.  The token is a JWT,
 * so the client can read its `exp` claim and say so beforehand.
 *
 * The claims are read ONLY to time a notice and a renewal.  They are never
 * trusted for anything else — the server decides whether a token is valid —
 * and the token is never logged.  A token with no readable expiry gives
 * `null`: no notice, no renewal, no error.
 */
import { loginUrlFrom } from './loginReturn';

/** The first notice: this long before the end. */
export const SESSION_WARN_MS = 10 * 60_000;
/** The second notice. */
export const SESSION_LAST_WARN_MS = 60_000;
/** A token this old is renewed at the reader's next key press or click. */
export const SESSION_RENEW_AFTER_MS = 5 * 60_000;

/** How soon a renewal that failed is tried again. */
export const SESSION_RENEW_RETRY_MS = 30_000;

/** The token requests are sent with: every tab shares it. */
export function storedSessionToken(): string | null {
  try {
    return window.localStorage.getItem('auth_token');
  } catch {
    return null;
  }
}

/** A time claim of the token (ms since the epoch), or `null` when it does not say. */
function tokenTime(token: string | null | undefined, claim: 'exp' | 'iat'): number | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded)) as Record<string, unknown> | null;
    const seconds = payload?.[claim];
    return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : null;
  } catch {
    return null;
  }
}

/** The moment the token expires (ms since the epoch), or `null` when it does not say. */
export function tokenExpiresAt(token: string | null | undefined): number | null {
  return tokenTime(token, 'exp');
}

/**
 * Whether activity at `now` should renew the session this token belongs to.
 *
 * Not on every key press: only once the token is `SESSION_RENEW_AFTER_MS` old
 * — or a quarter of its lifetime, when an installation set a short one — so a
 * busy reader renews every few minutes.  Every tab reads the same stored
 * token, so one tab's renewal is the others' too.  A session that has ended
 * is not renewed (the server would refuse), and neither is a token that does
 * not say when it was issued or when it ends.
 */
export function renewalDue(token: string | null | undefined, now: number): boolean {
  const issuedAt = tokenTime(token, 'iat');
  const expiresAt = tokenTime(token, 'exp');
  if (issuedAt === null || expiresAt === null || expiresAt <= now) return false;
  return now - issuedAt >= Math.min(SESSION_RENEW_AFTER_MS, (expiresAt - issuedAt) / 4);
}

export type SessionStage = 'ok' | 'soon' | 'last' | 'ended';

/** Where a session stands at `now`, and the moment that next changes (`null`: never). */
export function sessionStage(expiresAt: number, now: number): { stage: SessionStage; next: number | null } {
  const left = expiresAt - now;
  if (left <= 0) return { stage: 'ended', next: null };
  if (left <= SESSION_LAST_WARN_MS) return { stage: 'last', next: expiresAt };
  if (left <= SESSION_WARN_MS) return { stage: 'soon', next: expiresAt - SESSION_LAST_WARN_MS };
  return { stage: 'ok', next: expiresAt - SESSION_WARN_MS };
}

/** To the sign-in page, returning to the page the reader is on.  A function of
 *  its own so a test can replace it (jsdom cannot navigate). */
export function signInAgain(): void {
  window.location.assign(loginUrlFrom(window.location.pathname, window.location.search));
}
