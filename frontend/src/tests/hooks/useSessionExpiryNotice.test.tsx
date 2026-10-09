/**
 * An eight-hour session ended without a word, and the next click lost whatever
 * was typed.  The reader is told ten minutes before, again one minute before,
 * and when it has ended — from ONE timeout at a time, never a ticking interval.
 */
import { act, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toast = vi.hoisted(() => ({
  success: vi.fn(), info: vi.fn(), warning: vi.fn(), error: vi.fn(), dismiss: vi.fn(),
}));
const auth = vi.hoisted(() => ({ token: null as string | null }));
const signIn = vi.hoisted(() => vi.fn());
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ token: auth.token }) }));
vi.mock('../../utils/sessionExpiry', async (original) => ({
  ...(await original<typeof import('../../utils/sessionExpiry')>()),
  signInAgain: signIn,
}));

import SessionExpiryNotice, { SESSION_NOTICE_ID, sessionNoticeText } from '../../components/SessionExpiryNotice';
import { useSessionExpiryNotice } from '../../hooks/useSessionExpiryNotice';
import { formatClockTime } from '../../utils/relativeTime';
import { sessionStage, tokenExpiresAt } from '../../utils/sessionExpiry';

const MIN = 60_000;
const START = Date.parse('2026-10-08T09:00:00Z');

const base64url = (value: unknown) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
/** A token shaped like the server's, expiring `minutes` after START. */
const jwt = (minutes: number, extra: Record<string, unknown> = {}) =>
  `${base64url({ alg: 'HS256', typ: 'JWT' })}.${base64url({ sub: '1', exp: (START + minutes * MIN) / 1000, ...extra })}.signature`;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(START);
  vi.clearAllMocks();
  localStorage.clear();
  auth.token = null;
});
afterEach(() => {
  vi.useRealTimers();
});

describe('tokenExpiresAt', () => {
  it('reads the expiry claim, and nothing from a token that does not say', () => {
    expect(tokenExpiresAt(jwt(480))).toBe(START + 480 * MIN);
    // base64url padding and characters.
    expect(tokenExpiresAt(jwt(480, { name: 'ÿþ?>~' }))).toBe(START + 480 * MIN);
    for (const unreadable of [
      null, undefined, '', 'opaque-session-token', 'a.b', 'a.b.c',
      `x.${base64url({ sub: '1' })}.y`, `x.${base64url({ exp: 'soon' })}.y`, `x.${base64url({ exp: -1 })}.y`,
      `x.${base64url(null)}.y`, 'x.!!!.y',
    ]) {
      expect(tokenExpiresAt(unreadable)).toBeNull();
    }
  });
});

describe('sessionStage', () => {
  it('names the stage and the moment it next changes', () => {
    const end = START + 480 * MIN;
    expect(sessionStage(end, START)).toEqual({ stage: 'ok', next: end - 10 * MIN });
    expect(sessionStage(end, end - 10 * MIN)).toEqual({ stage: 'soon', next: end - MIN });
    expect(sessionStage(end, end - MIN)).toEqual({ stage: 'last', next: end });
    expect(sessionStage(end, end)).toEqual({ stage: 'ended', next: null });
  });
});

const setup = (token: string | null) => {
  const notify = vi.fn();
  const dismiss = vi.fn();
  let stored = token;
  const view = renderHook(({ t }) => useSessionExpiryNotice(t, { notify, dismiss, readToken: () => stored }), {
    initialProps: { t: token },
  });
  return {
    notify, dismiss, ...view,
    /** This tab signs in again (or out). */
    signIn: (next: string | null) => { stored = next; view.rerender({ t: next }); },
    /** Another tab replaces the stored token; this tab's own state does not change. */
    replacedElsewhere: (next: string | null) => {
      stored = next;
      act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'auth_token', newValue: next })); });
    },
  };
};
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

