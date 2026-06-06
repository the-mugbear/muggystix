import * as React from 'react';
import { Check, Circle } from 'lucide-react';
import { cn } from '../utils/cn';

/**
 * Shared password-rule checklist.  Extracted from ForceChangePassword
 * so the rules render the same way in every change-password flow —
 * forced-on-first-login, in-profile change, and any future admin
 * reset.  The previous shape duplicated the rules array and the
 * rendering UI; only the forced-change page actually showed them.
 *
 * Rules MUST match the backend password policy in
 * `backend/app/core/security.py`.  When the backend policy changes,
 * update PASSWORD_RULES below in lockstep.
 *
 * Wrap with the `id` you pass to the password Input's
 * `aria-describedby`, so the checklist announces as the user types.
 */

// v2.62.0 — exact backend whitelist, not "anything non-alphanumeric".
// Backend rejects e.g. space, `~`, `'`, `"`, `/`, `\` even though the
// previous frontend `/[^A-Za-z0-9]/` test accepted them.  Operators
// hit a 400 after the UI green-lit submit.  Source of truth:
// `app/core/security.py` — `"!@#$%^&*()_+-=[]{}|;:,.<>?"`.
const SPECIAL_CHAR_PATTERN = /[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]/;

export const PASSWORD_RULES: { label: string; test: (pw: string) => boolean }[] = [
  { label: 'At least 12 characters', test: (pw) => pw.length >= 12 },
  { label: 'An uppercase letter (A–Z)', test: (pw) => /[A-Z]/.test(pw) },
  { label: 'A lowercase letter (a–z)', test: (pw) => /[a-z]/.test(pw) },
  { label: 'A number (0–9)', test: (pw) => /[0-9]/.test(pw) },
  {
    // Comma-separated so the user doesn't read the spaces as part of
    // the allowed set — backend rejects whitespace as a special char.
    label: 'A special character — one of: ! @ # $ % ^ & * ( ) _ + - = [ ] { } | ; : , . < > ? (space not allowed)',
    test: (pw) => SPECIAL_CHAR_PATTERN.test(pw),
  },
];

export const isPasswordValid = (password: string): boolean =>
  PASSWORD_RULES.every((r) => r.test(password));

export interface PasswordRulesChecklistProps {
  password: string;
  /** id for the wrapping `<ul>` — pair with the Input's aria-describedby. */
  id?: string;
  className?: string;
}

export const PasswordRulesChecklist: React.FC<PasswordRulesChecklistProps> = ({
  password,
  id,
  className,
}) => {
  const ruleResults = PASSWORD_RULES.map((r) => ({ ...r, ok: r.test(password) }));
  return (
    <ul
      id={id}
      aria-live="polite"
      aria-label="Password requirements"
      className={cn('flex flex-col gap-xxs', className)}
    >
      {ruleResults.map((r) => (
        <li key={r.label} className="flex items-center gap-xs">
          {r.ok ? (
            <Check className="size-3.5 text-success" aria-hidden />
          ) : (
            <Circle className="size-3.5 text-muted-foreground" aria-hidden />
          )}
          <span className={cn('text-caption', r.ok ? 'text-success' : 'text-muted-foreground')}>
            <span className="sr-only">{r.ok ? 'Met: ' : 'Not met: '}</span>
            {r.label}
          </span>
        </li>
      ))}
    </ul>
  );
};
