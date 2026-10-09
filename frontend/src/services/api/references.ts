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

export const getSbom = async (signal?: AbortSignal): Promise<SbomResponse> => {
  const response = await api.get<SbomResponse>('/references/sbom', { signal });
  return response.data;
};



// --- Tool registry ---
// One source of truth for every tool BlueStick knows about: a catalogue, not
// agent policy. `reference` is the vetted catalogue, `suggested` an agent's
// proposal waiting for an admin, `rejected` a declined suggestion. No status
// grants or withholds permission — the operator drives their own agent.

export interface ToolRegistryEntry {
  name: string;
  description: string;
  category: string;
  ports: string | null;
  install: string | null;
  url: string | null;
  kali: boolean;
  /** Catalogue state. `suggested` waits for an admin to add it to the
   *  catalogue (`reference`) or decline it (`rejected`). */
  status: 'reference' | 'suggested' | 'rejected';
  phases: string[];
  intrusive: boolean | null;
  requires_privileges: boolean | null;
  output_format: string | null;
  /** Engineering: does BlueStick have a parser for its output. Independent of
   *  `status` — a catalogued tool may have no parser at all. */
  ingestible: boolean;
  suggested_rationale: string | null;
}

export interface ToolRegistryResponse {
  count: number;
  tools: ToolRegistryEntry[];
}

export const getToolRegistry = async (
  status?: string,
  signal?: AbortSignal,
): Promise<ToolRegistryResponse> => {
  const response = await api.get<ToolRegistryResponse>('/references/tools', {
    params: status ? { status } : undefined,
    signal,
  });
  return response.data;
};

// --- Parser coverage (v5.296.0) ---
// What BlueStick reads from each tool's output and where it ends up: the
// "What BlueStick reads" page.  Audited against the parsers and pinned by
// backend tests/test_parser_coverage.py.

/** How far BlueStick takes a value, in that order. */
export type CoverageLevel = 'observation' | 'field' | 'text' | 'stored' | 'discarded';

export interface CoverageLevelDef {
  id: CoverageLevel;
  label: string;
  description: string;
}

export interface CoverageSignal {
  /** What the tool reports, in analyst words. */
  what: string;
  /** How to recognise it in the file (element, key, line shape). */
  input: string;
  level: CoverageLevel;
  /** Model.attribute it lands in (empty when discarded). */
  stored_as: string[];
  /** Where an analyst sees or uses it. */
  shown: string | null;
  note: string | null;
}

export interface ToolCoverage {
  id: string;
  name: string;
  formats: Array<{ file_type: string; label: string }>;
  /** Tool-registry names whose output this parser reads (gobuster → dirbuster). */
  registry_tools: string[];
  accepted_input: string;
  signals: CoverageSignal[];
  gaps: string[];
  /** What was not checked against real output of the tool. */
  unverified: string[];
}

export interface ParserCoverageResponse {
  levels: CoverageLevelDef[];
  tools: ToolCoverage[];
}

export const getParserCoverage = async (signal?: AbortSignal): Promise<ParserCoverageResponse> => {
  const response = await api.get<ParserCoverageResponse>('/references/parser-coverage', { signal });
  return response.data;
};

/** Fields an admin may change when vetting. `ingestible` is absent on purpose:
 *  it records whether a parser exists in the codebase, not an operator call. */
export interface ToolRegistryUpdate {
  status?: 'reference' | 'rejected';
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
  /** 'write' iff the underlying endpoint mutates — decided by HTTP method.
   *  Whether a given session may perform the write is a separate question,
   *  answered by the operator's project role at request time, so there is no
   *  per-tool permission to show here. */
  kind: 'read' | 'write';
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
  /** The deployment certificate, riding along with the catalog so the page
   *  can show its fingerprint for a "right server?" check. */
  tls_certificate_url?: string;
  /** SHA-256 of the deployment certificate, for checking a downloaded copy.
   *  Null when the certificate isn't mounted in the backend container. */
  tls_fingerprint_sha256?: string | null;
  /** What the deployment actually presents: normally issued by the local
   *  root CA (ca/local-ca.sh); self-signed only before one was installed. */
  tls_certificate?: {
    fingerprint_sha256: string | null;
    self_signed: boolean | null;
    subject: string | null;
    expires_at: string | null;
  };
}

export const getMcpTools = async (signal?: AbortSignal): Promise<McpCatalog> => {
  const response = await api.get<McpCatalog>('/references/mcp-tools', { signal });
  return response.data;
};
