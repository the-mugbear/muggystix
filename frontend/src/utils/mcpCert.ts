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
  /** Absolute URL of the raw PEM — the Windows path downloads it directly,
   *  because the installer is bash and there is no bash to run it in. */
  certUrl: string;
  /** The three-line download / read / run sequence, ready for a CodeBlock. */
  commands: string;
  /** v5.217.0 — the same outcome for Windows without WSL, in PowerShell 7:
   *  fetch the PEM, print its SHA-256 in the same colon-separated form the
   *  catalog reports so the two can be compared by eye, and register it for
   *  the Node clients as a USER-level variable. `setx` rather than a profile
   *  export because VS Code / Claude Code launched from the Start menu never
   *  read a shell profile — the "add the exports to your profile" step that
   *  works on POSIX is exactly the one that silently fails on Windows. */
  windowsCommands: string;
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
  const certUrl = new URL(
    catalog?.tls_certificate_url ?? '/api/v1/references/tls-certificate',
    window.location.origin,
  ).toString();
  const commands = [
    `curl -sk ${trustScriptUrl} -o trust-cert.sh`,
    'less trust-cert.sh          # it installs a trust anchor — read it first',
    `bash trust-cert.sh --url ${deploymentUrl || 'https://<this-host>'}`,
  ].join('\n');
  // curl.exe, not curl: in PowerShell the bare name is an Invoke-WebRequest
  // alias that rejects these flags. GetCertHashString(SHA256) needs .NET 5+,
  // i.e. PowerShell 7 (pwsh), not the 5.1 that ships with Windows.
  const pem = '"$HOME\\.bluestick\\bluestick.pem"';
  const windowsCommands = [
    'New-Item -ItemType Directory -Force "$HOME\\.bluestick" | Out-Null',
    `curl.exe -sk ${certUrl} -o ${pem}`,
    `$c = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(${pem})`,
    "$c.GetCertHashString([System.Security.Cryptography.HashAlgorithmName]::SHA256) -replace '(..)(?!$)', '$1:'   # compare with the fingerprint",
    // Both lines, deliberately: setx writes the per-user variable that FUTURE
    // processes (a client launched from the Start menu, a new terminal) read,
    // but it does not touch the current shell — a client launched from this
    // same window would inherit the old environment and still refuse the cert.
    `setx NODE_EXTRA_CA_CERTS ${pem}   # per-user, for every process started from now on`,
    `$env:NODE_EXTRA_CA_CERTS = ${pem}   # this shell too — setx does not update it`,
  ].join('\n');
  return {
    fingerprint,
    selfSigned,
    trustScriptUrl,
    deploymentUrl,
    certUrl,
    commands,
    windowsCommands,
  };
}
