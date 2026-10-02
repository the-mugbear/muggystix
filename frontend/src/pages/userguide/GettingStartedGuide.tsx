import React from 'react';
import { Rocket, Compass, Keyboard } from 'lucide-react';
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
    id: 'first-steps',
    title: 'First steps',
    Icon: Rocket,
    summary: 'Log in, land in a project, upload your first scan.',
    content: (
      <div>
        <Para>
          BlueStick aggregates output from network scanning and reconnaissance tools into a single,
          deduplicated data model. You upload scan results, explore discovered hosts and services,
          track review progress, and coordinate with your team — and, optionally, hand parts of that
          work to an AI agent.
        </Para>
        <Subhead>Get going in five steps</Subhead>
        <OrderedList>
          <li>Log in with your credentials. On a new deployment the first administrator account (<Mono>admin</Mono> unless the deployment names another) has the password the deployment set, or a random one written to <Mono>uploads/initial-admin-password.txt</Mono> on the server — there is no <Mono>admin</Mono> / <Mono>admin</Mono> account. The first login forces a password change, and two-factor enrolment where the deployment requires it.</li>
          <li>No project exists on a new deployment. A global administrator creates one from <strong>Administration → All projects</strong> and adds its members under <strong>Settings → Project</strong>. Switch between your projects with the <strong>project selector</strong> in the sidebar.</li>
          <li>Go to <strong>Inventory → Scans</strong> and upload your first scan file (drag-and-drop, multiple at once). Review the format BlueStick detected for each file, then import.</li>
          <li>Watch it land on <strong>Inventory → Hosts</strong> — the primary triage surface.</li>
          <li>Define your engagement boundaries in <strong>Inventory → Scope</strong> so hosts are classified in-scope vs. out-of-scope.</li>
        </OrderedList>
        <Para>
          From there, the rest of this guide follows the work: <strong>Working with Data</strong>{' '}
          (formats &amp; ingestion), <strong>Triage &amp; Analysis</strong> (hosts, search, findings,
          posture), <strong>Agents</strong> (connecting the AI assistant you drive), and{' '}
          <strong>Administration</strong> (projects, users, reporting).
        </Para>
      </div>
    ),
  },
  {
    id: 'navigation',
    title: 'Navigating BlueStick',
    Icon: Compass,
    summary: 'The hubs in the sidebar, plus Portfolio, the project selector, and the command palette.',
    content: (
      <div>
        <Para>
          The sidebar lists the project's <strong>hubs</strong>. Most hubs open a secondary tab
          strip of related pages; <strong>Operations</strong> is a single page, and{' '}
          <strong>Findings</strong> and <strong>Posture</strong> open on their first page.
        </Para>
        <UnorderedList>
          <li><strong>Operations</strong> — your own page: one sentence on what is waiting on you, each number opening the tab it counts; what changed since your last visit and any blocked imports; then a tab bar with counts — <em>Findings</em>, <em>Hosts</em>, <em>Tests</em>, <em>Changed since review</em> (hosts <em>you</em> reviewed that changed), <em>Pick up</em> (what to take next) — and one full list at a time, 10 rows a page; and one line on your own agent sessions. <em>Start Agent Session</em> is in its header. Where the project stands — hosts tested, where the team has been, scanner observations, scope — is on <strong>Posture</strong>.</li>
          <li><strong>Inventory</strong> — the data itself: <strong>Hosts</strong>, <strong>Names</strong>, <strong>Scans</strong>, <strong>Ingestion Results</strong> (analysts and above) and <strong>Scope</strong>.</li>
          <li><strong>Findings</strong> — what the engagement produces: <strong>Findings</strong> (with its Scanner observations view) and <strong>Reports</strong>, the client report (auditors and above).</li>
          <li><strong>Posture</strong> — the analytical roll-up: the <strong>Posture</strong> overview, plus <strong>Segments</strong> (per-site and per-subnet exposure and hygiene), <strong>Patterns</strong> (estate-wide weaknesses) and <strong>Evidence</strong> (what has been assessed, and the gaps).</li>
          <li><strong>Workflows</strong> — agent-driven work: <strong>Agent Sessions</strong> (what agents are doing now, the tests each session proposed, and the controls to resume or end one), <strong>Proposals</strong> and <strong>Tool Activity</strong>. Tests themselves are on each host's page.</li>
          <li><strong>Collaboration</strong> — one page: host discussions and finding comments across the project, latest first.</li>
        </UnorderedList>
        <Para>
          At the foot of the sidebar: <strong>Settings</strong> (<strong>Project</strong> — details,
          members, tags — and <strong>Scanner Integrations</strong>), <strong>Administration</strong>{' '}
          (global administrators only: <strong>All projects</strong>, <strong>System</strong> and{' '}
          <strong>Agent Feedback</strong>) and <strong>Reference</strong> (this guide, MCP setup, the
          tool reference, What BlueStick reads, default credentials, the API documentation).{' '}
          <strong>Profile</strong> and <strong>LLM Providers</strong> are about you, not the project:
          open them from the user menu or the command palette.
        </Para>
        <Para>
          Above the hubs, the <strong>Portfolio</strong> page gives a cross-project overview for
          anyone managing multiple engagements — a table of every project you belong to; click a
          project's name to open it. What you can see and do in each project is governed by your{' '}
          <strong>per-project role</strong> (covered under Administration): a page or control your
          role cannot use is not shown.
        </Para>
        <Subhead>Move faster</Subhead>
        <UnorderedList>
          <li><strong>Command palette</strong> (<Mono>Ctrl</Mono>/<Mono>⌘</Mono> + <Mono>K</Mono>) — jump to any page or run an action without the mouse. The fastest way around once you know the page names.</li>
          <li><strong>Project selector</strong> — switch the active project from the sidebar; every data page re-scopes to it.</li>
          <li><strong>Themes</strong> — the theme picker in the top bar offers several looks, including a phosphor terminal mode.</li>
          <li><strong>One account per browser</strong> — the sign-in is shared by every tab. Signing in as a different account (or signing out) in one tab reloads the others, so no tab keeps showing the previous account's project.</li>
        </UnorderedList>
      </div>
    ),
  },
  {
    id: 'shortcuts',
    title: 'Keyboard shortcuts & tips',
    Icon: Keyboard,
    summary: 'Quick navigation keys and habits that pay off.',
    content: (
      <div>
        <UnorderedList>
          <li>Press <Mono>/</Mono> on the Hosts page to focus the query bar; type a boolean query, then <strong>Copy link</strong> to share the exact view.</li>
          <li>Quick-nav chords jump to the main pages — e.g. <Mono>g h</Mono> Hosts, <Mono>g s</Mono> Scans, <Mono>g p</Mono> Proposals, <Mono>g i</Mono> Inventory, <Mono>g o</Mono> Operations.</li>
          <li>On Hosts, Findings, Scanner observations, Names and Collaboration, <Mono>j</Mono> / <Mono>k</Mono> (or the arrow keys) move a row cursor and <Mono>Enter</Mono> opens the row. On a list that refreshes itself (Proposals, Findings, Names, Collaboration) the cursor stays on its row, not on its position.</li>
          <li>On Proposals, <Mono>j</Mono> / <Mono>k</Mono> move through the queue, <Mono>Enter</Mono> opens the finding, <Mono>a</Mono> accepts the highlighted proposal and <Mono>r</Mono> opens its reject reason. Press <Mono>?</Mono> anywhere for the full list.</li>
          <li>Single-letter keys do nothing while you are typing in a field, while a dropdown or menu is open, while a dialog is open, or with a modifier key held; holding <Mono>a</Mono> or <Mono>r</Mono> down acts once.</li>
          <li>Take a host into review by setting its review status — <strong>In Review</strong>, then <strong>Reviewed</strong>. Review is shared by the team: <Mono>follow:mine</Mono> lists the hosts <em>you</em> have In Review, <Mono>follow:in_review</Mono> the hosts <em>anyone</em> has In Review, <Mono>follow:reviewed</Mono> the hosts anyone marked Reviewed, and <Mono>follow:none</Mono> the hosts nobody has taken yet.</li>
          <li>Upload multiple scan files at once — each becomes its own import, and the review lets you start all the recognised ones with one click.</li>
          <li>Use the Collaboration page to catch up on the team's discussions across every host and finding.</li>
          <li>Save a query you reuse as a <strong>named view</strong> from the Hosts command bar.</li>
        </UnorderedList>
      </div>
    ),
  },
];

const GettingStartedGuide: React.FC = () => (
  <UserGuideShell activePath="/reference/user-guide">
    <GuidePage
      intro={
        <span>
          New here? Start with the five steps below, then learn how the app is laid out so the rest
          of the guide maps onto what you see on screen.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default GettingStartedGuide;
