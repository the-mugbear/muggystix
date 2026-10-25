/**
 * /reference/report-templates — how client report templates work, and how far
 * a team can change them before it needs to change BlueStick itself.
 *
 * The page explains the architecture and the safe way to work; the
 * field-by-field reference (every manifest key, every data key, every helper)
 * stays in `report-templates/README.md`, which this page names and does not
 * repeat.  Facts here follow `quarto_render.py`, `client_report_service.py`
 * and `.claude/rules/client-reports.md`: change them together.
 */
import React from 'react';
import { Link as RouterLink } from 'react-router-dom';
import {
  ChevronDown, ChevronRight, Code2, FileCog, FlaskConical, Layers, ShieldAlert, Signpost, Workflow,
} from 'lucide-react';

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import {
  GuidePage, type GuideSection, Mono, OrderedList, Para, Subhead, UnorderedList,
} from './userguide/UserGuideShell';

// ---------------------------------------------------------------------------
// The pipeline diagram — where each part of a template is applied, in the
// order `quarto_render.render` runs them.  Built as markup (not an image) so
// it follows the theme and stays readable to a screen reader as a list.
// ---------------------------------------------------------------------------

/** Who owns an input: BlueStick's code (level 4), the template folder
 *  (level 3), or what is uploaded on the Reports pages (levels 1–2). */
type Owner = 'bluestick' | 'template' | 'upload';

const OWNER: Record<Owner, { label: string; chip: string }> = {
  bluestick: { label: 'BlueStick', chip: 'border-border bg-muted text-muted-foreground' },
  template: { label: 'Template folder', chip: 'border-info/50 bg-info/10 text-foreground' },
  upload: { label: 'Uploaded', chip: 'border-success/50 bg-success/10 text-foreground' },
};

interface StageInput { name: string; owner: Owner; note?: string }
interface Stage { title: string; does: string; inputs: StageInput[]; out: string }

const PIPELINE: Stage[] = [
  {
    title: 'Build the data',
    does: 'BlueStick reads the report’s findings and details from the database into one object. A draft’s is built now; an issued report’s is the one frozen at issue.',
    inputs: [
      { name: 'client_report_service.build', owner: 'bluestick', note: 'what a report includes, in what order' },
    ],
    out: 'the dataset → data.json',
  },
  {
    title: 'Assemble a work folder',
    does: 'A private copy for this render: the base template’s files, then the template’s own files over them, then uploaded files in their places.',
    inputs: [
      { name: 'base template (extends)', owner: 'template' },
      { name: 'report.qmd, partials/, filters/, scripts/, reference.docx', owner: 'template' },
      { name: 'logo, title page image, Word styles', owner: 'upload', note: 'over an installed file' },
      { name: '_bluestick/fields.lua', owner: 'bluestick', note: 'copied in on every render' },
      { name: 'evidence images', owner: 'bluestick', note: 'the ones this report prints' },
    ],
    out: 'the work folder',
  },
  {
    title: 'Fill the template (Jinja)',
    does: 'report.qmd is filled with the data in a sandbox. Values print escaped; written text, images and commands print only as placeholders for the next stage.',
    inputs: [
      { name: 'report.qmd', owner: 'template', note: 'the entry in template.json' },
      { name: 'partials/*.qmd', owner: 'template', note: 'pulled in by include' },
      { name: 'helpers: md, image, code, todo, asset, plain', owner: 'bluestick' },
    ],
    out: 'a filled report.qmd',
  },
  {
    title: 'Render (Quarto, once per format)',
    does: 'Pandoc reads the filled source; the filters run in the order the front matter lists them; then the writer produces HTML or Word.',
    inputs: [
      { name: '1. quarto', owner: 'bluestick', note: 'Quarto’s own filters' },
      { name: '2. _bluestick/fields.lua', owner: 'bluestick', note: 'fills md/code/image from data.json, sets bluestick-meta, numbers figures, tables, listings' },
      { name: '3. filters/*.lua', owner: 'template', note: 'yours, listed after it' },
      { name: 'format options (front matter)', owner: 'template', note: 'theme, TOC, fonts' },
      { name: 'reference.docx', owner: 'template', note: 'Word: every style; or an uploaded one' },
    ],
    out: 'report.html · report.docx',
  },
  {
    title: 'Post-process (per format)',
    does: 'The script template.json names for a format runs on the rendered file — for Word, the logo and cover in place, the classification in header and footer, captions kept with their tables, and the table of contents numbered from a LibreOffice layout of a copy (Word may break a page differently; the reader updates the table once).',
    inputs: [
      { name: 'scripts/… (postprocess)', owner: 'template', note: 'pentest: fix-docx-report.py' },
      { name: 'data.json', owner: 'bluestick', note: 'what the script may read' },
    ],
    out: 'the finished file',
  },
  {
    title: 'Deliver',
    does: 'A draft’s file is a preview. Issuing keeps the data, the printed images and the whole work folder’s template in the report’s own storage, so it renders the same for ever. The QMD format skips rendering and zips the filled source with data.json and the filters.',
    inputs: [],
    out: 'preview, or an issued report',
  },
];

