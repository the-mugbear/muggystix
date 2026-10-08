import React from 'react';
import { FolderTree, ShieldCheck, FileDown } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/table';
import { Badge } from '../../components/ui/badge';
import {
  UserGuideShell,
  GuidePage,
  GuideSection,
  Para,
  Subhead,
  UnorderedList,
  Mono,
} from './UserGuideShell';

const ROLES: { role: string; desc: string }[] = [
  { role: 'Admin', desc: 'Everything an analyst can do, plus: add and remove members and change their roles, issue a client report (and render its files again), delete a scan, manage the project’s outbound webhooks, end any agent session in the project.' },
  { role: 'Analyst', desc: 'Everything an auditor can do, plus the writes: upload scans, change the scope, review and assign hosts, tags, notes, tests and their results, promote or dismiss scanner observations, triage findings, accept or reject agent proposals, draft and revise client reports. Sees Ingestion Results.' },
  { role: 'Auditor', desc: 'Everything a viewer can do, plus getting data out: the Reports page (client reports), reading the Remediation page where a global administrator has turned remediation tracking on in System settings (who each finding on each host was assigned to, its deadline and where the fix stands; a project admin records it and follows up with the contacts), Export targets / Download inventory on Hosts, the Scope and Names exports, Create briefing on the Posture pages. Can start an agent session, which is then read-only.' },
  { role: 'Viewer', desc: 'Reads the project: Operations, hosts, names, scans, scope, findings, posture, proposals, agent sessions, collaboration, project settings. No write control, export, Reports or Remediation page is shown.' },
];

