/**
 * Remediation tracking (5.335.0): the page's vocabulary and the pure half of
 * its edit dialog.  Client-free — the page and its tests import this without
 * the API client.
 */
import type {
  OverdueBand, RemediationApplyRow, RemediationFields, RemediationGroup, RemediationPage, RemediationPolicy,
  RemediationRow, RemediationState, RemediationStatus,
} from '../services/api';

export const REMEDIATION_PAGE_SIZE = 25;

export const REMEDIATION_STATUSES: RemediationStatus[] = ['open', 'closed', 'deferred'];
export const REMEDIATION_STATUS_LABEL: Record<RemediationStatus, string> = {
  open: 'Open',
  closed: 'Closed',
  deferred: 'Deferred',
};

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
  closed: 'Closed',
};
/** What each state means, for the chip's tooltip. */
export const REMEDIATION_STATE_HELP: Record<RemediationState, string> = {
  overdue: 'Open, and past the remediation deadline',
  due_soon: 'Open, and the deadline is within the warning window',
  on_track: 'Open, assigned, and the deadline is further away',
  not_assigned: 'Open with no assigned date: the clock has not started',
  no_deadline: 'Open, and this severity has no remediation timeline',
  deferred: 'Deferred: the clock is stopped',
  closed: 'The contact reported it fixed',
};
export const isRemediationState = (v: string | null): v is RemediationState =>
  v != null && (REMEDIATION_STATES as string[]).includes(v);

const days = (n: number): string => `${n.toLocaleString()} day${n === 1 ? '' : 's'}`;

/** The second line of the Due cell: how far the deadline is, in words. */
export const dueDistance = (row: Pick<RemediationRow, 'state' | 'days_left' | 'closed_days_late'>): string | null => {
  if (row.state === 'closed') {
    if (row.closed_days_late == null) return null;
    return row.closed_days_late === 0 ? 'closed on time' : `closed ${days(row.closed_days_late)} late`;
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
  closed_on: 'Closed date',
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
  note: string;
}

const shared = <K extends keyof RemediationRow>(rows: RemediationRow[], key: K): RemediationRow[K] | undefined => {
  const first = rows[0]?.[key];
  return rows.every((r) => r[key] === first) ? first : undefined;
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
  note: '',
});

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Why the draft cannot be saved, or null. */
export const draftProblem = (draft: RemediationDraft): string | null => {
  const email = draft.contact_email.trim();
  if (email && !EMAIL.test(email)) return 'The contact must be an email address.';
  if (draft.closed_on && draft.status !== 'closed') return 'A closed date goes with the status Closed.';
  return null;
};

/** Something worth a second look that does not stop the save: a fix dated
 *  before its contact was told is possible, and is left out of "average
 *  time to close" on Oversight (seen in the browser, 5.337.1). */
export const draftCaution = (draft: RemediationDraft): string | null =>
  (draft.status === 'closed' && draft.closed_on && draft.notified_on && draft.closed_on < draft.notified_on
    ? 'The closed date is before the assigned date. It is saved as entered, and left out of the average time to close.'
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
  const noted = new Set<number>();
  const out: RemediationApplyRow[] = [];
  for (const r of rows) {
    const withNote = !!body && !noted.has(r.host_id);
    noted.add(r.host_id);
    // A row with nothing to change and no note of its own has nothing to send.
    if (!withNote && Object.keys(changes).length === 0) continue;
    out.push({
      finding_host_id: r.finding_host_id,
      ...changes,
      ...(withNote ? { notes: [{ body, request_key: requestKey }] } : {}),
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
}
export const deadlineCell = (
  row: Pick<RemediationRow, 'state' | 'due_on' | 'days_left' | 'closed_days_late' | 'closed_on'>,
): DeadlineCell => {
  const quiet = 'text-muted-foreground';
  switch (row.state) {
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
        primary: late == null ? 'Closed' : late === 0 ? 'Closed on time' : `Closed ${days(late)} late`,
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
  ['Closed on', (r) => r.closed_on],
  ['Days late at close', (r) => r.closed_days_late],
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
  page: Pick<RemediationPage, 'state_counts' | 'severity_counts' | 'overdue_ages' | 'not_followed_up' | 'not_followed_up_days' | 'as_of'>,
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
  lines.push('', `Deferred: ${c.deferred.toLocaleString()}   Closed: ${c.closed.toLocaleString()}`);
  if (options.timeline) lines.push('', `Timeline: ${options.timeline}, counted from the day a finding is assigned.`);
  return lines.join('\n');
};
