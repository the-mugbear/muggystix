import React from 'react';
import { Link } from 'react-router-dom';
import { Bot, KeyRound, Radar, ClipboardCheck, MessagesSquare } from 'lucide-react';
import { Alert, AlertDescription } from '../../components/ui/alert';
import {
  UserGuideShell,
  GuidePage,
  GuideSection,
  Para,
  Subhead,
  OrderedList,
  UnorderedList,
  Mono,
} from './UserGuideShell';

/**
 * Agents guide — rewritten for the unified session (v2.337.0+, v5.218.0).
 *
 * The previous text described four workflow-locked keys, a 4-hour assist TTL
 * and an Assist that could never scan or plan. None of that is true any more:
 * one project-scoped session does every kind of work within the operator's
 * own role, the key TTL is the deployment's setting (24 h unless changed),
 * renewal keeps the same key, and sessions have an explicit end and a resume.
 * Every claim below was checked against the endpoint or component it names.
 */
const sections: GuideSection[] = [
  {
    id: 'how-agents-work',
    title: 'How agents work in BlueStick',
    Icon: Bot,
    summary: 'A coordinator model: the agent proposes, your terminal executes, BlueStick records.',
    content: (
      <div>
        <Para>
          BlueStick lets you connect an AI assistant of your choice (Claude Code, Codex, VS Code
          Copilot, or anything that can call an HTTPS API) to work alongside you. The agent is a{' '}
          <strong>coordinator, not an executor</strong>: it reads project data and proposes
          commands, but every target-touching command runs in <em>your</em> terminal under your
          approval, and every API call it makes is recorded.
        </Para>
        <Subhead>One session, one key, four kinds of work</Subhead>
        <Para>
          A session is bound to <strong>one project</strong> and mints <strong>one API key</strong>{' '}
          (<Mono>X-API-Key: nm_agent_…</Mono>). That single session answers questions about the
          inventory by default and can <strong>open a phase</strong> to do more — a
          reconnaissance run on a scope, a draft test plan, or an execution run on an approved
          plan. A session may open several phases over its life, and each is linked back to it.
        </Para>
        <Para>Four buttons start a session; they differ only in which phase is already open:</Para>
        <UnorderedList>
          <li><strong>Operations → <em>Start Agent Session</em></strong> — a plain session; the agent queries the project and opens phases as you ask.</li>
          <li><strong>Scope → <em>Start recon session</em></strong> (also on Recon Runs, and the Operations setup card) — a session with a reconnaissance run on that scope already open.</li>
          <li><strong>Test Plans → <em>Generate with AI</em></strong> — a session with a draft plan already created.</li>
          <li><strong><em>Execute with AI</em></strong> on an approved plan — a session with an execution run already open.</li>
        </UnorderedList>
        <Para>
          Whichever you use, the dialog shows the key <strong>once</strong>, plus two ways to hand it
          over: <em>Connect via MCP</em> (the tools appear natively in your client) or{' '}
          <em>Paste the prompt</em> (the agent drives the same session with curl). The agent reads
          its full contract from <strong>AGENTS.md</strong>, downloadable from the Reference page and
          served per-workflow at the URL baked into every prompt.
        </Para>
        <Subhead>What a key is allowed to do</Subhead>
        <Para>
          A key carries <strong>your</strong> permissions on the project, <strong>re-checked on
          every call</strong> — not frozen when it was minted. If your project role changes, you
          leave the project, or your account is disabled, the key follows immediately.
        </Para>
        <UnorderedList>
          <li><strong>Reads</strong> need current project membership — a viewer's agent sees what a viewer sees.</li>
          <li><strong>Bulk exports</strong> (the whole-project dossier, host dumps, target lists, evidence files) need <strong>auditor</strong>, the same floor the Reports and Export pages have.</li>
          <li><strong>Writes</strong> to project data — uploads, plan entries, test results, notes, corrections — need <strong>analyst</strong>. A 403 on a write is the guardrail working, not a fault.</li>
          <li>Reporting its environment, renewing its key, and filing feedback are about the session, not the project, so any member's agent can do them.</li>
          <li>Starting a plain session needs <strong>auditor</strong>; the three buttons that open a phase need <strong>analyst</strong>, because the phase changes project state.</li>
        </UnorderedList>
        <Para>Treat the key like a password with an expiry date. It is exactly as capable as you are.</Para>
        <Subhead>What runs without asking, and what stops</Subhead>
        <Para>
          Before the agent acts, it must <strong>say the bounds back</strong> in its own words —
          which project, which scope or plan, which directory, what it will run unprompted and what
          it will stop for. It gets a second, concrete read-back when it opens a phase (the scope's
          CIDRs, the plan's hosts). A command may run <strong>without waiting for your approval</strong>{' '}
          only when all three hold:
        </Para>
        <OrderedList>
          <li>The tool is in BlueStick's <Link to="/tool-reference" className="underline">approved set</Link>. Anything else it must ask for — <Mono>suggest_tool</Mono> records the gap for an admin to vet.</li>
          <li>The target is a host already in the inventory (or a name a declared in-scope domain covers). An address a name resolves to is not thereby in scope.</li>
          <li>The output lands in the session's working directory.</li>
        </OrderedList>
        <Para>
          Everything else — writing elsewhere, installing software, an unapproved tool, a host it
          inferred — stops and asks. <strong>BlueStick cannot enforce this.</strong> Commands run on
          your machine and the server sees only what the agent reports; the real boundary is your
          client's sandbox, and the session dialog hands you the flags that set it. What the server
          adds is the record.
        </Para>
        <Subhead>The environment probe</Subhead>
        <Para>
          A session's first call reports the operator's environment once — OS family, shell,
          PowerShell policy, WSL, tools on PATH. That probe rides into every run the session opens,
          so the same test intent becomes the right command for Kali and for Windows + RemoteSigned.
        </Para>
        <Alert variant="info" className="mt-sm">
          <AlertDescription>
            Every <Mono>/agent/*</Mono> call is logged. <strong>Workflows → Agent Runs</strong> shows
            each session and the runs it opened; <strong>Workflows → Agent Sessions</strong> shows
            what a session read and wrote; <strong>Collaboration → Tool Activity</strong> answers
            "which agent touched this host"; a plan's <em>API activity</em> tab filters by host,
            target IP and status code. Agents can never approve their own plans or reach user or
            admin surfaces.
          </AlertDescription>
        </Alert>
      </div>
    ),
  },
  {
    id: 'session-lifecycle',
    title: 'Keys, renewal, ending and resuming',
    Icon: KeyRound,
    summary: 'Expiry is not the control — ending is. A dead agent process has a way back.',
    content: (
      <div>
        <Subhead>Expiry and renewal</Subhead>
        <Para>
          The key's lifetime is the deployment's setting (<Mono>AGENT_KEY_TTL_HOURS</Mono>, 24 hours
          unless changed); the start dialog shows the value in effect. Recon in particular can
          outlive it: the agent starts nmap, masscan or Nessus, waits hours, and only discovers the
          key has lapsed when it tries to upload — with all the scanning already done.
        </Para>
        <Para>
          That case is handled and <strong>no work is lost</strong>. While the session is open the
          agent renews the key itself — <strong>the same key, a later deadline</strong>, and
          renewal is accepted even after expiry, so it never has to be re-bootstrapped mid-job.
          Renewal keeps working until the session reaches its maximum lifetime (7 days by default).
          You do not have to do anything, and the agent should never re-run a scan because of it.
        </Para>
        <Subhead>Ending</Subhead>
        <Para>
          <strong>Ending the session is what revokes the key</strong>, and it takes effect
          immediately. Waiting for expiry is not a revocation, because an open session can renew
          past it. A session does not end on its own until the lifetime cap:
        </Para>
        <UnorderedList>
          <li><strong>The agent ends it</strong> — its contract makes <Mono>POST /agent/session/end</Mono> (MCP <Mono>end_session</Mono>) the mandatory last step, after closing any recon or execution run it has open.</li>
          <li><strong>You end it</strong> — <em>End</em> on the session's row under Workflows → Agent Runs, or from the sessions panel in the start dialog. The session's owner or a project admin can end it; peers cannot cut off each other's agents.</li>
        </UnorderedList>
        <Subhead>Resuming after the agent process dies</Subhead>
        <Para>
          If the terminal closes or the agent hangs, the session is still open. <em>Resume</em> on
          its row under Agent Runs <strong>rotates the key</strong> — the previous one is revoked,
          the same session and its open runs are kept — and hands you the prompt and MCP setup
          again, with a notice telling the new agent to check the working directory for output the
          old one never uploaded and to read each open run's progress before continuing.
        </Para>
        <Para>
          A run that is genuinely dead can be marked <em>Abandoned</em> from its Recon Runs or
          Executions row (analyst); results already submitted stay.
        </Para>
      </div>
    ),
  },
  {
    id: 'recon',
    title: 'Reconnaissance',
    Icon: Radar,
    summary: 'The agent runs scanners locally and populates your host database.',
    content: (
      <div>
        <Para>
          <strong>Scope → Start recon session</strong> opens a session with a reconnaissance run on
          that scope; a session that is already running can open one itself when you ask. The
          agent's job is to <strong>populate BlueStick's host database</strong> for the scope: it
          reads the CIDRs and in-scope domains, runs scanners locally (nmap, masscan, rustscan,
          httpx, …) from its working directory, uploads the raw output for parsing, and iterates
          until the scope is characterised.
        </Para>
        <OrderedList>
          <li>The run's start response carries the scope's CIDRs, a size analysis, a recommended tool sequence, and the read-back the agent must state before scanning.</li>
          <li>Approval is <strong>plan-level</strong>: non-intrusive approved tools against in-scope hosts run without a prompt each; intrusive tools (nikto, nuclei, full-port or credentialed scans) and anything outside the bounds ask per command.</li>
          <li>Each upload goes through the same ingestion pipeline as a manual upload and dedupes into your hosts; the agent polls the job and fixes parse failures it caused.</li>
          <li>It reads the run summary, repeats across the scope, and calls <Mono>/agent/recon/complete</Mono>.</li>
        </OrderedList>
        <Para>
          Results land on <strong>Hosts</strong> and <strong>Scans</strong> like any other ingest; the
          run itself is under <strong>Recon Runs</strong>, and its session under Agent Runs.
        </Para>
      </div>
    ),
  },
  {
    id: 'plans',
    title: 'Test plans: generation & execution',
    Icon: ClipboardCheck,
    summary: 'Draft a structured plan from recon data, approve it, then execute with per-test gates.',
    content: (
      <div>
        <Para>
          A <strong>test plan</strong> is a prioritised, per-host list of validation and
          exploitation tests against already-known services. Generation and execution are two
          separate, human-gated steps.
        </Para>
        <Subhead>Generation</Subhead>
        <Para>
          <strong>Test Plans → Generate with AI</strong> creates a draft plan and a session bound to
          it (a running session can also create one). The agent reviews candidate hosts and drafts
          entries — each with a host, priority, test phase, and structured proposed tests (tool,
          command, expected result, references) — validates coverage, and submits the plan for
          human review. It may only propose tools from the approved set.
        </Para>
        <Subhead>Approval &amp; execution</Subhead>
        <Para>
          An analyst <strong>approves or rejects</strong>; an agent that tries to approve gets a 403.
          On an approved plan, <strong>Execute with AI</strong> opens an execution run — the one gate
          the unified session kept is that the plan must be human-approved — with three safety
          layers:
        </Para>
        <UnorderedList>
          <li><strong>Per-test approval</strong> — every command is presented as yes / modify / skip / abort before it runs.</li>
          <li><strong>Per-host sanity check</strong> — before any test on a host the agent verifies the target (a reverse-DNS lookup plus a banner grab on one known-open port, never a re-scan) and records the result; a failed check needs an explicit override reason to proceed.</li>
          <li><strong>Audit trail</strong> — every attempt, sanity check and result is recorded against the session that made it; progress is live under <strong>Executions</strong> and on the plan's Runs tab.</li>
        </UnorderedList>
        <Para>
          Once a plan is approved (or execution has started), its proposed-test list is{' '}
          <strong>locked</strong> — results reference tests by position, so changing the list would
          mis-attribute evidence. Revise while still Draft or Proposed, or clone the plan for a fresh
          revision. Plans can also be built by hand for offline workflows.
        </Para>
      </div>
    ),
  },
  {
    id: 'assist',
    title: 'AI Assist — ask anything about your project',
    Icon: MessagesSquare,
    summary: 'The default mode of every session: questions over all your project data, acting with your own permissions.',
    content: (
      <div>
        <Para>
          Every session starts in <strong>Assist</strong> mode: no phase open, the agent answers
          questions by querying BlueStick's already-ingested data and citing what it read. Start
          one from <strong>Operations → Start Agent Session</strong>. If you ask it to scan, draft a
          plan or execute, it opens the matching phase (within your role) rather than telling you
          to use another screen.
        </Para>
        <Subhead>What you can ask</Subhead>
        <Para>
          Assist runs the <strong>same boolean query language</strong> as the Hosts page (see Triage →
          Host search syntax), so it can answer questions the narrow filters can't — including
          operator-relative ones, because <Mono>follow:</Mono> and <Mono>assigned:</Mono> resolve to
          you, the person who started the session:
        </Para>
        <UnorderedList>
          <li>"Give me all hosts with port 21 exposed" → <Mono>port:21</Mono>.</li>
          <li>"Show me the hosts I have in review" → <Mono>follow:in_review</Mono>.</li>
          <li>"What's assigned to me?" → <Mono>assigned:me</Mono>.</li>
          <li>"Which hosts are exposed to Log4Shell?" → <Mono>cve:CVE-2021-44228 OR vuln:"log4j"</Mono>.</li>
          <li>Project-wide questions have their own tools — finding counts by severity, unowned findings, coverage, the worst segment, posture and patterns — so totals are computed once rather than rebuilt from per-host pages.</li>
        </UnorderedList>
        <Subhead>What it can write</Subhead>
        <UnorderedList>
          <li><strong>Notes</strong> on a host — attributed to you and marked with an <em>Agent</em> badge, so "did a person assert this?" stays answerable when notes feed findings and reports.</li>
          <li><strong>Review status</strong> — it may move a host you are following, but never marks a host <em>reviewed</em> on its own initiative.</li>
          <li><strong>Hostname / OS corrections</strong> — only when its investigation established the real value, with a note citing the evidence.</li>
        </UnorderedList>
        <Para>
          All of that needs analyst; an auditor's or viewer's session is read-only because they are.
          It sees every host in the one project it was started from and nothing in other projects.
        </Para>
        <Subhead>Connecting without the prompts</Subhead>
        <Para>
          Driving a session over <Mono>curl</Mono> means your assistant asks permission for every
          command, including pure reads. Connect it over <strong>MCP</strong> instead — one{' '}
          <Mono>bluestick</Mono> server entry serves every tool — and the read tools can be marked
          "always allow" once. See{' '}
          <Link to="/reference/mcp" className="underline">MCP for AI Assist</Link> for the per-client
          setup, the certificate step, and the full tool list.
        </Para>
        <Subhead>On Windows</Subhead>
        <Para>
          Assist needs no scanner toolchain — its "commands" are HTTPS API calls — so a Windows
          operator without WSL is fully served. Four things differ from the Linux/macOS path the
          rest of this page assumes:
        </Para>
        <UnorderedList>
          <li>
            <strong>The HTTP client.</strong> In PowerShell, bare <Mono>curl</Mono> is an alias for{' '}
            <Mono>Invoke-WebRequest</Mono> and rejects curl's flags. The pasted prompt says so; the
            agent should use <Mono>curl.exe -sk</Mono> or{' '}
            <Mono>Invoke-RestMethod -SkipCertificateCheck</Mono>, and build JSON bodies with{' '}
            <Mono>ConvertTo-Json</Mono> rather than bash single quotes.
          </li>
          <li>
            <strong>The certificate.</strong> The trust installer is a bash script. Without WSL,
            download the PEM with <Mono>curl.exe</Mono>, compare its SHA-256 with the fingerprint
            shown on the MCP reference page, and store <Mono>NODE_EXTRA_CA_CERTS</Mono> with{' '}
            <Mono>setx</Mono> so it is a per-user variable — VS Code or Claude Code launched from
            the Start menu never reads a shell profile, which is why the "add the exports to your
            profile" step does nothing on Windows. The exact PowerShell lines are in the start
            dialog's certificate step and on{' '}
            <Link to="/reference/mcp" className="underline">MCP for AI Assist</Link>.
          </li>
          <li>
            <strong>Codex.</strong> Its certificate pin (<Mono>SSL_CERT_DIR</Mono>) has only been
            verified on Linux and macOS, and its key-entry line (<Mono>read -rs</Mono>) is bash. On
            Windows, run Codex inside WSL and follow the Linux steps there.
          </li>
          <li>
            <strong>The environment probe</strong> still comes first, but for Assist it only needs{' '}
            <Mono>os_family: windows</Mono> and the shell — there is no tool inventory or preflight
            to report. Recon and execution on Windows do need one, and the agent's contract tells it
            how to build the tool list with <Mono>Get-Command</Mono> when there is no bash to run
            the preflight script.
          </li>
        </UnorderedList>
      </div>
    ),
  },
];

const AgentsGuide: React.FC = () => (
  <UserGuideShell activePath="/reference/user-guide/agents">
    <GuidePage
      intro={
        <span>
          BlueStick provides templates, guardrails, and an audit trail; your AI of choice does the
          coordinating, and you approve the actions. One project session covers everything — from
          asking questions about the data to populating it, planning against it, and executing an
          approved plan.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default AgentsGuide;
