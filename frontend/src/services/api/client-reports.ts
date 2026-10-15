/**
 * Client reports (v5.261.0) — the findings-first Quarto deliverable, its
 * history and its addenda.  Backend: app/api/v1/endpoints/client_reports.py.
 * A draft's preview is an ordinary report job (poll with getReportJob,
 * download with downloadReportJob); an issued report's files download here.
 */
import { api, projectPath } from './client';
import { saveBlob } from '../../utils/download';
import type { ReportJob } from '../api';

export type ClientReportKind = 'full' | 'addendum';
export type ClientReportStatus = 'draft' | 'issued' | 'superseded';
/** `qmd` is the Quarto source as a zip (report.qmd, data.json, filters, screenshots).
 *  No PDF (removed 5.293.0): the Word report carries the design and exports to PDF. */
export type ClientReportFormat = 'html' | 'docx' | 'qmd';
/** A stored file of an ISSUED report — a PDF issued before 5.293.0 still downloads. */
export type ReportFileFormat = ClientReportFormat | 'pdf';

export interface ReportTester {
  user_id?: number | null;
  name: string;
  role?: string | null;
  email?: string | null;
}

export interface ReportRecipient {
  name: string;
  email?: string | null;
}

export interface EngagementSettings {
  /** Owner, 2026-10-14 — the executive summary (Markdown) is the PROJECT's: a
   *  full report prints it unless it overrides it; an addendum's ("Summary of
   *  changes") is always its own. */
  executive_summary?: string | null;
  client_name: string | null;
  classification: string | null;
  engagement_type: string | null;
  testers: ReportTester[];
  distribution: ReportRecipient[];
  system_description: string | null;
  /** v5.263.0 — the other target lists ("if applicable"; Markdown). */
  applications?: string | null;
  thick_clients?: string | null;
  other_targets?: string | null;
  /** Who produced the report, and the disclaimer it closes with (Markdown).
   *  In the project's defaults, empty = the installation's. */
  author_name?: string | null;
  disclaimer?: string | null;
}

/** A draft's OVERRIDES (owner, 2026-10-14: every detail follows the project
 *  unless the report overrides it): only the keys it overrides.  A key absent
 *  (or null) follows the project; a list present — even `[]` — is overridden
 *  as a whole.  Sent to PATCH as the complete map: a key left out reverts. */
export type EngagementOverrides = Partial<EngagementSettings>;

/** The installation's author and disclaimer: what the project's defaults
 *  use for one they leave empty (`ReportProfile.inherited`). */
export interface InheritedReportDetails {
  author_name?: string | null;
  disclaimer?: string | null;
}

export interface ReportProfile extends EngagementSettings {
  template: string | null;
  updated_at?: string | null;
  /** No team is saved: `testers` is the project's analysts and admins. */
  testers_from_project?: boolean;
  /** The installation's values: what a detail this profile leaves empty uses. */
  inherited?: InheritedReportDetails;
}

/** An image the template itself expects (logo, cover art) — declared in its
 *  template.json, never finding evidence.  `path` is relative to
 *  `report-templates/<template name>/` on the server. */
export interface ReportTemplateAsset {
  id: string;
  path: string;
  label: string;
  description: string;
  /** Extra guidance from the template author (e.g. a Word header lives in reference.docx). */
  note: string;
  /** A missing required image blocks preview, issue and render. */
  required: boolean;
  formats: ClientReportFormat[];
  /** When installed, used in place of this template file (e.g. `reference.docx`). */
  replaces?: string | null;
  /** The file is available to the render: installed on the server or uploaded. */
  present: boolean;
  /** v5.311.0 — installed in the server's template folder. */
  installed?: boolean;
  /** Where the file the render uses comes from: an upload wins over the server's. */
  source?: 'uploaded' | 'installed' | null;
  /** png, jpeg, docx (uploadable); svg, gif, webp (server-installed only). */
  kind?: string;
  uploadable?: boolean;
  /** The template's guidance for an upload. */
  max_bytes?: number | null;
  min_width?: number | null;
  min_height?: number | null;
  /** "W:H" — a different shape is accepted with a warning. */
  aspect?: string | null;
  upload?: ReportTemplateAssetUpload | null;
}

/** The current upload of a template file (v5.311.0). */
export interface ReportTemplateAssetUpload {
  kind?: string | null;
  size?: number | null;
  width?: number | null;
  height?: number | null;
  sha256?: string | null;
  original_filename?: string | null;
  uploaded_at?: string | null;
  uploaded_by?: string | null;
}

