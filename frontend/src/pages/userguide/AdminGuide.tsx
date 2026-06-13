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
  { role: 'Admin', desc: 'Full access. Manage users, projects, system settings. Can manage any agent.' },
  { role: 'Analyst', desc: 'Upload scans, manage scopes, approve test plans, create notes, review hosts, start agent sessions.' },
  { role: 'Auditor', desc: 'Read-only access with audit-log visibility.' },
  { role: 'Viewer', desc: 'Read-only access to scans, hosts, and dashboards.' },
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
          {['active', 'in_progress', 'completed', 'archived'].map((s) => (
            <Badge key={s} variant="outline">{s.replace('_', ' ')}</Badge>
          ))}
        </div>
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
          Manage members and the project's AI agent from <strong>Settings → Project</strong>; create
          users and assign system roles from <strong>Settings → System</strong> (admin only).
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
          Admins manage users from <strong>Settings → System</strong>. The platform enforces strong
          password policies, server-side session tracking, and comprehensive audit logging.
        </Para>
        <UnorderedList>
          <li><strong>JWT authentication</strong> — 8-hour token expiry; sessions are tracked server-side and can be revoked individually.</li>
          <li><strong>Account lockout</strong> — 5 failed login attempts triggers a 30-minute lockout.</li>
          <li><strong>Session management</strong> — view and revoke your active sessions from your <strong>Profile</strong>.</li>
          <li><strong>Audit trail</strong> — actions are logged with timestamps, IP addresses, and user agents; auditors and admins can review them.</li>
          <li><strong>HTTPS</strong> — enforced in production with auto-generated SSL certificates.</li>
        </UnorderedList>
        <Para>
          Agent API keys are a separate, narrower surface — project-scoped, time-limited, and unable
          to reach user or admin endpoints (see Agentic Workflows).
        </Para>
      </div>
    ),
  },
  {
    id: 'reporting',
    title: 'Export & reporting',
    Icon: FileDown,
    summary: 'Tool-ready host lists, filtered reports, and async comprehensive exports.',
    content: (
      <div>
        <Para>
          BlueStick exports at two levels: quick <strong>tool-ready</strong> lists for piping into the
          next tool, and richer <strong>reports</strong> for humans.
        </Para>
        <UnorderedList>
          <li><strong>Tool-ready output</strong> (Hosts page) — export the filtered host/port list formatted for Nmap, Masscan, or custom scripts. Honours the full active filter + query.</li>
          <li><strong>Reports</strong> — generate filtered host reports with selectable columns in CSV, HTML, or JSON.</li>
          <li><strong>Comprehensive report</strong> — a host-dossier-first export correlating each host's findings with their source, execution evidence, tester notes, and untriaged items.</li>
          <li><strong>Scope export</strong> — scope coverage data and per-subnet host lists.</li>
        </UnorderedList>
        <Para>
          Heavy formats (large bundles, PDF/JSON) run as <strong>asynchronous report jobs</strong> on
          a dedicated worker, so the UI never blocks. A reports tray shows recent jobs with live
          status and lets you re-download a completed report or dismiss it. Generated artifacts carry
          the build's <Mono>app_version</Mono> for provenance.
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
