/**
 * Remediation tracking (5.335.0): the page's vocabulary and the pure half of
 * its edit dialog.  Client-free — the page and its tests import this without
 * the API client.
 */
import type {
  OverdueBand, RemediationApplyRow, RemediationFields, RemediationFlag, RemediationGroup, RemediationPage,
  RemediationPolicy, RemediationRow, RemediationState, RemediationStatus, RemediationVerification,
} from '../services/api';
import type { QueryClient } from '@tanstack/react-query';

import { GLOBAL } from '../lib/query';
import { formatDate } from './relativeTime';

/** Every read that shows remediation records, by API function name.  Not a
 *  follow-up being prepared (`getRemediationFollowUp`): its dialog holds a
 *  message the reader may have edited, and is closed by the write. */
const REMEDIATION_READS = new Set([
  'listRemediation', 'listRemediationOverview', 'listRemediationProjects', 'listRemediationContacts',
  'listRemediationTeams', 'getRemediationTrend', 'listRemediationEvents',
]);

/**
 * After a write to remediation records (the edit dialog, a recorded
 * follow-up, "start the clock from a report"): every remediation read on
 * screen is out of date — the list in place, the contacts, the teams, the
 * projects table, the trend.  One list, so a new read is added once.  The
 * cross-project page's reads are `GLOBAL` keys; both forms are reached.
 */
export const invalidateRemediationReads = (queryClient: QueryClient): Promise<void> =>
  queryClient.invalidateQueries({
    predicate: ({ queryKey }) => REMEDIATION_READS.has(String(queryKey[0] === GLOBAL ? queryKey[1] : queryKey[0])),
  });

export const REMEDIATION_PAGE_SIZE = 25;

export const REMEDIATION_STATUSES: RemediationStatus[] = ['open', 'closed', 'deferred'];
/** The record's `closed` is the CONTACT's claim, so it is said as "Reported
 *  fixed" everywhere — never "Closed", and never "Remediated", which is the
 *  assessor's conclusion about the endpoint.  The stored value is unchanged. */
export const REPORTED_FIXED = 'Reported fixed';
export const REMEDIATION_STATUS_LABEL: Record<RemediationStatus, string> = {
  open: 'Open',
  closed: REPORTED_FIXED,
  deferred: 'Deferred',
};

/** Where the contact's record and the assessor's endpoint status disagree.
 *  Derived by the server (`verification`); the page only names it. */
export const REMEDIATION_VERIFICATIONS: RemediationVerification[] = [
  'reported_fixed_not_retested', 'remediated_record_open',
];
export const REMEDIATION_VERIFICATION_LABEL: Record<RemediationVerification, string> = {
  reported_fixed_not_retested: 'Reported fixed, not retested',
  remediated_record_open: 'Remediated, record still open',
};
export const REMEDIATION_VERIFICATION_HELP: Record<RemediationVerification, string> = {
  reported_fixed_not_retested:
    'The contact reported it fixed, and the assessment has not concluded it is remediated on this host.',
  remediated_record_open:
    'The assessment concluded it is remediated on this host, and the remediation record is still open or deferred.',
};
export const isRemediationVerification = (v: string | null): v is RemediationVerification =>
  v != null && (REMEDIATION_VERIFICATIONS as string[]).includes(v);

/** Rows a manager is asked to look at again: a deferral whose review date has
 *  arrived (or that never had one), and a due date somebody set by hand.
 *  Derived by the server (`flag_counts`, `?flag=`); the page only names them. */
export const REMEDIATION_FLAGS: RemediationFlag[] = ['deferral_review_due', 'deadline_overridden'];
export const REMEDIATION_FLAG_LABEL: Record<RemediationFlag, string> = {
  deferral_review_due: 'Deferrals to review',
  deadline_overridden: 'Due date set by hand',
};
export const REMEDIATION_FLAG_HELP: Record<RemediationFlag, string> = {
  deferral_review_due: 'Deferred, and the review date has arrived or none was ever set.',
  deadline_overridden: 'The due date was set by hand and replaces the one the policy gives.',
};
export const isRemediationFlag = (v: string | null): v is RemediationFlag =>
  v != null && (REMEDIATION_FLAGS as string[]).includes(v);

/** The line under a row's deadline that relates the two statuses: the gap
 *  when there is one, "Remediated" when the assessment concluded so and the
 *  record agrees, nothing otherwise. */
