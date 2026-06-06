/**
 * Project coverage endpoint client (v3 alpha.3 backend, v3 alpha.5
 * UI consumer).
 *
 * One call returns everything the v3 Operations page's "Host
 * coverage" section needs: per-stage host counts, gap counts,
 * per-scope coverage with %, and the out-of-scope host count.
 *
 * See ``backend/app/api/v1/endpoints/coverage.py`` for the contract.
 */
import { api, p } from './client';


export interface ScopeCoverageRow {
  scope_id: number;
  scope_name: string;
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
  hosts_with_plan_entry: number;
  hosts_with_execution_result: number;
  /** Gap counts — explicit fields rather than derived so the UI
   *  doesn't drift from the backend's definition. */
  hosts_no_plan: number;
  hosts_no_execution: number;
  total_scopes: number;
  scopes: ScopeCoverageRow[];
  /** Hosts that don't map to any subnet under any declared scope.
   *  Zero when the project has no scopes declared (there's nothing
   *  to be outside of). */
  hosts_outside_scope: number;
}


export const getProjectCoverage = async (): Promise<ProjectCoverageResponse> => {
  const response = await api.get<ProjectCoverageResponse>(
    `${p()}/coverage/`,
  );
  return response.data;
};
