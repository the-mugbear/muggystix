/**
 * Certificate-trust story, derived from the MCP catalog.
 *
 * Every MCP client fails at the same wall first: the deployment's self-signed
 * certificate isn't in the client's trust store, so the connection is refused
 * before a single tool call. The commands that fix it are identical whether an
 * operator reads them on the reference page or in a start dialog, so the
 * derivation lives here rather than being spelled out twice (it drifted once
 * already — the reference page and the dialogs disagreed on whether the cert
 * step existed at all).
 */
import type { McpCatalog } from '../services/api/references';

export interface CertTrust {
  /** SHA-256 to check a downloaded cert against, or null if not mounted. */
  fingerprint: string | null;
  /** `false` only when the server explicitly reports a CA-issued cert; `null`
   *  ("couldn't read it") is treated like self-signed — the pin still helps. */
  selfSigned: boolean | null;
  /** Absolute URL of the trust-cert installer. */
  trustScriptUrl: string;
  /** Origin of this deployment, which the installer needs to fetch the cert. */
  deploymentUrl: string;
  /** The three-line download / read / run sequence, ready for a CodeBlock. */
  commands: string;
}

/**
 * Build the trust-setup strings from a catalog (or null, before it loads).
 * Falls back to absolute URLs off `window.location.origin` so the commands are
 * runnable even if the catalog call failed — a bare `/api/v1/...` path is not.
 */
export function buildCertTrust(catalog: McpCatalog | null): CertTrust {
  const fingerprint = catalog?.tls_fingerprint_sha256 ?? null;
  const selfSigned = catalog?.tls_certificate?.self_signed ?? null;
  const trustScriptUrl = new URL(
    catalog?.trust_script_url ?? '/api/v1/references/trust-cert-script',
    window.location.origin,
  ).toString();
  const deploymentUrl = trustScriptUrl.replace(/\/api\/v1\/references\/.*$/, '');
  const commands = [
    `curl -sk ${trustScriptUrl} -o trust-cert.sh`,
    'less trust-cert.sh          # it installs a trust anchor — read it first',
    `bash trust-cert.sh --url ${deploymentUrl || 'https://<this-host>'}`,
  ].join('\n');
  return { fingerprint, selfSigned, trustScriptUrl, deploymentUrl, commands };
}
