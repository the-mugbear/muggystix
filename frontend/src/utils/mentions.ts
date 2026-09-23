/**
 * @mention matching — the frontend twin of the backend's
 * `notification_service.find_mentions`, so what the composer highlights is
 * what the server notifies.
 *
 * Usernames are free text (`eval-ana`, `j.smith`), so a mention is matched
 * against the KNOWN member names, longest first and case-insensitively, never
 * with a `@\w+` pattern (which stops at the hyphen). A match ends at a
 * boundary: `@ana` does not match inside `@anabel` or `@ana-maria`, but does
 * before punctuation (`@ana.`). An `@` inside a word (an e-mail address) is
 * not a mention.
 */

export interface MentionSpan {
  start: number;
  end: number;
  username: string;
}

export interface MentionCandidate {
  username: string;
  full_name?: string | null;
}

const continuesName = (ch: string): boolean => ch !== '' && /[\p{L}\p{N}_]/u.test(ch);

/** Where `text` @mentions one of `usernames`, in order. */
export function findMentionSpans(text: string, usernames: readonly string[]): MentionSpan[] {
  if (!text || !text.includes('@') || usernames.length === 0) return [];
  const names = [...new Set(usernames.filter(Boolean))].sort((a, b) => b.length - a.length);
  const lowered = names.map((n) => [n, n.toLowerCase()] as const);
  const low = text.toLowerCase();
  const spans: MentionSpan[] = [];
  let at = text.indexOf('@');
  while (at !== -1) {
    const prev = at > 0 ? text[at - 1] : '';
    let next = at + 1;
    if (!(continuesName(prev) || prev === '.' || prev === '-')) {
      for (const [name, lname] of lowered) {
        if (!low.startsWith(lname, at + 1)) continue;
        const end = at + 1 + lname.length;
        const nxt = text[end] ?? '';
        const after = text[end + 1] ?? '';
        if (continuesName(nxt) || ((nxt === '.' || nxt === '-') && continuesName(after))) continue;
        spans.push({ start: at, end, username: name });
        next = end;
        break;
      }
    }
    at = text.indexOf('@', next);
  }
  return spans;
}

/** The `@partial` being typed at `caret`, if any: where its `@` is and the
 *  text after it. Null when the caret is not in a mention. */
export function activeMentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  const query = before.slice(at + 1);
  if (/\s/.test(query) || query.length > 64) return null;
  const prev = at > 0 ? text[at - 1] : '';
  if (continuesName(prev) || prev === '.' || prev === '-') return null;
  return { start: at, query };
}

/** Members matching a partial mention: username or a word of the full name
 *  starting with it first, then anything containing it. */
export function filterMentionCandidates<T extends MentionCandidate>(
  members: readonly T[],
  query: string,
  limit = 6,
): T[] {
  const q = query.toLowerCase();
  const starts: T[] = [];
  const contains: T[] = [];
  for (const m of members) {
    const user = m.username.toLowerCase();
    const words = (m.full_name ?? '').toLowerCase().split(/\s+/).filter(Boolean);
    if (user.startsWith(q) || words.some((w) => w.startsWith(q))) starts.push(m);
    else if (user.includes(q) || (m.full_name ?? '').toLowerCase().includes(q)) contains.push(m);
  }
  return [...starts, ...contains].slice(0, limit);
}
