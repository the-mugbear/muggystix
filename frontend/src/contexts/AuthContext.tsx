import React, { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { flushSync } from 'react-dom';
import { logger, createAuthLogger } from '../utils/logger';
import api, { setCurrentProjectId } from '../services/api';

interface User {
  id: number;
  username: string;
  full_name?: string;
  role: string;
  created_at?: string;
  last_login?: string | null;
  must_change_password?: boolean;
}

type AuthStatus = 'checking' | 'authenticated' | 'anonymous';

export type LoginOutcome =
  | { twoFactorRequired: false }
  | { twoFactorRequired: true; challengeToken: string };

interface AuthContextType {
  user: User | null;
  token: string | null;
  // Resolves to a 2FA challenge when the account has TOTP enabled (the caller
  // then collects a code and calls verify2fa); otherwise the session is live.
  login: (username: string, password: string) => Promise<LoginOutcome>;
  // Complete a 2FA login with the challenge token + a TOTP or recovery code.
  verify2fa: (challengeToken: string, code: string) => Promise<void>;
  logout: () => void;
  updateUser: (updates: Partial<User>) => void;
  isAuthenticated: boolean;
  isLoading: boolean;
  /**
   * Three-state auth status replaces the old "derive from user+token
   * while still loading" pattern that the UX audit flagged (#1).  Use
   * this instead of ``isLoading``/``isAuthenticated`` pairs in new
   * code — ``'checking'`` is the only correct state for the initial
   * render when a stored token is being verified.
   */
  authStatus: AuthStatus;
  hasRole: (role: string) => boolean;
  hasPermission: (requiredRole: string) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
}

// v4.8.0 — the global account role is binary: `admin` or `member`
// (the analyst/auditor/viewer vocabulary moved to per-project
// membership roles, which the backend enforces via
// `require_project_role`).  `hasPermission` here gates UI affordances
// only — the backend is the real authority — so a `member` is treated
// as analyst-equivalent for affordance visibility (level 3): they SEE
// project-action controls, and the backend's project-role check is
// what actually permits or 403s the action.  `auditor`/`viewer` are
// retained in the table so a stale token carrying an old global role
// still resolves sanely.
const ROLE_HIERARCHY = {
  admin: 100,
  member: 3,
  analyst: 3,
  auditor: 2,
  viewer: 1,
};

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();

  // Memoise the logger so we don't recreate it on every render
  // (audit PRF·H9). The per-render `AuthProvider initialized` debug
  // log was hot-path noise — fires once now, only in dev.
  const authLogger = React.useMemo(() => createAuthLogger(), []);

  React.useEffect(() => {
    if (process.env.NODE_ENV === 'production') return;
    authLogger.debug('AuthProvider initialized', {
      pathname: location.pathname,
      search: location.search,
      state: location.state,
    });
    // Intentionally only on mount; location changes are noise here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Fix for UX audit #1 (stale auth flash): the old implementation
    // set isLoading(false) synchronously right after kicking off the
    // async verifyToken, so if the stored token was invalid the user
    // briefly saw protected UI before getting bounced.  The fix is to
    // keep isLoading=true until verifyToken resolves or rejects, and
    // only drop it in the async path's finally block.  If there's no
    // stored token at all, drop isLoading immediately since there's
    // nothing to verify.
    let cancelled = false;
    const run = async () => {
      const timer = authLogger.timer('Initial auth check');
      authLogger.debug('Starting initial authentication check');

      const storedToken = localStorage.getItem('auth_token');
      const storedUser = localStorage.getItem('auth_user');

      authLogger.debug('Retrieved stored auth data', {
        hasToken: !!storedToken,
        hasUser: !!storedUser,
      });

      if (!storedToken || !storedUser) {
        authLogger.debug('No stored authentication data found');
        if (!cancelled) setIsLoading(false);
        timer();
        return;
      }

      try {
        const userData = JSON.parse(storedUser);
        authLogger.debug('Parsed stored user data', {
          userId: userData.id,
          username: userData.username,
          role: userData.role,
        });

        // Prime state optimistically so ProtectedRoute doesn't flash
        // the login redirect while verification is in flight.  The
        // isLoading=true gate (unchanged below) keeps protected child
        // routes from rendering until we confirm the token is valid.
        setToken(storedToken);
        setUser(userData);

        authLogger.info('Authentication restored from storage; verifying…', {
          userId: userData.id,
          username: userData.username,
        });

        await verifyToken(storedToken);
      } catch (error) {
        authLogger.error('Error parsing stored user data', {
          error: error instanceof Error ? error.message : String(error),
        });
        clearAuthData();
      } finally {
        // isLoading only drops after the async verification path
        // resolves — this is the core of the #1 fix.
        if (!cancelled) setIsLoading(false);
        timer();
      }
    };
    run();
    return () => { cancelled = true; };
  }, []);

  const verifyToken = async (authToken: string) => {
    const timer = authLogger.timer('Token verification');
    authLogger.debug('Starting token verification', {
      tokenLength: authToken?.length,
      hasToken: !!authToken
    });

    try {
      // Auth header is injected by the request interceptor in
      // services/api from the same localStorage token; no need to
      // write it twice (audit PRF·L3).
      const response = await api.get('/auth/profile');

      authLogger.info('Token verification successful', {
        userId: response.data.id,
        username: response.data.username
      });
      setUser(response.data);
      timer();
    } catch (error: unknown) {
      // 401 is handled by the axios interceptor (clears auth + redirects)
      authLogger.error('Token verification failed', {
        error: error instanceof Error ? error.message : String(error),
        action: 'clearing_auth_data'
      });
      clearAuthData();
      timer();
    }
  };

  // Shared tail for both the password-only and 2FA-completed paths: persist
  // the session and navigate.  ``data`` is the LoginResponse body.
  const completeLogin = useCallback((data: {
    access_token: string;
    user: {
      id: number; username: string; role: string;
      must_change_password?: boolean; must_setup_2fa?: boolean;
    } & Record<string, unknown>;
  }) => {
    localStorage.setItem('auth_token', data.access_token);
    localStorage.setItem('auth_user', JSON.stringify(data.user));
    flushSync(() => {
      setToken(data.access_token);
      setUser(data.user as unknown as User);
    });
    authLogger.audit('LOGIN_SUCCESS', {
      userId: data.user.id, username: data.user.username, role: data.user.role,
    });
    // The backend records the authoritative login_success audit row; the
    // frontend no longer double-posts one (code-review R7).
    if (data.user.must_change_password) {
      navigate('/force-change-password', { replace: true });
    } else if (data.user.must_setup_2fa) {
      // Mandatory 2FA not yet enrolled — go straight to the forced-setup page
      // (deterministic, instead of waiting for a gated call to 403).
      navigate('/force-2fa-setup', { replace: true });
    } else {
      const from = location.state?.from?.pathname || '/';
      navigate(from, { replace: true });
    }
  }, [authLogger, location.state, navigate]);

  const verify2fa = useCallback(async (challengeToken: string, code: string) => {
    const timer = authLogger.timer('2FA verify');
    try {
      const { data } = await api.post('/auth/login/2fa', { challenge_token: challengeToken, code });
      completeLogin(data);
      timer();
    } catch (error: unknown) {
      const axiosErr = error as { response?: { data?: { detail?: string } } };
      const detail = axiosErr?.response?.data?.detail || (error instanceof Error ? error.message : 'Verification failed');
      authLogger.audit('LOGIN_2FA_FAILED', { reason: detail });
      timer();
      throw new Error(detail);
    }
  }, [authLogger, completeLogin]);

  const login = useCallback(async (username: string, password: string): Promise<LoginOutcome> => {
    const timer = authLogger.timer('Login process');
    authLogger.info('Login attempt started', {
      username,
      currentPath: location.pathname,
      fromPath: location.state?.from?.pathname
    });
    authLogger.audit('LOGIN_ATTEMPT', { username });

    try {
      authLogger.debug('Sending login request to API');
      const { data } = await api.post('/auth/login', { username, password });

      // 2FA gate: password OK but the account needs a second factor. Don't
      // store anything yet — hand the challenge back so the Login page can
      // collect a code and call verify2fa.
      if (data.two_factor_required) {
        authLogger.info('Login requires second factor', { username });
        timer();
        return { twoFactorRequired: true, challengeToken: data.challenge_token };
      }

      completeLogin(data);
      timer();
      return { twoFactorRequired: false };

    } catch (error: unknown) {
      const axiosErr = error as { response?: { data?: { detail?: string } } };
      const detail = axiosErr?.response?.data?.detail || (error instanceof Error ? error.message : 'Login failed');
      authLogger.error('Login process failed', { error: detail, username });
      authLogger.audit('LOGIN_FAILED', { username, reason: detail });
      timer();
      throw new Error(detail);
    }
  }, [authLogger, completeLogin, location.pathname, location.state]);

  const logout = useCallback(async () => {
    const timer = authLogger.timer('Logout process');
    authLogger.info('Logout initiated', {
      hasToken: !!token,
      userId: user?.id,
      username: user?.username
    });
    authLogger.audit('LOGOUT_INITIATED', {
      userId: user?.id,
      username: user?.username
    });

    try {
      if (token) {
        authLogger.debug('Calling logout endpoint');
        // Call logout endpoint to revoke session
        await api.post('/auth/logout');
        authLogger.debug('Logout endpoint called successfully');
      } else {
        authLogger.debug('No token available for logout endpoint call');
      }
    } catch (error) {
      authLogger.error('Logout endpoint call failed', { error: error instanceof Error ? error.message : String(error) });
    } finally {
      authLogger.debug('Clearing auth data and navigating to login');
      clearAuthData();
      navigate('/login');
      timer();
    }
  }, [authLogger, navigate, token, user?.id, user?.username]);

  const clearAuthData = () => {
    authLogger.debug('Clearing authentication data', {
      hadUser: !!user,
      hadToken: !!token,
      userId: user?.id,
      username: user?.username
    });

    setUser(null);
    setToken(null);
    localStorage.removeItem('auth_token');
    localStorage.removeItem('auth_user');
    // Clear project scoping too.  Otherwise the previous user's
    // current_project_id leaks into the next session — it lives in
    // both localStorage *and* the api.ts module-level cache, and the
    // next user may not be a member of that project, which left the
    // app issuing 403s on every data endpoint with no way to recover.
    setCurrentProjectId(null);

    authLogger.info('Authentication data cleared');
    authLogger.audit('AUTH_DATA_CLEARED', {
      previousUserId: user?.id,
      previousUsername: user?.username
    });
  };

  const hasRole = useCallback((role: string): boolean => {
    return user?.role === role;
  }, [user?.role]);

  const hasPermission = useCallback((requiredRole: string): boolean => {
    if (!user) return false;

    const userLevel = ROLE_HIERARCHY[user.role as keyof typeof ROLE_HIERARCHY] || 0;
    const requiredLevel = ROLE_HIERARCHY[requiredRole as keyof typeof ROLE_HIERARCHY] || 0;

    return userLevel >= requiredLevel;
  }, [user]);

  const logAuditEvent = async (action: string, resourceType: string, details?: any) => {
    if (!token) {
      authLogger.debug('Audit logging skipped - no token available', { action, resourceType });
      return;
    }

    try {
      authLogger.debug('Sending audit log to backend', { action, resourceType, details });
      await api.post('/audit/log', {
        action,
        resource_type: resourceType,
        details: details || { timestamp: new Date().toISOString() }
      });
      authLogger.debug('Audit log sent successfully', { action, resourceType });
    } catch (error) {
      authLogger.error('Audit logging failed with exception', {
        action,
        resourceType,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  };

  // Track authentication state changes
  const isAuthenticated = !!user && !!token;
  const authStatus: AuthStatus = isLoading
    ? 'checking'
    : isAuthenticated
      ? 'authenticated'
      : 'anonymous';

  // Log whenever authentication state changes
  useEffect(() => {
    authLogger.debug('Authentication state changed', {
      isAuthenticated,
      hasUser: !!user,
      hasToken: !!token,
      isLoading,
      userId: user?.id,
      username: user?.username,
      role: user?.role
    });

    if (isAuthenticated && !isLoading) {
      authLogger.info('User is authenticated', {
        userId: user?.id,
        username: user?.username,
        role: user?.role
      });
    } else if (!isAuthenticated && !isLoading) {
      authLogger.info('User is not authenticated');
    }
  }, [isAuthenticated, isLoading, user, token]);

  const updateUser = useCallback((updates: Partial<User>) => {
    setUser(prev => prev ? { ...prev, ...updates } : prev);
    // Sync to localStorage
    const stored = localStorage.getItem('auth_user');
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        localStorage.setItem('auth_user', JSON.stringify({ ...parsed, ...updates }));
      } catch { /* ignore */ }
    }
  }, []);

  // Memoize the context value so every Auth consumer (Layout, every page,
  // ProtectedRoute, AgentActivityRail, HostInspector, ...) doesn't
  // re-render on every Provider render.  The previous shape allocated a
  // fresh object each render, which fanned out into the whole tree on
  // every notifications poll tick / location change.
  const value = useMemo(() => ({
    user,
    token,
    login,
    verify2fa,
    logout,
    updateUser,
    isAuthenticated,
    isLoading,
    authStatus,
    hasRole,
    hasPermission,
  }), [user, token, login, verify2fa, logout, updateUser, isAuthenticated, isLoading, authStatus, hasRole, hasPermission]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};