export interface VerificationNote { text: string; title: string; tone: string }
export const verificationNote = (
  row: Pick<RemediationRow, 'verification' | 'endpoint_status'>,
): VerificationNote | null => {
  if (row.verification === 'reported_fixed_not_retested') {
    return { text: 'Not retested', title: REMEDIATION_VERIFICATION_HELP.reported_fixed_not_retested, tone: 'text-warning' };
  }
  if (row.verification === 'remediated_record_open') {
    return { text: 'Remediated, record still open', title: REMEDIATION_VERIFICATION_HELP.remediated_record_open, tone: 'text-warning' };
  }
  if (row.endpoint_status === 'remediated') {
    return { text: 'Remediated', title: 'The assessment concluded it is remediated on this host.', tone: 'text-muted-foreground' };
  }
  return null;
};

/** Text written before the vocabulary changed — the record of a finding
 *  removed from a host says "status closed" — as the page says it now. */
export const heldRecordText = (text: string): string =>
  text.replace(/\bstatus closed\b/g, 'status reported fixed');

export const REMEDIATION_GROUPS: RemediationGroup[] = ['due', 'host', 'finding', 'contact', 'team'];
export const REMEDIATION_GROUP_LABEL: Record<RemediationGroup, string> = {
  due: 'Deadline',
  host: 'Host',
  finding: 'Finding',
  contact: 'Contact',
  team: 'Team',
};

/** Deadline states in the order a work list shows them: what needs someone first. */
export const REMEDIATION_STATES: RemediationState[] = [
  'overdue', 'due_soon', 'on_track', 'not_assigned', 'no_deadline', 'deferred', 'closed',
];
export const REMEDIATION_STATE_LABEL: Record<RemediationState, string> = {
  overdue: 'Overdue',
  due_soon: 'Due soon',
  on_track: 'On track',
  not_assigned: 'Not assigned',
  no_deadline: 'No deadline',
  deferred: 'Deferred',
  closed: REPORTED_FIXED,
};
/** What each state means, for the chip's tooltip. */
export const REMEDIATION_STATE_HELP: Record<RemediationState, string> = {
  overdue: 'Open, and past the remediation deadline',
  due_soon: 'Open, and the deadline is within the warning window',
  on_track: 'Open, assigned, and the deadline is further away',
  not_assigned: 'Open with no assigned date: the clock has not started',
  no_deadline: 'Open, and this severity has no remediation timeline',
  deferred: 'Deferred: the clock is stopped',
  closed: 'The contact reported it fixed. The assessment’s own conclusion is the endpoint’s status, which this does not change',
};
export const isRemediationState = (v: string | null): v is RemediationState =>
  v != null && (REMEDIATION_STATES as string[]).includes(v);

const days = (n: number): string => `${n.toLocaleString()} day${n === 1 ? '' : 's'}`;

/** The second line of the Due cell: how far the deadline is, in words. */
export const dueDistance = (row: Pick<RemediationRow, 'state' | 'days_left' | 'closed_days_late'>): string | null => {
  if (row.state === 'closed') {
    if (row.closed_days_late == null) return null;
    return row.closed_days_late === 0 ? 'reported fixed on time' : `reported fixed ${days(row.closed_days_late)} late`;
  }
  if (row.days_left == null) return null;
  if (row.days_left < 0) return `${days(-row.days_left)} overdue`;
  return row.days_left === 0 ? 'due today' : `in ${days(row.days_left)}`;
};

/** Text colour for a state that needs someone; always shown WITH its words. */
export const stateTone = (state: RemediationState): string =>
  (state === 'overdue' ? 'text-destructive' : state === 'due_soon' ? 'text-warning' : 'text-muted-foreground');

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'];
const SEVERITY_WORD: Record<string, string> = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Informational',
};
export const severityWord = (severity: string): string => SEVERITY_WORD[severity] ?? severity;

/** "Critical and High 30 days · Medium 90 · Low 120 · Informational none". */
export const timelineSummary = (policy: Pick<RemediationPolicy, 'days'>): string => {
  const groups: Array<{ value: number | null; names: string[] }> = [];
  SEVERITY_ORDER.forEach((severity) => {
    const value = policy.days[severity] ?? null;
    const last = groups[groups.length - 1];
    if (last && last.value === value) last.names.push(severityWord(severity));
    else groups.push({ value, names: [severityWord(severity)] });
  });
  return groups
    .map((g) => `${g.names.join(' and ')} ${g.value == null ? 'no deadline' : days(g.value)}`)
    .join(' · ');
};