const sections: GuideSection[] = [
  {
    id: 'projects',
    title: 'Projects & roles',
    Icon: FolderTree,
    summary: 'Each engagement is an isolated project; access is governed by per-project roles.',
    content: (
      <div>
        <Para>
          Projects isolate engagement data — each has its own hosts, scans, scopes, and findings.
          Switch with the <strong>project selector</strong> in the sidebar; the{' '}
          <strong>Portfolio</strong> page lists every project you belong to.
        </Para>
        <Subhead>Lifecycle</Subhead>
        <div className="mb-sm flex flex-wrap gap-xs">
          {['active', 'completed', 'archived'].map((s) => (
            <Badge key={s} variant="outline">{s}</Badge>
          ))}
        </div>
        <Para>
          <strong>Active</strong> while the assessment is under way; <strong>completed</strong> when it is done
          (still listed); <strong>archived</strong> to take it out of the project selector.
        </Para>
        <Subhead>Per-project roles</Subhead>
        <Para>
          Users are assigned a role <em>per project</em> through project memberships, so someone can
          be an analyst on one engagement and a viewer on another. Higher roles inherit lower-role
          permissions.
        </Para>
        <div className="overflow-x-auto rounded-panel border border-border">
          <Table className="min-w-[520px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/5">Role</TableHead>
                <TableHead>Capabilities</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ROLES.map((row) => (
                <TableRow key={row.role}>
                  <TableCell><strong>{row.role}</strong></TableCell>
                  <TableCell className="text-body">{row.desc}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <Para>
          A control your role cannot use is hidden, not greyed out, and a page your role cannot read
          leaves the sidebar; the server enforces the same rule whatever the page shows. An agent
          session's key carries the role of the person who started it.
        </Para>
        <Subhead>The account role is separate</Subhead>
        <Para>
          Besides the project role, an account is either a <strong>member</strong> or a{' '}
          <strong>global administrator</strong>. A global administrator passes every project check
          and alone sees the <strong>Administration</strong> hub: <strong>All projects</strong>{' '}
          (create a project), <strong>System</strong> (accounts, worker health, the audit log) and{' '}
          <strong>Agent Feedback</strong>, plus <strong>Oversight</strong>. Deleting a project,
          changing scanner integrations and uploading report-template files are theirs too. Project
          members and roles are managed from <strong>Settings → Project</strong> by a project admin.
        </Para>
      </div>
    ),
  },
  {
    id: 'security',
    title: 'User management & security',
    Icon: ShieldCheck,
    summary: 'Authentication, lockout, sessions, and the audit trail.',
    content: (
      <div>
        <Para>
          Global administrators manage accounts from <strong>Administration → System</strong>. The platform enforces strong
          password policies, server-side session tracking, and comprehensive audit logging.
        </Para>
        <UnorderedList>
          <li><strong>JWT authentication</strong> — 8-hour token expiry; sessions are tracked server-side and can be revoked individually.</li>
          <li><strong>Sign-in lockout</strong> — counted per account and client address: 5 failed sign-ins from one address within 30 minutes lock that address out of that account (the sign-in page says so; the API answers 429). The account's owner signing in from another address is not affected. Across all addresses, an account accepts at most 100 failed sign-ins per 15 minutes.</li>
          <li><strong>Session management</strong> — view and revoke your active sessions from your <strong>Profile</strong>. Changing your password signs out every session and ends your agent sessions. An administrator resetting a user's password does the same for that user; resetting their two-factor enrolment ends their agent sessions.</li>
          <li><strong>One account per browser</strong> — every tab shares the sign-in. When another tab signs in as a different account, or signs out, this tab reloads rather than keep showing the first account's project.</li>
          <li><strong>Audit trail</strong> — actions are logged with timestamps, IP addresses, and user agents; global administrators review them under Administration → System. (The project role "auditor" does not open the audit log.)</li>
          <li><strong>HTTPS</strong> — the app is served over HTTPS only. The certificate is self-signed by default, or issued from your organisation's local root CA (<Mono>ca/local-ca.sh</Mono>), which analysts install once so browsers and agents trust the server.</li>
        </UnorderedList>
        <Para>
          Agent API keys are a separate, narrower surface — project-scoped, time-limited, carrying
          the operator's own project role, and unable to reach user or admin endpoints (see Agents).
        </Para>
      </div>
    ),
  },
  {
    id: 'reporting',
    title: 'Export & reporting',
    Icon: FileDown,
    summary: 'The client report and its addenda, host exports, and tool-ready lists.',
    content: (
      <div>
        <Para>
          Getting data out needs <strong>auditor</strong> or above on the project. There are three
          kinds: the <strong>client report</strong> built from findings, <strong>host exports</strong>{' '}
          of the filtered inventory, and <strong>tool-ready</strong> lists for the next tool.
        </Para>
        <Subhead>The client report (Findings → Reports)</Subhead>
        <UnorderedList>
          <li><strong>What it includes</strong> — confirmed, accepted-risk and remediated findings with their written report text; systems marked false positive are left out; findings still under investigation are counted, not shown.</li>
          <li><strong>Draft, preview, issue</strong> — an analyst creates and edits a draft and previews it. A project admin <strong>issues</strong> it: the report is numbered and frozen, and never changes afterwards. A correction is a <strong>revision</strong> — a new draft that supersedes the original.</li>
          <li><strong>Addenda</strong> — an addendum reports only what changed since an issued report: new findings, findings on new systems, and a finding whose <strong>severity was re-rated</strong> (shown with its previous severity). A change of title or status is not reported.</li>
          <li><strong>Formats</strong> — HTML, Word (<Mono>.docx</Mono>) and the QMD source (<Mono>.zip</Mono>), as the template allows. There is no PDF: export it from Word. In the QMD source the written text stays in <Mono>data.json</Mono> and is filled in when the report is rendered — it is not pasted into <Mono>report.qmd</Mono>.</li>
          <li><strong>Images and test results</strong> — only images ticked <em>In report</em> on a finding print, and where depends on the template; the report page says how many print in the text, how many under Evidence, and which are not printed and why. An issued report keeps its own copies of its images. With the Penetration test template, a test result that showed the finding prints as how it was confirmed (tool, command, trimmed output).</li>
          <li><strong>A large scope</strong> is not printed: the report gives totals and names a separate scope CSV with its SHA-256, which you download from the report's page and send with it.</li>
          <li><strong>Templates</strong> — three ship: Penetration test report, Executive brief, Remediation worklist. A global administrator uploads the logo, cover image and Word reference file on the Reports page.</li>
        </UnorderedList>
        <Subhead>Tool-ready lists and the inventory download (Hosts page)</Subhead>
        <UnorderedList>
          <li><strong>Export targets</strong> — the filtered host/port list formatted for Nmap, Masscan, or custom scripts. Honours the full active filter + query.</li>
          <li><strong>Download inventory</strong> — the filtered hosts as a file, every matching host in either: <em>CSV</em> (one row per host; downloads at once) or <em>JSON</em> (everything recorded per host — ports, scanner observations, findings, tests, notes — then the project&rsquo;s findings and roll-ups; prepared in the background, and you are notified when it is ready). It is not the client report, which is on the Reports page.</li>
          <li><strong>Scope and Names exports</strong> — the scope's entries and out-of-scope hosts from the Scope page; names from the Names page.</li>
        </UnorderedList>
        <Para>
          The JSON is a <strong>background report job</strong> on a dedicated worker, so the UI never
          blocks. The dialog lists recent JSON downloads with their status and lets you download a
          finished one again, retry a failed one, or dismiss it; a file is kept for a day by default.
        </Para>
      </div>
    ),
  },
];

const AdminGuide: React.FC = () => (
  <UserGuideShell activePath="/reference/user-guide/admin">
    <GuidePage
      intro={
        <span>
          Running the platform: isolating engagements into projects, controlling who can do what, and
          getting data back out as tool-ready lists or formal reports.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default AdminGuide;
