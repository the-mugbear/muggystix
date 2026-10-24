/**
 * Presentation mode (5.424.0): a shared, driven view of an ISSUED report.
 * Backend: app/api/v1/endpoints/presentations.py (auditor and above).
 *
 * The slide list is the server's (`presentation_slides`): the same for the
 * same report and filter on every read.  The presentation's state carries
 * `revision` — raised only by a move, Take control and End — and says
 * whether THIS tab drives (`you_are_driving`); it never names a tab id.
 */
import { api, projectPath } from './client';

export type HostFilterKind = 'segment' | 'site' | 'text';
export interface HostFilter { kind: HostFilterKind; value: string }

export interface PresentationPerson { id: number; name: string; profile_image: string | null }

export type DisplayStatus = 'synchronized' | 'catching_up' | 'disconnected';
export interface PresentationDisplay {
  client: number;
  person: PresentationPerson | null;
  status: DisplayStatus;
  rendered: { revision: number; slide_key: string; host_filter: HostFilter | null } | null;
  fitted: boolean;
}

export type AgendaMode = 'agenda' | 'explore';

/** The agenda part of a position (5.425.0): the pinned version, following it
 *  or exploring the full report, and the ACTIVE item (0-based). */
export interface AgendaPosition { agenda_id: number; version: number; mode: AgendaMode; item_index: number }

/** The agenda a presentation runs: its pinned version's items (anchors). */
export interface PresentationAgendaState {
  id: number;
  name: string;
  version: number;
  latest_version: number;
  mode: AgendaMode;
  item_index: number;
  items: string[];
}

/** A point the driver clicked (stage coordinates), for the current revision
 *  only; `remaining_ms` is counted by the server. */
export interface PresentationSpotlight { seq: number; revision: number; x: number; y: number; remaining_ms: number }

export interface PresentationState {
  id: number;
  report_id: number;
  revision: number;
  slide_key: string;
  host_filter: HostFilter | null;
  driver: PresentationPerson | null;
  you_are_driving: boolean;
  /** The caller drives from another tab (or from this page before it reloaded). */
  driving_elsewhere: boolean;
  started_by: PresentationPerson | null;
  started_at: string | null;
  ended: { at: string; reason: 'ended' | 'idle'; by: PresentationPerson | null } | null;
  watching: Array<{ person: PresentationPerson | null; kind: 'display' | 'controller' | 'browse' }>;
  displays: PresentationDisplay[];
  agenda: PresentationAgendaState | null;
  spotlight: PresentationSpotlight | null;
}

/** A report's saved agenda: its latest version. */
export interface PresentationAgenda {
  id: number;
  report_id: number;
  name: string;
  version: number;
  items: string[];
  created_by: PresentationPerson | null;
  updated_at: string | null;
  in_live_presentation: boolean;
}

export type SlideBlock =
  | { kind: 'p' | 'h' | 'quote' | 'meta' | 'title'; md: string }
  | { kind: 'li'; md: string; level: number; marker: string; cont: boolean }
  | { kind: 'code'; lines: string[]; cont_before: boolean; cont_after: boolean }
  | {
    kind: 'table'; header: string[]; align: string[]; rows: string[][]; widths: number[];
    columns?: string | null; cont_before?: boolean; cont_after?: boolean;
  }
  | { kind: 'rule' }
  | { kind: 'counts'; counts: Array<{ severity: string; label: string; count: number }>; total: number }
  | {
    kind: 'facts'; severity: string; severity_label: string; cvss_score: number | null;
    status_note: string | null; affected_count: number;
  };

export interface Slide {
  key: string;
  kind: 'overview' | 'summary' | 'scope' | 'finding' | 'section' | 'image' | 'caption' | 'hosts';
  header: string;
  blocks: SlideBlock[];
  footer: string;
  page: number;
  pages: number;
  ref?: string;
  section?: string;
  empty?: boolean;
  image?: { attachment_id: number; available: boolean; height: number };
  /** The agenda items it belongs to (`overview`, `finding:F-01`, `section:F-01:impact`…) — the server's rule. */
  anchors: string[];
}

export type Stage = Record<string, number>;

export interface PresentationSlides {
  stage: Stage;
  slides: Slide[];
  filter: HostFilter | null;
  sites: string[];
  segments: string[];
}

/** A live presentation the reader can join, in one of their projects (5.426.0). */
export interface LivePresentation {
  presentation_id: number;
  project_id: number;
  project_name: string;
  report_id: number;
  report_number: number | null;
  report_title: string;
  started_by: PresentationPerson | null;
  driver: PresentationPerson | null;
  started_at: string | null;
}

/** The live presentations the reader can join, across their projects
 *  (auditor and above; the bell's glow and "Presenting now"). */
