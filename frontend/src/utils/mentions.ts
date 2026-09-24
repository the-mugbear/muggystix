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

/** An `@word` that matched no member: `start` is the index of its `@`. */
export interface UnmatchedMention {
  start: number;
  token: string;
}

/** The word written after an `@` at `from`: name characters, with `.`/`-`
 *  kept only when a name character follows (twin of `_mention_token`). */
function mentionToken(text: string, from: number): string {
  let end = from;
  while (end < text.length) {
    const ch = text[end];
    if (continuesName(ch)) end += 1;
    else if ((ch === '.' || ch === '-') && continuesName(text[end + 1] ?? '')) end += 1;
    else break;
  }
  return text.slice(from, end);
}

/** Matched spans and unmatched `@words` in one pass — the twin of the
 *  backend's `scan_mentions` (v5.290.0). An `@` in mention position where no
 *  member name matches reports the word after it; a bare `@` reports nothing. */
export function scanMentions(
  text: string,
  usernames: readonly string[],
): { spans: MentionSpan[]; unmatched: UnmatchedMention[] } {
  const spans: MentionSpan[] = [];
  const unmatched: UnmatchedMention[] = [];
  if (!text || !text.includes('@')) return { spans, unmatched };
  const names = [...new Set(usernames.filter(Boolean))].sort((a, b) => b.length - a.length);
  const lowered = names.map((n) => [n, n.toLowerCase()] as const);
  const low = text.toLowerCase();
  let at = text.indexOf('@');
  while (at !== -1) {
    const prev = at > 0 ? text[at - 1] : '';
    let next = at + 1;
    if (!(continuesName(prev) || prev === '.' || prev === '-')) {
      let hit = false;
      for (const [name, lname] of lowered) {
        if (!low.startsWith(lname, at + 1)) continue;
        const end = at + 1 + lname.length;
        const nxt = text[end] ?? '';
        const after = text[end + 1] ?? '';
        if (continuesName(nxt) || ((nxt === '.' || nxt === '-') && continuesName(after))) continue;
        spans.push({ start: at, end, username: name });
        next = end;
        hit = true;
        break;
      }
      if (!hit) {
        const token = mentionToken(text, at + 1);
        if (token) unmatched.push({ start: at, token });
      }
    }
    at = text.indexOf('@', next);
  }
  return { spans, unmatched };
}

/** Where `text` @mentions one of `usernames`, in order. */
export function findMentionSpans(text: string, usernames: readonly string[]): MentionSpan[] {
  if (usernames.length === 0) return [];
  return scanMentions(text, usernames).spans;
}

/** The distinct `@words` in `text` that match no member (first spelling
 *  kept, case-insensitive), skipping the one whose `@` is at `skipAt` — the
 *  mention still being typed. */
export function unmatchedMentionTokens(
  text: string,
  usernames: readonly string[],
  skipAt: number | null = null,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const u of scanMentions(text, usernames).unmatched) {
    if (u.start === skipAt || seen.has(u.token.toLowerCase())) continue;
    seen.add(u.token.toLowerCase());
    out.push(u.token);
  }
  return out;
}

/** What a note's author is told after posting, from the server's
 *  `mentions_notified` / `unmatched_mentions` (v5.290.0). */
export interface MentionOutcome {
  mentions_notified?: { username: string; name: string }[] | null;
  unmatched_mentions?: string[] | null;
}

const listNames = (names: string[]): string =>
  names.length <= 1
    ? names.join('')
    : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;

/** The toasts for a posted note: who was notified (success) and which
 *  `@words` reached nobody (warning). Either may be null. */
export function mentionOutcomeMessages(outcome: MentionOutcome): { notified: string | null; unmatched: string | null } {
  const notified = (outcome.mentions_notified ?? []).map((m) => m.name || m.username);
  const unmatched = (outcome.unmatched_mentions ?? []).map((t) => `@${t}`);
  return {
    notified: notified.length ? `Notified ${listNames(notified)}` : null,
    unmatched: unmatched.length
      ? `${listNames(unmatched)} ${unmatched.length === 1 ? "isn't a member" : "aren't members"} of this project — ${
          notified.length ? 'they were not notified' : 'nobody was notified'
        }`
      : null,
  };
}

/** Show `mentionOutcomeMessages` as toasts. Returns true when anything was
 *  said, so a caller can skip its own generic "posted" toast. */
export function announceMentionOutcome(
  toast: { success: (m: string) => void; warning: (m: string) => void },
  outcome: MentionOutcome,
): boolean {
  const { notified, unmatched } = mentionOutcomeMessages(outcome);
  if (notified) toast.success(notified);
  if (unmatched) toast.warning(unmatched);
  return !!(notified || unmatched);
}

/** The pre-post hint under a composer for `@words` matching no member. */
export function unmatchedMentionHint(tokens: readonly string[]): string | null {
  if (tokens.length === 0) return null;
  const list = listNames(tokens.map((t) => `@${t}`));
  return tokens.length === 1
    ? `${list} isn't a member of this project — they won't be notified`
    : `${list} aren't members of this project — they won't be notified`;
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