/** The deadline a row WOULD have — the edit dialog's preview.  Dates are
 *  plain YYYY-MM-DD and are moved in UTC so no time zone shifts the day. */
export const previewDueOn = (
  policy: Pick<RemediationPolicy, 'days'> | null, severity: string, assignedOn: string,
): string | null => {
  const span = policy?.days[severity.toLowerCase()];
  if (span == null || !/^\d{4}-\d{2}-\d{2}$/.test(assignedOn)) return null;
  const at = new Date(`${assignedOn}T00:00:00Z`);
  if (Number.isNaN(at.getTime())) return null;
  at.setUTCDate(at.getUTCDate() + span);
  return at.toISOString().slice(0, 10);
};

/** The policy's due date in words, with where it comes from — the edit
 *  dialog's read-only line: "Nov 4, 2026 — critical, 30 days from Oct 5, 2026". */
export const policyDueLine = (
  policy: Pick<RemediationPolicy, 'days'> | null, severity: string, assignedOn: string,
): string => {
  const word = severityWord(severity.toLowerCase());
  if (policy != null && policy.days[severity.toLowerCase()] == null) {
    return `No due date: ${word.toLowerCase()} findings have no remediation timeline.`;
  }
  const due = previewDueOn(policy, severity, assignedOn);
  if (!due) return 'No due date yet: it counts from the assigned date.';
  return `${formatDate(due)} — ${word.toLowerCase()}, ${days(policy?.days[severity.toLowerCase()] ?? 0)} from ${formatDate(assignedOn)}`;
};

/** The work list's search (`?q=`): at least two characters, at most 200. */
export const SEARCH_MIN = 2;
export const SEARCH_MAX = 200;
export const searchParam = (raw: string | null): string => {
  const text = (raw ?? '').trim().slice(0, SEARCH_MAX);
  return text.length >= SEARCH_MIN ? text : '';
};