/** The template after an upload or removal, and what to know about the file. */
export interface ReportTemplateAssetChange {
  template: ReportTemplate;
  warnings: string[];
}

/** Which evidence images a template prints (template.json → `images`): the
 *  written fields whose placed images it prints, and whether it prints the
 *  rest in a trailing Evidence block. */
export interface ReportTemplateImages {
  fields: string[];
  trailing: boolean;
}

export interface ReportTemplate {
  name: string;
  title: string;
  description: string;
  formats: ClientReportFormat[];
  assets?: ReportTemplateAsset[];
  images?: ReportTemplateImages;
  /** `contact` = the remediation list prepared for one contact: its files are
   *  managed here like any template's, but no client report is created with it. */
  kind?: 'client' | 'contact';
}

export interface ReportFile {
  format: ReportFileFormat;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string | null;
}

export interface ReportRef {
  id: number;
  number: number | null;
  title: string;
  status: ClientReportStatus;
  issued_at: string | null;
}

export interface ReportSummary {
  counts?: Record<'critical' | 'high' | 'medium' | 'low' | 'info' | 'total', number>;
  findings_shown?: number;
  under_investigation?: number;
  missing_text?: Array<{ id: number; ref: string; title: string; missing: string[] }>;
  /** v5.263.0 — report details still empty (printed as a highlighted TODO). */
  missing_details?: string[];
  /** Parallel to `missing_details`: each one's stable key (a
   *  `REQUIRED_DETAILS` key — `client_name`, `testers`, `project_dates`… — or
   *  `account_full_name`), which the checklist opens the field by.  Absent
   *  from a summary frozen before it. */
  missing_detail_keys?: string[];
  images?: number;
  /** Of `images`: placed inside a finding's written section by its author,
   *  and left for the trailing evidence block. Absent on a report issued
   *  before images could be placed.  What the AUTHORS did — where the report
   *  prints them is the three counts below. */
  images_placed?: number;
  images_unplaced?: number;
  /** Where THIS report's template prints the ticked images: inside a written
   *  section, in the trailing Evidence block, or nowhere (the brief prints no
   *  image; the worklist only those placed in the recommendation; an addendum
   *  lists a finding the client already has in one line).  The three add up
   *  to `images`.  Null when it could not be measured (the template is gone
   *  or broken), absent on a report issued before this — say nothing then. */
  images_printed?: number | null;
  images_trailing?: number | null;
  images_not_printed?: number | null;
  /** Why the unprinted ones print nowhere. */
  images_not_printed_reasons?: {
    /** On a finding the report lists without its details. */
    finding_not_detailed: number;
    /** Placed in a section this template does not print with images. */
    section_not_printed: number;
    /** Placed nowhere, and this template has no trailing Evidence block. */
    no_evidence_block: number;
  } | null;
  /** What the template declares it prints (template.json → `images`). */
  template_images?: ReportTemplateImages | null;
  images_skipped?: number;
  /** v5.316.0 — images in the report attached to an agent-written note. A warning, never a block. */
  agent_images?: number;
  /** v5.316.0 — reported findings with proposals nobody has decided yet. A warning, never a block. */
  pending_proposals?: Array<{ id: number; ref: string; title: string; count: number }>;
  /** Reported findings with images this report prints under the file's name,
   *  because nobody wrote a caption. A warning, never a block; absent from a
   *  report issued before it was counted. */
  uncaptioned_images?: Array<{ id: number; ref: string; title: string; count: number }>;
  /** v5.346.0 — reported findings whose written text names a BlueStick record
   *  ("Finding #277"), by section. A warning, never a block. */
  internal_references?: Array<{
    id: number; ref: string; title: string; fields: Array<{ field: string; phrases: string[] }>;
  }>;
  /** A report detail (the system description) that places an image the
   *  project no longer has, so the report prints nothing there. A warning,
   *  never a block; absent from a report read before it was counted. */
  unresolved_images?: Array<{ field: string; ids: number[] }>;
  /** v5.319.0 — the scope is over the template's cutoff: the report summarises it
   *  and names this file (with its SHA-256), which must be sent with the report. */
  scope_external?: {
    networks: number;
    domains: number;
    inline_max: number | null;
    domains_inline_max: number | null;
    file: { name: string; sha256: string; bytes: number } | null;
  } | null;
  delta?: {
    new_findings: number;
    findings_with_new_endpoints: number;
    withdrawn: number;
    /** Already-reported findings whose severity is different now.  Absent on
     *  reports issued before it was counted — read it as 0. */
    findings_with_changed_severity?: number;
  } | null;
  /** Test results the report prints as "how it was confirmed"… */
  evidence_records?: number;
  /** …those of findings the report lists without their details: not printed,
   *  and not in the report's data.  Null / absent: not measured. */
  evidence_records_not_printed?: number | null;
  /** …and how many of those an agent recorded.  A notice, never a block. */
  agent_evidence_records?: number;
  /** Notes printed as written before the image attached to them, under
   *  Evidence.  A notice, never a block; absent from a report issued before it. */
  evidence_notes?: number;
  /** The same notes by finding, each with how it begins (at most 200
   *  characters) — what the draft's page lists so a reviewer can read them. */
  evidence_note_findings?: Array<{ id: number; ref: string; title: string; notes: string[] }>;
  /** Set when the summary could not be built (e.g. an addendum lost its baseline). */
  error?: string;
}

