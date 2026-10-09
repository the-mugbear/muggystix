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
          <li><strong>Filter panel</strong> — point-and-click filters in six groups: network and scope (subnet, site, out of scope, tags, labels), services and web evidence, weaknesses and access, scanner observations (severity, exploit reported, CVE), analyst work (team review, assigned to me, notes, tested) and discovery (operating system, scans, registered owner). They combine with the query bar (AND).</li>
        </UnorderedList>
        <Subhead>Working the list</Subhead>
        <UnorderedList>
          <li><strong>Sorting</strong> — by critical scanner observations, exploit reported, open ports, most discoveries, most notes, IP address (numerically) or hostname.</li>
          <li><strong>Review workflow</strong> — take a host <strong>In Review</strong>, then mark it <strong>Reviewed</strong>. Review is the team's, not yours alone: <Mono>follow:in_review</Mono> and <Mono>follow:reviewed</Mono> match a host <em>any</em> teammate has in that state, <Mono>follow:none</Mono> a host nobody has taken, and <Mono>follow:mine</Mono> the hosts <em>you</em> have In Review. A host <em>you</em> reviewed that later gains an open port or a critical / high scanner observation is listed on your Operations page under <em>Changed since review</em> (<Mono>follow:revisit</Mono>); <Mono>has:changed_since_review</Mono> lists such hosts whoever reviewed them.</li>
          <li><strong>Assignment</strong> — assign hosts to teammates; find yours with <Mono>assigned:me</Mono>. Taking a host In Review also makes it yours.</li>
          <li><strong>Notes</strong> — threaded notes with @mentions for collaboration.</li>
          <li><strong>Export targets / Download inventory</strong> (auditors and above) — <em>Export targets</em> writes the filtered list in tool formats (IP list, Nmap targets, …); <em>Download inventory</em> saves the filtered hosts as CSV (one row per host) or JSON (everything recorded per host). Both honour the full active filter + query.</li>
          <li><strong>Share &amp; save</strong> — <strong>Copy link</strong> reproduces the exact view; <strong>Save view</strong> stores it as a named view.</li>
        </UnorderedList>
        <Para>
          Click any host to open it: ports and services, weaknesses (scanner observations), its
          tests and their evidence, scan history, the discussion thread, and connection-helper
          commands. Its assessment line says, per kind of evidence, whether the host was assessed —
          and, for vulnerability scans, whether the scan authenticated: <em>credentialed</em>,{' '}
          <em>not credentialed</em> or <em>credentials not stated</em>. That is shown beside
          "assessed", never instead of it: an unauthenticated scan still counts as assessed.
        </Para>
        <Para>
          Controls follow your role on the project: review, assignment, tags, notes and tests are
          offered to analysts and above; viewers and auditors get the same pages read-only.
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
          (<Mono>port:80 port:443</Mono> = has <em>both</em>). <Mono>port:</Mono>, <Mono>service:</Mono>{' '}
          and <Mono>version:</Mono> match <em>open</em> ports: for a closed or filtered port nmap only
          guesses the service name from the port number. Name another state after <Mono>@</Mono> —{' '}
          <Mono>service:ssh@closed</Mono>, <Mono>port:22@filtered</Mono>, <Mono>port:22@any</Mono>.
        </Para>
        <Subhead>Examples</Subhead>
        <UnorderedList>
          <li><Mono>has:critical AND NOT follow:in_review_any</Mono> — critical-vuln hosts nobody is reviewing yet.</li>
          <li><Mono>follow:mine</Mono> — the hosts <em>you</em> have in review (the other <Mono>follow:</Mono> values — <Mono>in_review</Mono>, <Mono>in_review_any</Mono>, <Mono>reviewed</Mono>, <Mono>none</Mono> — count any teammate's review).</li>
          <li><Mono>follow:revisit</Mono> — hosts <em>you</em> reviewed that are not done (they changed after your review, or you concluded that more evidence is needed): Operations' "Changed since review".</li>
          <li><Mono>has:changed_since_review OR conclusion:needs_evidence</Mono> — the same question for the whole team: any reviewed host that is not done.</li>
          <li><Mono>has:untouched AND has:critical</Mono> — hosts with a critical scanner observation that nobody has taken into review, been assigned, noted, tested or put in a finding: Operations' "Pick up" tab starts from these.</li>
          <li><Mono>vulnscan:uncredentialed</Mono> — hosts assessed by a vulnerability scan that did not authenticate (<Mono>credentialed</Mono> and <Mono>unstated</Mono> are the other two values).</li>
          <li><Mono>cve:CVE-2021-44228 OR vuln:"log4j"</Mono> — Log4Shell exposure by CVE or title.</li>
          <li><Mono>port:445 AND os:Windows AND label:"PCI"</Mono> — SMB-exposed Windows hosts in PCI subnets.</li>
          <li><Mono>service:http AND has:web AND NOT tag:reviewed</Mono> — un-reviewed web services.</li>
        </UnorderedList>
        <DslFieldReference />
        <Alert className="mt-sm">
          <AlertDescription>
            This same query language is available to your <strong>agent</strong> — so you can
            ask your AI of choice questions like "which hosts do I have in review?" and it answers
            with <Mono>follow:mine</Mono> against the live data. See <strong>Agents →
            Asking about your project</strong>.
          </AlertDescription>
        </Alert>
      </div>
    ),
  },
  {
    id: 'findings',
    title: 'Findings',
    Icon: ShieldAlert,
    summary: 'What the team has judged: findings, the scanner observations they come from, and the report text and images.',
    content: (
      <div>
        <Para>
          Two things are kept apart. <strong>Scanner observations</strong> are what the tools
          reported on a host (Nessus, OpenVAS, Nuclei, Nikto, nmap scripts, testssl, NetExec…),
          not yet judged. A <strong>finding</strong> is an issue the team has taken up: made by
          promoting a scanner observation or a test result that showed the issue, or written by
          hand — <em>Add finding</em> in a host&rsquo;s Findings section, for an issue no scanner row
          or test result on that host stands for. The <strong>Findings</strong> page (Findings hub) lists findings; its{' '}
          <strong>Scanner observations</strong> view lists the raw rows grouped by issue across
          hosts, and promotes several at once.
        </Para>
        <UnorderedList>
          <li><strong>Status</strong> — a finding is <em>under investigation</em> (Open, Retest), <em>Confirmed</em>, or <em>closed</em> (False positive, Accepted risk, Remediated). Closing one asks for a reason — you can save without one —, kept on the finding's history and carried into the report.</li>
          <li><strong>One finding per issue</strong> — the same issue promoted from another host joins the existing finding. Each affected system has its own state (Still present, Remediated here, Retest here, False positive here), so the finding's status is never read as every host's. On the finding's page the systems are listed 100 at a time with a filter; tick several to set their state in one step.</li>
          <li><strong>Who may change what</strong> — severity, owner and status are any analyst's. Renaming or deleting a finding, and its report text, belong to its author or a project admin. A comment is edited or deleted only by its author.</li>
          <li><strong>Report text</strong> — Description, Impact, Recommendation, Steps to reproduce and References are written on the finding and print in the client report. The page says which required sections are still empty. The reader has never seen BlueStick: name another finding by its title and a system by its address, never by a record number (&ldquo;Finding #277&rdquo;). An agent&rsquo;s or an AI draft&rsquo;s section that does is refused, and a draft report lists any written text that still does.</li>
          <li><strong>Images in the report</strong> — <em>Attach image</em> on one of the finding's comments, then tick <strong>In report</strong> on the image; an image is left out until it is ticked. Give it a caption (the filename is used otherwise). In the report-text editor, <em>Insert image</em> places a ticked image in that section: it writes <Mono>![](evidence:&lt;id&gt;)</Mono>, and the empty brackets print the image's own caption (text typed in the brackets replaces it for that place). Only the finding's own images can be placed, and the same image may be placed more than once. A ticked image that is not placed prints under Evidence, with its caption. An image that is placed cannot be deleted or un-ticked, and the comment holding it cannot be deleted, until the reference is removed from the text — the refusal names the section. Where images print also depends on the report's template; the report page says what its template prints.</li>
          <li><strong>Agent proposals</strong> — an agent's change to a finding (report text, a new finding, promoting or dismissing an observation, a system's state) waits as a proposal for a person to accept or reject, on <strong>Agents → Proposals</strong> and on the finding's page.</li>
        </UnorderedList>
        <Para>
          On the Hosts page, <Mono>cve:</Mono>, <Mono>vuln:</Mono>, and <Mono>has:critical</Mono>{' '}
          filter on scanner observations, so you can pivot from a host to what was reported on it
          and back.
        </Para>
      </div>
    ),
  },
  {
    id: 'posture',
    title: 'Posture',
    Icon: Gauge,
    summary: 'The overview, segments, estate-wide patterns, and what the evidence covers.',
    content: (
      <div>
        <Para>
          The <strong>Posture</strong> hub turns the raw inventory into management-facing analysis —
          useful when you need the shape of the engagement, not an individual host.
        </Para>
        <UnorderedList>
          <li><strong>Posture</strong> — the overview: one sentence on where the project stands, what that rests on, where to focus, and a grid of weakness families by site (or by subnet when the project defines no site). Below the grid: <em>Where the team has been</em> — how far each address block has been taken (tested, planned, someone has it, untouched); the sentence and the block to go to first are always shown, and <em>Show the map</em> opens the 3D map and its table view (the choice is remembered) — then <em>Scanner observations and scope</em>: scanner observations by severity (not yet judged) and the three scope states (in scope subnets, reached only through an in-scope name, outside scope), each number opening its hosts.</li>
          <li><strong>Segments</strong> — which sites and subnets need attention first: exposure, open assessment work and hygiene (EOL OS, weak TLS, risky services) per segment.</li>
          <li><strong>Patterns</strong> — estate-wide weaknesses: where a single weakness is spread across many hosts.</li>
          <li><strong>Evidence</strong> — per kind of evidence, how many eligible hosts were assessed, and the gaps with the step that closes each. For vulnerability scans it also counts the assessed hosts whose scan was credentialed, not credentialed, or did not say; each count opens its hosts (<Mono>vulnscan:</Mono>). All three count as assessed.</li>
        </UnorderedList>
        <Para>
          A project is one assessment window: evidence is assessed, not assessed or not applicable,
          and dates are shown as provenance — nothing goes "stale". For the day-to-day analyst view,{' '}
          <strong>Operations</strong> stays your home base (your work, blocked imports, the hosts
          you reviewed that changed since, and the untouched hosts with a reason to look) — it is
          about you, and the project's status is here on Posture;{' '}
          <strong>Portfolio</strong> rolls the same counts up across every project you belong to.
        </Para>
        <Subhead>Operations, top to bottom</Subhead>
        <UnorderedList>
          <li><strong>The lead</strong> says the kinds of work apart, with no total: first what needs you — findings that need a decision (under investigation, or a proposal waiting for you), findings that need report text, tests assigned to you; then what you hold — hosts you have In Review with the tests on them, and hosts you reviewed that changed since; then what there is to pick up. Each number opens the tab, and the filter, it counts.</li>
          <li><strong>The tab bar</strong> — <em>Findings</em>, <em>Hosts</em>, <em>Tests</em>, <em>Changed since review</em>, <em>Pick up</em> — each with its count. One list is on screen at a time, complete: 10 rows a page, with <em>1–10 of N</em> and previous / next under it. The tab is in the address (<Mono>?tab=</Mono>), so a link to one can be shared and Back returns to the tab you came from; with no tab named, the first one that holds something opens. A count shown as “—” could not be checked — it is not zero.</li>
          <li><strong>Findings</strong> — findings you own that need you, and the <em>Needs</em> column says why: under investigation, report text missing (which sections), or proposals to decide. The chips narrow the list to <em>Needs a decision</em> or <em>Needs report text</em> (a finding that needs both is under the decision). A confirmed finding with its text written is not listed.</li>
          <li><strong>Hosts</strong> — hosts you have In Review, with their open ports and critical / high scanner observations. <em>Open all N in Hosts</em> opens exactly this list.</li>
          <li><strong>Tests</strong> — one table of tests to do, and <em>Why it is here</em> says which kind: assigned to you, on a host you are reviewing, or free to claim (unassigned critical or high tests). The chips filter by kind. The number on the tab is yours; the claimable ones are shown beside it (“+ N to claim”) and not counted as yours. <em>Claim</em> assigns one to you.</li>
          <li><strong>Changed since review</strong> — hosts <em>you</em> reviewed that changed afterwards, or that you concluded need more evidence. A teammate's reviews are not listed (on Hosts, <Mono>has:changed_since_review</Mono> shows everyone's). <em>Still reviewed</em> says you saw the change and your review stands; <em>Re-open review</em> puts the host back In Review and clears your conclusion. Tick rows to do either in bulk; <em>Open all N in Hosts</em> opens exactly this list.</li>
          <li><strong>Pick up</strong> — hosts nobody has touched that carry a reason to look, ordered by a stated tier (never a score). The tier chips filter the list; <em>Review</em> takes a host — or the ticked hosts — into your queue. The link under the list opens exactly the tier's hosts where a Hosts query can say it; otherwise it says what it opens — every untouched host, with or without a reason, which is a longer list.</li>
          <li><strong>Your agent sessions</strong> — one line: how many of your own sessions are live and how many wait to be resumed. Every session of the project is on Agents → Agent Sessions.</li>
        </UnorderedList>
      </div>
    ),
  },
  {
    id: 'notes',
    title: 'Notes & collaboration',
    Icon: MessagesSquare,
    summary: 'Threaded discussion on hosts and findings, @mentions, and the project-wide Collaboration feed.',
    content: (
      <div>
        <Para>
          Notes are discussion: a host's <strong>Discussion</strong> section and a finding's
          comments are where the team asks, answers and hands over. They are not the record of
          work. Work is a <strong>test</strong> on the host and the result recorded on it; a
          finding is made by promoting a weakness or a test result that showed an issue. The{' '}
          <strong>Collaboration</strong> page shows every discussion in the project, latest first.
        </Para>
        <UnorderedList>
          <li><strong>Threading</strong> — reply to notes to build a conversation.</li>
          <li><strong>@Mentions</strong> — tag teammates with <Mono>@username</Mono> to notify them; the bell icon shows your unread mention count.</li>
          <li><strong>Type and pin</strong> — label a thread (question, decision, handoff…) and pin the ones that must stay at the top.</li>
          <li><strong>Screenshots for the report</strong> — put them on the finding: <em>Attach image</em> on one of its comments, then tick <strong>In report</strong>. An image is left out until it is ticked; captions and placing an image in the report text are described under Findings.</li>
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