/** Today as the reader's calendar says it (the date inputs' own notion). */
export const localToday = (now: Date = new Date()): string => {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

/** A field name from a timeline entry, as the page says it. */
export const REMEDIATION_FIELD_LABEL: Record<string, string> = {
  contact_email: 'Contact',
  contact_name: 'Contact name',
  team: 'Team',
  notified_on: 'Assigned on',
  status: 'Status',
  closed_on: 'Reported fixed on',
  due_override_on: 'Due date',
  deferred_review_on: 'Review on',
  severity: 'Severity',
  finding: 'Finding',
};

export const isRemediationStatus = (v: string | null): v is RemediationStatus =>
  v != null && (REMEDIATION_STATUSES as string[]).includes(v);
export const isRemediationGroup = (v: string | null): v is RemediationGroup =>
  v != null && (REMEDIATION_GROUPS as string[]).includes(v);

/** What the edit dialog holds: text as typed, and which fields were touched. */
export interface RemediationDraft {
  contact_email: string;
  contact_name: string;
  team: string;
  notified_on: string;
  /** '' = leave as it is (several rows that differ). */
  status: RemediationStatus | '';
  closed_on: string;
  /** A due date set by hand; '' = the policy's date (or, for several rows
   *  that differ, leave each as it is). */
  due_override_on: string;
  /** Several rows: put every one back on the policy's date, whatever each holds. */
  due_override_cleared: boolean;
  /** With the status Deferred: the day to look at it again. */
  deferred_review_on: string;
  note: string;
}

const shared = <K extends keyof RemediationRow>(rows: RemediationRow[], key: K): RemediationRow[K] | undefined => {
  const first = rows[0]?.[key] ?? null;
  return rows.every((r) => (r[key] ?? null) === first) ? (first as RemediationRow[K]) : undefined;
};

/** The dialog opens on what the selected rows AGREE on; a field they differ
 *  on starts empty and is not sent unless it is filled in. */
export const draftFor = (rows: RemediationRow[]): RemediationDraft => ({
  contact_email: shared(rows, 'contact_email') ?? '',
  contact_name: shared(rows, 'contact_name') ?? '',
  team: shared(rows, 'team') ?? '',
  notified_on: shared(rows, 'notified_on') ?? '',
  status: shared(rows, 'status') ?? '',
  closed_on: shared(rows, 'closed_on') ?? '',
  due_override_on: shared(rows, 'due_override_on') ?? '',
  due_override_cleared: false,
  deferred_review_on: shared(rows, 'deferred_review_on') ?? '',
  note: '',
});

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Is this draft making rows deferred, or changing a deferral's review date? */
const writesDeferral = (opened: RemediationDraft, draft: RemediationDraft): boolean =>
  draft.status === 'deferred'
  && (opened.status !== 'deferred' || draft.deferred_review_on.trim() !== opened.deferred_review_on.trim());

/** Why the draft cannot be saved, or null.  With `opened` (what the dialog
 *  opened on) and `today` (the SERVER's day) it also checks a deferral. */
export const draftProblem = (
  draft: RemediationDraft, opened?: RemediationDraft, today?: string,
): string | null => {
  const email = draft.contact_email.trim();
  if (email && !EMAIL.test(email)) return 'The contact must be an email address.';
  if (draft.closed_on && draft.status !== 'closed') return 'A “Reported fixed on” date goes with the status Reported fixed.';
  if (opened && writesDeferral(opened, draft)) {
    const review = draft.deferred_review_on.trim();
    if (!review) return 'A deferral needs a “Review on” date.';
    if (today && review < today) return 'The “Review on” date must be today or later.';
  }
  return null;
};

/** Why a note for the timeline is required for these changes, or null.  A
 *  due date set by hand and a deferral are decisions somebody will be asked
 *  about; the server refuses them without the reason. */
export const noteRequirement = (changes: RemediationFields): string | null => {
  if ('due_override_on' in changes) {
    return changes.due_override_on == null
      ? 'Going back to the policy’s due date needs a note for the timeline: say why.'
      : 'A due date set by hand needs a note for the timeline: say why.';
  }
  if (changes.status === 'deferred' || 'deferred_review_on' in changes) {
    return 'A deferral needs a note for the timeline: say why, and what the review will look at.';
  }
  return null;
};

/** Something worth a second look that does not stop the save: a fix dated
 *  before its contact was told is possible, and is left out of "average
 *  time to close" on Oversight (seen in the browser, 5.337.1). */
export const draftCaution = (draft: RemediationDraft): string | null =>
  (draft.status === 'closed' && draft.closed_on && draft.notified_on && draft.closed_on < draft.notified_on
    ? 'The “Reported fixed on” date is before the assigned date. It is saved as entered, and left out of the average time to a reported fix.'
    : null);

/**
 * The fields to send: only those whose value differs from what the dialog
 * opened on.  An emptied field is sent as null (cleared); a field the rows
 * disagreed on and nobody filled in is left out, so it stays as it is on
 * each row.
 */
export const draftChanges = (opened: RemediationDraft, draft: RemediationDraft): RemediationFields => {
  const out: RemediationFields = {};
  const text = (key: 'contact_email' | 'contact_name' | 'team' | 'notified_on' | 'closed_on') => {
    const before = opened[key].trim();
    const now = draft[key].trim();
    if (now !== before) out[key] = now || null;
  };
  text('contact_email');
  text('contact_name');
  text('team');
  text('notified_on');
  text('closed_on');
  if (draft.status && draft.status !== opened.status) out.status = draft.status;
  // The due date set by hand: a new date, or back to the policy's (null).
  // "Back to the policy's date" on a selection is sent to every row, because
  // the dialog opened blank on rows that differ.
  if (draft.due_override_cleared) out.due_override_on = null;
  else {
    const before = opened.due_override_on.trim();
    const now = draft.due_override_on.trim();
    if (now !== before) out.due_override_on = now || null;
  }
  // The review date belongs to Deferred only (leaving Deferred clears it on
  // the server), and a row that BECOMES deferred always carries it.
  if (draft.status === 'deferred') {
    const before = opened.deferred_review_on.trim();
    const now = draft.deferred_review_on.trim();
    if (now !== before) out.deferred_review_on = now || null;
    else if (now && opened.status !== 'deferred') out.deferred_review_on = now;
  }
  return out;
};

/**
 * One apply row per selected finding on a host.  The note is a line on the
 * HOST's timeline, so it goes once per host — on that host's first selected
 * row — however many of its findings are selected.
 */
export const applyRowsFor = (
  rows: RemediationRow[], changes: RemediationFields, note: string, requestKey: string,
): RemediationApplyRow[] => {
  const body = note.trim();
  // A due date set by hand and a deferral must carry their reason IN THE SAME
  // ROW (the server refuses the row otherwise), so then every row has the
  // note, under its own key.
  const everyRow = noteRequirement(changes) != null;
  const noted = new Set<number>();
  const out: RemediationApplyRow[] = [];
  for (const r of rows) {
    const withNote = !!body && (everyRow || !noted.has(r.host_id));
    noted.add(r.host_id);
    // A row with nothing to change and no note of its own has nothing to send.
    if (!withNote && Object.keys(changes).length === 0) continue;
    out.push({
      finding_host_id: r.finding_host_id,
      ...changes,
      ...(withNote
        ? { notes: [{ body, request_key: everyRow ? `${requestKey}:${r.finding_host_id}` : requestKey }] }
        : {}),
    });
  }
  return out;
};

/** Rows-per-page choices; the list route takes at most 200. */
export const REMEDIATION_PAGE_SIZES = [25, 100, 200];
export const remediationPageSize = (raw: string | null): number => {
  const n = Number(raw);
  return REMEDIATION_PAGE_SIZES.includes(n) ? n : REMEDIATION_PAGE_SIZE;
};

/** A positive whole id from the URL, or null. */
export const idParam = (raw: string | null): number | null => {
  const n = Number(raw);
  return raw != null && Number.isInteger(n) && n > 0 ? n : null;
};

/** A timeline entry as the panel shows it: a note, or the changes ONE save
 *  made to one finding on the host. */
export interface TimelineGroup<E> {
  first: E;
  /** Every event of the group, the first included (one for a note). */
  events: E[];
}

interface Groupable {
  kind: 'note' | 'change' | 'follow_up' | 'report';
  finding_host_id: number | null;
  finding_title: string | null;
  author: string | null;
  agent_session_id: number | null;
  recorded_at: string;
}

/**
 * One save writes an entry per field it changed, all in one transaction (one
 * recorded time).  Shown one by one, a single edit read as five entries each
 * repeating the finding's title; consecutive changes of one save to one
 * finding are one entry here.  Notes are never grouped.
 */
export function groupTimeline<E extends Groupable>(events: E[]): Array<TimelineGroup<E>> {
  const groups: Array<TimelineGroup<E>> = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    const same = last && event.kind === 'change' && last.first.kind === 'change'
      && last.first.recorded_at === event.recorded_at
      && last.first.finding_host_id === event.finding_host_id
      && last.first.finding_title === event.finding_title
      && last.first.author === event.author
      && last.first.agent_session_id === event.agent_session_id;
    if (same) last.events.push(event); else groups.push({ first: event, events: [event] });
  }
  return groups;
}

