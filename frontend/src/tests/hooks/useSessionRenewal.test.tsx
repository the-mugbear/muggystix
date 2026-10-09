/**
 * A session ends a fixed time after the reader last did something.  A key
 * press or a click renews it — a request alone never does, because pages poll
 * and an unattended tab would then stay signed in for ever.
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSessionRenewal } from '../../hooks/useSessionRenewal';
import { SESSION_RENEW_AFTER_MS, SESSION_RENEW_RETRY_MS, renewalDue } from '../../utils/sessionExpiry';

const MIN = 60_000;
const START = Date.parse('2026-10-09T09:00:00Z');

const base64url = (value: unknown) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
/** A token issued `issued` minutes after START that lasts `lifetime` minutes. */
const jwt = (issued: number, lifetime = 480) =>
  `${base64url({ alg: 'HS256', typ: 'JWT' })}.${base64url({
    sub: '1', iat: (START + issued * MIN) / 1000, exp: (START + (issued + lifetime) * MIN) / 1000,
  })}.signature`;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(START);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('renewalDue', () => {
  it('is due once the token is a few minutes old, and not before', () => {
    const token = jwt(0);
    expect(renewalDue(token, START)).toBe(false);
    expect(renewalDue(token, START + SESSION_RENEW_AFTER_MS - 1)).toBe(false);
    expect(renewalDue(token, START + SESSION_RENEW_AFTER_MS)).toBe(true);
    expect(renewalDue(token, START + 479 * MIN)).toBe(true);
  });

  it('is never due for a session that has ended, or a token that does not say', () => {
    expect(renewalDue(jwt(0), START + 480 * MIN)).toBe(false);
    expect(renewalDue(jwt(0), START + 600 * MIN)).toBe(false);
    const noIssue = `x.${base64url({ exp: (START + 480 * MIN) / 1000 })}.y`;
    const noEnd = `x.${base64url({ iat: START / 1000 })}.y`;
    for (const unreadable of [null, undefined, '', 'opaque-session-token', 'x.!!!.y', noIssue, noEnd]) {
      expect(renewalDue(unreadable, START + 60 * MIN)).toBe(false);
    }
  });

  it('a short lifetime is renewed after a quarter of it, so it can be renewed at all', () => {
    // An installation that set four minutes: five would never come.
    const token = jwt(0, 4);
    expect(renewalDue(token, START + MIN - 1)).toBe(false);
    expect(renewalDue(token, START + MIN)).toBe(true);
  });
});

const setup = (token: string | null) => {
  const renew = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
  let stored = token;
  const view = renderHook(({ t }) => useSessionRenewal(t, { renew, readToken: () => stored }), {
    initialProps: { t: token },
  });
  return {
    renew, ...view,
    /** What a renewal (or a sign-in) leaves behind: a new token, stored and in state. */
    replace: (next: string | null) => { stored = next; view.rerender({ t: next }); },
    /** Another tab replaces the stored token; this tab's own state does not change. */
    replacedElsewhere: (next: string | null) => { stored = next; },
  };
};
const press = (name: 'keydown' | 'pointerdown' = 'keydown') =>
  act(() => { window.dispatchEvent(new Event(name)); });
const later = (ms: number) => vi.setSystemTime(Date.now() + ms);

describe('useSessionRenewal', () => {
  it('a key press or a click renews once the token is a few minutes old', () => {
    const { renew } = setup(jwt(0));
    press();
    press('pointerdown');
    expect(renew).not.toHaveBeenCalled();

    later(SESSION_RENEW_AFTER_MS);
    press();
    expect(renew).toHaveBeenCalledTimes(1);
  });

  it('time passing alone renews nothing: an unattended tab is not activity', () => {
    const { renew } = setup(jwt(0));
    act(() => { vi.advanceTimersByTime(479 * MIN); });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'auth_token', newValue: 'x' })); });
    expect(renew).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a busy reader renews every few minutes, not on every key', () => {
    const { renew, replace } = setup(jwt(0));
    later(6 * MIN);
    press();
    expect(renew).toHaveBeenCalledTimes(1);
    // The renewal answered: a token issued now.
    replace(jwt(6));
    for (let i = 0; i < 50; i += 1) press();
    expect(renew).toHaveBeenCalledTimes(1);

    later(SESSION_RENEW_AFTER_MS);
    press('pointerdown');
    expect(renew).toHaveBeenCalledTimes(2);
  });

  it('a renewal that has not answered, or failed, is not asked for again on every key', async () => {
    const { renew } = setup(jwt(0));
    renew.mockRejectedValue(new Error('Network Error'));
    later(10 * MIN);
    press();
    press();
    await act(async () => { await Promise.resolve(); });
    press();
    expect(renew).toHaveBeenCalledTimes(1);

    later(SESSION_RENEW_RETRY_MS);
    press();
    expect(renew).toHaveBeenCalledTimes(2);
  });

  it('another tab’s renewal is this tab’s too: the stored token is the one read', () => {
    const { renew, replacedElsewhere } = setup(jwt(0));
    later(10 * MIN);
    replacedElsewhere(jwt(10));
    press();
    expect(renew).not.toHaveBeenCalled();
  });

  it('a session that has ended is not renewed, and nobody signed in renews nothing', () => {
    const over = setup(jwt(0));
    later(481 * MIN);
    press();
    expect(over.renew).not.toHaveBeenCalled();
    over.unmount();

    const anonymous = setup(null);
    press();
    expect(anonymous.renew).not.toHaveBeenCalled();
  });

  it('counts activity a component stopped from propagating, and stops listening when it goes away', () => {
    const { renew, unmount } = setup(jwt(0));
    later(10 * MIN);
    const button = document.createElement('button');
    button.addEventListener('pointerdown', (event) => event.stopPropagation());
    document.body.appendChild(button);
    act(() => { button.dispatchEvent(new Event('pointerdown', { bubbles: true })); });
    expect(renew).toHaveBeenCalledTimes(1);
    button.remove();

    unmount();
    later(10 * MIN);
    press();
    expect(renew).toHaveBeenCalledTimes(1);
  });
});
