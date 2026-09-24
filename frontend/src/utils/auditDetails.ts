/**
 * An audit event's `details` (a JSON column) as one readable line.
 *
 * The viewer used to print the raw JSON (`{"method":"totp"}`). Known keys get a
 * label and value vocabulary ("method: TOTP", "full name: Ana → Ana Ortiz");
 * any other key is shown as `key: value` with underscores spaced, so a new
 * event type still reads without a change here. Nested objects that are not a
 * known shape fall back to compact JSON rather than disappearing.
 */

const KEY_LABELS: Record<string, string> = {
  method: 'method',
  username: 'username',
  target_user: 'user',
  new_username: 'new user',
  deleted_username: 'deleted user',
  stage: 'stage',
  role: 'role',
};

const VALUE_LABELS: Record<string, Record<string, string>> = {
  method: { totp: 'TOTP', password: 'password', recovery_code: 'recovery code' },
  stage: { '2fa': '2FA' },
};

const MAX_LIST = 5;

const label = (key: string): string => KEY_LABELS[key] ?? key.replace(/_/g, ' ');

function scalar(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    const shown = value.slice(0, MAX_LIST).map(scalar).join(', ');
    return value.length > MAX_LIST ? `${shown} +${value.length - MAX_LIST} more` : shown || '—';
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

/** `{field: {old, new}}` — the shape user/profile updates record. */
function changes(value: unknown): string | null {
  if (!isObject(value)) return null;
  const parts = Object.entries(value).map(([field, change]) =>
    isObject(change) && ('old' in change || 'new' in change)
      ? `${label(field)}: ${scalar(change.old)} → ${scalar(change.new)}`
      : `${label(field)}: ${scalar(change)}`,
  );
  return parts.length ? parts.join(' · ') : 'no changes';
}

function entry(key: string, value: unknown): string | null {
  // Client-reported events are stamped server-side; say so plainly.
  if (key === 'source') return value === 'client' ? 'reported by the client' : `source: ${scalar(value)}`;
  if (key === 'changes') return changes(value) ?? `changes: ${scalar(value)}`;
  const vocab = VALUE_LABELS[key];
  const text = typeof value === 'string' && vocab?.[value.toLowerCase()]
    ? vocab[value.toLowerCase()]
    : scalar(value);
  return `${label(key)}: ${text}`;
}

/** Readable text for `details`, or null when there is nothing to show. */
export function formatAuditDetails(details: unknown): string | null {
  if (details === null || details === undefined) return null;
  if (typeof details === 'string') return details.trim() || null;
  if (!isObject(details)) return scalar(details);
  const parts = Object.entries(details)
    .map(([k, v]) => entry(k, v))
    .filter((p): p is string => !!p);
  return parts.length ? parts.join(' · ') : null;
}