export const hasChanges =(changes: RemediationFields, note: string): boolean =>
  Object.keys(changes).length > 0 || note.trim().length > 0;


// --- one Deadline column (5.341.0) ---------------------------------------------

/** What the Deadline cell says: the state and the distance in ONE phrase, the
 *  date under it.  "Overdue" beside "65 days overdue" said it twice. */
export interface DeadlineCell {
  primary: string;
  /** A date (ISO) to print under it, with words after it when there are any. */
  date: string | null;
  after: string | null;
  tone: string;
  /** A due date set by hand: a short second phrase with the policy's date,
   *  and the whole sentence for `title`. */
  source?: { text: string; title: string };
}

/** "set by hand · policy Nov 4" — only for a deadline that is still running. */
const overrideSource = (
  row: Pick<RemediationRow, 'state' | 'deadline_source' | 'policy_due_on'>,
): DeadlineCell['source'] => {
  if (row.deadline_source !== 'override' || !['overdue', 'due_soon', 'on_track'].includes(row.state)) return undefined;
  return row.policy_due_on
    ? {
      text: `set by hand · policy ${formatDate(row.policy_due_on)}`,
      title: `This due date was set by hand. The policy’s date is ${formatDate(row.policy_due_on)}.`,
    }
    : {
      text: 'set by hand · no policy date',
      title: 'This due date was set by hand. The policy gives this finding no due date.',
    };
};

