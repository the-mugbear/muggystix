import * as React from 'react';
import { Eye, EyeOff, Trash2 } from 'lucide-react';
import { Input, type InputProps } from './input';
import { cn } from '../../utils/cn';

/**
 * PasswordInput — wraps Input with a show/hide eye toggle and an
 * optional clear-existing-secret button.
 *
 * Used everywhere the user enters a secret (Login, ForceChangePassword,
 * Profile change-password, LLM API key, Integration credentials, any
 * settings page with an API key field).
 *
 * Audit M15 / M20 closed: visibility state announced via `aria-pressed`;
 * clear-secret action is a separate button only when `onClear` is
 * provided (used in edit dialogs to wipe an existing credential).
 */
export interface PasswordInputProps extends Omit<InputProps, 'type'> {
  /** Override the toggle button's accessible labels. */
  showLabel?: string;
  hideLabel?: string;
  /** Provided in edit-mode to expose a clear-existing-secret action. */
  onClear?: () => void;
  clearTooltip?: string;
}

export const PasswordInput = React.forwardRef<HTMLInputElement, PasswordInputProps>(
  (
    {
      className,
      showLabel = 'Show password',
      hideLabel = 'Hide password',
      onClear,
      clearTooltip = 'Clear stored secret',
      disabled,
      ...props
    },
    ref,
  ) => {
    const [visible, setVisible] = React.useState(false);
    // pr-9 (one button) or pr-16 (two buttons) so the typed value never
    // sits underneath the trailing controls.
    const padRight = onClear ? 'pr-16' : 'pr-9';
    return (
      <div className="relative">
        <Input
          ref={ref}
          type={visible ? 'text' : 'password'}
          autoComplete="new-password"
          className={cn(padRight, className)}
          disabled={disabled}
          {...props}
        />
        <div className="absolute inset-y-0 right-0 flex items-center">
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            disabled={disabled}
            aria-pressed={visible}
            aria-label={visible ? hideLabel : showLabel}
            className={cn(
              'flex h-full items-center px-xs text-muted-foreground hover:text-foreground',
              'focus:outline-none focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring rounded-control',
              'disabled:cursor-not-allowed disabled:opacity-50',
            )}
          >
            {visible ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
          </button>
          {onClear && (
            <button
              type="button"
              onClick={onClear}
              disabled={disabled}
              aria-label={clearTooltip}
              title={clearTooltip}
              className={cn(
                'flex h-full items-center px-xs text-muted-foreground hover:text-destructive',
                'focus:outline-none focus-visible:text-destructive focus-visible:ring-2 focus-visible:ring-ring rounded-control',
                'disabled:cursor-not-allowed disabled:opacity-50',
              )}
            >
              <Trash2 className="size-4" aria-hidden />
            </button>
          )}
        </div>
      </div>
    );
  },
);
PasswordInput.displayName = 'PasswordInput';

/**
 * Lightweight URL validator.  Returns an error string if the URL is
 * obviously malformed, ``null`` if it's empty or parses cleanly.  Used
 * by credential settings pages to flag bad ``base_url`` inputs inline
 * instead of letting them silently break at call time.
 */
export function validateBaseUrl(value: string | undefined | null): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!/^https?:\/\//i.test(trimmed)) {
    return 'URL must start with http:// or https://';
  }
  try {
    new URL(trimmed);
    return null;
  } catch {
    return 'Invalid URL';
  }
}
