import React from 'react';

import { Label } from './ui/label';
import { PasswordInput } from './ui/password-input';

/**
 * The account password asked before an authenticator is bound (5.409.0;
 * review 2026-10-16, N9) — the one field both enrolment screens show
 * (`pages/ForceTwoFactorSetup`, `components/TwoFactorCard`), driven by
 * `hooks/useTwoFactorEnrolment`'s `password`.
 */
const TwoFactorPasswordField: React.FC<{
  id: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}> = ({ id, value, onChange, disabled }) => (
  <div className="flex flex-col gap-xxs">
    <Label htmlFor={id}>Your password</Label>
    <PasswordInput
      id={id}
      autoComplete="current-password"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
    <p className="text-caption text-muted-foreground">
      Setting up two-factor authentication changes how your account signs in, so it asks for your password first.
    </p>
  </div>
);

export default TwoFactorPasswordField;
