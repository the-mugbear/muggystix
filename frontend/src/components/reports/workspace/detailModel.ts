/**
 * The report workspace's model of a report's details (owner, 2026-10-14;
 * planning/PROPOSAL_report_workspace_2026-10-14.md, "What a field becomes").
 *
 * The page never decides inheritance by itself.  It keeps PENDING edits over
 * what is saved, and sends only the details it edited (`toSave`); the server
 * drops a sent override that equals what the draft follows
 * (`report_defaults.drop_followed`).  Here:
 *
 * - a pending value is an override as typed, or `null` — "use the project's"
 *   (the override is removed);
 * - an edit that brings a field back to its saved state is no edit at all,
 *   so opening and cancelling, or typing a value back, leaves the draft clean;
 * - a field the author never edited keeps its saved state, whatever the
 *   project's value is now.
 */
import type {
  EngagementOverrides, EngagementSettings, FollowedSource, ReportRecipient, ReportTester,
} from '../../../services/api';

export type DetailKey = keyof EngagementSettings;
export type ListKey = 'testers' | 'distribution';
export type DetailValue = EngagementSettings[DetailKey];
/** A pending edit: an override as typed, or `null` = follow the project. */
export type Pending = Partial<Record<DetailKey, DetailValue | null>>;

export const LIST_KEYS: ReadonlyArray<DetailKey> = ['testers', 'distribution'];
export const isListKey = (key: DetailKey): key is ListKey => LIST_KEYS.includes(key);

export const DETAIL_LABELS: Record<DetailKey, string> = {
  executive_summary: 'Executive summary',
  client_name: 'Client', author_name: 'Author', classification: 'Classification', engagement_type: 'Engagement type',
  testers: 'Assessment team', distribution: 'Distribution', system_description: 'System description',
  applications: 'Application URLs and API endpoints', thick_clients: 'Thick client applications',
  other_targets: 'Other targets', disclaimer: 'Disclaimer',
};

/** The Details tab's sections, in the order the report reads. */
export interface DetailSection {
  id: 'summary' | 'engagement' | 'people' | 'scope' | 'disclaimer';
  title: string;
  keys: DetailKey[];
}
export const DETAIL_SECTIONS: ReadonlyArray<DetailSection> = [
  { id: 'summary', title: 'Summary', keys: ['executive_summary'] },
  { id: 'engagement', title: 'Client & engagement', keys: ['client_name', 'author_name', 'classification', 'engagement_type'] },
  { id: 'people', title: 'Team & distribution', keys: ['testers', 'distribution'] },
  { id: 'scope', title: 'Scope & targets', keys: ['system_description', 'applications', 'thick_clients', 'other_targets'] },
  { id: 'disclaimer', title: 'Disclaimer', keys: ['disclaimer'] },
];
export const sectionOf = (key: DetailKey): DetailSection =>
  DETAIL_SECTIONS.find((s) => s.keys.includes(key)) ?? DETAIL_SECTIONS[0];

const trimmed = (v: unknown): string | null => {
  const t = typeof v === 'string' ? v.trim() : '';
  return t || null;
};

/** A value as compared with another — the server's `_comparable`: a text
 *  trimmed (blank = none), a list as its rows' fields, trimmed, in order. */
export const comparable = (key: DetailKey, v: unknown): string => {
  if (!isListKey(key)) return JSON.stringify(trimmed(v));
  const rows = Array.isArray(v) ? v : [];
  return JSON.stringify(rows.map((r: ReportTester | ReportRecipient) => (key === 'testers'
    ? [(r as ReportTester).user_id ?? null, trimmed(r.name), trimmed((r as ReportTester).role), trimmed(r.email)]
    : [trimmed(r.name), trimmed(r.email)])));
};
export const sameValue = (key: DetailKey, a: unknown, b: unknown): boolean => comparable(key, a) === comparable(key, b);

/** Is a value written at all?  A list always is (an empty one says "nobody"). */
export const hasValue = (key: DetailKey, v: unknown): boolean =>
  isListKey(key) ? Array.isArray(v) : trimmed(v) !== null;

/** Does the saved report override this detail? */
export const savedOverride = (saved: EngagementOverrides, key: DetailKey): boolean =>
  saved[key] !== undefined && saved[key] !== null;

export interface DetailContext {
  /** The report's saved overrides (the defaults' own values on /reports/defaults). */
  saved: EngagementOverrides;
  /** What each detail prints when not overridden (a draft); null on the defaults page. */
  followed: EngagementSettings | null;
  /** Where each followed value comes from. */
  sources: Partial<Record<DetailKey, FollowedSource | null>> | null;
  pending: Pending;
  /** Details this report never follows (an addendum's summary). */
  ownOnly: ReadonlyArray<DetailKey>;
}

/** What a detail is with nothing pending: its saved override, else (a draft)
 *  what it follows, else (the defaults page) its stored value. */
export const baseline = (c: DetailContext, key: DetailKey): DetailValue | null | undefined =>
  (savedOverride(c.saved, key) || !c.followed || c.ownOnly.includes(key)) ? c.saved[key] : c.followed[key];

/** The value a detail has now AS TYPED, pending edits included — what an
 *  open editor holds (a blank recipient row being filled in, a field just
 *  cleared).  The read view and everything that judges the edit use
 *  {@link shown} / {@link edited}. */
export const current = (c: DetailContext, key: DetailKey): DetailValue | null | undefined => {
  if (key in c.pending) {
    const p = c.pending[key];
    return p === null ? c.followed?.[key] : p;
  }
  return baseline(c, key);
};

