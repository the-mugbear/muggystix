/**
 * App-wide toast / snackbar layer.
 *
 * Why this exists: feedback for small operations (copy, save, follow,
 * upload, mark-read) was previously inconsistent — some handlers used
 * an inline Alert, some swallowed errors, some triggered a `confirm()`.
 * This module centralizes that into a single non-blocking surface so
 * pages can call `useToast().success("Copied to clipboard")` from any
 * handler and get consistent placement, dismissal, and styling.
 *
 * v4.0.0-alpha.0 swap: the public hook surface (`useToast().success/
 * .error/.warning/.info/.show`) is unchanged.  Internally we now
 * delegate to Sonner.  Three reasons:
 *   1. a11y — Sonner sets `aria-live` (polite for success/info/warning,
 *      assertive for error) on the toast region.  MUI Snackbar did not.
 *   2. Severity-based duration — errors stay until dismissed; success
 *      / info auto-expire quickly.  The previous queue used 4s for
 *      everything.
 *   3. Deduplication — passing the same `id` in `ToastOptions` updates
 *      the existing toast in place instead of queuing.  Bulk actions
 *      ("Marked as In Review" × 5) no longer produce 20s of toast
 *      spam.
 *
 * No caller changes needed — the hook returns the same five methods
 * with the same signatures.  ToastOptions gained an optional `id`
 * field; callers that pass it get dedup.
 *
 * The Toaster JSX itself is rendered once from App.tsx (single
 * instance per app).  This module is the hook + provider shell only.
 */

import React, { createContext, useContext, useMemo } from 'react';
import { toast as sonnerToast, Toaster } from 'sonner';
import { useAppTheme } from './ThemeContext';

export type ToastSeverity = 'success' | 'info' | 'warning' | 'error';

export interface ToastOptions {
  /**
   * ms before auto-dismiss.  `null` means require manual close
   * (default for errors).  Omitting picks the severity default:
   *   success: 3000, info: 4000, warning: 6000, error: never (sticky)
   */
  autoHideMs?: number | null;
  /**
   * Stable id for dedupe.  Two calls with the same id update one
   * toast in place — used for bulk actions ("Marked as In Review" ×
   * N) so we never spam a queue.
   */
  id?: string | number;
}

interface ToastContextValue {
  /** Show a toast with the given severity and message. */
  show: (severity: ToastSeverity, message: string, options?: ToastOptions) => void;
  /** Convenience helpers. */
  success: (message: string, options?: ToastOptions) => void;
  info: (message: string, options?: ToastOptions) => void;
  warning: (message: string, options?: ToastOptions) => void;
  error: (message: string, options?: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

// Severity-based defaults.  Errors are sticky because dismissing an
// error you didn't read is worse than the toast hanging around.
const DEFAULT_DURATION_MS: Record<ToastSeverity, number | null> = {
  success: 3000,
  info: 4000,
  warning: 6000,
  error: null,
};

function resolveDuration(severity: ToastSeverity, options?: ToastOptions): number | undefined {
  // Sonner: omit `duration` to use its default; pass `Infinity` to make
  // sticky.  We translate our `null` (manual close) to Infinity.
  const explicit = options?.autoHideMs;
  if (explicit === undefined) {
    const d = DEFAULT_DURATION_MS[severity];
    return d === null ? Infinity : d;
  }
  if (explicit === null) return Infinity;
  return explicit;
}

function dispatch(severity: ToastSeverity, message: string, options?: ToastOptions) {
  const sonnerOpts = {
    id: options?.id,
    duration: resolveDuration(severity, options),
  };
  switch (severity) {
    case 'success':
      sonnerToast.success(message, sonnerOpts);
      return;
    case 'info':
      sonnerToast.info(message, sonnerOpts);
      return;
    case 'warning':
      sonnerToast.warning(message, sonnerOpts);
      return;
    case 'error':
      sonnerToast.error(message, sonnerOpts);
      return;
  }
}

/**
 * Resolve the active light/dark mode from ThemeContext.  Audit PRF·M8
 * removed the previous MutationObserver + matchMedia listener that
 * duplicated state ThemeContext already owns — single source of truth,
 * one fewer DOM observer per app instance.
 */
function useThemeMode(): 'light' | 'dark' {
  const { isDarkTheme } = useAppTheme();
  return isDarkTheme ? 'dark' : 'light';
}

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const themeMode = useThemeMode();
  const value = useMemo<ToastContextValue>(
    () => ({
      show: dispatch,
      success: (message, options) => dispatch('success', message, options),
      info: (message, options) => dispatch('info', message, options),
      warning: (message, options) => dispatch('warning', message, options),
      error: (message, options) => dispatch('error', message, options),
    }),
    [],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/*
        Single Toaster mount.  Sonner sets aria-live correctly
        (polite for non-error, assertive for error) on its toast
        region.  `richColors` uses Sonner's severity palette;
        `closeButton` puts an X on each toast so errors (which are
        sticky by default) can be dismissed by users who don't want
        to wait.  Theme follows MUI's mode so we don't have a white
        toast in dark mode.
      */}
      <Toaster
        position="bottom-right"
        richColors
        closeButton
        theme={themeMode}
        toastOptions={{
          // Sit above MUI's Modal (1300) and Tooltip (1500) so toasts
          // remain visible during dialog interactions.  When the
          // remaining MUI surfaces are removed this can drop back to
          // Sonner's default.
          style: { zIndex: 2000 },
        }}
      />
    </ToastContext.Provider>
  );
};

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Defensive: this should only happen if a component is rendered
    // outside the provider tree (e.g. in a test).  Return helpers that
    // call Sonner directly so toasts still work; just won't share the
    // provider's memo identity.
    if (process.env.NODE_ENV !== 'production') {
      // eslint-disable-next-line no-console
      console.warn('useToast called outside ToastProvider — falling back to direct dispatch');
    }
    return {
      show: dispatch,
      success: (message, options) => dispatch('success', message, options),
      info: (message, options) => dispatch('info', message, options),
      warning: (message, options) => dispatch('warning', message, options),
      error: (message, options) => dispatch('error', message, options),
    };
  }
  return ctx;
}
