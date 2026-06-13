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
          <li>Log in with your credentials. A default admin account (<Mono>admin</Mono> / <Mono>admin</Mono>) is created on first boot and forces a password change.</li>
          <li>A default project is created automatically. Create more from <strong>Settings → Project</strong>, and switch between them with the <strong>project selector</strong> in the sidebar.</li>
          <li>Go to <strong>Inventory → Scans</strong> and upload your first scan file (drag-and-drop, multiple at once).</li>
          <li>Watch it land on <strong>Inventory → Hosts</strong> — the primary triage surface.</li>
          <li>Define your engagement boundaries in <strong>Inventory → Scopes</strong> so hosts are classified in-scope vs. out-of-scope.</li>
        </OrderedList>
        <Para>
          From there, the rest of this guide follows the work: <strong>Working with Data</strong>{' '}
          (formats &amp; ingestion), <strong>Triage &amp; Analysis</strong> (hosts, search, findings,
          posture), <strong>Agentic Workflows</strong> (recon/test/assist agents), and{' '}
          <strong>Administration</strong> (projects, users, reporting).
        </Para>
      </div>
    ),
  },
  {
    id: 'navigation',
    title: 'Navigating BlueStick',
    Icon: Compass,
    summary: 'Six hubs in the sidebar, plus Portfolio, the project selector, and the command palette.',
    content: (
      <div>
        <Para>
          The sidebar is organised into <strong>six hubs</strong>. Most hubs open a secondary tab
          strip of related pages; <strong>Operations</strong> and <strong>Posture</strong> are
          landing pages in their own right.
        </Para>
        <UnorderedList>
          <li><strong>Operations</strong> — your analyst home base: project stats, your review queue, pending approvals, and recent team notes.</li>
          <li><strong>Inventory</strong> — the data itself: <strong>Scans</strong>, <strong>Hosts</strong>, <strong>Findings</strong>, <strong>Scopes</strong>, and network <strong>Topology</strong>.</li>
          <li><strong>Posture</strong> — the analytical roll-up: the manager-facing <strong>Posture</strong> dashboard, plus <strong>Insights</strong> (per-subnet hygiene) and <strong>Systemic</strong> (estate-wide blind spots).</li>
          <li><strong>Workflows</strong> — agent-driven work: <strong>Recon Runs</strong>, <strong>Test Plans</strong>, <strong>Executions</strong>, and <strong>Agent Runs</strong>.</li>
          <li><strong>Collaboration</strong> — <strong>Activity</strong> (notes across the project), <strong>Tool Activity</strong>, and <strong>Agent Feedback</strong>.</li>
          <li><strong>Settings</strong> — <strong>Project</strong>, <strong>LLM Providers</strong>, <strong>Scanner Integrations</strong>, <strong>System</strong> (admin), <strong>Profile</strong>, <strong>Reference</strong>, and <strong>Ingestion Results</strong>.</li>
        </UnorderedList>
        <Para>
          Above the hubs, the <strong>Portfolio</strong> page gives a cross-project overview for
          anyone managing multiple engagements — a sortable table of every project you belong to;
          click a row to switch into it. What you can see and do in each project is governed by your{' '}
          <strong>per-project role</strong> (covered under Administration).
        </Para>
        <Subhead>Move faster</Subhead>
        <UnorderedList>
          <li><strong>Command palette</strong> — jump to any page or run an action without the mouse. The fastest way around once you know the page names.</li>
          <li><strong>Project selector</strong> — switch the active project from the sidebar; every data page re-scopes to it.</li>
          <li><strong>Themes</strong> — the theme selector in the sidebar offers several looks, including a phosphor terminal mode.</li>
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
          <li>Quick-nav chords jump to the main pages — e.g. <Mono>g h</Mono> Hosts, <Mono>g s</Mono> Scans, <Mono>g p</Mono> Test Plans, <Mono>g i</Mono> Inventory, <Mono>g o</Mono> Operations.</li>
          <li>Bookmark hosts you're tracking by setting a review status (Watching / In Review / Reviewed) — then filter back to them with <Mono>follow:in_review</Mono>.</li>
          <li>Upload multiple scan files at once — they're processed in parallel by the ingestion worker.</li>
          <li>Use the Activity page (Collaboration) to catch up on team notes across every host.</li>
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