export const deadlineCell = (
  row: Pick<RemediationRow, 'state' | 'due_on' | 'days_left' | 'closed_days_late' | 'closed_on'>
    & Partial<Pick<RemediationRow, 'deadline_source' | 'policy_due_on' | 'deferred_review_on' | 'deferral_review_due'>>,
): DeadlineCell => {
  const cell = deadlineState(row);
  const source = overrideSource(row);
  return source ? { ...cell, source } : cell;
};

const deadlineState = (
  row: Pick<RemediationRow, 'state' | 'due_on' | 'days_left' | 'closed_days_late' | 'closed_on'>
    & Partial<Pick<RemediationRow, 'deferred_review_on' | 'deferral_review_due'>>,
): DeadlineCell => {
  const quiet = 'text-muted-foreground';
  switch (row.state) {
    case 'deferred':
      // The clock is stopped; what matters is when somebody looks again.  The
      // server says when that day has come — this never compares dates.
      if (row.deferral_review_due) {
        return row.deferred_review_on
          ? { primary: 'Deferred · review due', date: row.deferred_review_on, after: null, tone: 'font-medium text-warning' }
          : { primary: 'Deferred · no review date', date: null, after: null, tone: 'font-medium text-warning' };
      }
      return row.deferred_review_on
        ? { primary: `Deferred · review ${formatDate(row.deferred_review_on)}`, date: null, after: null, tone: quiet }
        : { primary: REMEDIATION_STATE_LABEL.deferred, date: null, after: null, tone: quiet };
    case 'overdue':
      return { primary: `${days(-(row.days_left ?? 0))} overdue`, date: row.due_on, after: null, tone: 'font-medium text-destructive' };
    case 'due_soon':
      return {
        primary: row.days_left === 0 ? 'Due today' : `Due in ${days(row.days_left ?? 0)}`,
        date: row.due_on, after: null, tone: 'font-medium text-warning',
      };
    case 'on_track':
      return { primary: `Due in ${days(row.days_left ?? 0)}`, date: row.due_on, after: null, tone: '' };
    case 'closed': {
      const late = row.closed_days_late;
      return {
        primary: late == null ? REPORTED_FIXED : late === 0 ? `${REPORTED_FIXED} on time` : `${REPORTED_FIXED} ${days(late)} late`,
        date: row.closed_on, after: null, tone: late != null && late > 0 ? 'text-warning' : quiet,
      };
    }
    default:
      return { primary: REMEDIATION_STATE_LABEL[row.state], date: null, after: null, tone: quiet };
  }
};

export const OVERDUE_BANDS: OverdueBand[] = ['1-7', '8-30', '31-90', '90+'];
export const OVERDUE_BAND_LABEL: Record<OverdueBand, string> = {
  '1-7': '1–7 days', '8-30': '8–30 days', '31-90': '31–90 days', '90+': 'Over 90 days',
};
export const isOverdueBand = (v: string | null): v is OverdueBand =>
  v != null && (OVERDUE_BANDS as string[]).includes(v);

// --- handing it to someone else --------------------------------------------------

/** A CSV cell: quoted when it must be, and never a formula (a leading = + - @
 *  or a tab / CR would run in a spreadsheet). */