export interface ClientReport {
  id: number;
  project_id: number;
  kind: ClientReportKind;
  status: ClientReportStatus;
  title: string;
  number: number | null;
  template: string;
  baseline: ReportRef | null;
  revision_of: ReportRef | null;
  superseded_by: ReportRef | null;
  /** The details this report overrides — only those keys. */
  settings: EngagementOverrides;
  /** The summary it prints (`effective.executive_summary`); a single read
   *  only — null on list rows. */
  executive_summary: string | null;
  template_fingerprint: string | null;
  quarto_version: string | null;
  render_status: 'pending' | 'done' | 'failed' | null;
  render_error: string | null;
  files: ReportFile[];
  created_by_name: string | null;
  issued_by_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  issued_at: string | null;
  summary: ReportSummary | null;
  /** A single read (null on list rows): every detail the report prints — a
   *  draft's overrides over what it follows, as the project stands now; an
   *  issued report's as it was issued. */
  effective?: EngagementSettings | null;
  /** A single DRAFT read: each detail's value were it not overridden (the
   *  project's default, else the installation's author / disclaimer, else the
   *  project's analysts and admins as the team).  Null for an issued report
   *  and for list rows. */
  followed?: EngagementSettings | null;
  /** A single DRAFT read: where each `followed` value comes from (null: set
   *  nowhere).  Null for an issued report and for list rows. */
  followed_from?: Partial<Record<keyof EngagementSettings, FollowedSource | null>> | null;
  can_edit: boolean;
  can_issue: boolean;
  /** Discard this draft: its creator or a project admin (on list rows too). */
  can_discard?: boolean;
}

/** Where a draft's followed detail comes from: the project's report
 *  defaults, the installation's (author, disclaimer), or the project's
 *  analysts and admins (the team). */
export type FollowedSource = 'project' | 'installation' | 'project_team';

export interface ClientReportList {
  items: ClientReport[];
  latest_issued_id: number | null;
  can_create: boolean;
  can_issue: boolean;
}

export interface ClientReportUpdate {
  title?: string;
  template?: string;
  baseline_report_id?: number;
  /** The details CHANGED in this save, each with its new override (`null` =
   *  follow the project; the executive summary is one of its keys).  A
   *  detail not sent keeps what is stored; one sent equal to what the draft
   *  follows follows it again. */
  settings?: EngagementOverrides;
}

const base = (projectId: number) => `${projectPath(projectId)}/client-reports`;

export const listClientReports = async (projectId: number, signal?: AbortSignal): Promise<ClientReportList> =>
  (await api.get<ClientReportList>(base(projectId), { signal })).data;

export const getClientReport = async (projectId: number, id: number, signal?: AbortSignal): Promise<ClientReport> =>
  (await api.get<ClientReport>(`${base(projectId)}/${id}`, { signal })).data;

export const createClientReport = async (projectId: number, body: {
  kind: ClientReportKind; title?: string; template?: string; baseline_report_id?: number;
}): Promise<ClientReport> => (await api.post<ClientReport>(base(projectId), body)).data;

export const updateClientReport = async (projectId: number, id: number, body: ClientReportUpdate): Promise<ClientReport> =>
  (await api.patch<ClientReport>(`${base(projectId)}/${id}`, body)).data;

