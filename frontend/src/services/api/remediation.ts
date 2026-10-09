import { api, p } from './client';

/** Where the fix stands, as the contact reports it — not the finding's own
 *  status.  `closed` is the contact's claim and is shown as "Reported fixed". */
export type RemediationStatus = 'open' | 'closed' | 'deferred';
/** Where that record and the assessor's `endpoint_status` disagree (derived by the server). */
export type RemediationVerification = 'reported_fixed_not_retested' | 'remediated_record_open';
export type RemediationGroup = 'host' | 'finding' | 'contact' | 'due' | 'team';
/** How many days past its deadline an overdue row is. */
export type OverdueBand = '1-7' | '8-30' | '31-90' | '90+';
/** Where a row stands against its deadline (`remediation_policy.STATES`). */
export type RemediationState =
  | 'overdue' | 'due_soon' | 'on_track' | 'not_assigned' | 'no_deadline' | 'deferred' | 'closed';

/** This installation's remediation settings.  `days[severity]` null = no deadline.
 *  `time_zone` (an IANA name) is the zone whose calendar day is "today" for
 *  every deadline state; the server derives the states, the pages only show it. */
export interface RemediationPolicy {
  enabled: boolean;
  days: Record<string, number | null>;
  due_soon_days: number;
  time_zone: string;
}

export const getRemediationPolicy = async (signal?: AbortSignal): Promise<RemediationPolicy> =>
  (await api.get<RemediationPolicy>('/remediation-policy', { signal })).data;

export const updateRemediationPolicy = async (
  body: Partial<Pick<RemediationPolicy, 'enabled' | 'days' | 'due_soon_days' | 'time_zone'>>,
): Promise<RemediationPolicy> => (await api.put<RemediationPolicy>('/remediation-policy', body)).data;

/** The cross-project mount of one project's remediation routes: it also
 *  serves ARCHIVED projects, which the project routes refuse (410). */
const base = (projectId?: number): string =>
  (projectId == null ? p() : `/remediation-overview/projects/${projectId}`);

/** One finding on one host, with what was recorded about fixing it. */
export interface RemediationRow {
  finding_host_id: number;
  finding_id: number;
  project_id: number;
  project_name: string;
  finding_title: string;
  severity: string;
  /** The assessor's statuses, shown beside the tracking and never moved by it. */
  finding_status: string;
  endpoint_status: string;
  host_id: number;
  ip_address: string;
  hostname: string | null;
  contact_email: string | null;
  contact_name: string | null;
  /** The group that owns the fix (free text). */
  team: string | null;
  /** ISO dates (YYYY-MM-DD), entered by hand. */
  notified_on: string | null;
  status: RemediationStatus;
  closed_on: string | null;
  updated_at: string | null;
  state: RemediationState;
  /** The deadline: derived while open, frozen at close; null when there is none. */
  due_on: string | null;
  /** Days to the deadline of an open row; negative once overdue. */
  days_left: number | null;
  /** Days after its deadline a row was closed (0 = on time); null = it had none. */
  closed_days_late: number | null;
  last_follow_up_on: string | null;
  /** The gap between `status` and `endpoint_status`; null where they agree. */
  verification: RemediationVerification | null;
}

export interface RemediationPage {
  items: RemediationRow[];
  total: number;
  has_more: boolean;
  limit: number;
  offset: number;
  /** Over the whole selection, before the status filter. */
  status_counts: Record<RemediationStatus, number>;
  state_counts: Record<RemediationState, number>;
  /** Rows where the record and the assessment disagree, over the selection,
   *  before the state, status and verification filters. */
  verification_counts: Record<RemediationVerification, number>;
  /** Overdue and due soon per severity, over the selection BEFORE the severity filter. */
  severity_counts: Record<string, { overdue: number; due_soon: number }>;
  /** The overdue rows by days past their deadline; adds up to `state_counts.overdue`. */
  overdue_ages: Record<OverdueBand, number>;
  /** Overdue and due-soon rows nobody followed up in `not_followed_up_days` (or ever). */
  not_followed_up: number;
  not_followed_up_days: number;
  /** The server's day the states were decided on. */
  as_of: string;
}

export interface RemediationQuery {
  status?: RemediationStatus;
  state?: RemediationState[];
  severity?: string;
  team?: string;
  overdue_band?: OverdueBand;
  no_follow_up_days?: number;
  verification?: RemediationVerification;
  contact?: string;
  /** Exactly this contact. */
  contact_email?: string;
  /** Cross-project list only: one of the caller's projects. */
  project_id?: number;
  unassigned?: boolean;
  host_id?: number;
  finding_id?: number;
  group?: RemediationGroup;
  limit?: number;
  offset?: number;
}

