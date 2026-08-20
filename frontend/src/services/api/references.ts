/**
 * SBOM — operational reference for CVE triage.  Public endpoint.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';
import type { McpClientSetup } from '../../components/McpConnectPanel';


// --- Software Bill of Materials ---
// Operational reference for CVE triage — "is package X in this build?".
// Public endpoint (no project scope, no auth) consistent with the rest
// of /api/v1/references/*.

export interface SbomComponent {
  name: string;
  version: string;
  ecosystem: 'python' | 'npm';
  application_layer: 'backend' | 'frontend';
  /** Where the package is *declared* — i.e. the manifest the user edits
   *  (requirements.txt / package.json).  Null for transitive npm deps,
   *  which the user never declared anywhere. */
  declared_in?: string | null;
  /** Where the exact installed version was *observed* — the live venv
   *  for Python, package-lock.json for npm.  This is the provenance
   *  signal a triage user actually needs. */
  resolved_from: string;
  /** True iff this package was explicitly chosen (listed in
   *  requirements.txt or package.json), false for transitive deps. */
  direct: boolean;
  license?: string | null;
}

export interface SbomSummary {
  total: number;
  direct: number;
  transitive: number;
  backend: number;
  frontend: number;
}

export interface SbomResponse {
  generated_at: string;
  app_version: string;
  manifests: { python: string | null; npm: string | null };
  summary: SbomSummary;
  components: SbomComponent[];
}

export const getSbom = async (): Promise<SbomResponse> => {
  const response = await api.get<SbomResponse>('/references/sbom');
  return response.data;
};



// --- Tool registry ---
// One source of truth for every tool BlueStick knows about. The reference page
// renders all of it as a human knowledge repo; the agent catalogue is the
// `approved` subset. Previously these were two lists in two languages that had
// already drifted (`testssl` was agent-usable with no human entry), and only
// the smaller backend one could gate anything.

export interface ToolRegistryEntry {
  name: string;
  description: string;
  category: string;
  ports: string | null;
  install: string | null;
  url: string | null;
  kali: boolean;
  /** Policy: may an agent run it. `suggested` is awaiting human vetting. */
  status: 'approved' | 'reference' | 'suggested' | 'rejected';
  phases: string[];
  intrusive: boolean | null;
  requires_privileges: boolean | null;
  output_format: string | null;
  /** Engineering: does BlueStick have a parser for its output. Independent of
   *  `status` — a tool can be safe to run with no parser at all. */
  ingestible: boolean;
  suggested_rationale: string | null;
}

export interface ToolRegistryResponse {
  count: number;
  tools: ToolRegistryEntry[];
}

export const getToolRegistry = async (
  status?: string,
): Promise<ToolRegistryResponse> => {
  const response = await api.get<ToolRegistryResponse>('/references/tools', {
    params: status ? { status } : undefined,
  });
  return response.data;
};

/** Fields an admin may change when vetting. `ingestible` is absent on purpose:
 *  it records whether a parser exists in the codebase, not an operator call. */
export interface ToolRegistryUpdate {
  status?: 'approved' | 'reference' | 'rejected';
  description?: string;
  category?: string;
  ports?: string;
  install?: string;
  url?: string;
  kali?: boolean;
}

/** Admin-only. Vetting a suggestion is a status change on the same row — which
 *  is why an agent's ask is stored as a row rather than a note elsewhere. */
export const updateToolRegistryEntry = async (
  name: string,
  update: ToolRegistryUpdate,
): Promise<ToolRegistryEntry> => {
  const response = await api.patch<ToolRegistryEntry>(
    `/references/tools/${encodeURIComponent(name)}`,
    update,
  );
  return response.data;
};


// --- MCP tool catalog ---
// Drives the /reference/mcp page. Read off the live server registry so the
// page describes what this deployment actually serves. Public endpoint,
// consistent with the rest of /api/v1/references/* — the same catalog is
// already reachable via an unauthenticated MCP `tools/list`.

export interface McpToolDoc {
  name: string;
  description: string;
  /** 'write' iff the underlying endpoint gates the call on a capability. */
  kind: 'read' | 'write';
  capability: string | null;
  method: string;
  path: string;
  /** Which key workflows see this tool in `tools/list` — a session only ever
   *  gets its own workflow's tools, so the page has to say which is which. */
  workflows: string[];
  input_schema: {
    type: string;
    properties?: Record<string, { type?: string; description?: string; enum?: string[] }>;
    required?: string[];
    [k: string]: unknown;
  };
}

export interface McpCatalog {
  server_name: string;
  protocol_version: string;
  endpoint: string;
  max_request_bytes: number;
  max_batch_messages: number;
  tools: McpToolDoc[];
  /** The connect recipes, built by the same code a live session uses, with a
   *  placeholder key. Served rather than duplicated in TypeScript: the two
   *  copies drifted twice — on the config wrapper key, and on the Codex TLS
   *  note — and both failures were silent. */
  sample_clients?: McpClientSetup[];
  sample_key_placeholder?: string;
  /** Connecting is the other half of this page's job, and every client fails at
   *  the certificate first. These ride along with the catalog so the page has
   *  them exactly when it needs them. */
  trust_script_url?: string;
  tls_certificate_url?: string;
  /** SHA-256 of the deployment certificate, for checking a downloaded copy.
   *  Null when the certificate isn't mounted in the backend container. */
  tls_fingerprint_sha256?: string | null;
  /** What the deployment actually presents. Self-signed is this project's
   *  default, not a guarantee — an operator can mount an internal-CA or
   *  DNS-validated certificate, and pinning is then likely unnecessary. */
  tls_certificate?: {
    fingerprint_sha256: string | null;
    self_signed: boolean | null;
    subject: string | null;
    expires_at: string | null;
  };
}

export const getMcpTools = async (): Promise<McpCatalog> => {
  const response = await api.get<McpCatalog>('/references/mcp-tools');
  return response.data;
};

// --- Host tool readiness ---
// The agent tool catalog cross-referenced against the calling user's
// most recent environment probe.  Authenticated (reflects *your* host),
// unlike the rest of /references/*.

export type ToolReadinessStatus = 'installed' | 'missing' | 'warn' | 'unknown';

export interface ToolReadinessItem {
  tool: string;
  phases: string[];
  intrusive: boolean;
  status: ToolReadinessStatus;
  /** Resolved binary path, when the probe's tools_status reported it. */
  path?: string | null;
  /** Problem note (e.g. the httpx Python-CLI collision), when present. */
  issue?: string | null;
  /** Install commands keyed by provider — apt / brew / cargo / go / pipx /
   *  binary / docker / etc.  May be empty for tools with no catalog hint. */
  install_hints: Record<string, string>;
}

export interface ToolReadinessResponse {
  /** False when the user has never run an agent workflow — every tool
   *  is then `unknown`. */
  has_probe: boolean;
  os_family?: string | null;
  os_release?: string | null;
  shell?: string | null;
  probed_at?: string | null;
  /** install_hints key the UI should prefer for this host's OS. */
  preferred_provider?: string | null;
  summary: {
    installed: number;
    missing: number;
    warn: number;
    unknown: number;
    total: number;
  };
  tools: ToolReadinessItem[];
}

export const getToolReadiness = async (): Promise<ToolReadinessResponse> => {
  const response = await api.get<ToolReadinessResponse>('/references/tool-readiness');
  return response.data;
};
