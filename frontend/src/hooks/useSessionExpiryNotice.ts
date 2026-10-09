/**
 * Says when the session is about to end, and when it has.
 *
 * One timeout, set for the next moment something changes (ten minutes before
 * the end, one minute before, the end) — never a ticking interval for a
 * session that lasts hours.  A browser slows or pauses timers in a hidden tab
 * and across sleep, so the state is worked out again from the clock whenever
 * the tab becomes visible; and again when another tab replaces the stored
 * token (every tab shares it), which is also how a new sign-in — or a renewal
 * there (`useSessionRenewal`) — clears the notice here.
 *
 * `token` is this tab's own (it changes on sign-in, renewal and sign-out); the expiry
 * is read from the STORED token, the one requests are actually sent with.
 */
import { useEffect, useRef } from 'react';

import { SessionStage, sessionStage, storedSessionToken, tokenExpiresAt } from '../utils/sessionExpiry';

export type NoticeStage = Exclude<SessionStage, 'ok'>;

export interface SessionExpiryDeps {
  /** Show (or replace) the one notice. */
  notify: (stage: NoticeStage, expiresAt: number) => void;
  /** Take the notice away: the session is no longer about to end. */
  dismiss: () => void;
  readToken?: () => string | null;
  now?: () => number;
}

/** `setTimeout` takes a signed 32-bit delay; a longer one fires at once. */
const MAX_TIMEOUT_MS = 2 ** 31 - 1;

export function useSessionExpiryNotice(token: string | null, deps: SessionExpiryDeps): void {
  // The callers' functions are new every render; the schedule must not be.
  const latest = useRef(deps);
  latest.current = deps;
  // What is on screen: "<stage>:<expiry>".  A notice the reader closed is not
  // shown again for the same stage of the same session.
  const shown = useRef<string | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const clear = () => {
      if (shown.current !== null) {
        shown.current = null;
        latest.current.dismiss();
      }
    };
    const check = () => {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      const { readToken = storedSessionToken, now = Date.now } = latest.current;
      const expiresAt = tokenExpiresAt(readToken());
      if (expiresAt === null) {
        clear();
        return;
      }
      const { stage, next } = sessionStage(expiresAt, now());
      if (stage === 'ok') {
        clear();
      } else {
        const key = `${stage}:${expiresAt}`;
        if (shown.current !== key) {
          shown.current = key;
          latest.current.notify(stage, expiresAt);
        }
      }
      if (next !== null) {
        timer = setTimeout(check, Math.min(Math.max(next - now(), 1), MAX_TIMEOUT_MS));
      }
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') check();
    };
    const onStorage = (event: StorageEvent) => {
      // `key === null` is `localStorage.clear()`.
      if (event.key === null || event.key === 'auth_token') check();
    };

    check();
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('storage', onStorage);
    return () => {
      if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('storage', onStorage);
    };
  }, [token]);
}
