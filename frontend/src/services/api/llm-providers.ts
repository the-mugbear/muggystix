/**
 * Per-user LLM provider credentials + chat completion proxy.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';


// ---------------------------------------------------------------------------
// LLM Providers (per-user)
// ---------------------------------------------------------------------------

export interface LLMProviderEntry {
  id: number;
  name: string;
  provider_type: string;
  base_url: string | null;
  model_id: string | null;
  has_api_key: boolean;
  extra_config: Record<string, any> | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface LLMProviderTypeOption {
  value: string;
  label: string;
}

export interface LLMProviderCreatePayload {
  name: string;
  provider_type: string;
  base_url?: string;
  model_id?: string;
  api_key?: string;
  extra_config?: Record<string, any>;
  is_default?: boolean;
}

export interface LLMProviderUpdatePayload {
  name?: string;
  base_url?: string;
  model_id?: string;
  api_key?: string;
  clear_api_key?: boolean;
  extra_config?: Record<string, any>;
  is_default?: boolean;
}

export const listLLMProviders = async (): Promise<LLMProviderEntry[]> => {
  const r = await api.get<LLMProviderEntry[]>('/llm-providers/');
  return r.data;
};

export const listLLMProviderTypes = async (): Promise<LLMProviderTypeOption[]> => {
  const r = await api.get<LLMProviderTypeOption[]>('/llm-providers/types');
  return r.data;
};

export const createLLMProvider = async (body: LLMProviderCreatePayload): Promise<LLMProviderEntry> => {
  const r = await api.post<LLMProviderEntry>('/llm-providers/', body);
  return r.data;
};

export const updateLLMProvider = async (
  id: number,
  body: LLMProviderUpdatePayload,
): Promise<LLMProviderEntry> => {
  const r = await api.patch<LLMProviderEntry>(`/llm-providers/${id}`, body);
  return r.data;
};

export const deleteLLMProvider = async (id: number): Promise<void> => {
  await api.delete(`/llm-providers/${id}`);
};

export const testLLMProvider = async (
  id: number,
): Promise<{ ok: boolean; detail: string }> => {
  const r = await api.post<{ ok: boolean; detail: string }>(`/llm-providers/${id}/test`);
  return r.data;
};

export interface LLMCompletionResponse {
  provider_id: number;
  provider_name: string;
  model_id: string | null;
  content: string;
  raw_metadata: Record<string, any>;
}

export const llmComplete = async (
  providerId: number,
  body: { system?: string; prompt: string; max_tokens?: number; temperature?: number },
  // Optional axios opts so callers can pass an AbortController signal
  // for user-initiated cancel of long completions (audit C9).
  opts?: { signal?: AbortSignal },
): Promise<LLMCompletionResponse> => {
  const r = await api.post<LLMCompletionResponse>(`/llm-providers/${providerId}/complete`, body, {
    signal: opts?.signal,
  });
  return r.data;
};
