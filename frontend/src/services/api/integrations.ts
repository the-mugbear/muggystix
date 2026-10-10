/**
 * The installation's scanner integrations (Nessus, OpenVAS, ...).
 *
 * One list for the whole installation: an integration belongs to no user and
 * no project.  Every signed-in user reads it (never a secret — the server
 * answers `has_secret` flags); a global administrator changes it.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';


// ---------------------------------------------------------------------------
// Integration credentials (scanner tool creds)
// ---------------------------------------------------------------------------

export interface IntegrationEntry {
  id: number;
  name: string;
  integration_type: string;
  base_url: string | null;
  has_secret: boolean;
  has_secret2: boolean;
  extra_config: Record<string, any> | null;
  is_active: boolean;
  /** Username of the account that configured it; null once that account is gone. */
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface IntegrationCreatePayload {
  name: string;
  integration_type: string;
  base_url?: string;
  secret?: string;
  secret2?: string;
  extra_config?: Record<string, any>;
  is_active?: boolean;
}

export interface IntegrationUpdatePayload {
  name?: string;
  base_url?: string;
  secret?: string;
  clear_secret?: boolean;
  secret2?: string;
  clear_secret2?: boolean;
  extra_config?: Record<string, any>;
  is_active?: boolean;
}

export const listIntegrations = async (signal?: AbortSignal): Promise<IntegrationEntry[]> => {
  const r = await api.get<IntegrationEntry[]>('/integrations/', { signal });
  return r.data;
};

export const listIntegrationTypes = async (signal?: AbortSignal): Promise<Array<{ value: string; label: string }>> => {
  const r = await api.get<Array<{ value: string; label: string }>>('/integrations/types', { signal });
  return r.data;
};

export const createIntegration = async (body: IntegrationCreatePayload): Promise<IntegrationEntry> => {
  const r = await api.post<IntegrationEntry>('/integrations/', body);
  return r.data;
};

export const updateIntegration = async (
  id: number,
  body: IntegrationUpdatePayload,
): Promise<IntegrationEntry> => {
  const r = await api.patch<IntegrationEntry>(`/integrations/${id}`, body);
  return r.data;
};

export const deleteIntegration = async (id: number): Promise<void> => {
  await api.delete(`/integrations/${id}`);
};


// ---------------------------------------------------------------------------
// Pre-save connection test (v2.49.4)
// ---------------------------------------------------------------------------

/** Result of `POST /integrations/test`.
 *
 * `ok` is tri-state:
 *  - `true`  → probe authenticated and reached the server.
 *  - `false` → probe failed; `message` carries the reason.
 *  - `null`  → no concrete probe is implemented for this integration
 *               type yet; the URL passed the policy gate but no
 *               deeper check ran. */
export interface IntegrationTestResult {
  ok: boolean | null;
  integration_type: string;
  message: string;
  http_status?: number | null;
  details?: Record<string, any> | null;
  duration_ms: number;
}

export const testIntegrationConfig = async (
  body: IntegrationCreatePayload,
): Promise<IntegrationTestResult> => {
  const r = await api.post<IntegrationTestResult>('/integrations/test', body);
  return r.data;
};