describe('useSessionExpiryNotice', () => {
  it('warns ten minutes before the end, again at one minute, and says when it has ended', () => {
    const { notify, dismiss } = setup(jwt(480));
    const end = START + 480 * MIN;
    advance(470 * MIN - 1);
    expect(notify).not.toHaveBeenCalled();
    advance(1);
    expect(notify.mock.calls).toEqual([['soon', end]]);
    advance(9 * MIN - 1);
    expect(notify).toHaveBeenCalledTimes(1);
    advance(1);
    expect(notify.mock.calls[1]).toEqual(['last', end]);
    advance(MIN - 1);
    expect(notify).toHaveBeenCalledTimes(2);
    advance(1);
    expect(notify.mock.calls[2]).toEqual(['ended', end]);
    // Nothing more, however long the tab stays open.
    advance(24 * 60 * MIN);
    expect(notify).toHaveBeenCalledTimes(3);
    expect(dismiss).not.toHaveBeenCalled();
  });

  it('waits on one timeout, not an interval', () => {
    const every = vi.spyOn(globalThis, 'setInterval');
    setup(jwt(480));
    expect(vi.getTimerCount()).toBe(1);
    advance(469 * MIN);
    expect(vi.getTimerCount()).toBe(1);
    advance(480 * MIN);
    // The session has ended: nothing is left running.
    expect(vi.getTimerCount()).toBe(0);
    expect(every).not.toHaveBeenCalled();
  });

  it('a token with no readable expiry gives no notice and no error', () => {
    for (const token of [null, 'opaque-session-token', 'x.!!!.y']) {
      const { notify, dismiss, unmount } = setup(token);
      advance(24 * 60 * MIN);
      expect(notify).not.toHaveBeenCalled();
      expect(dismiss).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
      unmount();
    }
  });

  it('a page opened inside the last ten minutes, or after the end, says so at once', () => {
    const late = setup(jwt(4));
    expect(late.notify.mock.calls).toEqual([['soon', START + 4 * MIN]]);
    late.unmount();
    const over = setup(jwt(-30));
    expect(over.notify.mock.calls).toEqual([['ended', START - 30 * MIN]]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('signing in again takes the notice away and waits for the new session’s end', () => {
    const { notify, dismiss, signIn } = setup(jwt(480));
    advance(475 * MIN);
    expect(notify).toHaveBeenCalledTimes(1);
    // A new session: eight hours from now.
    signIn(jwt(475 + 480));
    expect(dismiss).toHaveBeenCalledTimes(1);
    // The old session's one-minute and end moments pass in silence.
    advance(10 * MIN);
    expect(notify).toHaveBeenCalledTimes(1);
    advance(460 * MIN);
    expect(notify.mock.calls[1]).toEqual(['soon', START + (475 + 480) * MIN]);
  });

  it('another tab signing in again reschedules this one; signing out there clears it', () => {
    const { notify, dismiss, replacedElsewhere } = setup(jwt(480));
    advance(479 * MIN);
    expect(notify.mock.calls.map(([stage]) => stage)).toEqual(['soon', 'last']);
    replacedElsewhere(jwt(479 + 480));
    expect(dismiss).toHaveBeenCalledTimes(1);
    advance(5 * MIN);
    // The first session's end came and went: this tab is on the new one.
    expect(notify).toHaveBeenCalledTimes(2);
    advance(465 * MIN);
    expect(notify.mock.calls[2]).toEqual(['soon', START + (479 + 480) * MIN]);

    replacedElsewhere(null);
    expect(dismiss).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('an unrelated stored key changes nothing', () => {
    const { notify } = setup(jwt(480));
    act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'theme', newValue: 'dark' })); });
    expect(vi.getTimerCount()).toBe(1);
    expect(notify).not.toHaveBeenCalled();
  });

  it('works the state out again when the tab is shown: a sleeping laptop does not run timers', () => {
    const { notify } = setup(jwt(480));
    // The clock moves on; the timer never fired.
    vi.setSystemTime(START + 479.5 * MIN);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(notify.mock.calls).toEqual([['last', START + 480 * MIN]]);
    vi.setSystemTime(START + 600 * MIN);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(notify.mock.calls[1]).toEqual(['ended', START + 480 * MIN]);
  });

  it('a notice the reader closed is not shown again for the same stage', () => {
    const { notify } = setup(jwt(480));
    advance(472 * MIN);
    expect(notify).toHaveBeenCalledTimes(1);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'auth_token', newValue: 'same' })); });
    expect(notify).toHaveBeenCalledTimes(1);
  });

  it('stops waiting when it goes away', () => {
    const { unmount } = setup(jwt(480));
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('SessionExpiryNotice', () => {
  it('is one toast that stays, says the local time, and offers to sign in again', () => {
    const token = jwt(480);
    const end = START + 480 * MIN;
    localStorage.setItem('auth_token', token);
    auth.token = token;
    render(<SessionExpiryNotice />);
    expect(toast.warning).not.toHaveBeenCalled();

    advance(470 * MIN);
    expect(toast.warning).toHaveBeenCalledTimes(1);
    const [message, options] = toast.warning.mock.calls[0];
    expect(message).toBe(`Your session ends at ${formatClockTime(end)}. Save your work: anything unsaved is lost when it ends.`);
    // One id: each stage replaces the last.  It stays until closed.
    expect(options).toMatchObject({ id: SESSION_NOTICE_ID, autoHideMs: null, action: { label: 'Sign in again' } });
    options.action.onClick();
    expect(signIn).toHaveBeenCalledTimes(1);

    advance(9 * MIN);
    expect(toast.warning).toHaveBeenCalledTimes(2);
    expect(toast.warning.mock.calls[1][0]).toBe(sessionNoticeText('last', end));
    expect(toast.warning.mock.calls[1][1]).toMatchObject({ id: SESSION_NOTICE_ID, autoHideMs: null });

    advance(MIN);
    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error.mock.calls[0][0]).toBe(`Your session ended at ${formatClockTime(end)}. Copy anything unsaved before you sign in again.`);
    expect(toast.error.mock.calls[0][1]).toMatchObject({ id: SESSION_NOTICE_ID, autoHideMs: null, action: { label: 'Sign in again' } });
  });

  it('says nothing for a session whose token does not give an end', () => {
    localStorage.setItem('auth_token', 'opaque-session-token');
    auth.token = 'opaque-session-token';
    render(<SessionExpiryNotice />);
    advance(24 * 60 * MIN);
    expect(toast.warning).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.dismiss).not.toHaveBeenCalled();
  });
});

describe('signInAgain', () => {
  it('goes to the sign-in page with the page the reader is on as the way back', async () => {
    const actual = await vi.importActual<typeof import('../../utils/sessionExpiry')>('../../utils/sessionExpiry');
    const assign = vi.fn();
    const original = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { pathname: '/findings/7', search: '?endpoint=41', assign },
    });
    try {
      actual.signInAgain();
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original });
    }
    expect(assign).toHaveBeenCalledWith(`/login?from=${encodeURIComponent('/findings/7?endpoint=41')}`);
  });
});
