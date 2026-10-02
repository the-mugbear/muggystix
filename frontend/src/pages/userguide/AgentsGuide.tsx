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
 * again for 5.313.0: you drive your agent — and for 5.320.0: tests are
 * proposed on hosts and shown on each host's page; there are no test plans
 * and no execution runs.
 *
 * Gone since 5.313.0: the per-object buttons that minted a key for one kind of
 * work, the "approved set" of tools, plan approval, and the sanity check as a
 * gate. One session per operator (Start Agent Session) is the way an agent
 * starts (5.313.1: recon runs are gone — it reads a scope and uploads to its
 * session). Kept:
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
          <strong>proposes tests</strong> on hosts, or <strong>runs them</strong> and records what
          came back — in whatever order the engagement needs. Each upload, test and evidence
          record is linked back to the session that made it.
        </Para>
        <Para>
          Pages that deal with one object — a scope, a host, a host selection — have a button such
          as <em>Scan with your agent</em>, <em>Propose tests</em> or <em>Ask agent</em>. It opens
          the same start dialog with a one-line task to copy (for example{' '}
          <Mono>Propose tests in BlueStick for these hosts only (host ids): 12, 14.</Mono>); if your session is
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
          <li><strong>Starting a session</strong> needs <strong>auditor</strong> or above on the project; a viewer is not offered <em>Start Agent Session</em>.</li>
          <li><strong>Reads</strong> follow the page they mirror — an agent sees what its operator's role sees there.</li>
          <li><strong>Bulk exports</strong> (the whole-project dossier, host dumps, target lists) need <strong>auditor</strong>, the same floor the Reports page and the export controls have.</li>
          <li><strong>Writes</strong> to project data — uploads, host tests, evidence records, notes, corrections — need <strong>analyst</strong>. A 403 on a write is the guardrail working, not a fault.</li>
          <li>Renewing its key and filing feedback are about the session, not the project, so any member's agent can do them.</li>
        </UnorderedList>
        <Para>Treat the key like a password with an expiry date. It is exactly as capable as you are.</Para>
        <Subhead>The bounds, and what BlueStick can and cannot enforce</Subhead>
        <Para>
          Before the agent acts, it <strong>says the bounds back</strong> in its own words — which
          project, which scope (its CIDRs and in-scope domains), which working directory. It shows you every command it
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
        <Subhead>Which agent did the work</Subhead>
        <Para>
          The client (Claude Code, Codex, VS Code…) is recorded from the MCP handshake, and the
          prompt version by the server. The model is the agent's own report: it may pass{' '}
          <Mono>agent_model</Mono> when it proposes tests, records evidence, proposes a change or ends the session.
        </Para>
        <Alert variant="info" className="mt-sm">
          <AlertDescription>
            Every <Mono>/agent/*</Mono> call is logged. <strong>Workflows → Agent Sessions</strong> shows
            what is live and each session with the tests it proposed and the evidence it recorded;
            a session's own page shows what it read and wrote — its API activity filters by host,
            target IP and status code — and has its Resume and End controls;{' '}
            <strong>Workflows → Tool Activity</strong> answers "was this, at this time, part of
            our testing?" — uploaded scans and the commands agents recorded, by time, tool and
            target IP, across your projects. Agents never reach user or admin surfaces.
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
        <Subhead>What a session's state means</Subhead>
        <Para>
          A session is one row with one id — the number in its page address
          (<Mono>/agent-sessions/&lt;id&gt;</Mono>) is the same one its uploads, tests, evidence,
          notes and API calls carry. Agent Sessions shows where each stands:
        </Para>
        <UnorderedList>
          <li><strong>Live</strong> — the key is valid: an agent can use the session now.</li>
          <li><strong>Resumable</strong> — the key ran out (or was rotated away) but the session is inside its lifetime. <em>Resume</em> reconnects an agent to it.</li>
          <li><strong>Expired</strong> — the session is past its maximum lifetime and was never ended. It cannot be resumed; start a new one. Its work stays.</li>
          <li><strong>Ended</strong> — the agent or a person ended it; the key is revoked.</li>
        </UnorderedList>
        <Para>
          Operations and Agent Sessions print the same sentence about the project's sessions — how
          many are live now, and how many wait to be resumed.
        </Para>
        <Subhead>Ending</Subhead>
        <Para>
          <strong>Ending the session is what revokes the key</strong>, and it takes effect
          immediately. Waiting for expiry is not a revocation, because an open session can renew
          past it. A session does not end on its own until the lifetime cap:
        </Para>
        <UnorderedList>
          <li><strong>The agent ends it</strong> when you tell it the work is done — its contract makes <Mono>POST /agent/session/end</Mono> (MCP <Mono>end_session</Mono>) the last step. The tests it proposed and the evidence it recorded stay.</li>
          <li><strong>You end it</strong> — <em>End</em> on the session under Workflows → Agent Sessions or on its own page, or from the sessions panel in the start dialog. The session's owner or a project admin can end it; peers cannot cut off each other's agents.</li>
        </UnorderedList>
        <Subhead>Resuming after the agent process dies</Subhead>
        <Para>
          If the terminal closes or the agent hangs, the session is still open. <em>Resume</em> on
          the session under Agent Sessions (or on its page) <strong>rotates the key</strong> — the
          previous one is revoked, the same session is kept — and hands you the
          prompt and MCP setup again, with a notice telling the new agent to check the working
          directory for output the old one never uploaded and to read the tests and evidence the
          session already recorded before continuing.
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
    id: 'tests',
    title: 'Tests on hosts',
    Icon: ClipboardCheck,
    summary: 'Individual tests proposed for a host, shown on its page, with the evidence of what they produced. Nothing waits on an approval.',
    content: (
      <div>
        <Para>
          A <strong>test</strong> is one check on one host: the tool, what it establishes, the
          exact command, and why it is worth running. Tests belong to the host — open a host and
          its <strong>Tests</strong> section lists them. There is no test plan to open and no run
          to start: a test is <strong>proposed</strong>, then <strong>in progress</strong>, then{' '}
          <strong>done</strong>, or <strong>dismissed</strong> with a reason.
        </Para>
        <Subhead>Proposing tests</Subhead>
        <Para>
          Write one yourself: <strong><em>Add test</em></strong> in a host's Tests section, or{' '}
          <strong><em>Add a test</em></strong> on an open weakness (the test is then tied to that
          weakness). Say what to check and with which tool; the command, what counts as a finding
          and the reason are optional when a weakness is named. It is the same test an agent
          proposes, and you record its result the same way.
        </Para>
        <Para>
          Or ask your agent: <strong><em>Ask agent</em></strong> in a host's Tests section, or{' '}
          <strong><em>Propose tests</em></strong> on the Hosts page for a selection (which names
          exactly those hosts, up to 200), or on the Evidence page for the hosts in a gap. With a
          session already running the task is copied for you to paste; otherwise the Start Agent
          Session dialog opens with it. Say what you want tested — for example "the critical
          vulnerabilities" or "SMB signing and anonymous shares" — or leave it open and the agent
          proposes from what each host exposes. The tests appear on the hosts at once; an agent
          gives a batch one label so you can find it again (<Mono>testlabel:"…"</Mono> on the
          Hosts page).
        </Para>
        <Subhead>Tests that confirm a weakness</Subhead>
        <Para>
          Open a weakness on a host and <strong><em>Add a test</em></strong> or{' '}
          <strong><em>Ask agent to propose a test</em></strong>:
          the test is tied to that weakness. Its row then says where its tests stand
          ("1 test to do", "test showed it · no finding yet", "tested · not present"), and
          opening it lists them with <strong><em>Record result</em></strong> beside each. A
          result that shows the issue is promoted as that weakness — it joins the finding the
          issue already has, on this host, rather than starting a second one.
        </Para>
        <Subhead>Working them</Subhead>
        <Para>
          Each test is one row: its status and priority, what it checks, where it stands, and its
          command with a copy button. <strong><em>Ask agent → Run the tests to do</em></strong>{' '}
          hands them to your agent, which shows you each command before running it and records
          what came back. To work a test yourself, copy its command, run it, and press{' '}
          <strong><em>Record result</em></strong>: a panel opens beside the page with the command
          and what counts as a finding still in view. Choose what it showed (finding, no finding,
          inconclusive, could not run), write one line, and paste the output if you want it kept.
          Either way the result is <strong>evidence</strong> — the command as run, the outcome,
          the output — listed under the test. A finding or no-finding result closes the test; the
          other two leave it open. A test whose result showed an issue stays under{' '}
          <strong>To do</strong>, open, until someone makes a finding of it:{' '}
          <strong><em>Promote to finding</em></strong> when the test confirms a weakness,{' '}
          <strong><em>Create finding</em></strong> (title and severity) when it does not. The
          confirmation offers <strong><em>Write it up</em></strong>, which opens the new finding
          with its report text ready to type. Claim,
          Dismiss and Reopen are in the row's menu. Commands recorded against the host that
          answer no test are under <strong>Other evidence</strong>.
        </Para>
        <UnorderedList>
          <li><strong>Planned</strong> — a host with a test that is proposed or in progress (<Mono>has:planned</Mono>). <strong>Tested</strong> — a host with evidence of a test that ran: a finding, no finding, or inconclusive (<Mono>has:tested</Mono>). An attempt that could not run does not count.</li>
          <li><strong>A test is closed by its result</strong>, not by a "done" button — a test nobody ran is dismissed with a reason instead.</li>
          <li><strong>Two people, one test</strong> — a change made on an out-of-date copy is refused, and the list is read again, rather than one overwriting the other.</li>
          <li><strong>Your queue</strong> — tests assigned to you and tests on hosts you have in review are on Operations' Tests tab; unassigned critical or high tests anyone may claim are listed in the same table as “free to claim”, shown beside the tab's count and not counted as yours.</li>
          <li><strong>A finding</strong> an agent believes its evidence shows is a <em>proposal</em> for a person to accept — see Proposals.</li>
        </UnorderedList>
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
          already-ingested data and citing what it read. If you ask it to scan, propose tests or
          run them, it does so itself (within your role) rather than telling you to use
          another screen.
        </Para>
        <Subhead>What you can ask</Subhead>
        <Para>
          The agent runs the <strong>same boolean query language</strong> as the Hosts page (see
          Triage → Host search syntax), so it can answer questions the narrow filters can't —
          including operator-relative ones, because <Mono>follow:mine</Mono> and{' '}
          <Mono>assigned:me</Mono> resolve to you, the person who started the session (the other{' '}
          <Mono>follow:</Mono> values — <Mono>in_review</Mono>, <Mono>reviewed</Mono>,{' '}
          <Mono>none</Mono> — are about the whole team's review):
        </Para>
        <UnorderedList>
          <li>"Give me all hosts with port 21 exposed" → <Mono>port:21</Mono>.</li>
          <li>"Show me the hosts I have in review" → <Mono>follow:mine</Mono>.</li>
          <li>"What's assigned to me?" → <Mono>assigned:me</Mono>.</li>
          <li>"Which hosts are exposed to Log4Shell?" → <Mono>cve:CVE-2021-44228 OR vuln:"log4j"</Mono>.</li>
          <li>Project-wide questions have their own tools — finding counts by severity, unowned findings, coverage, the worst segment, posture and patterns — so totals are computed once rather than rebuilt from per-host pages.</li>
        </UnorderedList>
        <Subhead>What it can write</Subhead>
        <UnorderedList>
          <li><strong>Notes</strong> on a host — attributed to you and marked with an <em>Agent</em> badge, so "did a person assert this?" stays answerable. A note is discussion; what the agent ran is recorded as evidence.</li>
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
          setup, the certificate, and the full tool list.
        </Para>
        <Subhead>On Windows</Subhead>
        <Para>
          Asking questions needs no scanner toolchain — its "commands" are HTTPS API calls — so a
          Windows operator without WSL is fully served. Three things differ from the Linux/macOS path
          the rest of this page assumes:
        </Para>
        <UnorderedList>
          <li>
            <strong>The HTTP client.</strong> In PowerShell, bare <Mono>curl</Mono> is an alias for{' '}
            <Mono>Invoke-WebRequest</Mono> and rejects curl's flags. The pasted prompt says so; the
            agent should use <Mono>curl.exe -s</Mono> or <Mono>Invoke-RestMethod</Mono>, and build
            JSON bodies with <Mono>ConvertTo-Json</Mono> rather than bash single quotes. It should
            not skip certificate checking (<Mono>-k</Mono>, <Mono>-SkipCertificateCheck</Mono>)
            unless you tell it to: that accepts any server claiming BlueStick's address. Trust the
            certificate instead (next item).
          </li>
          <li>
            <strong>The certificate.</strong> Install the local root CA once, elevated:{' '}
            <Mono>certutil -addstore -f Root rootCA.crt</Mono> (your administrator gives you the file
            and its fingerprint; <Mono>ca/local-ca.sh trust-help</Mono> prints the steps for every
            system). Node-based clients (VS Code, Claude Code) may also need{' '}
            <Mono>NODE_EXTRA_CA_CERTS</Mono> pointed at that root — set it with <Mono>setx</Mono>,
            since a client launched from the Start menu never reads a shell profile.
          </li>
          <li>
            <strong>Codex.</strong> Its key-entry line (<Mono>read -rs</Mono>) is bash. On Windows,
            run Codex inside WSL and follow the Linux steps there.
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
          — from asking questions about the data to populating it, proposing tests on hosts and
          running them.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default AgentsGuide;
