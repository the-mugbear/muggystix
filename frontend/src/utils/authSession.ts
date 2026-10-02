/**
 * One browser, several tabs, ONE stored session.
 *
 * The token lives in localStorage (`auth_token` / `auth_user`), which every
 * tab shares, and the request interceptor reads it on each call — while the
 * account a tab DISPLAYS (its user, its current project, its polls) lives in
 * that tab's memory.  So when another tab signed in as someone else, this tab
 * kept showing the first account's project and sent its polls with the second
 * account's token: a 403 "Not a member of this project" on
 * `/proposals/summary` and `/agent-sessions` once a minute (browser pass
 * 2026-10-01), and — worse — any change made from that tab would have been
 * recorded as the other person's.
 *
 * `accountChangedElsewhere` decides, from a `storage` event (which a browser
 * delivers only to the OTHER tabs), whether the stored session stopped being
 * this tab's.  The provider then reloads, so the tab shows whoever is signed
 * in now (or the sign-in page).
 */
type StorageChange = Pick<StorageEvent, 'key' | 'newValue'>;

export function accountChangedElsewhere(
  event: StorageChange,
  currentUserId: number | null | undefined,
): boolean {
  // A tab with nobody signed in has nothing to protect: the login page keeps
  // working with whatever session it creates.
  if (currentUserId == null) return false;
  // `key === null` is `localStorage.clear()`: the session went with the rest.
  if (event.key === null) return true;
  if (event.key === 'auth_token') {
    // Removed = signed out elsewhere.  A REPLACED token says nothing by
    // itself (it may be this account's again); `auth_user` names the account.
    return event.newValue === null;
  }
  if (event.key !== 'auth_user') return false;
  if (event.newValue === null) return true;
  try {
    const stored = JSON.parse(event.newValue) as { id?: unknown } | null;
    // Same account (a profile edit, a second sign-in as the same person).
    return !stored || stored.id !== currentUserId;
  } catch {
    // Unreadable: this tab can no longer say whose session it is using.
    return true;
  }
}

/** Start this tab again from the stored session.  A function of its own so a
 *  test can replace it (jsdom's `location.reload` cannot be spied on). */
export function reloadForAccountChange(): void {
  window.location.reload();
}
