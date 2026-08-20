import React from 'react';
import { Link } from 'react-router-dom';
import { Bot, Radar, ClipboardCheck, TerminalSquare, MessagesSquare } from 'lucide-react';
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

const sections: GuideSection[] = [
  {
    id: 'how-agents-work',
    title: 'How agents work in BlueStick',
    Icon: Bot,
    summary: 'A coordinator model: the agent proposes, your terminal executes, BlueStick records.',
    content: (
      <div>
        <Para>
          BlueStick lets you connect an AI assistant of your choice (Claude Code, Codex, ChatGPT, …)
          to work alongside you. The agent is a <strong>coordinator, not an executor</strong>: it
          reads project data and proposes commands, but every target-touching command runs in{' '}
          <em>your</em> terminal under your approval, and every API call it makes is recorded.
        </Para>
        <Subhead>Four workflows, four keys</Subhead>
        <Para>
          Each workflow is started from the UI, which mints a <strong>scope-bound, time-limited API
          key</strong> and a copy-pasteable instructions block. The key is locked to exactly one
          workflow — cross-workflow calls are rejected — and to one project:
        </Para>
        <UnorderedList>
          <li><strong>Reconnaissance</strong> — populate host data for a scope from scanner output (Scopes → <em>Start Agentic Recon</em>).</li>
          <li><strong>Test plan generation</strong> — draft a structured test plan from already-scanned hosts (Test Plans → <em>Generate with AI</em>).</li>
          <li><strong>Execution</strong> — work through an approved plan with per-test approval (<em>Execute with AI</em> on an approved plan).</li>
          <li><strong>AI Assist</strong> — read-only, ask-anything queries over your project (Operations → <em>AI Assist</em>).</li>
        </UnorderedList>
        <Para>
          The agent reads its full contract from <strong>AGENTS.md</strong> (downloadable from the
          Reference page; the deployment-specific URL is baked into each instructions block). You
          authenticate with <Mono>X-API-Key: nm_agent_…</Mono>.
        </Para>
        <Subhead>What a key is allowed to do</Subhead>
        <Para>
          A key carries <strong>your</strong> permissions on the project, re-checked on every single
          call — not frozen at the moment it was minted. If your project role changes, or you are
          removed from the project, or your account is disabled, the key follows immediately rather
          than staying powerful until it expires. An auditor's agent is read-only for the same
          reason yours is not: because that is what <em>they</em> can do.
        </Para>
        <Para>
          Bulk exports are held to the same bar a person is. Pulling the whole-project dossier, a
          host dump, or evidence files needs <strong>auditor</strong> — the role the equivalent
          Reports and Export pages already require. An agent cannot be used to get data its
          operator would be refused in the UI.
        </Para>
        <Para>
          Treat the key itself like a password with an expiry date. It is as capable as you are.
        </Para>
        <Subhead>When a key expires mid-run</Subhead>
        <Para>
          Recon in particular can outlive its key: the agent starts nmap, masscan, or Nessus, waits
          hours for it to finish, and only discovers the key has lapsed when it tries to upload the
          results — with all the scanning already done.
        </Para>
        <Para>
          That case is handled and <strong>no work is lost</strong>. While the session is still open,
          the agent renews the key itself — same key, later deadline — and retries the upload. You do
          not have to do anything, and the agent should never re-run a scan because of it. Renewal
          keeps working until the session reaches its maximum lifetime (7 days by default); after
          that, or once you end the session, the key is finished and you start a new one.
        </Para>
        <Para>
          <strong>Ending the session is what revokes a key</strong> — it takes effect immediately.
          Waiting for expiry is not a revocation, because an open session can renew past it.
        </Para>
        <Alert variant="info" className="mt-sm">
          <AlertDescription>
            Every <Mono>/agent/*</Mono> call is logged and surfaced back to you (filterable by host,
            target IP, and status code), so you can verify exactly what the agent did. Agents can
            never approve their own plans or reach user/admin surfaces.
          </AlertDescription>
        </Alert>
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
          Start from <strong>Scopes → Start Agentic Recon</strong>. The agent's job is to{' '}
          <strong>populate BlueStick's host database</strong> for a scope: it reads the scope's CIDRs
          and a suggested tool sequence, runs scanners locally (nmap, masscan, rustscan, httpx, …),
          uploads the raw output for parsing, and iterates until the scope is characterised.
        </Para>
        <OrderedList>
          <li>The agent fetches scope context — CIDRs, size analysis, and a recommended tool sequence tuned to scope size.</li>
          <li>It proposes each scanner command for your approval, runs it locally, and uploads the machine-readable output.</li>
          <li>BlueStick parses each upload through the same ingestion pipeline as a manual upload, deduping into your hosts.</li>
          <li>It polls progress and repeats across the scope, then closes the session.</li>
        </OrderedList>
        <Para>
          Results land on your <strong>Hosts</strong> and <strong>Scans</strong> pages like any other
          ingest; the run itself is visible under <strong>Workflows → Recon Runs</strong>.
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
          A <strong>test plan</strong> is a prioritised, per-host list of validation/exploitation
          tests against already-known services. Generation and execution are two separate, human-gated
          steps.
        </Para>
        <Subhead>Generation</Subhead>
        <Para>
          From <strong>Test Plans → Generate with AI</strong>, the agent reviews candidate hosts and
          drafts entries — each with a host, priority, test phase, and structured proposed tests (tool,
          command, expected result, references) — then submits the plan for human review.
        </Para>
        <Subhead>Approval &amp; execution</Subhead>
        <Para>
          You review and <strong>approve or reject</strong> (agents can never self-approve). On an
          approved plan, <strong>Execute with AI</strong> drives execution with three safety layers:
        </Para>
        <UnorderedList>
          <li><strong>Per-test approval</strong> — every command is presented for yes / modify / skip / abort before it runs.</li>
          <li><strong>Per-host sanity check</strong> — the target is verified (reverse DNS + a banner grab on a known port) before any test, so you never test the wrong host.</li>
          <li><strong>Audit trail</strong> — every attempt, sanity check, and result is recorded; progress is visible live under <strong>Workflows → Executions</strong>.</li>
        </UnorderedList>
        <Para>
          Once a plan is approved (or execution has started), its proposed-test list is{' '}
          <strong>locked</strong> — results reference tests by position, so changing the list would
          mis-attribute evidence. Revise while still in Draft/Proposed, or clone the plan for a fresh
          revision. You can also build plans manually for offline workflows.
        </Para>
      </div>
    ),
  },
  {
    id: 'assist',
    title: 'AI Assist — ask anything about your project',
    Icon: MessagesSquare,
    summary: 'An agent that answers ad-hoc questions over all your project data — read-only unless you grant narrow writes.',
    content: (
      <div>
        <Para>
          <strong>AI Assist</strong> (Operations → <em>AI Assist</em>) connects an AI of your choice
          as a <strong>read-only research partner</strong> over your whole project. No scanning, no
          plan creation, no execution — it answers questions by querying BlueStick's already-ingested
          data and citing what it read.
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
        </UnorderedList>
        <Subhead>How it's bounded</Subhead>
        <UnorderedList>
          <li><strong>Read-only unless you say otherwise</strong> — by default every write endpoint rejects the assist key. Ticking the write box when you start a session grants exactly three narrow writes (add a note, set review status, correct hostname/OS), and only on hosts <em>assigned to you</em>. Scanning, plan creation, and execution are never available.</li>
          <li><strong>Project-scoped</strong> — it sees all hosts in the one project you started it from, and nothing in other projects.</li>
          <li><strong>Short-lived, but recoverable</strong> — assist keys expire quickly (4h by default) and can be ended at any time. If a key lapses while the session is still open, the agent renews it itself and carries on — see <em>When a key expires mid-run</em> below.</li>
          <li><strong>Who can start one</strong> — auditor role or above. Recon, plan generation and execution still require analyst, because they exist to change project state.</li>
          <li><strong>It can only do what you can do</strong> — the key acts with <em>your</em> permissions on the project, re-checked on every call. If your role changes or you leave the project, the key follows immediately.</li>
        </UnorderedList>
        <Subhead>Connecting without the prompts</Subhead>
        <Para>
          Driving Assist over <Mono>curl</Mono> means your assistant asks permission for every
          single command, including pure reads. Connect it over <strong>MCP</strong> instead and the
          read tools can be marked "always allow" once — see{' '}
          <Link to="/reference/mcp" className="underline">MCP for AI Assist</Link> for the setup and
          the full tool list.
        </Para>
        <Alert variant="info" className="mt-sm">
          <AlertDescription>
            Assist runs on any OS — its "commands" are HTTPS API calls, so Windows, macOS, and Linux
            operators are all first-class. When you ask it to do something it can't (scan, create a
            plan, change status), it tells you which UI surface to use instead.
          </AlertDescription>
        </Alert>
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
          coordinating, and you approve the actions. Four workflows, from populating data to asking
          questions about it.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default AgentsGuide;
