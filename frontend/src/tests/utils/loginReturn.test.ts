/**
 * An expired session comes back to the page it left: the 401 handler sends
 * `/login?from=<path>`, and sign-in honours it — but only a path inside the
 * app.
 */
import { describe, expect, it } from 'vitest';

import { loginUrlFrom, returnPathAfterLogin, safeReturnPath } from '../../utils/loginReturn';

describe('loginUrlFrom', () => {
  it('carries the path and its query', () => {
    expect(loginUrlFrom('/findings/7', '?edit=report-text'))
      .toBe(`/login?from=${encodeURIComponent('/findings/7?edit=report-text')}`);
    expect(loginUrlFrom('/hosts', '?q=has%3Acritical&page=3'))
      .toBe(`/login?from=${encodeURIComponent('/hosts?q=has%3Acritical&page=3')}`);
  });

  it('is the bare login page from home or from the login page', () => {
    expect(loginUrlFrom('/', '')).toBe('/login');
    expect(loginUrlFrom('/login', '?from=%2Fhosts')).toBe('/login');
  });
});

describe('safeReturnPath', () => {
  it.each([
    '/hosts', '/findings/7?edit=report-text', '/remediation?status=open&host=4#x',
  ])('accepts a path inside the app: %s', (path) => {
    expect(safeReturnPath(path)).toBe(path);
  });

  it.each([
    null, undefined, '', 'hosts', '//evil.example/hosts', '/\\evil.example', 'https://evil.example/',
    'javascript:alert(1)', '/\t/evil.example', '/login', '/login?from=%2Fhosts',
  ])('refuses %j', (raw) => {
    expect(safeReturnPath(raw)).toBeNull();
  });
});

describe('returnPathAfterLogin', () => {
  it('prefers the route guard’s state, with its query', () => {
    expect(returnPathAfterLogin({
      search: '?from=%2Fscans', state: { from: { pathname: '/hosts/5', search: '?tab=notes' } },
    })).toBe('/hosts/5?tab=notes');
  });

  it('reads ?from= after a reload to the login page', () => {
    expect(returnPathAfterLogin({ search: `?from=${encodeURIComponent('/findings/7?edit=report-text')}`, state: null }))
      .toBe('/findings/7?edit=report-text');
  });

  it('goes home for nothing, or for a target outside the app', () => {
    expect(returnPathAfterLogin({ search: '', state: null })).toBe('/');
    expect(returnPathAfterLogin({ search: `?from=${encodeURIComponent('//evil.example/x')}` })).toBe('/');
    expect(returnPathAfterLogin({ search: `?from=${encodeURIComponent('https://evil.example/x')}` })).toBe('/');
  });
});