export const deleteClientReport = async (projectId: number, id: number): Promise<void> => {
  await api.delete(`${base(projectId)}/${id}`);
};

export const previewClientReport = async (projectId: number, id: number, format: ClientReportFormat): Promise<ReportJob> =>
  (await api.post<ReportJob>(`${base(projectId)}/${id}/preview`, { format })).data;

export const issueClientReport = async (projectId: number, id: number): Promise<ClientReport> =>
  (await api.post<ClientReport>(`${base(projectId)}/${id}/issue`)).data;

export const rerenderClientReport = async (projectId: number, id: number): Promise<ClientReport> =>
  (await api.post<ClientReport>(`${base(projectId)}/${id}/render`)).data;

export const reviseClientReport = async (projectId: number, id: number): Promise<ClientReport> =>
  (await api.post<ClientReport>(`${base(projectId)}/${id}/revise`)).data;

export const listReportTemplates = async (projectId: number, signal?: AbortSignal): Promise<ReportTemplate[]> =>
  (await api.get<ReportTemplate[]>(`${base(projectId)}/templates`, { signal })).data;

/** Upload a file a template expects (global administrators; instance-wide). */
export const uploadReportTemplateAsset = async (
  projectId: number, template: string, assetId: string, file: File,
): Promise<ReportTemplateAssetChange> => {
  const form = new FormData();
  form.append('file', file);
  // The client's default Content-Type is JSON, and axios then serialises a
  // FormData body AS JSON — the file never arrives (422 "field required").
  return (await api.put<ReportTemplateAssetChange>(
    `${base(projectId)}/templates/${encodeURIComponent(template)}/assets/${encodeURIComponent(assetId)}`, form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )).data;
};

/** Remove an uploaded template file (it falls back to a server-installed one). */
export const removeReportTemplateAsset = async (
  projectId: number, template: string, assetId: string,
): Promise<ReportTemplateAssetChange> =>
  (await api.delete<ReportTemplateAssetChange>(
    `${base(projectId)}/templates/${encodeURIComponent(template)}/assets/${encodeURIComponent(assetId)}`,
  )).data;

/** The image the render would use for a template file (authenticated → blob). */
export const fetchReportTemplateAssetPreview = async (
  projectId: number, template: string, assetId: string, signal?: AbortSignal,
): Promise<Blob> =>
  (await api.get(
    `${base(projectId)}/templates/${encodeURIComponent(template)}/assets/${encodeURIComponent(assetId)}/preview`,
    { responseType: 'blob', signal },
  )).data as Blob;

/** A folder under report-templates/ that is not offered, and why (v2.409.0). */
export interface ReportTemplateProblem {
  name: string;
  error: string;
}

export const listReportTemplateProblems = async (projectId: number, signal?: AbortSignal): Promise<ReportTemplateProblem[]> =>
  (await api.get<ReportTemplateProblem[]>(`${base(projectId)}/templates/problems`, { signal })).data;

/** The project's analysts and admins as an assessment team (name, role, email). */
export const getProjectReportTeam = async (projectId: number, signal?: AbortSignal): Promise<ReportTester[]> =>
  (await api.get<ReportTester[]>(`${base(projectId)}/team`, { signal })).data;

export const getReportProfile = async (projectId: number, signal?: AbortSignal): Promise<ReportProfile> =>
  (await api.get<ReportProfile>(`${base(projectId)}/profile`, { signal })).data;

export const saveReportProfile = async (
  projectId: number, body: Omit<ReportProfile, 'updated_at' | 'inherited'>,
): Promise<ReportProfile> =>
  (await api.put<ReportProfile>(`${base(projectId)}/profile`, body)).data;

/** Change only the project's report defaults SENT (a draft's "Set for the
 *  project"): the rest stay as stored, so a stale read of the profile never
 *  overwrites someone else's change to another detail. */
export const patchReportProfile = async (
  projectId: number, body: EngagementOverrides & { template?: string | null },
): Promise<ReportProfile> =>
  (await api.patch<ReportProfile>(`${base(projectId)}/profile`, body)).data;

/** "Make this the project's default": each detail sent becomes the project's
 *  report default and leaves the draft's overrides, in one transaction.
 *  Answers the draft. */
