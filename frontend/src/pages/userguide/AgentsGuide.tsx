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
 * Agents guide — rewritten for the unified session (v2.337.0+, v5.218.0), and
 * again for 5.313.0: you drive your agent, and it executes its own plans.
 *
 * Gone since 5.313.0: the per-object buttons that minted a key for one kind of
 * work, the "approved set" of tools, plan approval, and the sanity check as a
 * gate. One session per operator (Start Agent Session) is the way an agent
 * starts; the agent opens plans and execution runs itself (5.313.1: recon
 * runs are gone — it reads a scope and uploads to its session). Kept:
 * the key is your role, renewal, ending and resuming, every command shown,
 * the declared scope, the working directory and the audit trail.
 */
const sections: GuideSection[] = [
  {
    id: 'how-agents-work',
    title: 'How agents work in BlueStick',
    Icon: Bot,
    summary: 'You drive your agent; it works with your permissions, and BlueStick records everything it does.',
    content: (
      <div>
        <Para>
          BlueStick lets you connect an AI assistant of your choice (Claude Code, Codex, VS Code
          Copilot, or anything that can call an HTTPS API) to work alongside you. You drive it: it
          reads project data, runs the commands you ask for in <em>your</em> terminal — showing you
          each one — and records what it did. Every API call it makes is logged.
        </Para>
        <Subhead>One session, one key</Subhead>
        <Para>
          <strong>Operations → <em>Start Agent Session</em></strong> is the way to start an agent. A
          session is bound to <strong>one project</strong> and mints <strong>one API key</strong>{' '}
          (<Mono>X-API-Key: nm_agent_…</Mono>). The same session answers questions about the
          inventory and, when you ask, <strong>scans a scope</strong> and uploads the output,
          writes a <strong>test plan</strong>, or opens an <strong>execution run</strong> to work a
          plan — in whatever order the engagement needs. Each upload, plan and run is linked back
          to the session that made it.
        </Para>
        <Para>
          Pages that deal with one object — a scope, a plan, a host selection — have a button such
          as <em>Scan with your agent</em>, <em>Work with your agent</em> or{' '}
          <em>Have your agent draft it</em>. It opens the same start dialog with a one-line task to
          copy (for example <Mono>Work test plan #12 in BlueStick.</Mono>); if your session is
          already live, the dialog says so and you paste the task to that agent instead of
          starting another.
        </Para>
        <Para>
          The dialog shows the key <strong>once</strong>, plus two ways to hand it over:{' '}
          <em>Connect via MCP</em> (the tools appear natively in your client) or{' '}
          <em>Paste the prompt</em> (the agent drives the same session with curl). The agent reads
          its full contract from the <strong>agent guide</strong>, downloadable from the Reference
          page and served at the URL baked into every prompt.
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
          <li><strong>Writes</strong> to project data — uploads, execution runs, plans and their entries, test results, notes, corrections — need <strong>analyst</strong>. A 403 on a write is the guardrail working, not a fault.</li>
          <li>Reporting its environment, renewing its key, and filing feedback are about the session, not the project, so any member's agent can do them.</li>
        </UnorderedList>
        <Para>Treat the key like a password with an expiry date. It is exactly as capable as you are.</Para>
        <Subhead>The bounds, and what BlueStick can and cannot enforce</Subhead>
        <Para>
          Before the agent acts, it <strong>says the bounds back</strong> in its own words — which
          project, which scope or plan, which working directory — and it gets a concrete read-back
          when it opens a run (the scope's CIDRs, the plan's hosts). It shows you every command it
          runs, keeps to hosts in the inventory or names a declared in-scope domain covers (an
          address a name resolves to is not thereby in scope), and writes its output to the
          session's working directory.
        </Para>
        <Para>
          <strong>BlueStick cannot enforce any of that.</strong> Commands run on your machine and the
          server sees only what the agent reports; the real boundary is your client's sandbox and
          what you confirm in it, and the session dialog hands you the flags that set it. What the
          server adds is the record. The{' '}
          <Link to="/tool-reference" className="underline">Tool Reference</Link> is a catalogue both
          you and the agent read — it does not decide what may run; an agent that needs a tool the
          catalogue lacks records it with <Mono>suggest_tool</Mono> for an admin to add.
        </Para>
        <Subhead>The environment probe</Subhead>
        <Para>
          A session's first call reports the operator's environment once — OS family, shell,
          PowerShell policy, WSL, tools on PATH. That probe rides into every run the session opens,
          so the same test intent becomes the right command for Kali and for Windows + RemoteSigned.
        </Para>
        <Alert variant="info" className="mt-sm">
          <AlertDescription>
            Every <Mono>/agent/*</Mono> call is logged. <strong>Workflows → Agent Sessions</strong> shows
            what is live, each session with the runs it opened, and what an ended session left
            open; a session's own page shows what it read and wrote, and has its Resume and End
            controls; <strong>Workflows → Tool Activity</strong> answers
            "which agent touched this host"; a plan's <em>API activity</em> tab filters by host,
            target IP and status code. Agents never reach user or admin surfaces.
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
          unless changed); the start dialog shows the value in effect. A long scan in particular
          can outlive it: the agent starts nmap, masscan or Nessus, waits hours, and only discovers the
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
          <li><strong>The agent ends it</strong> when you tell it the work is done — its contract makes <Mono>POST /agent/session/end</Mono> (MCP <Mono>end_session</Mono>) the last step, after closing any execution run it has open.</li>
          <li><strong>You end it</strong> — <em>End</em> on the session under Workflows → Agent Sessions or on its own page, or from the sessions panel in the start dialog. The session's owner or a project admin can end it; peers cannot cut off each other's agents.</li>
        </UnorderedList>
        <Subhead>Resuming after the agent process dies</Subhead>
        <Para>
          If the terminal closes or the agent hangs, the session is still open. <em>Resume</em> on
          the session under Agent Sessions (or on its page) <strong>rotates the key</strong> — the
          previous one is revoked, the same session and its open runs are kept — and hands you the
          prompt and MCP setup again, with a notice telling the new agent to check the working
          directory for output the old one never uploaded and to read each open run's progress
          before continuing. Resuming is always done on the session: a plan or execution run links
          to its session rather than carrying a key of its own.
        </Para>
        <Para>
          A run that is genuinely dead can be marked <em>Abandoned</em> from its own page (analyst) —
          every run is linked from its session, and runs an ended session left open are listed
          under <em>Left open</em> on Agent Sessions; results already submitted stay.
        </Para>
      </div>
    ),
  },
  {
    id: 'scanning',
    title: 'Scanning a scope',
    Icon: Radar,
    summary: 'The agent reads the scope, runs scanners locally and uploads the output to its session.',
    content: (
      <div>
        <Para>
          Ask your agent to scan a scope — or use <strong>Scope → <em>Scan with your
          agent</em></strong> for a ready-made task. Its job is to <strong>populate BlueStick's host
          database</strong> for the scope: it reads the scope's subnets and in-scope domains, runs
          scanners locally (nmap, masscan, rustscan, httpx, …) from its working directory, and
          uploads the raw output to its session for parsing. There is no run to open or close — the
          uploads are the record.
        </Para>
        <OrderedList>
          <li>It reads the scope — subnets, in-scope domains, the hosts already known, live hosts and web targets — and states what it is about to scan before it starts.</li>
          <li>It shows you each command before it runs it; whether you confirm each one is up to you and your client.</li>
          <li>Each upload goes through the same ingestion pipeline as a manual upload and dedupes into your hosts; the agent polls the job and fixes parse failures it caused.</li>
          <li>It repeats until the scope is characterised.</li>
        </OrderedList>
        <Para>
          Results land on <strong>Hosts</strong> and <strong>Scans</strong> like any other ingest, as
          an upload batch attributed to the agent's session.
        </Para>
      </div>
    ),
  },
  {
    id: 'plans',
    title: 'Test plans',
    Icon: ClipboardCheck,
    summary: 'A record of what you or your agent intend to test and what came of it. Nothing waits on an approval.',
    content: (
      <div>
        <Para>
          A <strong>test plan</strong> is a prioritised, per-host list of validation and
          exploitation tests against already-known services — written by you, or by your agent, and
          kept for posterity with the results recorded against it. A plan is{' '}
          <strong>draft</strong> while it is written, <strong>in progress</strong> once a run works
          it, then <strong>completed</strong>; <em>Abandon</em> archives one you no longer want.
        </Para>
        <Subhead>Writing a plan</Subhead>
        <Para>
          Ask your agent to draft one (<strong>Test Plans → <em>Draft with your agent</em></strong>,
          or <em>Have your agent draft it</em> from a host selection on the Hosts page, which names
          exactly those hosts). It reviews candidate hosts and adds entries — each with a host,
          priority, test phase, and structured proposed tests (tool, command, expected result,
          references). You can also build a plan by hand from a host selection, or add hosts to a
          draft.
        </Para>
        <Subhead>Working a plan</Subhead>
        <Para>
          A draft with entries can be worked straight away: <strong><em>Work with your agent</em></strong>{' '}
          on the plan hands it to your session, and the agent opens an execution run, which moves
          the plan to in progress. <em>Export Bundle</em> does the same for an offline agent whose
          results you import later.
        </Para>
        <UnorderedList>
          <li><strong>Every command shown</strong> — the agent shows each command before it runs it.</li>
          <li><strong>Sanity checks as evidence</strong> — before testing a host the agent can verify the target (a reverse-DNS lookup plus a banner grab on one known-open port, never a re-scan) and record what it saw. The record is shown beside the results; nothing waits on it.</li>
          <li><strong>Audit trail</strong> — every attempt, sanity check and result is recorded against the run and the session that made it; progress is live under <strong>Executions</strong> and on the plan's Runs tab.</li>
        </UnorderedList>
        <Para>
          Once a run has started, a plan's proposed-test list is <strong>locked</strong> — results
          reference tests by position, so changing the list would mis-attribute evidence. Revise
          while it is still a draft with no run, or clone the plan for a fresh revision.
        </Para>
      </div>
    ),
  },
  {
    id: 'assist',
    title: 'Asking about your project',
    Icon: MessagesSquare,
    summary: 'The default mode of every session: questions over all your project data, acting with your own permissions.',
    content: (
      <div>
        <Para>
          Until you ask for more, a session's agent answers questions by querying BlueStick's
          already-ingested data and citing what it read. If you ask it to scan, draft a plan or work
          one, it opens the matching run itself (within your role) rather than telling you to use
          another screen.
        </Para>
        <Subhead>What you can ask</Subhead>
        <Para>
          The agent runs the <strong>same boolean query language</strong> as the Hosts page (see
          Triage → Host search syntax), so it can answer questions the narrow filters can't —
          including operator-relative ones, because <Mono>follow:</Mono> and <Mono>assigned:</Mono>{' '}
          resolve to you, the person who started the session:
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
          Asking questions needs no scanner toolchain — its "commands" are HTTPS API calls — so a
          Windows operator without WSL is fully served. Four things differ from the Linux/macOS path
          the rest of this page assumes:
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
            <strong>The environment probe</strong> still comes first, but for questions alone it
            only needs <Mono>os_family: windows</Mono> and the shell — there is no tool inventory or
            preflight to report. Scanning and execution on Windows do need one, and the agent's
            contract tells it how to build the tool list with <Mono>Get-Command</Mono> when there is
            no bash to run the preflight script.
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
          You drive your AI of choice; BlueStick gives it the project's data, a key that carries your
          own role, and an audit trail of everything it does. One project session covers everything
          — from asking questions about the data to populating it, writing test plans and working
          them.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default AgentsGuide;