/** A value as a save would store it: a list without its nameless rows
 *  (names trimmed), a text trimmed (blank = none). */
export const cleaned = (key: DetailKey, v: unknown): DetailValue | null => {
  if (!isListKey(key)) return trimmed(v) as DetailValue | null;
  return (Array.isArray(v) ? v : [])
    .filter((r: { name?: string | null }) => (r.name ?? '').trim())
    .map((r: { name: string }) => ({ ...r, name: r.name.trim() })) as DetailValue;
};

const followable = (c: DetailContext, key: DetailKey) => !!c.followed && !c.ownOnly.includes(key);

/** The override a detail carries as saved; undefined = none (it follows the
 *  project, or on the defaults page holds nothing). */
const savedState = (c: DetailContext, key: DetailKey) => (savedOverride(c.saved, key) ? c.saved[key] : undefined);

/** The override a pending edit makes once saved, judged on what a save
 *  stores (third review 2026-10-15, H6/H7: the page judged the raw value —
 *  a blank recipient row made the draft's distribution its own empty list,
 *  and clearing a followed text said "Not written" then printed the
 *  project's value).  undefined = none: `null` ("use the project's"), a
 *  blank text (it follows, or clears a default), or a value equal to what
 *  the draft follows — except an empty list chosen as such ("nobody", which
 *  the server always keeps; review 2026-10-14, R9). */
const pendingState = (c: DetailContext, key: DetailKey) => {
  const p = c.pending[key];
  if (p === null || p === undefined) return undefined;
  const value = cleaned(key, p);
  if (value === null) return undefined;
  if (!followable(c, key)) return value;
  const nobodyChosen = isListKey(key) && Array.isArray(p) && p.length === 0;
  return !nobodyChosen && sameValue(key, value, c.followed![key]) ? undefined : value;
};

/** Is the detail EDITED — would a save change what is stored?  An edit that
 *  stores what is already stored (a blank row, a cleared followed text, a
 *  value typed back) is none: nothing is sent, nothing is held unsaved. */
export const edited = (c: DetailContext, key: DetailKey): boolean => {
  if (!(key in c.pending)) return false;
  const now = pendingState(c, key);
  const before = savedState(c, key);
  if (now === undefined || before === undefined) return now !== before;
  return !sameValue(key, now, before);
};

/** The value a detail shows when read (and prints once saved). */
export const shown = (c: DetailContext, key: DetailKey): DetailValue | null | undefined => {
  if (!edited(c, key)) return baseline(c, key);
  const now = pendingState(c, key);
  return now !== undefined ? now : (c.followed && !c.ownOnly.includes(key) ? c.followed[key] : null);
};

/** Is the detail this report's own (customised) — with pending edits? */
export const customised = (c: DetailContext, key: DetailKey): boolean => {
  if (!followable(c, key)) return false;
  if (key in c.pending) return pendingState(c, key) !== undefined;
  return savedOverride(c.saved, key);
};

/** Pending after the author sets `key` to `value` (`null` = use the
 *  project's).  An edit that leaves the detail as it is saved is no edit at
 *  all, so opening a field and typing nothing, or typing a value back,
 *  leaves the draft clean. */
export const withEdit = (c: DetailContext, key: DetailKey, value: DetailValue | null): Pending => {
  const next = { ...c.pending };
  let unchanged: boolean;
  if (value === null) {
    unchanged = !savedOverride(c.saved, key);                     // already follows
  } else if (savedOverride(c.saved, key) || !c.followed || c.ownOnly.includes(key)) {
    unchanged = sameValue(key, value, c.saved[key]);              // its own saved value
  } else {
    unchanged = sameValue(key, value, c.followed[key]);           // what it follows
  }
  if (unchanged) delete next[key]; else next[key] = value;
  return next;
};

/** The complete override map with the EDITED details applied — the values
 *  the page shows as this report's own. */
export const withPending = (c: DetailContext): EngagementOverrides => {
  const settings: EngagementOverrides = { ...c.saved };
  for (const key of Object.keys(c.pending) as DetailKey[]) {
    if (!edited(c, key)) continue;
    const now = pendingState(c, key);
    if (now === undefined) delete settings[key];
    else (settings as Record<string, unknown>)[key] = now;
  }
  return settings;
};

/** What a save sends: ONLY the details edited, each with its new value
 *  (`null` = follow the project / clear the default).  A detail not edited
 *  is not sent, so a page that read the report a while ago never reverts
 *  someone else's change to it (review 2026-10-14, H1). */
export const toSave = (c: DetailContext): EngagementOverrides => {
  const all = withPending(c);
  return Object.fromEntries(
    (Object.keys(c.pending) as DetailKey[]).filter((k) => edited(c, k)).map((k) => [k, all[k] ?? null]),
  ) as EngagementOverrides;
};

/** What a section's status line says (a draft): every value followed, or
 *  how many are this report's own. */
export const sectionStatus = (c: DetailContext, section: DetailSection): string | null => {
  if (!c.followed) return null;
  const keys = section.keys.filter((k) => !c.ownOnly.includes(k));
  if (!keys.length) return null;
  const own = keys.filter((k) => customised(c, k)).length;
  if (!own) return 'Uses the project’s defaults';
  return own === keys.length && keys.length === 1
    ? 'Customised for this report'
    : `${own} customised for this report`;
};

/** The words for a followed value that does not come from the project's
 *  defaults (said beside the value), else null. */
export const sourceCaption = (source: FollowedSource | null | undefined): string | null => {
  if (source === 'installation') return 'From the installation’s defaults';
  if (source === 'project_team') return 'The project’s analysts and admins';
  return null;
};