export const listLivePresentations = async (signal?: AbortSignal): Promise<LivePresentation[]> => {
  const response = await api.get('/presentations/live', { signal });
  return response.data;
};

const reportBase = (projectId: number, reportId: number) =>
  `${projectPath(projectId)}/client-reports/${reportId}/presentation`;
const presentationBase = (projectId: number, presentationId: number) =>
  `${projectPath(projectId)}/presentations/${presentationId}`;

export const getLivePresentation = async (
  projectId: number, reportId: number, clientId: string | null, signal?: AbortSignal,
): Promise<{ live: PresentationState | null }> => {
  const response = await api.get(reportBase(projectId, reportId), {
    params: clientId ? { client_id: clientId } : undefined, signal,
  });
  return response.data;
};

export const startPresentation = async (
  projectId: number, reportId: number, clientId: string,
): Promise<PresentationState> => {
  const response = await api.post(reportBase(projectId, reportId), { client_id: clientId });
  return response.data;
};

export const getPresentationSlides = async (
  projectId: number, reportId: number, filter: HostFilter | null, signal?: AbortSignal,
): Promise<PresentationSlides> => {
  const params = filter ? { host_filter_kind: filter.kind, host_filter_value: filter.value } : undefined;
  const response = await api.get(`${reportBase(projectId, reportId)}/slides`, { params, signal });
  return response.data;
};

export const fetchPresentationImage = async (
  projectId: number, reportId: number, attachmentId: number, signal?: AbortSignal,
): Promise<Blob> => {
  const response = await api.get(`${reportBase(projectId, reportId)}/images/${attachmentId}`, {
    responseType: 'blob', signal,
  });
  return response.data as Blob;
};

export const reportPresentationLayout = async (
  projectId: number, reportId: number, body: { slide_key: string; overflow_px: number; font: string },
): Promise<{ recorded: boolean }> => {
  const response = await api.post(`${reportBase(projectId, reportId)}/layout-reports`, body);
  return response.data;
};

export interface RenderedAck { revision: number; slide_key: string; host_filter: HostFilter | null; fitted: boolean }

export const heartbeatPresentation = async (
  projectId: number, presentationId: number,
  body: { client_id: string; kind: 'display' | 'controller' | 'browse'; rendered?: RenderedAck | null },
  signal?: AbortSignal,
): Promise<PresentationState> => {
  const response = await api.post(`${presentationBase(projectId, presentationId)}/heartbeat`, body, { signal });
  return response.data;
};

export const movePresentation = async (
  projectId: number, presentationId: number,
  body: {
    client_id: string; revision: number; slide_key: string; host_filter: HostFilter | null;
    agenda: AgendaPosition | null;
  },
  timeoutMs?: number,
): Promise<PresentationState> => {
  const response = await api.post(`${presentationBase(projectId, presentationId)}/move`, body,
    timeoutMs ? { timeout: timeoutMs } : undefined);
  return response.data;
};

export const spotlightPresentation = async (
  projectId: number, presentationId: number,
  body: { client_id: string; revision: number; x: number; y: number },
): Promise<PresentationState> => {
  const response = await api.post(`${presentationBase(projectId, presentationId)}/spotlight`, body);
  return response.data;
};

export const listPresentationAgendas = async (
  projectId: number, reportId: number, signal?: AbortSignal,
): Promise<PresentationAgenda[]> => {
  const response = await api.get(`${reportBase(projectId, reportId)}/agendas`, { signal });
  return response.data;
};

export const createPresentationAgenda = async (
  projectId: number, reportId: number, body: { name: string; items: string[] },
): Promise<PresentationAgenda> => {
  const response = await api.post(`${reportBase(projectId, reportId)}/agendas`, body);
  return response.data;
};

export const savePresentationAgenda = async (
  projectId: number, reportId: number, agendaId: number,
  body: { name: string; items: string[]; expected_version: number },
): Promise<PresentationAgenda> => {
  const response = await api.put(`${reportBase(projectId, reportId)}/agendas/${agendaId}`, body);
  return response.data;
};

export const deletePresentationAgenda = async (
  projectId: number, reportId: number, agendaId: number,
): Promise<void> => {
  await api.delete(`${reportBase(projectId, reportId)}/agendas/${agendaId}`);
};

export const takeControlOfPresentation = async (
  projectId: number, presentationId: number, clientId: string,
): Promise<PresentationState> => {
  const response = await api.post(`${presentationBase(projectId, presentationId)}/take-control`, { client_id: clientId });
  return response.data;
};

export const endPresentation = async (
  projectId: number, presentationId: number, clientId: string,
): Promise<PresentationState> => {
  const response = await api.post(`${presentationBase(projectId, presentationId)}/end`, { client_id: clientId });
  return response.data;
};
