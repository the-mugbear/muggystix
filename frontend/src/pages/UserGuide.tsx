import React, { useMemo, useState } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { ChevronsDownUp, ChevronsUpDown, ChevronRight } from 'lucide-react';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';

interface Section {
  title: string;
  content: React.ReactNode;
}

const INITIALLY_EXPANDED = [
  'Getting Started',
  'Supported File Formats',
  'Projects & Multi-Tenancy',
];

// Inline subhead used inside each section's content for the secondary
// headings.  Keeps the prose-style typography consistent across
// sections without inventing a new primitive.
const Subhead: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <h3 className="mb-xs mt-md text-subheading font-semibold text-foreground">{children}</h3>
);

const Para: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="mb-sm text-body leading-relaxed text-foreground">{children}</p>
);

const OrderedList: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <ol className="ml-lg list-decimal space-y-xxs text-body text-foreground marker:text-muted-foreground">
    {children}
  </ol>
);

const UnorderedList: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <ul className="ml-lg list-disc space-y-xxs text-body text-foreground marker:text-muted-foreground">
    {children}
  </ul>
);

const UserGuide: React.FC = () => {
  const navigate = useNavigate();

  const sections: Section[] = useMemo(
    () => [
      {
        title: 'Getting Started',
        content: (
          <div>
            <Para>
              BlueStick aggregates output from network scanning and reconnaissance tools into a
              single, deduplicated data model. Upload scan results, explore discovered hosts and
              services, track review progress, and coordinate with your team.
            </Para>
            <Subhead>First Steps</Subhead>
            <OrderedList>
              <li>Log in with your credentials. A default admin account is created on first boot.</li>
              <li>A default project is created automatically. Create additional projects from <strong>Project Settings</strong>.</li>
              <li>Navigate to <strong>Scans</strong> and upload your first scan file.</li>
              <li>View discovered hosts on the <strong>Hosts</strong> page.</li>
              <li>Define network boundaries in <strong>Scopes</strong> to classify in-scope vs. out-of-scope hosts.</li>
            </OrderedList>
          </div>
        ),
      },
      {
        title: 'Supported File Formats',
        content: (
          <div>
            <Para>
              BlueStick auto-detects file formats by extension and content inspection. You can
              upload multiple files at once.
            </Para>
            <div className="overflow-x-auto rounded-panel border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-1/5">Tool</TableHead>
                    <TableHead className="w-1/4">Formats</TableHead>
                    <TableHead>Notes</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[
                    { tool: 'Nmap', formats: '.xml, .gnmap', notes: 'XML format recommended for richest data (OS detection, scripts, service versions).' },
                    { tool: 'Masscan', formats: '.xml, .json, .txt', notes: 'All three output formats supported. XML gives best results.' },
                    { tool: 'Nessus', formats: '.nessus', notes: 'Vulnerability data with severity levels and plugin details.' },
                    { tool: 'OpenVAS', formats: '.xml', notes: 'Greenbone/OpenVAS XML vulnerability reports.' },
                    { tool: 'Eyewitness', formats: '.json, .csv', notes: 'Web screenshot tool results for visual enumeration.' },
                    { tool: 'httpx', formats: '.json', notes: 'Web service fingerprinting — status, title, server header, and tech stack (JSONL).' },
                    { tool: 'Nikto', formats: '.json, .csv, .txt', notes: 'Web server vulnerability scanner output.' },
                    { tool: 'NetExec', formats: '.json, .txt', notes: 'Network service enumeration results (SMB, WinRM, etc.).' },
                    { tool: 'DNS Records', formats: '.csv', notes: 'CSV format with hostname, record type, and value columns.' },
                    { tool: 'Subnet Scopes', formats: '.csv', notes: 'CIDR notation for defining network scope boundaries.' },
                    { tool: 'Naabu', formats: '.json, .txt', notes: 'Fast port scanner output.' },
                    { tool: 'Amass', formats: '.json', notes: 'Subdomain discovery results.' },
                    { tool: 'Subfinder', formats: '.json, .txt', notes: 'Subdomain discovery results.' },
                    { tool: 'dnsx', formats: '.json', notes: 'DNS resolver / record probing (A, AAAA, CNAME, MX, NS, TXT).' },
                    { tool: 'Gobuster / Dirsearch / Feroxbuster / ffuf', formats: '.txt, .csv, .json', notes: 'Web content discovery results.' },
                    { tool: 'RustScan', formats: '.txt', notes: 'Fast port scanner output.' },
                    { tool: 'BloodHound', formats: '.json', notes: 'Active Directory enumeration data.' },
                  ].map((row) => (
                    <TableRow key={row.tool}>
                      <TableCell><strong>{row.tool}</strong></TableCell>
                      <TableCell><code className="font-mono text-caption">{row.formats}</code></TableCell>
                      <TableCell>{row.notes}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        ),
      },
      {
        title: 'Projects & Multi-Tenancy',
        content: (
          <div>
            <Para>
              Projects isolate engagement data. Each project has its own hosts, scans, scopes, and
              findings. Users are assigned per-project roles through project memberships.
            </Para>
            <Subhead>Project Lifecycle</Subhead>
            <div className="mb-sm flex flex-wrap gap-xs">
              {['active', 'in_progress', 'completed', 'archived'].map((s) => (
                <Badge key={s} variant="outline">{s.replace('_', ' ')}</Badge>
              ))}
            </div>
            <Para>
              Use the <strong>project selector</strong> in the sidebar to switch between projects.
              The <strong>Portfolio</strong> page shows an overview of all your projects in one
              table.
            </Para>
            <Subhead>Project Roles</Subhead>
            <div className="overflow-x-auto rounded-panel border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-1/5">Role</TableHead>
                    <TableHead>Capabilities</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[
                    { role: 'Admin', desc: 'Full access. Manage users, projects, system settings. Can manage any agent.' },
                    { role: 'Analyst', desc: 'Upload scans, manage scopes, approve test plans, create notes, review hosts.' },
                    { role: 'Auditor', desc: 'Read-only access with audit log visibility.' },
                    { role: 'Viewer', desc: 'Read-only access to scans, hosts, and dashboards.' },
                  ].map((row) => (
                    <TableRow key={row.role}>
                      <TableCell><strong>{row.role}</strong></TableCell>
                      <TableCell>{row.desc}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        ),
      },
      {
        title: 'Uploading & Ingestion',
        content: (
          <div>
            <Para>
              Navigate to <strong>Scans</strong> and use the upload area to drag-and-drop or select
              files. Multiple files can be uploaded simultaneously. Each upload creates an ingestion
              job that processes the file asynchronously.
            </Para>
            <Subhead>Ingestion Pipeline</Subhead>
            <OrderedList>
              <li><strong>Upload</strong> — File is received and queued for processing.</li>
              <li><strong>Format Detection</strong> — Parser is auto-selected by file extension and content inspection.</li>
              <li><strong>Parsing</strong> — Host, port, and vulnerability data extracted.</li>
              <li><strong>Deduplication</strong> — Hosts are deduplicated by IP within the project. Ports are merged.</li>
              <li><strong>Correlation</strong> — Hosts are mapped to scopes/subnets automatically.</li>
            </OrderedList>
            <Para>
              View upload history and any parse errors on the <strong>Ingestion Results</strong>{' '}
              page. Failed uploads show detailed error messages to help diagnose format issues.
            </Para>
            <Subhead>Viewing scan results</Subhead>
            <Para>
              Open a scan from the <strong>Scans</strong> list to see its hosts, ports, and the
              command that produced it. <strong>DNS-resolution scans</strong> (e.g.{' '}
              <code className="font-mono">dnsx</code>) also get a <strong>DNS Records</strong> tab:
              only A / AAAA answers become hosts, so CNAME / MX / NS / TXT records are listed there
              with their resolver and TTL — the full answer set, not just the hosts the scan
              produced. The tab shows the true record count and notes when a very large result set
              is truncated.
            </Para>
          </div>
        ),
      },
      {
        title: 'Hosts & Host Detail',
        content: (
          <div>
            <Para>
              The <strong>Hosts</strong> page is the primary triage interface. It shows all
              discovered hosts in a sortable, filterable table with inline port summaries.
            </Para>
            <Subhead>Query Bar (boolean search)</Subhead>
            <Para>
              The command bar at the top of the page is the fastest way to filter. Type plain
              text to search IP / hostname / OS / port / service, or use the scoped boolean query
              language for precise queries:
            </Para>
            <UnorderedList>
              <li><strong>Fields</strong> — <code className="font-mono">port:</code>, <code className="font-mono">os:</code>, <code className="font-mono">service:</code>, <code className="font-mono">subnet:</code>, <code className="font-mono">tag:</code>, <code className="font-mono">has:</code> (e.g. <code className="font-mono">has:exploit</code>), and evidence fields <code className="font-mono">cve:</code>, <code className="font-mono">vuln:</code>, <code className="font-mono">header:</code>, <code className="font-mono">note:</code>.</li>
              <li><strong>Operators</strong> — combine with <code className="font-mono">AND</code> / <code className="font-mono">OR</code> / <code className="font-mono">NOT</code> and parentheses. A comma is OR within one field (<code className="font-mono">port:80,443</code>); repeating a field is AND (<code className="font-mono">port:80 port:443</code> = has <em>both</em>).</li>
              <li><strong>Examples</strong> — <code className="font-mono">cve:CVE-2021-44228 OR vuln:"log4j"</code>, <code className="font-mono">os:windows AND NOT tag:test</code>, <code className="font-mono">port:443 has:exploit</code>.</li>
              <li><strong>Live feedback</strong> — the bar validates as you type and shows the live match count; press <code className="font-mono">/</code> to focus it.</li>
              <li><strong>Share &amp; save</strong> — <strong>Copy link</strong> produces a URL that reproduces the exact view; <strong>Save view</strong> stores it as a named view; <strong>Convert filters → query</strong> turns the panel selections into editable query text.</li>
            </UnorderedList>
            <Subhead>Key Features</Subhead>
            <UnorderedList>
              <li><strong>Filter panel</strong> — point-and-click filters for state, OS, ports, services, subnets, tags, review status, assignment, and scan; they combine with the query bar (AND).</li>
              <li><strong>Sorting</strong> — Sort by critical / high findings, open ports, notes count, last seen, hostname, or IP (IP sorts numerically by octet).</li>
              <li><strong>Review Workflow</strong> — Mark hosts as Watching, In Review, or Reviewed to track progress.</li>
              <li><strong>Notes</strong> — Add threaded notes to hosts with @mentions for team collaboration.</li>
              <li><strong>Tool-Ready Output</strong> — Export filtered host lists in formats ready for downstream tools (IP list, Nmap targets, etc.). Exports honour the full active filter + query.</li>
            </UnorderedList>
            <Para>
              Click any host to open the <strong>Host Detail</strong> side panel, which shows complete
              port/service information, vulnerabilities, scan history, notes thread, and connection
              helper commands.
            </Para>
          </div>
        ),
      },
      {
        title: 'Scopes & Subnets',
        content: (
          <div>
            <Para>
              Scopes define the network boundaries of your engagement. Upload a CSV of CIDR ranges
              or add them manually. Hosts are automatically classified as in-scope or out-of-scope
              based on their IP address.
            </Para>
            <Subhead>How It Works</Subhead>
            <OrderedList>
              <li>Navigate to <strong>Scopes</strong> and create a scope (e.g., "Client Network").</li>
              <li>Add subnets via CSV upload or manual entry (CIDR notation like <code className="font-mono">10.0.0.0/24</code>).</li>
              <li>BlueStick automatically maps each host to matching subnets.</li>
              <li>Use the "Out of Scope" filter on the Hosts page to identify hosts outside your boundaries.</li>
            </OrderedList>
          </div>
        ),
      },
      {
        title: 'Dashboard & Portfolio',
        content: (
          <div>
            <Para>
              The <strong>Operations</strong> page shows project-level statistics: host counts, open
              ports, scan history, vulnerability breakdown, review progress, and recent notes.
            </Para>
            <Para>
              The <strong>Portfolio</strong> page provides a cross-project overview for users
              managing multiple engagements. It shows a sortable table of all your projects with
              aggregate statistics. Click any row to switch to that project's dashboard.
            </Para>
          </div>
        ),
      },
      {
        title: 'Activity & Notes',
        content: (
          <div>
            <Para>
              The <strong>Activity</strong> page shows all notes across the project, grouped by host
              with threading support. Use notes to document findings, coordinate with team members,
              and track remediation.
            </Para>
            <Subhead>Note Features</Subhead>
            <UnorderedList>
              <li><strong>Threading</strong> — Reply to existing notes to create conversations.</li>
              <li><strong>@Mentions</strong> — Tag team members with <code className="font-mono">@username</code> to send notifications.</li>
              <li><strong>Status</strong> — Notes have statuses: Open, In Progress, Resolved.</li>
              <li><strong>Notifications</strong> — Bell icon in the toolbar shows unread mention count.</li>
            </UnorderedList>
          </div>
        ),
      },
      {
        title: 'AI Agents & Test Plans',
        content: (
          <div>
            <Para>
              BlueStick supports AI agents that can programmatically read project data and
              propose structured test plans. Each user can create one agent per project.
            </Para>
            <Subhead>Agent Setup</Subhead>
            <OrderedList>
              <li>Go to <strong>Project Settings</strong> and scroll to the AI Agent section.</li>
              <li>Click <strong>Create My Agent</strong> and provide a name.</li>
              <li>Copy the API key (shown once only). Store it securely.</li>
              <li>Your external AI tool authenticates with <code className="font-mono">Authorization: Bearer nm_agent_...</code></li>
            </OrderedList>
            <Subhead>Test Plan Workflow</Subhead>
            <OrderedList>
              <li><strong>Draft</strong> — Agent or user creates a plan and adds per-host entries.</li>
              <li><strong>Proposed</strong> — Agent submits the plan for human review.</li>
              <li><strong>Approved / Rejected</strong> — Analyst reviews and approves or rejects with feedback.</li>
              <li><strong>In Progress</strong> — Testing begins, entry statuses updated as work proceeds.</li>
              <li><strong>Completed</strong> — All entries resolved, findings documented.</li>
            </OrderedList>
            <Para>
              Each test plan entry specifies a host, priority level, test phase (reconnaissance
              through reporting), proposed techniques, and rationale. Users can also create plans
              manually through the UI for offline agent workflows.
            </Para>
            <Para>
              Once a plan is <strong>approved</strong> (or an execution session has started), an
              entry's <strong>proposed test list is locked</strong> — execution results reference
              tests by position, so changing the list afterwards would mis-attribute recorded
              evidence. Revise tests while the plan is still in Draft/Proposed, or clone the plan to
              start a fresh revision.
            </Para>
            <Alert variant="info" className="mt-sm">
              <AlertDescription>
                Agents can read project data (hosts, ports, scans, scopes) but cannot approve test
                plans or access user information. API keys can be rotated or revoked at any time
                from Project Settings.
              </AlertDescription>
            </Alert>
          </div>
        ),
      },
      // TODO(risk-scoring): the "Risk Assessment" guide section is removed while
      // risk scoring is in a broken state (HostRiskAssessment is unpopulated).
      // Restore it when risk scoring is reworked (admin-tunable weights). See
      // TODO.md and frontend/src/config/featureFlags.ts.
      {
        title: 'Export & Reporting',
        content: (
          <div>
            <Para>BlueStick offers multiple export options from the Hosts page:</Para>
            <UnorderedList>
              <li><strong>Tool-Ready Output</strong> — Export filtered host/port lists formatted for tools like Nmap, Masscan, or custom scripts.</li>
              <li><strong>Reports</strong> — Generate filtered host reports with customizable columns.</li>
              <li><strong>Scope Export</strong> — Export scope coverage data and subnet host lists.</li>
            </UnorderedList>
          </div>
        ),
      },
      {
        title: 'User Management & Security',
        content: (
          <div>
            <Para>
              Admins manage users from <strong>System Settings</strong>. The platform enforces
              strong password policies, session management, and comprehensive audit logging.
            </Para>
            <Subhead>Security Features</Subhead>
            <UnorderedList>
              <li><strong>JWT Authentication</strong> — 8-hour token expiry, max 3 concurrent sessions.</li>
              <li><strong>Account Lockout</strong> — 5 failed login attempts triggers a 30-minute lockout.</li>
              <li><strong>Session Management</strong> — View and revoke active sessions from your profile.</li>
              <li><strong>Audit Trail</strong> — All actions logged with timestamps, IP addresses, and user agents.</li>
              <li><strong>HTTPS</strong> — Enforced in production with auto-generated SSL certificates.</li>
            </UnorderedList>
          </div>
        ),
      },
      {
        title: 'Keyboard Shortcuts & Tips',
        content: (
          <div>
            <UnorderedList>
              <li>Use the <strong>project selector</strong> in the sidebar to quickly switch contexts.</li>
              <li>Press <code className="font-mono">/</code> to jump to the Hosts query bar; type a boolean query, then <strong>Copy link</strong> to share the exact view.</li>
              <li>On the Hosts page, use filters or the query bar to build targeted views, then export with Tool-Ready Output.</li>
              <li>Bookmark hosts you want to track by setting a review status (Watching / In Review / Reviewed).</li>
              <li>Use the Activity page to catch up on team notes across all hosts.</li>
              <li>Upload multiple scan files at once — they'll be processed in parallel.</li>
              <li>The <strong>theme selector</strong> in the sidebar offers 5 visual themes including a phosphor terminal mode.</li>
            </UnorderedList>
          </div>
        ),
      },
    ],
    [navigate],
  );

  const allTitles = sections.map((s) => s.title);
  const [openItems, setOpenItems] = useState<string[]>(INITIALLY_EXPANDED);
  const allExpanded = openItems.length === sections.length;

  const toggleAll = () => {
    setOpenItems(allExpanded ? [] : allTitles);
  };

  return (
    <div className="p-md md:p-lg">
      <nav className="mb-sm flex items-center gap-xs text-metadata text-muted-foreground" aria-label="Breadcrumb">
        <RouterLink
          to="/reference"
          className="hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
        >
          Reference
        </RouterLink>
        <ChevronRight className="size-3" aria-hidden />
        <span className="text-foreground" aria-current="page">User Guide</span>
      </nav>

      <div className="mb-md flex flex-col gap-xs sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-page-title">User Guide</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Complete walkthrough of BlueStick features, workflows, and best practices.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={toggleAll}>
          {allExpanded ? (
            <>
              <ChevronsDownUp className="size-4" aria-hidden /> Collapse All
            </>
          ) : (
            <>
              <ChevronsUpDown className="size-4" aria-hidden /> Expand All
            </>
          )}
        </Button>
      </div>

      <Accordion
        type="multiple"
        value={openItems}
        onValueChange={setOpenItems}
        className="rounded-panel border border-border bg-card"
      >
        {sections.map((section) => (
          <AccordionItem key={section.title} value={section.title} className="px-md last:border-b-0">
            <AccordionTrigger>{section.title}</AccordionTrigger>
            <AccordionContent>{section.content}</AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>
    </div>
  );
};

export default UserGuide;
