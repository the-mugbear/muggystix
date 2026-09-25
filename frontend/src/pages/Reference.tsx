/**
 * /reference (v5.266.0) — the index of reading material: guides, what you use
 * while testing, and the API documentation.
 *
 * A compact list per group, not a wall of tiles: each entry is its title, one
 * line on what it is for, and what following it does — opens a page here,
 * downloads a file, or opens in a new tab.  No colour per entry (colour
 * follows meaning, and these have none to carry), no count badges, and no
 * group badge repeated inside its own group.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import {
  BookOpen, Bot, Download, ExternalLink, FileCode, FileSearch, FileText, KeyRound, Package, Plug, Terminal,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import PostureSection from '../components/posture/PostureSection';

type Entry = {
  title: string;
  description: string;
  Icon: LucideIcon;
} & (
  | { kind: 'page'; path: string }
  | { kind: 'download'; href: string; filename: string; label: string }
  | { kind: 'external'; href: string }
);

const GROUPS: Array<{ title: string; description: string; entries: Entry[] }> = [
  {
    title: 'Guides',
    description: 'How BlueStick works, for people and for the AI agents working with them.',
    entries: [
      {
        kind: 'page', path: '/reference/user-guide', Icon: BookOpen, title: 'User guide',
        description: 'Getting started, bringing data in, triage and reporting, agents, administration.',
      },
      {
        kind: 'page', path: '/reference/mcp', Icon: Plug, title: 'MCP for AI Assist',
        description: 'Connect an AI assistant as native tools — setup per client, the tool catalogue, what a session may do.',
      },
      {
        kind: 'download', href: '/api/v1/agents-guide', filename: 'AGENTS.md', label: 'AGENTS.md', Icon: Bot,
        title: 'AI agent guide',
        description: 'The contract an agent reads at startup, with this deployment’s URLs filled in.',
      },
    ],
  },
  {
    title: 'While testing',
    description: 'Reference material for triage, validation and planning.',
    entries: [
      {
        kind: 'page', path: '/tool-reference', Icon: Terminal, title: 'Tool reference',
        description: 'The tools BlueStick knows, by category — install commands, output BlueStick can ingest, and agent policy.',
      },
      {
        kind: 'page', path: '/reference/tool-coverage', Icon: FileSearch, title: 'What BlueStick reads',
        description: 'For each import format: what the tool reports, how far BlueStick takes it, where you see it, and what it drops.',
      },
      {
        kind: 'page', path: '/default-credentials', Icon: KeyRound, title: 'Default credentials',
        description: 'Vendor default usernames and passwords, searchable, for authorised testing.',
      },
      {
        kind: 'page', path: '/reference/sbom', Icon: Package, title: 'Software bill of materials',
        description: 'Every package bundled with this build — to answer “is package X in the app?”.',
      },
    ],
  },
  {
    title: 'API documentation',
    description: 'The OpenAPI schema of this deployment, for integrations.',
    entries: [
      {
        kind: 'external', href: '/docs', Icon: FileText, title: 'Swagger UI',
        description: 'Interactive documentation: explore and try the endpoints.',
      },
      {
        kind: 'external', href: '/redoc', Icon: FileCode, title: 'ReDoc',
        description: 'Reference-style documentation with the full schema.',
      },
    ],
  },
];

const titleClass = 'font-medium text-foreground group-hover:text-info group-hover:underline';

const EntryRow: React.FC<{ entry: Entry }> = ({ entry }) => {
  const { Icon } = entry;
  const body = (
    <>
      <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-xs">
          <span className={titleClass}>{entry.title}</span>
          {entry.kind === 'download' && (
            <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
              <Download className="size-3" aria-hidden /> downloads {entry.label}
            </span>
          )}
          {entry.kind === 'external' && (
            <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
              <ExternalLink className="size-3" aria-hidden /> opens in a new tab
            </span>
          )}
        </span>
        <span className="block text-caption text-muted-foreground">{entry.description}</span>
      </span>
    </>
  );
  const cls = 'group flex min-w-0 items-start gap-sm rounded-control py-xs pr-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';
  if (entry.kind === 'page') return <Link to={entry.path} className={cls}>{body}</Link>;
  if (entry.kind === 'download') return <a href={entry.href} download={entry.filename} className={cls}>{body}</a>;
  return <a href={entry.href} target="_blank" rel="noreferrer" className={cls}>{body}</a>;
};

const Reference: React.FC = () => (
  // The same container as the other hub pages (full width, p-md md:p-lg) —
  // a centred max-width box started the title further right than theirs.
  <div className="space-y-lg p-md md:p-lg">
    <header>
      <h1 className="text-page-title">Reference</h1>
      <p className="mt-xxs max-w-3xl text-metadata text-muted-foreground">
        Guides and reference material, available from every page.
      </p>
    </header>
    {GROUPS.map((group) => (
      <PostureSection key={group.title} title={group.title} description={group.description}>
        <ul className="grid gap-x-lg gap-y-xxs md:grid-cols-2">
          {group.entries.map((entry) => (
            <li key={entry.title} className="min-w-0"><EntryRow entry={entry} /></li>
          ))}
        </ul>
      </PostureSection>
    ))}
  </div>
);

export default Reference;
