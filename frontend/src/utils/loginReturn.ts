/**
 * Where to go back to after signing in again.
 *
 * An expired session reloads the app at `/login?from=<path>`; the route guard
 * hands the same thing over as `location.state.from`.  Only a path inside the
 * app is honoured: it begins with exactly one `/` (so never `//host` or a
 * scheme), and is never the login page itself.
 */
export const LOGIN_RETURN_PARAM = 'from';

export function safeReturnPath(raw: string | null | undefined): string | null {
  if (!raw || raw.length > 2048) return null;
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return null;
  // Control characters are dropped by URL parsers, which would turn "/\t/host" into "//host".
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f]/.test(raw)) return null;
  const path = raw.split(/[?#]/)[0];
  if (path === '/login') return null;
  return raw;
}

/** The login URL an expired session is sent to from `pathname` + `search`. */
export function loginUrlFrom(pathname: string, search = ''): string {
  const back = safeReturnPath(`${pathname}${search}`);
  return back && back !== '/' ? `/login?${LOGIN_RETURN_PARAM}=${encodeURIComponent(back)}` : '/login';
}

interface LoginLocation {
  search?: string;
  state?: { from?: { pathname?: string; search?: string } | null } | null;
}

/** After sign-in: the route guard's `state.from`, else `?from=`, else home. */
export function returnPathAfterLogin(location: LoginLocation): string {
  const from = location.state?.from;
  const fromState = from?.pathname ? safeReturnPath(`${from.pathname}${from.search ?? ''}`) : null;
  if (fromState) return fromState;
  const fromQuery = new URLSearchParams(location.search ?? '').get(LOGIN_RETURN_PARAM);
  return safeReturnPath(fromQuery) ?? '/';
}