// `state` repeats (`state=overdue&state=due_soon`), which is how FastAPI reads a list.
const serialize = (query: object): URLSearchParams => {
  const out = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value == null || value === '' || value === false) return;
    if (Array.isArray(value)) value.forEach((v) => out.append(key, String(v)));
    else out.append(key, String(value));
  });
  return out;
};

export const listRemediation = async (query: RemediationQuery = {}, signal?: AbortSignal): Promise<RemediationPage> =>
  (await api.get<RemediationPage>(`${p()}/remediation`, { params: serialize(query), signal })).data;

/** The same rows across every project the caller administers (archived included). */
export const listRemediationOverview = async (
  query: RemediationQuery = {}, signal?: AbortSignal,
): Promise<RemediationPage> =>
  (await api.get<RemediationPage>('/remediation-overview', { params: serialize(query), signal })).data;

export interface RemediationProjectRow {
  project_id: number;
  name: string;
  archived: boolean;
  states: Record<RemediationState, number>;
}
export interface RemediationProjects {
  items: RemediationProjectRow[];
  totals: Record<RemediationState, number>;
  as_of: string;
  policy: RemediationPolicy;
}
export const listRemediationProjects = async (signal?: AbortSignal): Promise<RemediationProjects> =>
  (await api.get<RemediationProjects>('/remediation-overview/projects', { signal })).data;

/** One contact with their findings on hosts by state; most overdue first. */
export interface RemediationContact {
  contact_email: string;
  contact_name: string | null;
  total: number;
  open: number;
  overdue: number;
  due_soon: number;
  on_track: number;
  deferred: number;
  closed: number;
  last_follow_up_on: string | null;
  projects: number;
}
/** `projectId` undefined = the current project; `'all'` = every project the caller administers. */
export const listRemediationContacts = async (
  scope?: 'all', projectId?: number, signal?: AbortSignal,
): Promise<RemediationContact[]> =>
  (await api.get<{ items: RemediationContact[] }>(
    scope === 'all' ? '/remediation-overview/contacts' : `${p()}/remediation/contacts`,
    { params: scope === 'all' && projectId != null ? { project_id: projectId } : undefined, signal },
  )).data.items;

/** One contact's remediation list, prepared as a document on the report worker. */
export type ContactReportFormat = 'contact-docx' | 'contact-html';
export interface ContactReportJob {
  id: number;
  /** The report job's status as the server stores it (`report_jobs.status`). */
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled';
  format: ContactReportFormat;
  message: string | null;
  error: string | null;
  filename: string | null;
  contact_email: string | null;
  created_at: string | null;
  /** Images the finished document left out because their finding also
   *  affects a system that is not this contact's. */
  images_withheld: number;
  /** The file exists and can be downloaded. */
  ready: boolean;
}
export const prepareContactReport = async (
  body: { contact_email: string; format: ContactReportFormat }, projectId?: number,
): Promise<ContactReportJob> =>
  (await api.post<ContactReportJob>(`${base(projectId)}/remediation/contact-report`, body)).data;

export const getContactReport = async (jobId: number, projectId?: number, signal?: AbortSignal): Promise<ContactReportJob> =>
  (await api.get<ContactReportJob>(`${base(projectId)}/remediation/contact-report/${jobId}`, { signal })).data;

export const downloadContactReport = async (jobId: number, projectId?: number): Promise<Blob> =>
  (await api.get(`${base(projectId)}/remediation/contact-report/${jobId}/download`, { responseType: 'blob' })).data;

/** One team with its findings on hosts by state; `team: null` = a contact and no team. */
export interface RemediationTeam {
  team: string | null;
  total: number;
  open: number;
  overdue: number;
  due_soon: number;
  on_track: number;
  deferred: number;
  closed: number;
  contacts: number;
  projects: number;
}
export const listRemediationTeams = async (
  scope?: 'all', projectId?: number, signal?: AbortSignal,
): Promise<RemediationTeam[]> =>
  (await api.get<{ items: RemediationTeam[] }>(
    scope === 'all' ? '/remediation-overview/teams' : `${p()}/remediation/teams`,
    { params: scope === 'all' && projectId != null ? { project_id: projectId } : undefined, signal },
  )).data.items;

