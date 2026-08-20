/**
 * MCP reference — /reference/mcp
 *
 * Introduces the MCP (Model Context Protocol) transport that fronts the
 * AI-Assist surface: what it is, how to connect a client, what the tools do,
 * and what the auth/audit model is.
 *
 * The tool table is fetched from `/references/mcp-tools`, which reads the live
 * server registry. A hand-written list would drift the first time a tool is
 * added; this page can't. Everything else on the page is stable prose about
 * the transport and is written inline.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Copy, Lock, Radio, ShieldCheck } from 'lucide-react';
import { getMcpTools, type McpCatalog, type McpToolDoc } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { copyToClipboard } from '../utils/clipboard';
import { CardListSkeleton } from '../components/PageSkeleton';
import McpConnectPanel from '../components/McpConnectPanel';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

const CAPABILITY_LABEL: Record<string, string> = {
  'write:notes': 'write:notes',
  'write:follow': 'write:follow',
  'write:host': 'write:host',
  'write:execution': 'write:execution',
};

/** The workflow a key belongs to decides which tools it is offered. Order
 *  follows the engagement: recon feeds planning, planning feeds execution. */
const WORKFLOW_GROUPS: Array<{ key: string; label: string; blurb: string }> = [
  {
    key: 'recon',
    label: 'Reconnaissance',
    blurb:
      'Populates host data from scanners run on your machine. Bulk uploads and target-file downloads stay curl — see below.',
  },
  {
    key: 'plan_generation',
    label: 'Plan generation',
    blurb:
      'Proposes tests against what recon found, then hands the draft to a human. Nothing here runs anything.',
  },
  {
    key: 'execution',
    label: 'Execution',
    blurb:
      'Works an approved plan and records what each test produced. The commands run on your machine, under your client’s sandbox.',
  },
  {
    key: 'assist',
    label: 'Assist',
    blurb:
      'Interactive read over the existing inventory, plus the three writes an operator can grant.',
  },
  {
    key: 'shared',
    label: 'Every workflow',
    blurb:
      'Offered to every session: what am I, and how do I ask for a tool you don’t approve.',
  },
];

const formatBytes = (bytes: number): string =>
  bytes >= 1024 * 1024 ? `${Math.round(bytes / (1024 * 1024))} MiB` : `${Math.round(bytes / 1024)} KiB`;

/** Parameter names for a tool, required ones first and marked. */
const paramSummary = (tool: McpToolDoc): Array<{ name: string; required: boolean }> => {
  const props = Object.keys(tool.input_schema?.properties ?? {});
  const required = new Set(tool.input_schema?.required ?? []);
  return props
    .map((name) => ({ name, required: required.has(name) }))
    .sort((a, b) => Number(b.required) - Number(a.required) || a.name.localeCompare(b.name));
};