export const csvCell = (value: string | number | null | undefined): string => {
  if (typeof value === 'number') return String(value);       // "-8" days left is a number, not a formula
  let text = value == null ? '' : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

/** The assessor's endpoint status in the finding page's words (there `open`
 *  reads "Still present", so it is never taken for the record's Open). */
const ENDPOINT_STATUS_WORD: Record<string, string> = {
  open: 'Still present', remediated: 'Remediated', retest: 'Retest', false_positive: 'False positive',
};
const endpointStatusWord = (status: string): string => ENDPOINT_STATUS_WORD[status] ?? status;

const CSV_COLUMNS: Array<[string, (r: RemediationRow) => string | number | null]> = [
  ['Project', (r) => r.project_name],
  ['Host', (r) => r.ip_address],
  ['Host name', (r) => r.hostname],
  ['Finding', (r) => r.finding_title],
  ['Severity', (r) => r.severity],
  ['State', (r) => REMEDIATION_STATE_LABEL[r.state]],
  ['Assigned on', (r) => r.notified_on],
  ['Due on', (r) => r.due_on],
  ['Days left', (r) => r.days_left],
  ['Due date from', (r) => (r.deadline_source === 'override' ? 'Set by hand' : r.deadline_source === 'policy' ? 'Policy' : null)],
  ['Policy due date', (r) => (r.deadline_source === 'override' ? r.policy_due_on ?? null : null)],
  ['Deferral review on', (r) => r.deferred_review_on ?? null],
  ['Reported fixed on', (r) => r.closed_on],
  ['Days late when reported fixed', (r) => r.closed_days_late],
  ['Assessment status on this host', (r) => endpointStatusWord(r.endpoint_status)],
  ['Record and assessment', (r) => (r.verification ? REMEDIATION_VERIFICATION_LABEL[r.verification] : null)],
  ['Contact', (r) => r.contact_name],
  ['Contact email', (r) => r.contact_email],
  ['Team', (r) => r.team],
  ['Last followed up', (r) => r.last_follow_up_on],
];

export const remediationCsv = (rows: RemediationRow[]): string =>
  [CSV_COLUMNS.map(([name]) => csvCell(name)).join(','),
    ...rows.map((r) => CSV_COLUMNS.map(([, get]) => csvCell(get(r))).join(','))].join('\r\n');

/** The page as plain text, for a status mail or a slide's notes. */
export const remediationSummary = (
  page: Pick<RemediationPage, 'state_counts' | 'severity_counts' | 'overdue_ages' | 'not_followed_up' | 'not_followed_up_days' | 'as_of'>
    & Partial<Pick<RemediationPage, 'verification_counts' | 'flag_counts'>>,
  options: { where: string; dueSoonDays: number; timeline?: string },
): string => {
  const c = page.state_counts;
  const open = c.overdue + c.due_soon + c.on_track + c.not_assigned + c.no_deadline;
  const bySeverity = (key: 'overdue' | 'due_soon') => SEVERITY_ORDER
    .filter((s) => (page.severity_counts[s]?.[key] ?? 0) > 0)
    .map((s) => `${page.severity_counts[s][key].toLocaleString()} ${severityWord(s).toLowerCase()}`).join(', ');
  const lines = [
    `Remediation deadlines ${options.where}, as of ${page.as_of}`,
    '',
    `Open findings on hosts: ${open.toLocaleString()}`,
    `- Overdue: ${c.overdue.toLocaleString()}${c.overdue ? ` (${bySeverity('overdue')})` : ''}`,
    `- Due within ${options.dueSoonDays} days: ${c.due_soon.toLocaleString()}${c.due_soon ? ` (${bySeverity('due_soon')})` : ''}`,
    `- On track: ${c.on_track.toLocaleString()}`,
    `- Not assigned (no deadline running): ${c.not_assigned.toLocaleString()}`,
  ];
  if (c.no_deadline) lines.push(`- No deadline for their severity: ${c.no_deadline.toLocaleString()}`);
  if (c.overdue) {
    lines.push('', 'How late the overdue ones are:',
      ...OVERDUE_BANDS.filter((b) => page.overdue_ages[b] > 0)
        .map((b) => `- ${OVERDUE_BAND_LABEL[b]}: ${page.overdue_ages[b].toLocaleString()}`));
  }
  if (c.overdue + c.due_soon > 0) {
    lines.push('', `Overdue or due soon with no follow-up in ${page.not_followed_up_days} days: ${page.not_followed_up.toLocaleString()}`);
  }
  lines.push('', `Deferred: ${c.deferred.toLocaleString()}   ${REPORTED_FIXED}: ${c.closed.toLocaleString()}`);
  const gaps = REMEDIATION_VERIFICATIONS.filter((v) => (page.verification_counts?.[v] ?? 0) > 0);
  const flags = REMEDIATION_FLAGS.filter((f) => (page.flag_counts?.[f] ?? 0) > 0);
  if (gaps.length + flags.length > 0) {
    lines.push('',
      ...gaps.map((v) => `${REMEDIATION_VERIFICATION_LABEL[v]}: ${(page.verification_counts?.[v] ?? 0).toLocaleString()}`),
      ...flags.map((f) => `${REMEDIATION_FLAG_LABEL[f]}: ${(page.flag_counts?.[f] ?? 0).toLocaleString()}`));
  }
  if (options.timeline) lines.push('', `Timeline: ${options.timeline}, counted from the day a finding is assigned.`);
  return lines.join('\n');
};
