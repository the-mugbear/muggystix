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

/** The value a detail has now, pending edits included — what the page shows. */
export const current = (c: DetailContext, key: DetailKey): DetailValue | null | undefined => {
  if (key in c.pending) {
    const p = c.pending[key];
    return p === null ? c.followed?.[key] : p;
  }
  return baseline(c, key);
};

/** Is the detail this report's own (customised) — with pending edits? */
export const customised = (c: DetailContext, key: DetailKey): boolean => {
  if (!c.followed || c.ownOnly.includes(key)) return false;
  if (key in c.pending) {
    const p = c.pending[key];
    // An edit equal to the project's value follows it once saved (the
    // server's "changed back" rule): it is not shown as customised — except
    // an empty list, which the server always keeps ("nobody", chosen on
    // purpose; review 2026-10-14, R9: the page said "uses the project's"
    // until it was saved).
    if (isListKey(key) && Array.isArray(p) && p.length === 0) return true;
    return p !== null && p !== undefined && hasValue(key, p) && !sameValue(key, p, c.followed[key]);
  }
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

/** The complete override map with the pending edits applied — the values
 *  the page shows as this report's own. */
export const withPending = (c: DetailContext): EngagementOverrides => {
  const settings: EngagementOverrides = { ...c.saved };
  for (const key of Object.keys(c.pending) as DetailKey[]) {
    const p = c.pending[key];
    if (p === null || p === undefined || (!isListKey(key) && trimmed(p) === null)) {
      delete settings[key];
    } else if (isListKey(key)) {
      (settings as Record<string, unknown>)[key] = (p as Array<{ name: string }>)
        .filter((r) => r.name.trim()).map((r) => ({ ...r, name: r.name.trim() }));
    } else {
      (settings as Record<string, unknown>)[key] = p;
    }
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
    (Object.keys(c.pending) as DetailKey[]).map((k) => [k, all[k] ?? null]),
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
