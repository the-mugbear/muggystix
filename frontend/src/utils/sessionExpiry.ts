/**
 * When the signed-in session ends, read from the token itself.
 *
 * A session has a fixed lifetime (the server's `ACCESS_TOKEN_EXPIRE_MINUTES`)
 * and there is no renewal: at the end the next request answers 401 and the
 * reader is sent to sign in, losing anything typed and unsaved.  The token is
 * a JWT, so the client can read its `exp` claim and say so beforehand.
 *
 * The claim is read ONLY to time a notice.  It is never trusted for anything
 * else — the server decides whether a token is valid — and the token is never
 * logged.  A token with no readable expiry gives `null`: no notice, no error.
 */
import { loginUrlFrom } from './loginReturn';

/** The first notice: this long before the end. */
export const SESSION_WARN_MS = 10 * 60_000;
/** The second notice. */
export const SESSION_LAST_WARN_MS = 60_000;

/** The moment the token expires (ms since the epoch), or `null` when it does not say. */
export function tokenExpiresAt(token: string | null | undefined): number | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded)) as { exp?: unknown } | null;
    const exp = payload?.exp;
    return typeof exp === 'number' && Number.isFinite(exp) && exp > 0 ? exp * 1000 : null;
  } catch {
    return null;
  }
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
