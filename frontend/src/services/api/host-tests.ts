import { api, projectPath } from './client';

export type HostTestStatus = 'proposed' | 'in_progress' | 'done' | 'dismissed';
export interface HostTest {
  id: number;
  host_id: number;
  host_ip: string;
  tool: string | null;
  description: string;
  command: string | null;
  rationale: string;
  expected_result: string | null;
  references: string[] | null;
  target_fqdn: string | null;
  priority: string;
  label: string | null;
  status: HostTestStatus;
  assigned_to_id: number | null;
  assigned_to: string | null;
  created_by: string | null;
  source: 'agent' | 'person';
  agent_session_id: number | null;
  agent_model: string | null;
  agent_client: string | null;
  tester_summary: string | null;
  dismissed_reason: string | null;
  revision: number;
  /** The weakness this test confirms: the issue's identity on this host, and
   *  what it was called when linked. Null for a test about nothing scanned. */
  issue_key?: string | null;
  issue_title?: string | null;
  evidence_count: number;
  /** What its results say: the latest outcome, how many results showed an
   *  issue and are not on a finding yet, and the findings its results are on. */
  last_outcome?: string | null;
  unpromoted_findings?: number;
  finding_ids?: number[];
  created_at: string;
}
export interface HostTestPage { items: HostTest[]; total: number; has_more: boolean }
export const listHostTests = async (projectId: number, query: { host_id?: number; status?: HostTestStatus; active_only?: boolean; label?: string; mine?: boolean; agent_session_id?: number; limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<HostTestPage> =>
  (await api.get<HostTestPage>(`${projectPath(projectId)}/host-tests`, { params: query, signal })).data;
export const updateHostTest = async (projectId: number, id: number, change: { expected_revision: number; status?: HostTestStatus; assigned_to_id?: number | null; tester_summary?: string; dismissed_reason?: string }): Promise<HostTest> =>
  (await api.patch<HostTest>(`${projectPath(projectId)}/host-tests/${id}`, change)).data;

export type HostTestPriority = 'critical' | 'high' | 'medium' | 'low' | 'info';
export interface HostTestCreateBody {
  /** Stable key for this test — a retry returns the test already stored. */
  request_key: string;
  host_id: number;
  tool: string;
  description: string;
  rationale: string;
  command?: string;
  expected_result?: string;
  priority?: HostTestPriority;
  assigned_to_id?: number;
  /** The scanner observation on this host that the test is meant to confirm. */
  vulnerability_id?: number;
}
/** Add tests to hosts by hand — the same route an agent proposes through. */
export const createHostTests = async (projectId: number, tests: HostTestCreateBody[]): Promise<{ items: HostTest[] }> =>
  (await api.post<{ items: HostTest[] }>(`${projectPath(projectId)}/host-tests`, { tests })).data;

export type HostTestOutcome ='finding' | 'no_finding' | 'inconclusive' | 'failed';
export interface HostTestResultBody {
  expected_revision: number;
  /** Stable key for this result — a retry returns the record already stored. */
  request_key: string;
  outcome: HostTestOutcome;
  summary: string;
  command?: string;
  raw_output?: string;
  observed_ip?: string;
}
/** A person's result for a test: records the evidence and moves the test on
 *  (finding / no finding close it; the others leave it in progress). */
export const recordHostTestResult = async (projectId: number, id: number, body: HostTestResultBody): Promise<{ test: HostTest }> =>
  (await api.post<{ test: HostTest }>(`${projectPath(projectId)}/host-tests/${id}/result`, body)).data;
