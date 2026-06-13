import React from 'react';
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
        <Alert variant="info" className="mt-sm">
          <AlertDescription>
            Every <Mono>/agent/*</Mono> call is logged and surfaced back to you (filterable by host,
            target IP, and status code), so you can verify exactly what the agent did. Keys are
            time-limited and can be revoked at any time; agents can never approve their own plans or
            reach user/admin surfaces.
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
    summary: 'A read-only agent that answers ad-hoc questions over all your project data.',
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
          <li><strong>Read-only</strong> — every write endpoint rejects the assist key; it can never change follow status, notes, or plans.</li>
          <li><strong>Project-scoped</strong> — it sees all hosts in the one project you started it from, and nothing in other projects.</li>
          <li><strong>Short-lived</strong> — assist keys expire quickly (4h by default) and can be ended at any time.</li>
          <li><strong>Who can start one</strong> — analyst role or above (auditors/viewers cannot mint a key).</li>
        </UnorderedList>
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