const CodeBlock: React.FC<{ text: string; label: string }> = ({ text, label }) => {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (await copyToClipboard(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <div className="relative">
      <pre className="max-h-72 overflow-auto rounded-control border border-border bg-accent p-sm pr-xl font-mono text-caption">
        {text}
      </pre>
      <Button
        variant="ghost"
        size="icon"
        onClick={copy}
        aria-label={`Copy ${label}`}
        className="absolute right-xxs top-xxs"
      >
        {copied ? (
          <CheckCircle2 className="size-4 text-success" aria-hidden />
        ) : (
          <Copy className="size-4" aria-hidden />
        )}
      </Button>
    </div>
  );
};

const McpReference: React.FC = () => {
  const [catalog, setCatalog] = useState<McpCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMcpTools()
      .then((c) => {
        if (!cancelled) setCatalog(c);
      })
      .catch((e) => {
        if (!cancelled) setError(formatApiError(e, 'Could not load the tool catalog.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Grouped by workflow rather than read/write (v5.168.0). A session sees only
  // its own workflow's tools, so "which of these will my agent actually get?"
  // is the first question the table has to answer; read-vs-write is a per-row
  // property and shows as a badge.
  const groups = useMemo(() => {
    const tools = catalog?.tools ?? [];
    const shared = tools.filter((t) => t.workflows?.length >= 4);
    const byWorkflow = (wf: string) =>
      tools.filter((t) => t.workflows?.includes(wf) && !shared.includes(t));
    return WORKFLOW_GROUPS.map((g) => ({
      ...g,
      tools: g.key === 'shared' ? shared : byWorkflow(g.key),
    })).filter((g) => g.tools.length > 0);
  }, [catalog]);

  // The certificate story, resolved from the same catalog call: the script URL
  // and the fingerprint to check a download against. Falls back to relative
  // paths so the commands still read correctly if the catalog call failed.
  const fingerprint = catalog?.tls_fingerprint_sha256 ?? null;
  // Self-signed is the shipped default, not an invariant: an operator can mount
  // an internal-CA or DNS-validated certificate. `null` means we couldn't read
  // the certificate, which is not the same as "it is self-signed" — so the
  // pinning block stays for null and only softens on an explicit `false`.
  const selfSigned = catalog?.tls_certificate?.self_signed ?? null;
  const keyPlaceholder = catalog?.sample_key_placeholder ?? '<your-session-key>';
  // Absolute, because the fallback is a bare path and `curl -sk /api/v1/...`
  // is not a runnable command — it has no host.
  const trustScriptUrl = new URL(
    catalog?.trust_script_url ?? '/api/v1/references/trust-cert-script',
    window.location.origin,
  ).toString();
  // Origin of the deployment, which is what the script needs to fetch the cert.
  const deploymentUrl = trustScriptUrl.replace(/\/api\/v1\/references\/.*$/, '');
  const trustScriptCommands = [
    `curl -sk ${trustScriptUrl} -o trust-cert.sh`,
    'less trust-cert.sh          # it installs a trust anchor — read it first',
    `bash trust-cert.sh --url ${deploymentUrl || 'https://<this-host>'}`,
  ].join('\n');

  // The endpoint is server-resolved; fall back to a relative path so the
  // connect snippets still read correctly if the catalog call failed.
  const endpoint = catalog?.endpoint ?? '/api/v1/mcp';

  const toolRows = (tools: McpToolDoc[]) => (
    <Table style={{ tableLayout: 'fixed' }}>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[15rem]">Tool</TableHead>
          <TableHead>What it does</TableHead>
          <TableHead className="w-[14rem]">Parameters</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tools.map((tool) => (
          <TableRow key={tool.name}>
            <TableCell className="align-top">
              <div className="min-w-0">
                <p className="truncate font-mono text-caption font-semibold" title={tool.name}>
                  {tool.name}
                </p>
                <div className="mt-xxs flex flex-wrap gap-xxs">
                  <Badge variant={tool.kind === 'read' ? 'secondary' : 'warning'}>
                    {tool.kind}
                  </Badge>
                  {tool.capability ? (
                    <Badge variant="outline" className="max-w-full truncate">
                      {CAPABILITY_LABEL[tool.capability] ?? tool.capability}
                    </Badge>
                  ) : null}
                </div>
              </div>
            </TableCell>
            <TableCell className="align-top">
              <p className="text-caption text-muted-foreground">{tool.description}</p>
              <p className="mt-xxs truncate font-mono text-caption text-muted-foreground/70">
                {tool.method} {tool.path}
              </p>
            </TableCell>
            <TableCell className="align-top">
              <div className="flex flex-wrap gap-xxs">
                {paramSummary(tool).length === 0 ? (
                  <span className="text-caption text-muted-foreground">—</span>
                ) : (
                  paramSummary(tool).map((p) => (
                    <span
                      key={p.name}
                      className={
                        p.required
                          ? 'max-w-full truncate rounded-control bg-accent px-xxs font-mono text-caption text-foreground'
                          : 'max-w-full truncate rounded-control px-xxs font-mono text-caption text-muted-foreground'
                      }
                      title={p.required ? `${p.name} (required)` : p.name}
                    >
                      {p.name}
                      {p.required ? '*' : ''}
                    </span>
                  ))
                )}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );

  return (
    <div className="p-md md:p-lg">
      <h1 className="text-page-title">MCP for AI Assist</h1>
      <p className="mt-xxs mb-md max-w-4xl text-metadata text-muted-foreground">
        MCP (Model Context Protocol) lets an AI coding assistant call this project&rsquo;s assist
        surface as <strong>native tools</strong> instead of shelling <span className="font-mono">curl</span>.
        The practical difference is approval prompts: every curl is a per-command confirmation,
        even for a pure read, whereas MCP read tools can be marked &ldquo;always allow&rdquo; once
        and then run silently.
      </p>

      <Alert variant="info" className="mb-md">
        <AlertDescription>
          MCP changes <strong>how</strong> an agent reaches this project, not{' '}
          <strong>what</strong> it may do. Every tool call re-enters the same authenticated
          endpoint a curl would hit — same key, same capability gate, same audit row. Start a
          session from a project&rsquo;s <Link to="/operations" className="underline">Operations</Link>{' '}
          page to get a key and a ready-to-paste config.
        </AlertDescription>
      </Alert>

      {/* --- Transport facts, straight off the running server --- */}
      <Card className="mb-lg">
        <CardContent className="flex flex-wrap gap-lg p-md">
          <div className="min-w-0">
            <p className="text-caption text-muted-foreground">Endpoint</p>
            <p className="truncate font-mono text-metadata" title={endpoint}>
              {endpoint}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-caption text-muted-foreground">Transport</p>
            <p className="text-metadata">Streamable HTTP · JSON-RPC 2.0</p>
          </div>
          <div className="min-w-0">
            <p className="text-caption text-muted-foreground">Protocol version</p>
            <p className="font-mono text-metadata">{catalog?.protocol_version ?? '—'}</p>
          </div>
          <div className="min-w-0">
            <p className="text-caption text-muted-foreground">Request limits</p>
            <p className="text-metadata">
              {catalog ? `${formatBytes(catalog.max_request_bytes)} body` : '—'}
              {catalog ? ` · ${catalog.max_batch_messages}-message batch` : ''}
            </p>
          </div>
        </CardContent>
      </Card>

      {/* --- Connect --- */}
      <h2 className="text-section-title">Connecting a client</h2>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        Clients disagree on config shape — VS Code reads{' '}
        <span className="font-mono">servers</span> where Claude Code reads{' '}
        <span className="font-mono">mcpServers</span>, and Codex takes neither — so use your own
        client&rsquo;s snippet rather than adapting another&rsquo;s. Starting an assist session
        emits these with the key already filled in; the placeholder below is only for reading.
      </p>
      {/* Every client fails here first, and each needs a different variable —
          so the page leads with the one command that handles both rather than
          six steps an operator has to translate for their client. */}
      <Card className="mb-sm border-warning/40">
        <CardContent className="space-y-sm p-md">
          <div className="flex gap-sm">
            <ShieldCheck className="mt-xxs size-4 shrink-0 text-warning" aria-hidden />
            <div className="min-w-0">
              <p className="text-metadata font-semibold text-foreground">
                First: trust this deployment&rsquo;s certificate
              </p>
              <p className="mt-xxs text-caption text-muted-foreground">
                {selfSigned === false ? (
                  <>
                    This deployment presents a <strong className="text-foreground">CA-issued</strong>{' '}
                    certificate, so a client whose trust store includes that CA connects with no
                    setup at all — skip this section and try connecting first. If the CA is an
                    internal one your client doesn&rsquo;t know, pin it exactly as below.
                  </>
                ) : (
                  <>
                    BlueStick defaults to a self-signed certificate, and no public CA will issue one
                    for a private address. Until the client trusts it, every connection is refused.
                  </>
                )}{' '}
                The variable{' '}
                <strong className="text-foreground">differs per client</strong>:
                VS Code and Claude Code are Node-based and read{' '}
                <span className="font-mono">NODE_EXTRA_CA_CERTS</span> (a file); Codex is a Rust
                binary and reads <span className="font-mono">SSL_CERT_DIR</span> (a directory of
                hash-named symlinks). This script installs both and prints the exports:
              </p>
            </div>
          </div>
          <CodeBlock
            text={trustScriptCommands}
            label="certificate trust setup"
          />
          <p className="text-caption text-muted-foreground">
            Read it before running it — it installs a trust anchor, which is not something to pipe
            from a download straight into a shell.
            {fingerprint ? (
              <>
                {' '}The script prints the certificate&rsquo;s SHA-256; it should match{' '}
                <span className="break-all font-mono text-foreground">{fingerprint}</span>. If it
                doesn&rsquo;t, you fetched a different host — stop.
              </>
            ) : null}
          </p>
          <p className="text-caption text-muted-foreground">
            <strong className="text-foreground">Then restart the client.</strong> Both variables
            are read at process startup, so exporting them inside a running client changes
            nothing — that is the usual reason a pin looks like it didn&rsquo;t work. Verified
            against Codex 0.147.0: pinning adds this certificate to the client&rsquo;s existing
            trust, so it keeps validating public hosts normally. That is the difference from{' '}
            <span className="font-mono">NODE_TLS_REJECT_UNAUTHORIZED=0</span>, which switches
            verification off for everything the process talks to.
          </p>
        </CardContent>
      </Card>
      <Alert variant="warning" className="mb-sm">
        <AlertDescription>
          <strong>The key is a live credential.</strong> A project-scoped config
          (<span className="font-mono">.vscode/mcp.json</span>, or{' '}
          <span className="font-mono">.mcp.json</span> from{' '}
          <span className="font-mono">claude mcp add -s project</span>) sits inside your repo — add
          it to <span className="font-mono">.gitignore</span>, or use the user-scoped location
          instead. Codex avoids the question entirely by reading the key from the environment.
          Keys expire on the session TTL and can be revoked by ending the session, but a committed
          key is a committed key until then.
        </AlertDescription>
      </Alert>
      {/* The recipes come from the server — the same builder a live session
          uses, with a placeholder key. The page used to carry its own copy in
          TypeScript, and the pair drifted twice: on the config wrapper key (the
          bug the shared builder exists to fix) and on the Codex TLS note. */}
      {catalog?.sample_clients?.length ? (
        <McpConnectPanel
          clients={catalog.sample_clients}
          blurb={`Exactly what the Start AI Assist dialog emits, with ${keyPlaceholder} standing in for the key a session mints:`}
        />
      ) : null}
      <p className="mb-lg mt-xs max-w-4xl text-caption text-muted-foreground">
        VS Code can keep the key out of the file entirely: declare an{' '}
        <span className="font-mono">inputs</span> entry and reference it as{' '}
        <span className="font-mono">${'{'}input:...{'}'}</span> in place of the key. Claude Code
        takes <span className="font-mono">-s project</span> to share the server via{' '}
        <span className="font-mono">.mcp.json</span> or <span className="font-mono">-s user</span>{' '}
        for every project — neither with a live key in it.
      </p>

      {/* --- What a call actually does --- */}
      <h2 className="text-section-title">What happens on a tool call</h2>
      <Card className="mb-lg mt-xs">
        <CardContent className="p-md">
          <ol className="ml-md list-decimal space-y-xxs text-caption text-muted-foreground">
            <li>
              Your client POSTs a JSON-RPC <span className="font-mono">tools/call</span> to{' '}
              <span className="font-mono">/api/v1/mcp</span> with your{' '}
              <span className="font-mono">X-API-Key</span> header.
            </li>
            <li>
              The MCP layer maps the tool to its real endpoint and calls it{' '}
              <strong>in-process</strong>, forwarding your key unchanged. It makes no
              authorization decision of its own.
            </li>
            <li>
              That endpoint runs its normal checks — assist-scope, capability gate, row scope —
              and records an audit row, exactly as it would for a curl.
            </li>
            <li>
              The response comes back as the tool result. A <strong>403</strong> — valid key,
              but this session may not do that — is surfaced to the agent verbatim as an error
              result, so it can see why and work around it. A <strong>401</strong> is different:
              no usable credential is a fact about the connection, not the call, so the transport
              answers a real HTTP 401 with a bearer challenge and the client can prompt for a key
              instead of retrying forever.
            </li>
          </ol>
        </CardContent>
      </Card>

      {/* --- Tools --- */}
      <h2 className="text-section-title">Available tools</h2>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        Read live from this deployment&rsquo;s server registry, so it always matches what your
        agent will see from <span className="font-mono">tools/list</span>.{' '}
        <strong className="text-foreground">A session is offered only its own workflow&rsquo;s
        tools</strong> — the key you connect with decides the group, and there is no key that spans
        them. Required parameters are
        marked <span className="font-mono">*</span>. Every tool carries MCP annotations
        (<span className="font-mono">readOnlyHint</span> and friends) so a client can offer
        &ldquo;always allow&rdquo; on the reads without you classifying them by hand, and results
        come back as <span className="font-mono">structuredContent</span> as well as text —
        except where the endpoint answers 204 with no body (setting review status), which reports a
        plain <span className="font-mono">OK</span>.
        Connecting <em>with</em> a key narrows the list to what your session may actually do.
      </p>

      {loading ? (
        <CardListSkeleton />
      ) : error ? (
        <Alert variant="destructive" className="mb-lg">
          <AlertDescription>
            Could not load the tool catalog: {error}. The rest of this page still applies — the
            tool list is the only part read from the server.
          </AlertDescription>
        </Alert>
      ) : (
        <>
          {groups.map((group) => (
            <div key={group.key} className="mb-lg">
              <div className="mb-xxs flex flex-wrap items-center gap-xs">
                {group.key === 'shared' ? (
                  <Radio className="size-4 text-info" aria-hidden />
                ) : (
                  <Lock className="size-4 text-warning" aria-hidden />
                )}
                <h3 className="text-metadata font-semibold">{group.label}</h3>
                <Badge variant="secondary">{group.tools.length}</Badge>
              </div>
              <p className="mb-xs max-w-4xl text-caption text-muted-foreground">{group.blurb}</p>
              <div className="overflow-x-auto">{toolRows(group.tools)}</div>
            </div>
          ))}
        </>
      )}

      {/* --- Authority --- */}
      <h2 className="text-section-title">What a session may do</h2>
      <Card className="mb-lg mt-xs">
        <CardContent className="space-y-sm p-md">
          <div className="flex gap-sm">
            <ShieldCheck className="mt-xxs size-4 shrink-0 text-success" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">Read-only by default.</strong> An assist session
              gets the read tools and nothing else unless the operator ticks the write box when
              starting it.
            </p>
          </div>
          <div className="flex gap-sm">
            <Lock className="mt-xxs size-4 shrink-0 text-warning" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">Project writes are capability-gated and
              row-scoped.</strong>{' '}
              The three that touch project data — notes, review status, hostname/OS — need the
              matching capability <em>and</em> a host assigned to the operator who started the
              session; anything else is refused, through MCP or otherwise. The one exception is
              <span className="font-mono"> assist_record_environment</span>, which writes session
              metadata (your OS and shell) rather than project data and is therefore open to every
              session.
            </p>
          </div>
          <div className="flex gap-sm">
            <Radio className="mt-xxs size-4 shrink-0 text-info" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">Everything is audited.</strong> Each call lands
              in the agent API log with the session, the tool, the hosts it touched, and the
              status — visible on the session&rsquo;s activity view.
            </p>
          </div>
          <div className="flex gap-sm">
            <Lock className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">Keys are short-lived.</strong> An assist key
              expires on the session&rsquo;s TTL (4 hours by default) and can be revoked at any
              time by ending the session.
            </p>
          </div>
        </CardContent>
      </Card>

      {/* --- The deliberate omission --- */}
      <h2 className="text-section-title">Writing a report over many hosts</h2>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        The full per-host dossier stream is deliberately <strong>not</strong> an MCP tool. It is a
        download-to-file, not a context load: paging thousands of hosts through tool results would
        fill the model&rsquo;s context with data it should be reading off disk. Fetch it with curl
        and point the agent at the file.
      </p>
      <CodeBlock
        text={`curl -sk -H "X-API-Key: ${keyPlaceholder}" \\\n  ${endpoint.replace(/\/mcp$/, '')}/agent/assist/report-context.ndjson \\\n  -o report-context.ndjson`}
        label="report-context download"
      />
      <p className="mt-xxs text-caption text-muted-foreground">
        One JSON object per host, uncapped — identity, ports, findings with evidence, notes, tags,
        and review state.
      </p>
    </div>
  );
};

export default McpReference;
