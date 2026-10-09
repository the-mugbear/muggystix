/**
 * Keeps the session open while the reader is working.
 *
 * A session ends a fixed time after its last renewal, and a renewal is asked
 * for when the reader presses a key or clicks — never by a request alone:
 * pages poll, and an unattended tab would then stay signed in for ever.
 *
 * Not on every key press: only once the stored token is a few minutes old
 * (`renewalDue`).  Every tab shares that token, so a renewal in one tab is
 * the others' too.  A renewal that fails is tried again at the next activity
 * after `SESSION_RENEW_RETRY_MS`; one refused with 401 ends the session the
 * way any request does.
 */
import { useEffect, useRef } from 'react';

import { SESSION_RENEW_RETRY_MS, renewalDue, storedSessionToken } from '../utils/sessionExpiry';

export interface SessionRenewalDeps {
  /** Ask the server for a later end and store the token that carries it. */
  renew: () => Promise<void>;
  readToken?: () => string | null;
  now?: () => number;
}

/** What counts as the reader doing something. */
const ACTIVITY_EVENTS = ['keydown', 'pointerdown'] as const;

export function useSessionRenewal(token: string | null, deps: SessionRenewalDeps): void {
  // The caller's functions are new every render; the listeners must not be.
  const latest = useRef(deps);
  latest.current = deps;

  useEffect(() => {
    if (!token) return undefined;
    let lastAttempt = Number.NEGATIVE_INFINITY;
    const onActivity = () => {
      const { renew, readToken = storedSessionToken, now = Date.now } = latest.current;
      const at = now();
      if (at - lastAttempt < SESSION_RENEW_RETRY_MS) return;
      if (!renewalDue(readToken(), at)) return;
      lastAttempt = at;
      renew().catch(() => undefined);
    };
    // Capture: a component that stops an event's propagation still counts.
    const options = { capture: true, passive: true };
    ACTIVITY_EVENTS.forEach((name) => window.addEventListener(name, onActivity, options));
    return () => {
      ACTIVITY_EVENTS.forEach((name) => window.removeEventListener(name, onActivity, options));
    };
  }, [token]);
}
