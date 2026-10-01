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
import { Lock, Radio, ShieldCheck } from 'lucide-react';
import { getMcpTools, type McpCatalog, type McpToolDoc } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { CardListSkeleton } from '../components/PageSkeleton';
import McpConnectPanel from '../components/McpConnectPanel';
import McpFlowDiagram from '../components/mcp/McpFlowDiagram';
import { CodeBlock } from '../components/ui/code-block';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

/** Tools grouped by capability (5.313.0). Every session is offered every
 *  tool — the key is the operator's project role, not a workflow — so the
 *  groups are presentation only. The `key` is the catalog's `workflows` tag the
 *  group is read from. */
const CAPABILITY_GROUPS: Array<{ key: string; label: string; blurb: string }> = [
  {
    key: 'assist',
    label: 'Read the inventory and write notes',
    blurb:
      'What is here, what state it is in, what the estate has a systemic problem with, and the evidence behind a finding — plus the writes your project role allows (notes, review status, corrections).',
  },
  {
    key: 'scope',
    label: 'Read a scope, upload scans',
    blurb:
      'Read a scope’s subnets and in-scope names, then upload what the scanners on your machine produced. Bulk uploads and target-file downloads stay curl — see below.',
  },
  {
    key: 'testing',
    label: 'Propose and work host tests',
    blurb:
      'Propose tests on hosts — one check each, with its command and why — and change their status as they are worked. They appear on each host’s page; nothing waits on an approval. What a test produced is recorded as evidence (record_evidence, under Session and catalogue). The commands run on your machine, under your client’s sandbox.',
  },
  {
    key: 'shared',
    label: 'Session and catalogue',
    blurb:
      'Who am I, read the guide and the tool catalogue, suggest a tool the catalogue lacks, and end the session.',
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

  // Grouped by capability; read-vs-write is a per-row property and shows as a
  // badge. A tool tagged for every kind of work is filed once, under
  // "Session and catalogue". "Every kind" follows the groups listed here
  // (5.320.0: it was a literal 4, and with three kinds the shared group never
  // rendered and each universal tool was repeated in every group).
  const groups = useMemo(() => {
    const tools = catalog?.tools ?? [];
    const kinds = CAPABILITY_GROUPS.filter((g) => g.key !== 'shared').map((g) => g.key);
    const shared = tools.filter((t) => kinds.every((k) => t.workflows?.includes(k)));
    const byWorkflow = (wf: string) =>
      tools.filter((t) => t.workflows?.includes(wf) && !shared.includes(t));
    return CAPABILITY_GROUPS.map((g) => ({
      ...g,
      tools: g.key === 'shared' ? shared : byWorkflow(g.key),
    })).filter((g) => g.tools.length > 0);
  }, [catalog]);

  // The certificate: its fingerprint to check, and whether a CA issued it.
  // `selfSigned` is null when the backend could not read it.
  const fingerprint = catalog?.tls_fingerprint_sha256 ?? null;
  const selfSigned = catalog?.tls_certificate?.self_signed ?? null;
  const keyPlaceholder = catalog?.sample_key_placeholder ?? '<your-session-key>';

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
          endpoint a curl would hit — same key, same permission checks, same audit row. Start a
          session from a project&rsquo;s <Link to="/operations" className="underline">Operations</Link>{' '}
          page to get a key and a ready-to-paste config.
        </AlertDescription>
      </Alert>

      {/* 5.313.0 — no pipeline diagram: there is no fixed order and no
          per-workflow key. */}
      <h2 className="text-section-title">One session, one key</h2>
      <p className="mt-xxs mb-lg max-w-4xl text-caption text-muted-foreground">
        One project session and key do everything your project role allows: the agent reads the
        inventory and scopes, uploads scan output, proposes tests on hosts and records what it ran
        whenever you ask it to — in any order, with nothing waiting on an approval. Every call is
        recorded against the session.
      </p>

      {/* --- Transport facts, straight off the running server --- */}
      <div className="mb-lg border-y border-border py-sm">
        <div className="flex flex-wrap gap-lg">
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
        </div>
      </div>

      {/* --- Connect --- */}
      <h2 className="text-section-title">Connecting a client</h2>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        Clients disagree on config shape — VS Code reads{' '}
        <span className="font-mono">servers</span> where Claude Code reads{' '}
        <span className="font-mono">mcpServers</span>, and Codex takes neither — so use your own
        client&rsquo;s snippet rather than adapting another&rsquo;s. Starting an assist session
        emits these with the key already filled in; the placeholder below is only for reading.
      </p>
      {/* The per-client pinning installer (scripts/trust-cert.sh) is retired:
          BlueStick's certificate comes from the organisation's local root CA
          (ca/local-ca.sh), which each analyst machine trusts once. */}
      <div className="mb-sm flex gap-sm border-l-4 border-l-info py-xs pl-md">
        <ShieldCheck className="mt-xxs size-4 shrink-0 text-info" aria-hidden />
        <div className="min-w-0 space-y-xs">
          <p className="text-metadata font-semibold text-foreground">Certificate</p>
          <p className="text-caption text-muted-foreground">
            BlueStick&rsquo;s certificate is issued by your organisation&rsquo;s local root CA
            (<span className="font-mono">ca/local-ca.sh</span>). Install that root once on the
            machine running your agent client &mdash; your administrator gives you{' '}
            <span className="font-mono">rootCA.crt</span> and its fingerprint, and{' '}
            <span className="font-mono">ca/local-ca.sh trust-help</span> prints the steps for each
            system (see <span className="font-mono">ca/README.md</span>, step 6). The client then
            trusts BlueStick with no per-client pinning.
          </p>
          {selfSigned === true ? (
            <p className="text-caption text-warning">
              This deployment still presents a self-signed certificate, so clients will refuse it.
              Ask your administrator to issue one from the local root CA.
            </p>
          ) : null}
          {fingerprint ? (
            <p className="text-caption text-muted-foreground">
              To check you reached the right server, compare the certificate your client receives
              with this SHA-256:{' '}
              <span className="break-all font-mono text-foreground">{fingerprint}</span>
            </p>
          ) : null}
        </div>
      </div>
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
          bug the shared builder exists to fix) and on a per-client note. */}
      {catalog?.sample_clients?.length ? (
        <McpConnectPanel
          clients={catalog.sample_clients}
          blurb={`Exactly what the Start Agent Session dialog emits, with ${keyPlaceholder} standing in for the key a session mints:`}
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
      <div className="mb-lg mt-xs">
        <div>
          <McpFlowDiagram />
          <ol className="ml-md mt-md list-decimal space-y-xxs text-caption text-muted-foreground">
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
              That endpoint runs its normal checks — the session&rsquo;s project, and the project
              role of the operator who started the session — and records an audit row, exactly as
              it would for a curl.
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
        </div>
      </div>

      {/* --- Tools --- */}
      <h2 className="text-section-title">Available tools</h2>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        Read live from this deployment&rsquo;s server registry, so it always matches what your
        agent will see from <span className="font-mono">tools/list</span>.{' '}
        <strong className="text-foreground">Every session is offered the whole catalogue</strong>
        — the groups below are by what a tool does, not by a key that can only reach one of
        them. Whether a given call succeeds is decided at the endpoint by your project role and
        the run it names. Required parameters are
        marked <span className="font-mono">*</span>. Every tool carries MCP annotations
        (<span className="font-mono">readOnlyHint</span> and friends) so a client can offer
        &ldquo;always allow&rdquo; on the reads without you classifying them by hand, and results
        come back as <span className="font-mono">structuredContent</span> as well as text —
        except where the endpoint answers 204 with no body (setting review status), which reports a
        plain <span className="font-mono">OK</span>.
        Connecting <em>with</em> a key lists the same tools; the key decides what each call is
        allowed to do, not which tools appear.
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
      <div className="mb-lg mt-xs">
        <div className="space-y-sm">
          <div className="flex gap-sm">
            <ShieldCheck className="mt-xxs size-4 shrink-0 text-success" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">Reads need only a valid session.</strong> Every
              session can run the read tools. The write tools are separate and gated on the
              operator&rsquo;s own project role, checked per request — see the next point.
            </p>
          </div>
          <div className="flex gap-sm">
            <Lock className="mt-xxs size-4 shrink-0 text-warning" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">An agent writes exactly what its operator
              can write.</strong>{' '}
              The tools that change project data — uploads, plans and their results, notes,
              review status, hostname/OS — succeed only if the person who started the session
              may write to the project
              (analyst or above), checked on <em>every</em> request rather than at key-mint
              time, so a role change reaches a live session immediately. An agent can ask
              first: <span className="font-mono">agent_identity</span> returns{' '}
              <span className="font-mono">can_write_project_data</span>.
            </p>
          </div>
          <div className="flex gap-sm">
            <Radio className="mt-xxs size-4 shrink-0 text-info" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">Everything is audited.</strong> Each call lands
              in the agent API log with the session, the tool, the hosts it touched, and the
              status — visible on the session&rsquo;s activity view. The client is recorded from
              the MCP handshake; the model is the agent&rsquo;s own report, passed as the optional{' '}
              <span className="font-mono">agent_model</span> argument of{' '}
              <span className="font-mono">host_tests_propose</span>,{' '}
              <span className="font-mono">record_evidence</span>, the{' '}
              <span className="font-mono">propose_*</span> tools or{' '}
              <span className="font-mono">end_session</span>.
            </p>
          </div>
          <div className="flex gap-sm">
            <Lock className="mt-xxs size-4 shrink-0 text-muted-foreground" aria-hidden />
            <p className="text-caption text-muted-foreground">
              <strong className="text-foreground">Keys are short-lived.</strong> A session key
              expires on the session&rsquo;s TTL, the agent can renew it within the session&rsquo;s
              lifetime, and ending the session revokes it at any time.
            </p>
          </div>
        </div>
      </div>

      {/* --- The deliberate omissions --- */}
      <h2 className="text-section-title">Writing the engagement up</h2>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        Most of a report comes through tools: <span className="font-mono">assist_get_posture</span>{' '}
        for the condition an executive summary states,{' '}
        <span className="font-mono">assist_get_patterns</span> for what the estate has a systemic
        problem with, and <span className="font-mono">assist_get_finding</span> for the note a
        colleague wrote to justify a finding. Two things are deliberately{' '}
        <strong>not</strong> tools, because they belong on disk rather than in the
        model&rsquo;s context.
      </p>
      <p className="mt-xxs mb-sm max-w-4xl text-caption text-muted-foreground">
        <strong className="text-foreground">The per-host dossier stream.</strong> Paging thousands
        of hosts through tool results would fill the context with data the agent should be reading
        off a file. Fetch it with curl and point the agent at the file.
      </p>
      <CodeBlock
        text={`curl -sk -H "X-API-Key: ${keyPlaceholder}" \\\n  ${endpoint.replace(/\/mcp$/, '')}/agent/assist/report-context.ndjson \\\n  -o report-context.ndjson`}
        label="report-context download"
      />
      <p className="mt-xxs mb-sm text-caption text-muted-foreground">
        One JSON object per host, uncapped — identity, ports, findings with evidence, notes, tags,
        and review state.
      </p>
      <p className="mt-sm mb-sm max-w-4xl text-caption text-muted-foreground">
        <strong className="text-foreground">Evidence screenshots.</strong>{' '}
        <span className="font-mono">assist_get_finding</span> returns each attachment as a
        reference — filename, type, size and a{' '}
        <span className="font-mono">download_path</span> — never as image bytes. A base64
        screenshot would cost thousands of tokens for a picture the model cannot show anyone, and
        the finished report needs the file sitting next to it either way. The agent saves each one
        into its working directory and links to it.
      </p>
      <CodeBlock
        text={`curl -sk -H "X-API-Key: ${keyPlaceholder}" \\\n  ${endpoint.replace(/\/mcp$/, '')}/agent/assist/attachments/<attachment-id> \\\n  -o evidence-<attachment-id>.png`}
        label="evidence attachment download"
      />
      <p className="mt-xxs text-caption text-muted-foreground">
        Project-scoped and key-authenticated — the same attachment is served to the browser under{' '}
        <span className="font-mono">/projects/…</span>, but that path wants a login session an
        agent does not have.
      </p>

      {/* --- The limit worth stating plainly --- */}
      <h2 className="mt-lg text-section-title">What these tools do not answer</h2>
      <Alert variant="info" className="mt-xs">
        <AlertDescription className="text-caption">
          <span className="font-mono">assist_get_patterns</span> compares{' '}
          <strong>across the estate</strong>, not over time — this subnet against the others, this
          condition&rsquo;s spread across the whole inventory. Nothing here answers &ldquo;what
          changed since last week&rdquo;: an engagement runs weeks, so there is no baseline to
          compare a quarter against, and the analysis is cross-sectional by design. If an agent
          phrases these findings as trends, or says something got better or worse, it is making a
          claim the data cannot support. The tool descriptions say so, but it is worth knowing when
          you read the output.
        </AlertDescription>
      </Alert>
    </div>
  );
};

export default McpReference;
