import React from 'react';
import { Link } from 'react-router-dom';
import { FileUp, Workflow, Network } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/table';
import {
  UserGuideShell,
  GuidePage,
  GuideSection,
  Para,
  Subhead,
  OrderedList,
  Mono,
} from './UserGuideShell';
// The table is the upload dialog's own list (data/uploadFormats.ts), which a
// test pins to documentation/UPLOAD_FORMATS.md — a second hand-copied table
// here had drifted from both.
import { SUPPORTED_FORMATS } from '../../data/uploadFormats';

const sections: GuideSection[] = [
  {
    id: 'formats',
    title: 'Supported file formats',
    Icon: FileUp,
    summary: 'What you can upload, and which output flag gives the best data.',
    content: (
      <div>
        <Para>
          BlueStick detects each file's format from its content first and its filename second, so
          you can drop a mixed batch. A filename is only a hint: a file recognised by its name
          alone waits for you to confirm the format. Prefer machine-readable output (XML/JSON) over
          plain text wherever a tool offers it. For what each format keeps, where it is shown and what
          it drops, see <Link to="/reference/tool-coverage" className="underline">What BlueStick reads</Link>.
        </Para>
        <div className="overflow-x-auto rounded-panel border border-border">
          <Table className="min-w-[760px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/5">Tool</TableHead>
                <TableHead className="w-1/5">Formats</TableHead>
                <TableHead>Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {SUPPORTED_FORMATS.map((row) => (
                <TableRow key={row.tool}>
                  <TableCell className="break-words"><strong>{row.tool}</strong></TableCell>
                  <TableCell><code className="font-mono text-caption break-words">{row.formats}</code></TableCell>
                  <TableCell className="break-words text-body">
                    {row.desc}
                    {row.hint && (
                      <span className="mt-xxs block text-caption text-muted-foreground">
                        Recognised by: {row.hint}
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <Para>
          The scope file is not a scan upload: it goes in on the <strong>Scope</strong> page (CSV —
          CIDR rows and domain or wildcard rows, with optional labels, description and site).
        </Para>
        <Para>
          A file whose content does not match its extension is refused on upload, and so is a file
          identical to one already in the project (import it again on purpose from the review, or{' '}
          <em>Re-process</em> on Ingestion Results). If a file fails to parse, check{' '}
          <strong>Inventory → Ingestion Results</strong> (analysts and above) for the error, the
          format chain (detected → chosen → final) and, while the file is retained — 7 days by
          default — <em>Review the format and retry</em>.
        </Para>
      </div>
    ),
  },
  {
    id: 'ingestion',
    title: 'Uploading & ingestion',
    Icon: Workflow,
    summary: 'How an upload becomes deduplicated hosts, ports, and findings.',
    content: (
      <div>
        <Para>
          On <strong>Inventory → Scans</strong>, drag-and-drop or select files (multiple at once).
          Uploading is an analyst's action. Nothing is parsed until you press Import; each file
          then becomes an import job that runs in the background while the page follows it.
        </Para>
        <Subhead>The pipeline</Subhead>
        <OrderedList>
          <li><strong>Upload</strong> — the file is received and held, not yet imported.</li>
          <li><strong>Review formats</strong> — each file shows the format detected and how: <em>by structure</em> (ready to import), <em>by filename only</em> (confirm the suggestion or choose), or <em>not recognised</em> (choose the format). A format you choose is the only parser tried, so a wrong choice fails visibly instead of importing as something else.</li>
          <li><strong>Parsing</strong> — host, port, service, and scanner-observation data extracted.</li>
          <li><strong>Deduplication</strong> — hosts deduped by IP within the project; ports and services merged with conflict tracking.</li>
          <li><strong>Correlation</strong> — hosts automatically mapped to the scope's subnets.</li>
          <li><strong>Result</strong> — the scan says what it added: hosts added and already known, conflicts, new open ports, records skipped.</li>
        </OrderedList>
        <Para>
          A running import can be <strong>cancelled</strong> from the Scans page, and every import
          stops at the deployment's time limit (30 minutes unless changed). Parsers that drop
          records report a skipped count and warnings on the job, so data lost to malformed input
          is visible rather than hidden.
        </Para>
        <Subhead>When an import does not finish</Subhead>
        <Para>
          A failed, cancelled, timed-out or interrupted import leaves <strong>no scan</strong>, and
          nothing that only it created: the hosts and ports it added, and the observations and DNS
          records it first recorded, are removed with it. A host someone has worked on — a note, a
          review, a tag, a finding, a test, evidence — is kept. What it does <em>not</em> undo:
          changes it made to hosts and ports that already existed, and the names it added to the
          Names inventory. Importing the same file again writes the same values. A truncated Nessus
          export keeps nothing.
        </Para>
        <Para>
          Deleting a finished scan is a project admin's action (<em>Delete scan…</em> on the Scans
          page).
        </Para>
        <Subhead>Viewing scan results</Subhead>
        <Para>
          Open a scan to see its hosts, ports, and the command that produced it. For a Nessus scan
          the hosts table says, per host, whether the scan authenticated (Credentialed / Not
          credentialed; blank when the file did not say).{' '}
          <strong>DNS-resolution scans</strong> (e.g. <Mono>dnsx</Mono>) also get a{' '}
          <strong>DNS Records</strong> tab: only A / AAAA answers become hosts, so CNAME / MX / NS /
          TXT records are listed there with their resolver and TTL — the full answer set, with the
          true record count and a note when a very large set is truncated.
        </Para>
      </div>
    ),
  },
  {
    id: 'scopes',
    title: 'Scope, subnets & sites',
    Icon: Network,
    summary: 'Define engagement boundaries; group subnets into sites for roll-ups.',
    content: (
      <div>
        <Para>
          A project has one scope: the subnets and domains the engagement is authorised to test.
          Every member can read it; analysts change it.
        </Para>
        <Subhead>How it works</Subhead>
        <OrderedList>
          <li>Go to <strong>Inventory → Scope</strong>.</li>
          <li>Add entries one at a time (<Mono>10.0.0.0/24</Mono>, a single address, a domain, or <Mono>*.example.com</Mono> for a domain with its subdomains) or with <em>Upload scope file</em>.</li>
          <li>BlueStick maps each host to matching subnets automatically.</li>
          <li>A host is then in one of three states: in a scope subnet, reached only through an in-scope name, or outside scope. A name in scope does not put the address it resolves to in subnet scope. Filter with <Mono>scope:subnet</Mono>, <Mono>scope:name</Mono>, <Mono>scope:none</Mono>, or the <em>Out of scope</em> filter on the Hosts page.</li>
        </OrderedList>
        <Subhead>Sites &amp; labels</Subhead>
        <Para>
          Subnets can be tagged with <strong>labels</strong> (e.g. "PCI") and grouped into{' '}
          <strong>sites</strong> (e.g. "London DC") with a criticality tier. These power the{' '}
          <strong>Posture</strong> roll-ups and let you filter hosts by{' '}
          <Mono>label:</Mono> / <Mono>site:</Mono> on the Hosts page. Per-site and per-subnet
          exposure and hygiene are summarised on <strong>Posture → Segments</strong>.
        </Para>
      </div>
    ),
  },
];

const DataGuide: React.FC = () => (
  <UserGuideShell activePath="/reference/user-guide/data">
    <GuidePage
      intro={
        <span>
          Everything that gets data <em>into</em> BlueStick: what you can upload, what happens to it,
          and how to carve the network into scopes and sites.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default DataGuide;