const PipelineDiagram: React.FC = () => (
  <figure className="mb-md" aria-labelledby="pipeline-caption">
    <div className="mb-sm flex flex-wrap gap-xs text-caption" aria-hidden>
      {(Object.keys(OWNER) as Owner[]).map((owner) => (
        <span key={owner} className={`rounded-control border px-xs py-xxs ${OWNER[owner].chip}`}>{OWNER[owner].label}</span>
      ))}
    </div>
    <ol className="space-y-xxs" aria-label="How a report is rendered, stage by stage">
      {PIPELINE.map((stage, n) => (
        <li key={stage.title} className="min-w-0">
          <div className="grid gap-sm rounded-panel border border-border bg-card p-sm md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <div className="min-w-0">
              <p className="text-body font-semibold text-foreground">
                <span className="mr-xs font-mono text-muted-foreground">{n + 1}</span>{stage.title}
              </p>
              <p className="mt-xxs text-caption text-muted-foreground">{stage.does}</p>
            </div>
            <div className="min-w-0">
              {stage.inputs.length > 0 && (
                <ul className="flex flex-wrap gap-xxs" aria-label={`What stage ${n + 1} uses`}>
                  {stage.inputs.map((input) => (
                    <li key={input.name}
                      className={`max-w-full rounded-control border px-xs py-xxs text-caption [overflow-wrap:anywhere] ${OWNER[input.owner].chip}`}>
                      <span className="font-mono">{input.name}</span>
                      {input.note && <span className="text-muted-foreground"> — {input.note}</span>}
                      <span className="sr-only"> ({OWNER[input.owner].label})</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-xs text-caption text-foreground">→ {stage.out}</p>
            </div>
          </div>
          {n < PIPELINE.length - 1 && (
            <div className="flex justify-center py-xxs text-muted-foreground" aria-hidden>
              <ChevronDown className="size-4" />
            </div>
          )}
        </li>
      ))}
    </ol>
    <figcaption id="pipeline-caption" className="mt-xs text-caption text-muted-foreground">
      Where each part of a template is applied. Grey is BlueStick’s own code (changing it is level 4); blue is the
      template folder (level 3); green is what is uploaded on Reports → Report templates (levels 1–2).
    </figcaption>
  </figure>
);

/** One way of changing a report, and what it needs. */
interface Level {
  level: string;
  what: string;
  where: React.ReactNode;
  needs: string;
}

const LEVELS: Level[] = [
  {
    level: '1 · No files',
    what: 'Logo, title page image, Word styles; which template a project uses by default; a draft’s own template.',
    where: <>Reports → <strong>Report templates</strong> (uploads, global administrator); Reports → <strong>Defaults</strong>; a draft’s Template picker.</>,
    needs: 'Nothing. Takes effect on the next preview.',
  },
  {
    level: '2 · Word styles',
    what: 'Fonts, colours, heading and table styles, the title page’s layout, headers and footers, page size and margins.',
    where: <>The template’s <Mono>reference.docx</Mono> — or your own uploaded as the template’s Word styles.</>,
    needs: 'A Word file only. Keep the placeholder names BlueStick fills (below).',
  },
  {
    level: '3 · The template',
    what: 'Which sections appear and in what order, their wording and headings, which data each prints and when, tables, the title and subtitle, your own Pandoc filters and Word post-processing.',
    where: <>A template folder: <Mono>template.json</Mono>, <Mono>report.qmd</Mono>, <Mono>partials/</Mono>, <Mono>filters/</Mono>, <Mono>scripts/</Mono>.</>,
    needs: 'Files on the server. No rebuild, no restart: the folder is read on every request.',
  },
  {
    level: '4 · BlueStick source',
    what: 'Data the report does not receive today, a new written section, a new output format, a new helper, what counts as a reportable finding, how text is escaped or figures are numbered.',
    where: <>The backend (<Mono>client_report_service.py</Mono>, <Mono>quarto_render.py</Mono>, <Mono>quarto_fields.lua</Mono>) and often the database.</>,
    needs: 'A code change, its tests and a release.',
  },
];

const SOURCE_NEEDED: Array<[string, string]> = [
  ['A value the data does not carry', 'Every value a template can print is in the dataset BlueStick builds (see “The data” in the README). A field from elsewhere — a host’s OS, a scan date, a custom tag — has to be added to that dataset first, and it is then frozen into every report issued with it.'],
  ['A new written section on findings', 'The written sections are a fixed list (description, impact, recommendation, references, steps to reproduce, evidence). A new one is a database column, an editor field, the drafting rules and the report’s checklist.'],
  ['PDF, or another output format', 'Formats are HTML, Word and the Quarto source zip. PDF is made by exporting the Word file.'],
  ['Including other findings', 'What a report includes is BlueStick’s rule: confirmed, accepted risk and remediated findings; false positives dropped; findings under investigation counted, not shown. A template can leave findings out, never add ones the data does not hold.'],
  ['A new helper or a change to escaping', 'The helpers (md, image, code, todo, asset, plain) and the rule that every value prints as text are what keep client-written text from becoming code. They are not configurable on purpose.'],
  ['A different finding order', 'Findings arrive worst first. A template can group or filter them in Jinja (for example by host), but a sort the data cannot express from its own keys needs the data to carry the key.'],
  ['SVG, GIF or WebP uploads', 'Those image types can be installed on the server, not uploaded on the page: an SVG would carry script into the HTML report.'],
];

const sections: GuideSection[] = [
  {
    id: 'pipeline',
    title: 'How a report is made',
    Icon: Workflow,
    summary: 'Data, a work folder, the Jinja fill, Quarto with its filters, post-processing — where partials, filters and scripts apply.',
    content: (
      <div>
        <Para>
          The template never reads the database: it sees only the data BlueStick builds. Partials apply when the
          template is filled; filters while Quarto renders, after BlueStick’s own; scripts on the rendered file.
          Drafts always use the template as it is now; an issued report keeps its own copy.
        </Para>
        <PipelineDiagram />
      </div>
    ),
  },
  {
    id: 'levels',
    title: 'How far you can go without changing BlueStick',
    Icon: Layers,
    summary: 'Four levels: uploads, Word styles, the template folder, and — only then — the source.',
    content: (
      <div>
        <Para>
          Most changes people want — branding, layout, wording, section order, what appears on the title page — are
          levels 1 to 3 and need no code change. Start at the lowest level that does the job.
        </Para>
        <div className="mb-md overflow-x-auto rounded-panel border border-border">
          <Table className="min-w-[640px] table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[14%]">Level</TableHead>
                <TableHead className="w-[34%]">Changes</TableHead>
                <TableHead className="w-[30%]">Where</TableHead>
                <TableHead>Needs</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {LEVELS.map((l) => (
                <TableRow key={l.level}>
                  <TableCell className="align-top font-medium">{l.level}</TableCell>
                  <TableCell className="align-top text-body">{l.what}</TableCell>
                  <TableCell className="align-top text-body">{l.where}</TableCell>
                  <TableCell className="align-top text-body">{l.needs}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <Subhead>When it needs BlueStick source (level 4)</Subhead>
        <UnorderedList>
          {SOURCE_NEEDED.map(([what, why]) => (
            <li key={what}><strong>{what}.</strong> {why}</li>
          ))}
        </UnorderedList>
      </div>
    ),
  },
  {
    id: 'files',
    title: 'What is in a template folder',
    Icon: FileCog,
    summary: 'template.json, report.qmd and optional partials, filters, Word styles and scripts.',
    content: (
      <div>
        <UnorderedList>
          <li><Mono>template.json</Mono> — the manifest: title and description (what people choose by), formats, which images it prints, its own images (assets), the Word post-processor, whether it prints a finding&rsquo;s status and an endpoint&rsquo;s state (<Mono>status_notes</Mono> — presentation mode shows them only where the template does), and <Mono>extends</Mono>.</li>
          <li><Mono>report.qmd</Mono> — the report itself. Its front matter sets the title page (<Mono>bluestick-meta</Mono>: title, subtitle, author) and the filters; its body is every section in order.</li>
          <li><Mono>partials/*.qmd</Mono> — reusable pieces pulled in with <Mono>include</Mono> (a finding, its affected systems, how it was confirmed).</li>
          <li><Mono>reference.docx</Mono> — Word styles: fonts, headings, tables, title page, header and footer.</li>
          <li><Mono>filters/*.lua</Mono> and <Mono>scripts/</Mono> — your own Pandoc filters and a post-processing script per format.</li>
          <li><Mono>sample-data.json</Mono> — an example of the data, for rendering locally.</li>
        </UnorderedList>
        <Subhead>Placeholders BlueStick fills in Word</Subhead>
        <Para>
          Keep these when you edit a <Mono>reference.docx</Mono>, or the post-processor has nothing to replace: a
          header or footer run whose whole text is <Mono>CLASSIFICATION</Mono> (replaced by the report’s
          classification), and the drawings named <Mono>bluestick-logo</Mono> and <Mono>bluestick-cover</Mono>
          (replaced by the uploaded logo and title page image). A Word file without them keeps its own pictures.
        </Para>
      </div>
    ),
  },
  {
    id: 'safely',
    title: 'Changing a template safely',
    Icon: Signpost,
    summary: 'Extend a shipped template instead of editing it, test it, then install it.',
    content: (
      <div>
        <Subhead>Extend, don’t edit the shipped folders</Subhead>
        <Para>
          An upgrade replaces <Mono>pentest</Mono>, <Mono>executive-brief</Mono> and the others, and your edits with
          them. Make your own folder that <strong>extends</strong> one and holds only the files you change; every file
          you leave out — partials, filters, Word styles, the post-processor — is the base’s, so later fixes to the
          base still reach you:
        </Para>
        <pre className="mb-sm overflow-x-auto rounded-control bg-muted p-sm font-mono text-caption">{`report-templates/my-pentest/
  template.json   { "extends": "pentest", "title": "Penetration test report (ours)",
                    "description": "…", "entry": "report.qmd" }
  report.qmd      a copy of pentest's, edited`}</pre>
        <UnorderedList>
          <li>One level only: a base extends nothing.</li>
          <li><Mono>title</Mono>, <Mono>description</Mono> and <Mono>kind</Mono> are never inherited; every other manifest key you leave out is the base’s.</li>
          <li>Branding is never inherited: upload your template’s own logo, title page image and Word styles on Reports → Report templates.</li>
          <li>To start a template from nothing instead, copy <Mono>remediation-worklist</Mono>: its <Mono>report.qmd</Mono> explains every part in comments.</li>
        </UnorderedList>
        <Subhead>Install it</Subhead>
        <OrderedList>
          <li>Put the folder in <Mono>report-templates/</Mono> on the server (folder name: lower-case letters, digits, <Mono>-</Mono> or <Mono>_</Mono>).</li>
          <li>Reload the Reports page: it is listed at once. A folder that cannot be offered is shown to global administrators on Reports → Report templates, with the reason.</li>
          <li>Choose it on a draft, or as the project’s default, and preview.</li>
          <li><strong>Keep it across upgrades.</strong> The upgrade script carries a template’s images, not its folder: copy your folder into the new tree, or keep it under version control.</li>
        </OrderedList>
      </div>
    ),
  },
  {
    id: 'rules',
    title: 'Rules a template must never break',
    Icon: ShieldAlert,
    summary: 'They keep client-written text from becoming code in the report. The renderer enforces most of them.',
    content: (
      <div>
        <OrderedList>
          <li><strong>Print data as values</strong> (<Mono>{'<< value >>'}</Mono>): it is escaped, so it reads as text whatever it holds.</li>
          <li><strong>Print written text only with <Mono>md()</Mono></strong> — never as a value. Written text is inserted after Quarto’s filters, with HTML, headings and foreign images removed.</li>
          <li><strong>Never print data in the YAML front matter.</strong> Quarto expands shortcodes there even when escaped. Name the data with <Mono>bluestick-meta</Mono> instead; plain fixed text in the YAML is fine.</li>
          <li><strong>Never print a value inside <Mono>`code`</Mono> or a code block.</strong> A command or tool output goes through <Mono>code()</Mono>.</li>
          <li><strong>Reuse with includes, not macros</strong> — a macro’s output is escaped like a value.</li>
          <li><strong>Handle drafts, addenda and empty values:</strong> <Mono>report.draft</Mono>, <Mono>report.kind == "addendum"</Mono>, and a <Mono>todo()</Mono> where something is missing — never a blank.</li>
          <li><strong>Declare the images it prints</strong> in <Mono>template.json</Mono> → <Mono>images</Mono>, so the draft’s page tells authors truthfully what will print.</li>
        </OrderedList>
        <Para>
          The template runs in Jinja’s sandbox: no Python internals and no includes outside its folder. Changing
          those limits is level 4.
        </Para>
      </div>
    ),
  },
  {
    id: 'testing',
    title: 'Testing a change',
    Icon: FlaskConical,
    summary: 'Render the sample data locally, then preview a real draft before anyone issues.',
    content: (
      <div>
        <UnorderedList>
          <li><Mono>make</Mono> in the template’s folder renders <Mono>sample-data.json</Mono> (needs Quarto and Python with Jinja; an extending template needs its base folder beside it).</li>
          <li>Without installing anything, the report-worker image renders it (the command is in the README).</li>
          <li>Preview a draft that uses the template: the draft’s Review section says which images the template prints and what it leaves out, from the template’s own logic.</li>
          <li>A team that ships its template in the repository adds it to the backend tests (<Mono>test_report_templates_shipped.py</Mono>); the hostile-text test renders every folder with text built to break it.</li>
          <li>Never commit data saved from a real report: it is client data.</li>
        </UnorderedList>
      </div>
    ),
  },
  {
    id: 'example',
    title: 'Example: the client on the title page',
    Icon: Code2,
    summary: 'Remove or replace the subtitle — a level 3 change in your own report.qmd.',
    content: (
      <div>
        <Para>In <Mono>pentest</Mono>’s <Mono>report.qmd</Mono> the title page comes from:</Para>
        <pre className="mb-sm overflow-x-auto rounded-control bg-muted p-sm font-mono text-caption">{`bluestick-meta:
  title: report.title
  subtitle: report.heading        # the client, else the project; "— DRAFT" on a draft
  author: engagement.author_name`}</pre>
        <UnorderedList>
          <li><strong>Remove the client:</strong> delete the <Mono>subtitle</Mono> line in your copy. The draft marker goes with it, so keep the body’s “Draft — not for distribution” callout.</li>
          <li><strong>Show something else:</strong> point it at another data path, such as <Mono>engagement.classification</Mono>, or write fixed text as ordinary YAML (<Mono>subtitle: "Internal assessment"</Mono>).</li>
          <li><strong>Move the title page’s parts around</strong> (Word): the order Title, Subtitle, Author, Date is fixed; their position and look are the Word styles’ — level 2.</li>
          <li>The client also appears in the Project Information table — ordinary body text you can move or delete.</li>
        </UnorderedList>
      </div>
    ),
  },
];

const ReportTemplateGuide: React.FC = () => (
  <div className="p-md md:p-lg">
    <nav className="mb-sm flex items-center gap-xs text-metadata text-muted-foreground" aria-label="Breadcrumb">
      <RouterLink to="/reference"
        className="rounded-control hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        Reference
      </RouterLink>
      <ChevronRight className="size-3" aria-hidden />
      <span className="text-foreground" aria-current="page">Report templates</span>
    </nav>
    <h1 className="mb-md text-page-title">Changing report templates</h1>
    <GuidePage
      intro={(
        <>
          How a client report is produced, what each part of a template controls, and how far a team can change
          its reports before BlueStick itself has to change. Every manifest key, data field and helper is listed in
          <Mono> report-templates/README.md</Mono> on the server; the templates and their files are on{' '}
          <RouterLink to="/reports/templates" className="text-info hover:underline">Reports → Report templates</RouterLink>.
        </>
      )}
      sections={sections}
    />
  </div>
);

export default ReportTemplateGuide;
