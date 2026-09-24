/**
 * How a person is shown: by name, never by id. Usernames are free text and a
 * full name is optional, so both helpers fall back in the same order — the
 * full name, then the username, then a neutral placeholder.
 */

/** "Ana Ortiz" → "AO"; no full name → the username's first letter. */
export function personInitials(fullName?: string | null, username?: string | null): string {
  const name = (fullName ?? '').trim();
  if (name) {
    const words = name.split(/\s+/).filter(Boolean);
    const letters = words.length > 1 ? [words[0], words[words.length - 1]] : words;
    return letters.map((w) => Array.from(w)[0] ?? '').join('').toUpperCase();
  }
  const user = (username ?? '').trim();
  return user ? (Array.from(user)[0] ?? '?').toUpperCase() : '?';
}

/** The display name: full name, else username, else "—". */
export function personName(fullName?: string | null, username?: string | null): string {
  return (fullName ?? '').trim() || (username ?? '').trim() || '—';
}
