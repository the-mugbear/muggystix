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
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

/** Stand-in for the real key, which only exists inside a live assist session. */
const KEY_PLACEHOLDER = '<your-session-key>';

type ClientRecipe = {
  id: string;
  label: string;
  /** Where the snippet goes, or null when it's a command to run. */
  path: string | null;
  snippet: (endpoint: string) => string;
  note: React.ReactNode;
};

// Same three shapes the Start Assist dialog emits — kept here as a *reference*
// (placeholder key, no session), so an operator can see what they're signing up
// for before starting a session. The wrapper key genuinely differs per client:
// VS Code reads `servers`, Claude Code and Cursor read `mcpServers`.
const CLIENTS: ClientRecipe[] = [
  {
    id: 'vscode',
    label: 'VS Code Copilot',
    path: '.vscode/mcp.json',
    snippet: (endpoint) =>
      JSON.stringify(
        {
          servers: {
            'bluestick-assist': {
              type: 'http',
              url: endpoint,
              headers: { 'X-API-Key': KEY_PLACEHOLDER },
            },
          },
        },
        null,
        2,
      ),
    note: 'Start the server from the Copilot MCP panel once the file is saved. To keep the key out of the file entirely, VS Code supports a password input: declare an `inputs` entry and reference it as ${input:...} in place of the key.',
  },
  {
    id: 'claude_code',
    label: 'Claude Code',
    path: null,
    snippet: (endpoint) =>
      `claude mcp add --transport http bluestick-assist ${endpoint} \\\n  --header "X-API-Key: ${KEY_PLACEHOLDER}"`,
    note: 'Run it in your project directory. Add -s project to share it via .mcp.json, or -s user for every project.',
  },
  {
    id: 'codex',
    label: 'Codex',
    path: null,
    snippet: (endpoint) =>
      `read -rs BLUESTICK_ASSIST_KEY && export BLUESTICK_ASSIST_KEY   # paste the key, then Enter\ncodex mcp add bluestick-assist --url ${endpoint} \\\n  --bearer-token-env-var BLUESTICK_ASSIST_KEY`,
    note: 'Codex reads the env var at run time, so the key never enters config.toml. `read -rs` keeps it out of shell history — re-run it in each new shell rather than writing the key into a profile.',
  },
  {
    id: 'cursor',
    label: 'Cursor',
    path: '.cursor/mcp.json',
    snippet: (endpoint) =>
      JSON.stringify(
        {
          mcpServers: {
            'bluestick-assist': {
              type: 'http',
              url: endpoint,
              headers: { 'X-API-Key': KEY_PLACEHOLDER },
            },
          },
        },
        null,
        2,
      ),
    note: 'Use ~/.cursor/mcp.json instead to make the server available in every project.',
  },
];

const CAPABILITY_LABEL: Record<string, string> = {
  'write:notes': 'write:notes',
  'write:follow': 'write:follow',
  'write:host': 'write:host',
};

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

  const { reads, writes } = useMemo(() => {
    const tools = catalog?.tools ?? [];
    return {
      reads: tools.filter((t) => t.kind === 'read'),
      writes: tools.filter((t) => t.kind === 'write'),
    };
  }, [catalog]);

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
                {tool.capability ? (
                  <Badge variant="outline" className="mt-xxs">
                    {CAPABILITY_LABEL[tool.capability] ?? tool.capability}
                  </Badge>
                ) : null}
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
        The clients disagree on the wrapper key — VS Code reads{' '}
        <span className="font-mono">servers</span>, Claude Code and Cursor read{' '}
        <span className="font-mono">mcpServers</span> — so use your own client&rsquo;s snippet
        rather than adapting another&rsquo;s. Starting an assist session emits these with the key
        already filled in; the placeholder below is only for reading.
      </p>
      <Alert variant="warning" className="mb-sm">
        <AlertDescription>
          <strong>Self-signed certificate?</strong> Every MCP client here is Node-based and will
          refuse the default deployment certificate with{' '}
          <span className="font-mono">DEPTH_ZERO_SELF_SIGNED_CERT</span> before it sends a single
          request. Either trust the certificate on your machine, or export{' '}
          <span className="font-mono">NODE_TLS_REJECT_UNAUTHORIZED=0</span> in the shell you launch
          the client from. The error names TLS, not your config — so this is worth ruling out first.
        </AlertDescription>
      </Alert>
      <Alert variant="warning" className="mb-sm">
        <AlertDescription>
          <strong>The key in these files is a live credential.</strong> A project-scoped config
          (<span className="font-mono">.vscode/mcp.json</span>,{' '}
          <span className="font-mono">.cursor/mcp.json</span>,{' '}
          <span className="font-mono">.mcp.json</span>) sits inside your repo — add it to{' '}
          <span className="font-mono">.gitignore</span>, or use the user-scoped location instead.
          Keys expire on the session TTL and can be revoked by ending the session, but a committed
          key is a committed key until then.
        </AlertDescription>
      </Alert>
      <Tabs defaultValue={CLIENTS[0].id} className="mb-lg">
        <TabsList className="mb-xs">
          {CLIENTS.map((c) => (
            <TabsTrigger key={c.id} value={c.id}>
              {c.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {CLIENTS.map((client) => (
          <TabsContent key={client.id} value={client.id}>
            <p className="mb-xxs text-caption text-muted-foreground">
              {client.path ? (
                <>
                  Save as <span className="font-mono">{client.path}</span>
                </>
              ) : (
                'Run this command'
              )}
            </p>
            <CodeBlock text={client.snippet(endpoint)} label={`${client.label} setup`} />
            <p className="mt-xxs text-caption text-muted-foreground">{client.note}</p>
          </TabsContent>
        ))}
      </Tabs>

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
              The response comes back as the tool result. A 401 or 403 is surfaced to the agent
              verbatim as an error result, so it can see <em>why</em> it was refused.
            </li>
          </ol>
        </CardContent>
      </Card>

      {/* --- Tools --- */}
      <h2 className="text-section-title">Available tools</h2>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        Read live from this deployment&rsquo;s server registry, so it always matches what your
        agent will see from <span className="font-mono">tools/list</span>. Required parameters are
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
          <div className="mb-md">
            <div className="mb-xxs flex items-center gap-xs">
              <Radio className="size-4 text-info" aria-hidden />
              <h3 className="text-metadata font-semibold">Reads</h3>
              <Badge variant="secondary">{reads.length}</Badge>
              <span className="text-caption text-muted-foreground">
                Available to every assist session. Safe to mark &ldquo;always allow&rdquo;.
              </span>
            </div>
            <div className="overflow-x-auto">{toolRows(reads)}</div>
          </div>

          <div className="mb-lg">
            <div className="mb-xxs flex items-center gap-xs">
              <Lock className="size-4 text-warning" aria-hidden />
              <h3 className="text-metadata font-semibold">Writes</h3>
              <Badge variant="secondary">{writes.length}</Badge>
              <span className="text-caption text-muted-foreground">
                Refused unless the session was granted the matching capability.
              </span>
            </div>
            <div className="overflow-x-auto">{toolRows(writes)}</div>
          </div>
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
        text={`curl -sk -H "X-API-Key: ${KEY_PLACEHOLDER}" \\\n  ${endpoint.replace(/\/mcp$/, '')}/agent/assist/report-context.ndjson \\\n  -o report-context.ndjson`}
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
