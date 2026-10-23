/**
 * Project coverage endpoint client (v3 alpha.3 backend, v3 alpha.5
 * UI consumer).
 *
 * One call: hosts planned and tested, per-scope coverage with %, and
 * the out-of-scope host count.
 *
 * See ``backend/app/api/v1/endpoints/coverage.py`` for the contract.
 */
import { api, projectPath } from './client';


export interface ScopeCoverageRow {
  subnet_count: number;
  total_scoped_ips: number;
  discovered_in_scope: number;
  /** 0–100 or null when the scope's total_scoped_ips is 0.
   *  UI renders null as "—" rather than 0%. */
  coverage_percent: number | null;
}


export interface ProjectCoverageResponse {
  project_id: number;
  total_hosts: number;
  /** Hosts with a test still to do (proposed / in progress) — the hosts
   *  `has:planned` lists. */
  hosts_planned: number;
  /** Hosts with evidence of a test that ran (finding / no finding /
   *  inconclusive) — the hosts `has:tested` lists. */
  hosts_tested: number;
  total_scopes: number;
  scopes: ScopeCoverageRow[];
  /** Hosts that don't map to any subnet under any declared scope.
   *  Zero when the project has no scopes declared (there's nothing
   *  to be outside of). */
  hosts_outside_scope: number;
  /** v2.424.0 — with hosts_outside_scope, the three coverage states
   *  (`scope:subnet` / `scope:name` / `scope:none`); they add up to total_hosts. */
  hosts_in_subnet_scope?: number;
  hosts_name_scope_only?: number;
}


export const getProjectCoverage = async (projectId: number, signal?: AbortSignal): Promise<ProjectCoverageResponse> => {
  const response = await api.get<ProjectCoverageResponse>(
    `${projectPath(projectId)}/coverage/`,
    { signal },
  );
  return response.data;
};