/** The recorded history: a day nobody recorded is absent, never a zero. */
export interface RemediationTrend {
  as_of: string;
  days: number;
  daily: Array<{ day: string; overdue: number; due_soon: number; on_track: number; not_assigned: number; deferred: number; closed: number }>;
  closed_by_month: Array<{ month: string; on_time: number; late: number; no_deadline: number }>;
}
export const getRemediationTrend = async (
  scope?: 'all', projectId?: number, signal?: AbortSignal,
): Promise<RemediationTrend> =>
  (await api.get<RemediationTrend>(
    scope === 'all' ? '/remediation-overview/trend' : `${p()}/remediation/trend`,
    { params: scope === 'all' && projectId != null ? { project_id: projectId } : undefined, signal },
  )).data;

/** What to say to one contact: their at-risk rows and the message as text. */
export interface RemediationFollowUp {
  contact_email: string;
  contact_name: string | null;
  as_of: string;
  overdue: number;
  due_soon: number;
  items: RemediationRow[];
  has_more: boolean;
  project_ids: number[];
  text: string;
}
export const getRemediationFollowUp = async (
  contactEmail: string, scope?: 'all', projectId?: number, signal?: AbortSignal,
): Promise<RemediationFollowUp> =>
  (await api.get<RemediationFollowUp>(
    scope === 'all' ? '/remediation-overview/follow-up' : `${p()}/remediation/follow-up`,
    { params: { contact_email: contactEmail, ...(scope === 'all' && projectId != null ? { project_id: projectId } : {}) }, signal },
  )).data;

export interface RemediationFollowUpResult {
  recorded: number;
  already_recorded: number;
  followed_up_on: string;
  finding_host_ids: number[];
}
export const recordRemediationFollowUp = async (
  body: { contact_email: string; followed_up_on?: string; note?: string; finding_host_ids?: number[] },
  projectId?: number,
): Promise<RemediationFollowUpResult> =>
  (await api.post<RemediationFollowUpResult>(`${base(projectId)}/remediation/follow-up`, body)).data;

/** The tracked fields.  A field left out is not touched; `null` clears it. */
export interface RemediationFields {
  contact_email?: string | null;
  contact_name?: string | null;
  team?: string | null;
  notified_on?: string | null;
  status?: RemediationStatus;
  closed_on?: string | null;
}

export interface RemediationNoteInput {
  body: string;
  occurred_at?: string;
  request_key?: string;
}

export interface RemediationApplyRow extends RemediationFields {
  finding_host_id: number;
  notes?: RemediationNoteInput[];
}

export interface RemediationChange {
  finding_host_id: number;
  field: string;
  from: string | null;
  to: string | null;
}

export interface RemediationApplyResult {
  dry_run: boolean;
  overwrite: boolean;
  summary: {
    targets: number;
    changed: number;
    unchanged: number;
    conflicts: number;
    notes_added: number;
    notes_already_recorded: number;
  };
  rows: Array<{ row: number; finding_host_ids: number[]; changed: RemediationChange[]; conflicts: RemediationChange[] }>;
}

/** The one write path.  The page's own edits replace what is there
 *  (`overwrite`), because the person editing is looking at it. */
export const applyRemediation = async (
  rows: RemediationApplyRow[], options: { overwrite?: boolean; dry_run?: boolean } = {}, projectId?: number,
): Promise<RemediationApplyResult> =>
  (await api.post<RemediationApplyResult>(`${base(projectId)}/remediation/apply`, { rows, ...options })).data;

export interface RemediationEvent {
  id: number;
  host_id: number;
  kind: 'note' | 'change' | 'follow_up' | 'report';
  finding_id: number | null;
  finding_host_id: number | null;
  finding_title: string | null;
  field: string | null;
  from: string | null;
  to: string | null;
  body: string | null;
  /** When it happened (a note may be backdated)… */
  occurred_at: string;
  /** …and when it was recorded. */
  recorded_at: string;
  edited_at: string | null;
  author: string | null;
  agent_session_id: number | null;
  can_modify: boolean;
}

export interface RemediationEventPage {
  items: RemediationEvent[];
  total: number;
  has_more: boolean;
}

export const listRemediationEvents = async (
  hostId: number, query: { finding_host_id?: number; limit?: number; offset?: number } = {}, signal?: AbortSignal,
  projectId?: number,
): Promise<RemediationEventPage> =>
  (await api.get<RemediationEventPage>(`${base(projectId)}/remediation/hosts/${hostId}/events`, { params: query, signal })).data;

export const addRemediationNote = async (
  body: { host_id: number; body: string; finding_host_id?: number; occurred_at?: string; request_key?: string },
  projectId?: number,
): Promise<RemediationEvent> =>
  (await api.post<RemediationEvent>(`${base(projectId)}/remediation/events`, body)).data;

export const deleteRemediationNote = async (eventId: number, projectId?: number): Promise<void> => {
  await api.delete(`${base(projectId)}/remediation/events/${eventId}`);
};
