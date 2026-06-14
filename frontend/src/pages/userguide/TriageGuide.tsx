import React, { useEffect, useState } from 'react';
import { ServerCog, SearchCode, ShieldAlert, Gauge, MessagesSquare } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/table';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { getHostQuerySchema, type HostQuerySchema } from '../../services/api';
import {
  UserGuideShell,
  GuidePage,
  GuideSection,
  Para,
  Subhead,
  UnorderedList,
  Mono,
} from './UserGuideShell';

// Live field reference — fetched from the DSL schema endpoint so the guide can
// never drift from the actual registry (the command bar's syntax popover reads
// the same source).  The schema endpoint is project-scoped; on a brand-new
// deployment with no project selected the fetch fails and we point the reader
// at the in-page syntax help instead.
const DslFieldReference: React.FC = () => {
  const [schema, setSchema] = useState<HostQuerySchema | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    getHostQuerySchema()
      .then((s) => { if (active) setSchema(s); })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, []);

  if (failed) {
    return (
      <Para>
        Select a project to load the live field list. Every field is also listed in
        the Hosts query bar’s <strong>syntax help</strong> (the <Mono>?</Mono> button),
        which reads the same source.
      </Para>
    );
  }
  if (!schema) return <Para>Loading field reference…</Para>;

  const hasField = schema.fields.find((f) => f.name === 'has');

  return (
    <>
      <Subhead>Fields &amp; where the data comes from</Subhead>
      <div className="overflow-x-auto rounded-panel border border-border">
        <Table className="min-w-[600px]">
          <TableHeader>
            <TableRow>
              <TableHead className="w-1/4">Field</TableHead>
              <TableHead>Matches — and where it’s populated from</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {schema.fields.map((f) => (
              <TableRow key={f.name}>
                <TableCell>
                  <code className="font-mono text-caption">{f.name}:</code>
                  {f.aliases.length > 0 && (
                    <span className="ml-xxs text-caption text-muted-foreground">
                      ({f.aliases.map((a) => `${a}:`).join(' ')})
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-body">{f.description}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {hasField && Object.keys(hasField.enum_descriptions).length > 0 && (
        <>
          <Subhead>has: flags</Subhead>
          <UnorderedList>
            {hasField.enum_values.map((v) => (
              <li key={v}>
                <Mono>has:{v}</Mono> — {hasField.enum_descriptions[v]}
              </li>
            ))}
          </UnorderedList>
        </>
      )}
    </>
  );
};

const sections: GuideSection[] = [
  {
    id: 'hosts',
    title: 'Hosts & host detail',
    Icon: ServerCog,
    summary: 'The primary triage table — filter, sort, review, and drill into any host.',
    content: (
      <div>
        <Para>
          The <strong>Hosts</strong> page (Inventory hub) is the primary triage interface: every
          discovered host in a sortable, filterable table with inline port summaries.
        </Para>
        <Subhead>Two ways to filter</Subhead>
        <UnorderedList>
          <li><strong>Query bar</strong> — type plain text to search IP / hostname / OS, or use the boolean query language (next section) for precise filters. Press <Mono>/</Mono> to focus it; it validates as you type and shows a live match count.</li>
          <li><strong>Filter panel</strong> — point-and-click filters for state, OS, ports, services, subnets, tags, review status, assignment, and scan. They combine with the query bar (AND).</li>
        </UnorderedList>
        <Subhead>Working the list</Subhead>
        <UnorderedList>
          <li><strong>Sorting</strong> — by critical / high findings, open ports, notes count, last seen, hostname, or IP (sorted numerically by octet).</li>
          <li><strong>Review workflow</strong> — mark hosts Watching, In Review, or Reviewed to track progress (and filter back with <Mono>follow:</Mono>).</li>
          <li><strong>Assignment</strong> — assign hosts to teammates; find yours with <Mono>assigned:me</Mono>.</li>
          <li><strong>Notes</strong> — threaded notes with @mentions for collaboration.</li>
          <li><strong>Tool-ready output</strong> — export the filtered list in tool formats (IP list, Nmap targets, …). Exports honour the full active filter + query.</li>
          <li><strong>Share &amp; save</strong> — <strong>Copy link</strong> reproduces the exact view; <strong>Save view</strong> stores it as a named view.</li>
        </UnorderedList>
        <Para>
          Click any host to open <strong>Host Detail</strong>: full port/service info, vulnerabilities,
          scan history, the notes thread, and connection-helper commands.
        </Para>
      </div>
    ),
  },
  {
    id: 'search-syntax',
    title: 'Host search syntax',
    Icon: SearchCode,
    summary: 'The boolean query DSL: fields, operators, and where each field’s data comes from.',
    content: (
      <div>
        <Para>
          The command bar accepts a boolean query language. Combine terms with <Mono>AND</Mono>,{' '}
          <Mono>OR</Mono>, and <Mono>NOT</Mono> (case-insensitive), group with parentheses, and quote
          multi-word values. A bare word with no field searches IP, hostname, and OS. A comma is OR
          within one field (<Mono>port:80,443</Mono>); repeating a field is AND
          (<Mono>port:80 port:443</Mono> = has <em>both</em>).
        </Para>
        <Subhead>Examples</Subhead>
        <UnorderedList>
          <li><Mono>has:critical AND NOT follow:in_review_any</Mono> — critical-vuln hosts nobody is reviewing yet.</li>
          <li><Mono>cve:CVE-2021-44228 OR vuln:"log4j"</Mono> — Log4Shell exposure by CVE or title.</li>
          <li><Mono>port:445 AND os:Windows AND label:"PCI"</Mono> — SMB-exposed Windows hosts in PCI subnets.</li>
          <li><Mono>service:http AND has:web AND NOT tag:reviewed</Mono> — un-reviewed web services.</li>
        </UnorderedList>
        <DslFieldReference />
        <Alert className="mt-sm">
          <AlertDescription>
            This same query language is available to an <strong>AI Assist agent</strong> — so you can
            ask your AI of choice questions like "which hosts do I have in review?" and it answers
            with <Mono>follow:in_review</Mono> against the live data. See <strong>Agentic
            Workflows → AI Assist</strong>.
          </AlertDescription>
        </Alert>
      </div>
    ),
  },
  {
    id: 'findings',
    title: 'Findings',
    Icon: ShieldAlert,
    summary: 'Vulnerabilities consolidated across scanners, with triage and evidence threads.',
    content: (
      <div>
        <Para>
          The <strong>Findings</strong> page (Inventory hub) is the vulnerability triage surface. It
          consolidates findings from every scanner that reports them — Nessus, OpenVAS, Nikto, and
          agentic test results — deduplicated per host so the same issue from two scans is one row,
          not two.
        </Para>
        <UnorderedList>
          <li><strong>Severity &amp; disposition</strong> — sort and filter by severity; track triage state as you work through them.</li>
          <li><strong>Evidence threads</strong> — attach comments and evidence to a finding; terminal determinations (e.g. confirmed / false-positive) require a justification, which is captured for the report.</li>
          <li><strong>Source attribution</strong> — each finding records which scan and tool produced it, so you can trace it back.</li>
        </UnorderedList>
        <Para>
          On the Hosts page, the same data drives <Mono>cve:</Mono>, <Mono>vuln:</Mono>, and{' '}
          <Mono>has:critical</Mono> filters, so you can pivot from a host to its findings and back.
        </Para>
      </div>
    ),
  },
  {
    id: 'posture',
    title: 'Posture, Insights & Topology',
    Icon: Gauge,
    summary: 'Manager roll-ups, per-subnet hygiene, estate-wide blind spots, and the network map.',
    content: (
      <div>
        <Para>
          The <strong>Posture</strong> hub turns the raw inventory into management-facing analysis —
          useful when you need the shape of the engagement, not an individual host.
        </Para>
        <UnorderedList>
          <li><strong>Posture</strong> — the headline roll-up: exposure, coverage, severity mix, and ownership/review progress across the project, with by-site breakdowns.</li>
          <li><strong>Insights</strong> — per-subnet exposure, neglect, and hygiene (EOL OS, weak TLS, risky services) so you can spot the worst-tended corners of the estate.</li>
          <li><strong>Systemic</strong> — estate-wide patterns and blind spots: outliers, common vectors, and where a single weakness is spread across many hosts.</li>
        </UnorderedList>
        <Para>
          The <strong>Topology</strong> page (Inventory hub) renders the discovered hosts and subnets
          as a navigable network map for a visual sense of structure.
        </Para>
        <Para>
          For the day-to-day analyst view, <strong>Operations</strong> stays your home base (your
          queue, pending approvals, recent notes); <strong>Portfolio</strong> rolls posture up across
          every project you belong to.
        </Para>
      </div>
    ),
  },
  {
    id: 'notes',
    title: 'Notes & collaboration',
    Icon: MessagesSquare,
    summary: 'Threaded notes, @mentions, and the project-wide Activity feed.',
    content: (
      <div>
        <Para>
          Notes attach to hosts (and findings) and are how a team documents findings, coordinates,
          and tracks remediation. The <strong>Activity</strong> page (Collaboration hub) shows all
          notes across the project, grouped by host with threading.
        </Para>
        <UnorderedList>
          <li><strong>Threading</strong> — reply to notes to build a conversation.</li>
          <li><strong>@Mentions</strong> — tag teammates with <Mono>@username</Mono> to notify them; the bell icon shows your unread mention count.</li>
          <li><strong>Status</strong> — notes carry Open / In Progress / Resolved.</li>
          <li><strong>Markdown</strong> — headers, lists, and bold render in the UI.</li>
        </UnorderedList>
      </div>
    ),
  },
];

const TriageGuide: React.FC = () => (
  <UserGuideShell activePath="/reference/user-guide/triage">
    <GuidePage
      intro={
        <span>
          The core analyst loop: find the hosts that matter, triage their findings, and step back to
          the posture roll-ups when you need the bigger picture.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default TriageGuide;
