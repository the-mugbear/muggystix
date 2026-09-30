/**
 * Agent proposals and evidence records (v5.316.0; backend 2.436.0).
 *
 * An agent's change to what the team concluded — a finding's report text, a
 * new finding, promoting or dismissing a scanner observation, an endpoint's
 * status — is a PROPOSAL a person accepts (then may edit) or rejects.  The
 * in-app "Draft empty sections" produces the same thing (`source: llm_draft`).
 * Evidence records are what an agent ran against a host and what came back.
 * Backend: app/api/v1/endpoints/proposals.py.
 */
import { api, p } from './client';

export type ProposalKind =
  | 'finding_text'
  | 'finding_create'
  | 'observation_promote'
  | 'observation_dismiss'
  | 'endpoint_status';
export type ProposalStatus = 'pending' | 'accepted' | 'rejected' | 'superseded';

export interface Proposal {
  id: number;
  kind: ProposalKind;
  status: ProposalStatus;
  source: 'agent' | 'llm_draft';
  finding_id: number | null;
  vulnerability_id: number | null;
  finding_host_id: number | null;
  /** finding_text: the report field. */
  field: string | null;
  /** finding_text: {value, accepted_value?}; finding_create: {title, severity,
   *  status, host_ids, report_text}; observation_*: {scope, severity, summary};
   *  endpoint_status: {host_status}. */
  payload: Record<string, unknown> | null;
  /** finding_text: the finding's text in that field now. */
  current_value: string | null;
  target: {
    finding_title: string | null;
    observation_title: string | null;
    host_id: number | null;
    host_ip: string | null;
  };
  rationale: string | null;
  evidence_ids: number[];
  agent_session_id: number | null;
  proposed_by: string | null;
  agent_model: string | null;
  agent_client: string | null;
  prompt_version: string | null;
  created_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  result_finding_id: number | null;
  /** Why the last accept failed (the target changed); the proposal stays pending. */
  error: string | null;
}

export interface ProposalList {
  total: number;
  items: Proposal[];
  has_more: boolean;
}

export interface ProposalQuery {
  status?: ProposalStatus;
  kind?: ProposalKind;
  finding_id?: number;
  host_id?: number;
  agent_session_id?: number;
  limit?: number;
  offset?: number;
}

export const listProposals = async (query: ProposalQuery = {}): Promise<ProposalList> =>
  (await api.get<ProposalList>(`${p()}/proposals`, { params: query })).data;

export interface ProposalSummary {
  pending: number;
  by_kind: Partial<Record<ProposalKind, number>>;
}

export const getProposalSummary = async (): Promise<ProposalSummary> =>
  (await api.get<ProposalSummary>(`${p()}/proposals/summary`)).data;

/** Apply it as you.  `editedValue` (report text only) accepts with your edit. */
export const acceptProposal = async (
  id: number, opts: { note?: string; editedValue?: string } = {},
): Promise<Proposal> =>
  (await api.post<Proposal>(`${p()}/proposals/${id}/accept`, {
    ...(opts.note ? { note: opts.note } : {}),
    ...(opts.editedValue !== undefined ? { edited_value: opts.editedValue } : {}),
  })).data;

export const rejectProposal = async (id: number, note?: string): Promise<Proposal> =>
  (await api.post<Proposal>(`${p()}/proposals/${id}/reject`, note ? { note } : {})).data;

export interface BulkDecision {
  decided: number[];
  failed: Array<{ id: number; status_code: number; detail: unknown }>;
}

/** Each is decided on its own; one refusal does not stop the rest. */
export const decideProposals = async (
  ids: number[], action: 'accept' | 'reject', note?: string,
): Promise<BulkDecision> =>
  (await api.post<BulkDecision>(`${p()}/proposals/bulk`, { ids, action, ...(note ? { note } : {}) })).data;

export type EvidenceOutcome = 'finding' | 'no_finding' | 'inconclusive' | 'failed' | 'info';

export interface EvidenceRecord {
  id: number;
  host_id: number;
  host_ip: string | null;
  finding_id: number | null;
  finding_host_id: number | null;
  tool: string;
  command: string | null;
  outcome: EvidenceOutcome;
  summary: string;
  raw_output_preview: string | null;
  raw_output_bytes: number | null;
  raw_output_truncated_in_preview: boolean;
  observed_ip: string | null;
  executed_at: string | null;
  agent_session_id: number | null;
  recorded_by: string | null;
  agent_model: string | null;
  agent_client: string | null;
  created_at: string | null;
}

export interface EvidenceList {
  total: number;
  items: EvidenceRecord[];
  has_more: boolean;
}

export const listEvidenceRecords = async (
  query: { host_id?: number; finding_id?: number; agent_session_id?: number; limit?: number; offset?: number } = {},
): Promise<EvidenceList> =>
  (await api.get<EvidenceList>(`${p()}/evidence`, { params: query })).data;

/** The whole raw output (the list carries a 2,000-character preview). */
export const getEvidenceRawOutput = async (id: number): Promise<string> =>
  (await api.get<string>(`${p()}/evidence/${id}/raw`, { responseType: 'text' })).data;
