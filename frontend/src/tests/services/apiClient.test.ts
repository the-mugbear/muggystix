/**
 * The API client's ONE auth-failure rule and its error bodies (review
 * 2026-10-23b, FEP-1 / FEP-3 / FEP-13).
 *
 * - A download's error body arrives as a Blob: the interceptor decodes a JSON
 *   one so the server's curated `detail` reaches `formatApiError` (and the 403
 *   redirects fire on a download too).
 * - A 401 ends the session only when the refused request carried the token
 *   stored NOW: an older request answering after a new sign-in keeps it.
 * - The upload's raw XHR goes through the same rule: `?from=` is kept, and a
 *   network failure reads as "could not reach the server".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AxiosAdapter } from 'axios';

// setupTests mocks axios for every test; this one is OF the real client.
vi.unmock('axios');

import { api, handleAuthFailure } from '../../services/api/client';
import { uploadFile } from '../../services/api/uploads';
import { formatApiError } from '../../utils/apiErrors';

const failWith = (status: number, data: unknown): AxiosAdapter => (config) =>
  Promise.reject(Object.assign(new Error(`HTTP ${status}`), {
    config,
    isAxiosError: true,
    response: { status, data, headers: {}, config, statusText: '' },
  }));

let hrefSet: string | null;
const realLocation = window.location;

beforeEach(() => {
  hrefSet = null;
  localStorage.clear();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      pathname: '/scans',
      search: '?tab=history',
      get href() { return 'https://app/scans?tab=history'; },
      set href(v: string) { hrefSet = v; },
    },
  });
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
  vi.unstubAllGlobals();
});

describe('a download’s error body', () => {
  it('is decoded so the server’s detail is what the reader is told', async () => {
    const body = new Blob([JSON.stringify({ detail: 'Restore this report’s storage from a backup.' })], {
      type: 'application/json',
    });
    const err = await api
      .get('/x', { responseType: 'blob', adapter: failWith(500, body) })
      .catch((e: unknown) => e);
    expect(formatApiError(err, 'Download failed')).toBe('Restore this report’s storage from a backup.');
  });

  it('a 403 asking for a password change redirects on a download too', async () => {
    const body = new Blob([JSON.stringify({ detail: 'password_change_required' })], { type: 'application/json' });
    await api.get('/x', { responseType: 'blob', adapter: failWith(403, body) }).catch(() => undefined);
    expect(hrefSet).toBe('/force-change-password');
  });

  it('a body that is not JSON is left alone', async () => {
    const body = new Blob(['<html>bad gateway</html>'], { type: 'text/html' });
    const err = (await api
      .get('/x', { responseType: 'blob', adapter: failWith(502, body) })
      .catch((e: unknown) => e)) as { response: { data: unknown } };
    expect(err.response.data).toBeInstanceOf(Blob);
  });
});

describe('a 401', () => {
  it('ends the session it was sent under and keeps the return path', async () => {
    localStorage.setItem('auth_token', 'old');
    localStorage.setItem('auth_user', '{}');
    await api.get('/x', { adapter: failWith(401, { detail: 'expired' }) }).catch(() => undefined);
    expect(localStorage.getItem('auth_token')).toBeNull();
    expect(hrefSet).toBe(`/login?from=${encodeURIComponent('/scans?tab=history')}`);
  });

  it('keeps a session stored after the refused request was sent', () => {
    localStorage.setItem('auth_token', 'new');
    handleAuthFailure(401, 'expired', 'old');
    expect(localStorage.getItem('auth_token')).toBe('new');
    expect(hrefSet).toBeNull();
  });
});

class FakeXhr {
  static last: FakeXhr;
  status = 0;
  responseText = '';
  upload = {};
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() { FakeXhr.last = this; }
  open() {}
  setRequestHeader() {}
  send() {}
  abort() {}
}

describe('the upload’s XHR', () => {
  beforeEach(() => vi.stubGlobal('XMLHttpRequest', FakeXhr));

  it('a 401 goes through the same rule: the reader returns to the page after sign-in', async () => {
    localStorage.setItem('auth_token', 'tok');
    const done = uploadFile(3, new File(['x'], 'a.xml')).catch((e: unknown) => e);
    FakeXhr.last.status = 401;
    FakeXhr.last.responseText = JSON.stringify({ detail: 'expired' });
    FakeXhr.last.onload!();
    const err = await done;
    expect(localStorage.getItem('auth_token')).toBeNull();
    expect(hrefSet).toBe(`/login?from=${encodeURIComponent('/scans?tab=history')}`);
    expect(formatApiError(err, 'Upload failed')).toBe('expired');
  });

  it('a 403 asking for a password change redirects', async () => {
    const done = uploadFile(3, new File(['x'], 'a.xml')).catch(() => undefined);
    FakeXhr.last.status = 403;
    FakeXhr.last.responseText = JSON.stringify({ detail: 'password_change_required' });
    FakeXhr.last.onload!();
    await done;
    expect(hrefSet).toBe('/force-change-password');
  });

  it('a network failure reads as "could not reach the server"', async () => {
    const done = uploadFile(3, new File(['x'], 'a.xml')).catch((e: unknown) => e);
    FakeXhr.last.onerror!();
    expect(formatApiError(await done, 'Upload failed')).toMatch(/^Could not reach the server/);
  });
});
