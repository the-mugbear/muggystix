import React from 'react';
import { FolderTree, ShieldCheck, FileDown, PenLine } from 'lucide-react';
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
          (create a project), <strong>System</strong> (worker health — with the host&apos;s memory and load, each process&apos;s memory against its own limit, and the database&apos;s size and connections —, accounts, remediation
          tracking, the installation&rsquo;s report author and disclaimer, report writing guidance — its
          own section below), <strong>Audit log</strong> and{' '}
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
    id: 'report-writing',
    title: 'Report writing guidance',
    Icon: PenLine,
    summary: 'The instructions an AI is given for each section of a finding’s report text — one set for the installation.',
    content: (
      <div>
        <Para>
          A finding&rsquo;s report text can be drafted by an AI in two ways: <em>Draft empty
          sections</em> on the finding&rsquo;s page (the signed-in person&rsquo;s own LLM provider),
          and an agent session proposing a section. Both are told how to write by the same record,
          which a <strong>global administrator</strong> edits under{' '}
          <strong>Administration → System → Report writing</strong>. It applies to every project:
          set it once to your organisation&rsquo;s house style.
        </Para>
        <Subhead>The boxes</Subhead>
        <UnorderedList>
          <li><strong>Every section</strong> — voice and audience for all of them (who is writing, for whom, how plainly).</li>
          <li><strong>Description</strong>, <strong>Impact</strong>, <strong>Recommendation</strong>, <strong>Steps to reproduce</strong>, <strong>References</strong> — what that one section must contain: length, structure, what it has to name, what to leave out.</li>
        </UnorderedList>
        <Para>
          Each box holds up to 4,000 characters and starts on a short shipped default, marked{' '}
          <em>Default</em>. An edited box offers <em>Reset to default</em>, and an emptied box goes
          back to its default when saved. Only the boxes you changed are saved; the page says who
          last changed the guidance and when, and each change is written to the audit log.
        </Para>
        <Subhead>Writing it</Subhead>
        <UnorderedList>
          <li>Write to the model as you would brief a new colleague: &ldquo;Two short paragraphs: what the weakness is, then where it was seen. No CVSS wording.&rdquo;</li>
          <li>Keep to style and content. The guidance decides how a section reads; it cannot make a model state what the data does not show.</li>
          <li>Say it once, in the right box: something true of every section goes in <em>Every section</em>.</li>
          <li>Do not paste client names or secrets: the text is sent to whichever LLM provider drafts, and every agent session can read it.</li>
        </UnorderedList>
        <Subhead>Rules that always apply</Subhead>
        <Para>
          Under the boxes, <em>Rules that always apply</em> lists what is not editable. They come
          after your guidance and win over it:
        </Para>
        <UnorderedList>
          <li>Every statement is grounded in the finding&rsquo;s data — no invented hosts, CVEs, versions or results.</li>
          <li>A section the data cannot support is <strong>declined</strong>, with what is missing said beside it — never filled with a guess, a placeholder or a note to the analyst.</li>
          <li>The reader is the client: no mention of BlueStick, a record number from it, the analysts&rsquo; notes or how the text was produced.</li>
          <li>Each section is its complete text in Markdown, without headings.</li>
        </UnorderedList>
        <Subhead>When it takes effect, and what it does not do</Subhead>
        <UnorderedList>
          <li><strong>The next draft</strong> is written to it. Text already written, and proposals already waiting, are not rewritten.</li>
          <li><strong>An agent reads it when it reads the finding</strong> (or on its own, before proposing a new finding with report text), so a running session picks up a change at its next read — no Resume is needed.</li>
          <li><strong>Nothing is applied without a person.</strong> A draft or an agent&rsquo;s section is a proposal, reviewed beside the current text on the finding&rsquo;s page or under <strong>Agents → Proposals</strong>.</li>
          <li><strong>It does not bind people.</strong> What an analyst types into a section is saved as written.</li>
          <li><strong>It is not the report template.</strong> Layout, headings, logo and which sections print belong to the template (Export &amp; reporting, below).</li>
        </UnorderedList>
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
          <li><strong>Draft, preview, issue</strong> — an analyst creates and edits a draft and previews it. <em>New report</em> on a project that already has a draft first offers to <em>continue</em> the most recently updated one (or another), with <em>Start a new draft</em> beside it; <em>New addendum</em> does the same for drafts of an addendum to the same report. A draft&rsquo;s page opens with one <strong>Before issuing</strong> checklist: the report details still empty, the findings still missing report text, proposals nobody has decided, images without a caption, text naming a BlueStick record, and the notes the client will read — each line opens where it is fixed. A detail puts the caret in its field under Report details; a finding opens its report text with a bar that says how many are left and steps to the <em>Previous</em> and <em>Next</em> one, and <em>Back to the report</em>. The <strong>template</strong> is chosen under the report&rsquo;s title, and is the one it is issued with; the (i) beside it says what the template is for and how its files stand, and a required template file that is not installed is said on the page, because it stops preview and issue. A preview renders in the background and the page says when it is ready; an HTML preview <em>opens in a new tab</em> (if the browser blocks the tab, the file is downloaded instead, and the page says so), and every preview can be downloaded: its <em>Download</em> button shows <em>Downloading…</em> and then <em>Downloaded</em>, so a file is not fetched twice by mistake (an issued report&rsquo;s files do the same). The assessment team and the distribution list print in the order shown: drag a row by its grip, or use its <em>Move up</em> / <em>Move down</em> buttons. A project admin <strong>issues</strong> it: the report is numbered and frozen, and never changes afterwards. A correction is a <strong>revision</strong> — a new draft that supersedes the original.</li>
          <li><strong>Addenda</strong> — an addendum reports only what changed since an issued report: new findings, findings on new systems, and a finding whose <strong>severity was re-rated</strong> (shown with its previous severity). A change of title or status is not reported.</li>
          <li><strong>Formats</strong> — HTML, Word (<Mono>.docx</Mono>) and the QMD source (<Mono>.zip</Mono>), as the template allows. There is no PDF: export it from Word. In the QMD source the written text stays in <Mono>data.json</Mono> and is filled in when the report is rendered — it is not pasted into <Mono>report.qmd</Mono>.</li>
          <li><strong>Images and test results</strong> — only images ticked <em>In report</em> on a finding print, and where depends on the template; the report page says how many print in the text, how many under Evidence, and which are not printed and why. An issued report keeps its own copies of its images. With the Penetration test template, a finding&rsquo;s Evidence section prints its written <em>Evidence</em> text, then each test result that showed the finding as how it was confirmed (tool, command, trimmed output), then every ticked image no section places — each after the text of the comment or note it is attached to, as written. The report page says how many notes print that way, and the issue dialog repeats it.</li>
          <li><strong>Report details follow the project</strong> — client, author, classification, engagement type, team, distribution, system description, other targets and disclaimer are set once in <em>Reports → Report defaults</em>, and every draft follows them until it is issued: change a default and every draft that follows it changes too. On a draft&rsquo;s page a followed detail shows the project&rsquo;s value as text, marked <em>From the project&rsquo;s defaults</em> (or <em>From the installation&rsquo;s defaults</em>, or <em>Not set in the project&rsquo;s defaults</em>); <em>Override for this report</em> turns it into this report&rsquo;s own copy to edit, and <em>Revert to the project&rsquo;s</em> follows the project again. A change can become the standard for future drafts: <em>Make this the project&rsquo;s default</em> on an overridden detail saves it to the project&rsquo;s defaults and the draft follows them again (if the project already holds a different value, you are asked first — every draft that follows it changes too); <em>Set for the project</em> on a detail the project leaves empty (or <em>Edit the project&rsquo;s default</em> on one it sets) edits it in place and saves it to the defaults, not to this report. Each detail shows one of these as a link — <em>Set for the project</em> where the project leaves it empty, <em>Revert to the project&rsquo;s</em> where the report overrides it, <em>Override for this report</em> otherwise — and the others are in the small &ldquo;⋯&rdquo; menu beside it. The team and the distribution list are overridden, and made the default, as a whole — a report can list nobody on purpose. <em>Continue a draft?</em> says who started each draft and when, when it last changed, which details it overrides and its template when not the project&rsquo;s; whoever started a draft, or a project admin, can <em>Discard</em> it there. An addendum or a revision starts with the details its report was issued with. Issued reports keep what they were issued with.</li>
          <li><strong>Author and disclaimer</strong> — who produced the report, and the disclaimer it closes with: the report&rsquo;s override, else the project&rsquo;s default, else the installation&rsquo;s, which a global administrator sets under Administration → System → Report writing → <em>Report defaults</em>. In the project&rsquo;s defaults an empty one says what the installation&rsquo;s is, and <em>Edit a copy</em> starts from the installation&rsquo;s disclaimer. With no disclaimer written anywhere the template prints its own; with no author anywhere the report lists it under <em>Before issuing</em>. With no team saved in the defaults, the project&rsquo;s analysts and admins are the team.</li>
          <li><strong>Images in the System description</strong> — a network diagram or similar is uploaded under the System description (PNG, JPEG or GIF, up to 10 MB) and placed with <em>Insert image</em>, like a finding&rsquo;s. These images belong to the project: its report defaults and every draft place the same ones, an image no text places is not printed, and one that a text still places cannot be deleted — the refusal names where.</li>
          <li><strong>The Penetration test report&rsquo;s tables</strong> — its summary table links each finding by its section number (it no longer prints Ref there, nor Status, Port and Note columns), a table written in report text can carry a numbered caption (a <Mono>Table:</Mono> line after it), and the Word header and footer print the report&rsquo;s classification.</li>
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
          Running the platform: isolating engagements into projects, controlling who can do what,
          telling an AI how your reports are written, and getting data back out as tool-ready lists
          or formal reports.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default AdminGuide;
