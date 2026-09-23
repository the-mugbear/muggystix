/**
 * Client reports (v5.261.0) — the findings-first Quarto deliverable, its
 * history and its addenda.  Backend: app/api/v1/endpoints/client_reports.py.
 * A draft's preview is an ordinary report job (poll with getReportJob,
 * download with downloadReportJob); an issued report's files download here.
 */
import { api, p } from './client';
import type { ReportJob } from '../api';

export type ClientReportKind = 'full' | 'addendum';
export type ClientReportStatus = 'draft' | 'issued' | 'superseded';
/** `qmd` is the Quarto source as a zip (report.qmd, data.json, filters, screenshots). */
export type ClientReportFormat = 'html' | 'docx' | 'pdf' | 'qmd';

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
}

export interface ReportProfile extends EngagementSettings {
  template: string | null;
  updated_at?: string | null;
  /** No team is saved: `testers` is the project's analysts and admins. */
  testers_from_project?: boolean;
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
  /** The file is installed where the manifest says. */
  present: boolean;
}

export interface ReportTemplate {
  name: string;
  title: string;
  description: string;
  formats: ClientReportFormat[];
  assets?: ReportTemplateAsset[];
}

export interface ReportFile {
  format: ClientReportFormat;
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
  images?: number;
  images_skipped?: number;
  delta?: { new_findings: number; findings_with_new_endpoints: number; withdrawn: number } | null;
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
  settings: EngagementSettings;
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
  can_edit: boolean;
  can_issue: boolean;
}

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
  executive_summary?: string | null;
  settings?: EngagementSettings;
}

const base = () => `${p()}/client-reports`;

export const listClientReports = async (): Promise<ClientReportList> =>
  (await api.get<ClientReportList>(base())).data;

export const getClientReport = async (id: number): Promise<ClientReport> =>
  (await api.get<ClientReport>(`${base()}/${id}`)).data;

export const createClientReport = async (body: {
  kind: ClientReportKind; title?: string; template?: string; baseline_report_id?: number;
}): Promise<ClientReport> => (await api.post<ClientReport>(base(), body)).data;

export const updateClientReport = async (id: number, body: ClientReportUpdate): Promise<ClientReport> =>
  (await api.patch<ClientReport>(`${base()}/${id}`, body)).data;

export const deleteClientReport = async (id: number): Promise<void> => {
  await api.delete(`${base()}/${id}`);
};

export const previewClientReport = async (id: number, format: ClientReportFormat): Promise<ReportJob> =>
  (await api.post<ReportJob>(`${base()}/${id}/preview`, { format })).data;

export const issueClientReport = async (id: number): Promise<ClientReport> =>
  (await api.post<ClientReport>(`${base()}/${id}/issue`)).data;

export const rerenderClientReport = async (id: number): Promise<ClientReport> =>
  (await api.post<ClientReport>(`${base()}/${id}/render`)).data;

export const reviseClientReport = async (id: number): Promise<ClientReport> =>
  (await api.post<ClientReport>(`${base()}/${id}/revise`)).data;

export const listReportTemplates = async (): Promise<ReportTemplate[]> =>
  (await api.get<ReportTemplate[]>(`${base()}/templates`)).data;

/** The project's analysts and admins as an assessment team (name, role, email). */
export const getProjectReportTeam = async (): Promise<ReportTester[]> =>
  (await api.get<ReportTester[]>(`${base()}/team`)).data;

export const getReportProfile = async (): Promise<ReportProfile> =>
  (await api.get<ReportProfile>(`${base()}/profile`)).data;

export const saveReportProfile = async (body: Omit<ReportProfile, 'updated_at'>): Promise<ReportProfile> =>
  (await api.put<ReportProfile>(`${base()}/profile`, body)).data;

/** Save an issued report's file (authenticated blob → browser download). */
export const downloadClientReportFile = async (id: number, file: ReportFile): Promise<void> => {
  const response = await api.get(`${base()}/${id}/files/${file.format}`, { responseType: 'blob' });
  const url = window.URL.createObjectURL(new Blob([response.data], { type: file.media_type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = file.filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
};