export const promoteClientReportDetails = async (
  projectId: number, id: number, values: EngagementOverrides,
): Promise<ClientReport> =>
  (await api.post<ClientReport>(`${base(projectId)}/${id}/promote`, { values })).data;

/** Per detail, how many OTHER drafts of the project follow the project's
 *  value — what "Update project defaults…" says each push-up reaches.  An
 *  estimate when read. */
export const getClientReportFollowers = async (
  projectId: number, id: number, signal?: AbortSignal,
): Promise<Record<keyof EngagementSettings, number>> =>
  (await api.get<Record<keyof EngagementSettings, number>>(`${base(projectId)}/${id}/followers`, { signal })).data;

/** A draft's inputs as a preview would render them now: equal to a preview
 *  job's `inputs_fingerprint` while nothing it was built from has changed.
 *  Null when the draft cannot be built. */
export const getClientReportInputs = async (
  projectId: number, id: number, signal?: AbortSignal,
): Promise<{ fingerprint: string | null }> =>
  (await api.get<{ fingerprint: string | null }>(`${base(projectId)}/${id}/inputs`, { signal })).data;

/** An image of the project's own report text (the system description): the
 *  project's, so its report defaults and every draft place the same ones with
 *  `![](evidence:<id>)`.  Its bytes, caption and delete are the attachment's
 *  own functions (`getNoteAttachmentBlob`, `setNoteAttachmentCaption`,
 *  `deleteNoteAttachment`). */
export interface ReportTextImage {
  id: number;
  filename: string;
  caption: string | null;
  content_type: string;
  size_bytes: number;
  uploaded_by_id: number | null;
  created_at: string | null;
  /** Where the project's CURRENT text places it: `defaults` and / or
   *  `report <id>` for each draft. */
  placed_in: string[];
  /** The uploader, a project admin or a global admin. */
  can_modify: boolean;
}

export const listReportTextImages = async (projectId: number, signal?: AbortSignal): Promise<ReportTextImage[]> =>
  (await api.get<ReportTextImage[]>(`${base(projectId)}/images`, { signal })).data;

/** PNG, JPEG or GIF, at most 10 MB (analyst and above). */
export const uploadReportTextImage = async (
  projectId: number, file: File, caption?: string | null,
): Promise<ReportTextImage> => {
  const form = new FormData();
  form.append('file', file);
  if (caption?.trim()) form.append('caption', caption.trim());
  // See `uploadReportTemplateAsset`: without this the file is sent as JSON.
  return (await api.post<ReportTextImage>(
    `${base(projectId)}/images`, form, { headers: { 'Content-Type': 'multipart/form-data' } },
  )).data;
};

/** The installation's own report details: who produces its reports and the
 *  disclaimer they close with.  Every signed-in user reads them; a global
 *  admin writes them (System settings). */
export interface ReportDefaults {
  values: { author_name: string | null; disclaimer: string | null };
  labels: Record<string, string>;
  max_chars: Record<string, number>;
  updated_at: string | null;
  updated_by: string | null;
}

export const getReportDefaults = async (signal?: AbortSignal): Promise<ReportDefaults> =>
  (await api.get<ReportDefaults>('/report-defaults', { signal })).data;

/** Only the keys sent are touched; null or a blank text removes the value. */
export const saveReportDefaults = async (
  values: Partial<Record<'author_name' | 'disclaimer', string | null>>,
): Promise<ReportDefaults> =>
  (await api.put<ReportDefaults>('/report-defaults', { values })).data;

/** v5.319.0 — save the report's complete scope as CSV: the file a report over its
 *  template's scope cutoff names.  A draft's is today's scope; an issued report's
 *  is the frozen one, so its SHA-256 is the one the report prints. */
export const downloadClientReportScope = async (
  projectId: number, id: number, filename: string, signal?: AbortSignal,
): Promise<void> => {
  const response = await api.get(`${base(projectId)}/${id}/scope.csv`, { responseType: 'blob', signal });
  saveBlob(new Blob([response.data], { type: 'text/csv' }), filename);
};

/** Save an issued report's file (authenticated blob → browser download). */
export const downloadClientReportFile = async (
  projectId: number, id: number, file: ReportFile, signal?: AbortSignal,
): Promise<void> => {
  const response = await api.get(`${base(projectId)}/${id}/files/${file.format}`, { responseType: 'blob', signal });
  saveBlob(new Blob([response.data], { type: file.media_type }), file.filename);
};
