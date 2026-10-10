import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { copyToClipboard } from '../utils/clipboard';
import { Search, ExternalLink, Loader2 } from 'lucide-react';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import ToolVettingDialog from '../components/ToolVettingDialog';
import SectionJumpBar, { jumpTargetStyle, sectionId } from '../components/SectionJumpBar';
import {
  getToolRegistry,
  ToolRegistryEntry,
} from '../services/api';
import { queryErrorText } from '../lib/query';
import { cn } from '../utils/cn';
import { safeHttpHref } from '../utils/safeHref';

// ---------------------------------------------------------------------------
// Tool catalogue
//
// v5.167.0 — served by the backend tool registry rather than hardcoded here.
// This page and the agent catalogue used to be separate lists in separate
// languages; they had already drifted. One registry, read by people here and
// by agents through the API. 5.313.0 — a catalogue, not agent policy: the
// `approved` status was merged into `reference`, and no status says what an
// agent may run (the operator driving it decides). Vetted-in suggestions
// surface here.
// ---------------------------------------------------------------------------

type ToolEntry = ToolRegistryEntry;

// Only the rows that are not (yet) plain catalogue entries carry a badge; a
// `reference` row is the normal case and says nothing extra.
const STATUS_BADGE: Record<string, { tone: CategoryTone; label: string; title: string }> = {
  suggested: {
    tone: 'warning',
    label: 'Suggested',
    title: 'Proposed by an agent, awaiting review.',
  },
  rejected: {
    tone: 'destructive',
    label: 'Declined',
    title: 'Reviewed and declined; kept so it is not proposed again.',
  },
};

// The "Run for BlueStick" command — the invocation that writes a file BlueStick
// can ingest — is the registry row's own `run_command` / `run_note` (5.365.0).
// It was a table typed into this page beside the registry the server owns.

const CATEGORIES = [
  'Web Content Discovery',
  'Web Analysis',
  'Port Scanning',
  'SMB / NetBIOS',
  'Remote Access',
  'Network Services',
  'Databases',
  'Active Directory',
  'General Purpose',
] as const;

// Categories the curated set uses, in the order they should render. A tool
// whose category isn't listed here (a vetted-in suggestion, say) still shows —
// see `orderedCategories` — rather than disappearing from the page.
type Category = string;

type CategoryTone = 'default' | 'destructive' | 'warning' | 'success' | 'secondary' | 'info' | 'muted' | 'outline';

const CATEGORY_TONE: Record<string, CategoryTone> = {
  'Web Content Discovery': 'default',
  'Web Analysis': 'info',
  'Port Scanning': 'destructive',
  'SMB / NetBIOS': 'warning',
  'Remote Access': 'secondary',
  'Network Services': 'success',
  Databases: 'muted',
  'Active Directory': 'warning',
  'General Purpose': 'muted',
};

// The 61-entry hardcoded catalogue that used to live here is gone (v5.167.0) —
// it is now rows in the backend tool registry, fetched below. See the header
// comment for why: it was a second list the backend could not see, and the two
// had already drifted.

const ToolReference: React.FC = () => {
  const toast = useToast();
  const [filter, setFilter] = useState('');
  const [vetting, setVetting] = useState<ToolEntry | null>(null);
  const { hasRole } = useAuth();

  // A vetted row is put back into this read by the dialog that saved it.
  const registry = useQuery({
    queryKey: ['getToolRegistry'],
    queryFn: ({ signal }) => getToolRegistry(undefined, signal),
  });
  const tools = useMemo(() => registry.data?.tools ?? [], [registry.data]);
  const loading = registry.isPending;
  const error = queryErrorText(registry.error, 'Could not load the tool catalogue.');

  // Vetting is admin-only and deployment-wide (the catalogue is shared by every
  // project), so the affordance only exists for admins — the read view is
  // unchanged for everyone else.
  const isAdmin = hasRole('admin');
  const pending = useMemo(() => tools.filter((t) => t.status === 'suggested'), [tools]);

  const lowerFilter = filter.toLowerCase();
  const filtered = tools.filter(
    (t) =>
      t.name.toLowerCase().includes(lowerFilter) ||
      t.description.toLowerCase().includes(lowerFilter) ||
      t.category.toLowerCase().includes(lowerFilter) ||
      (t.ports || '').toLowerCase().includes(lowerFilter),
  );

  // Curated categories render in their intended order; anything else — a
  // vetted-in suggestion filed under a category nobody has curated yet — still
  // renders, appended alphabetically. The alternative (a fixed list) would
  // silently drop tools the registry knows about, which is the failure mode
  // this migration exists to end.
  const presentCategories = Array.from(new Set(filtered.map((t) => t.category)));
  const orderedCategories: Category[] = [
    ...CATEGORIES.filter((c) => presentCategories.includes(c)),
    ...presentCategories.filter((c) => !CATEGORIES.includes(c as (typeof CATEGORIES)[number])).sort(),
  ];

  const grouped = orderedCategories.reduce<Record<Category, ToolEntry[]>>(
    (acc, cat) => {
      const items = filtered.filter((t) => t.category === cat);
      if (items.length) acc[cat] = items;
      return acc;
    },
    {} as Record<Category, ToolEntry[]>,
  );

  const copyInstall = (cmd: string, toolName: string) => {
    const trimmed = cmd.split('#')[0].trim();
    copyToClipboard(trimmed).then((ok) =>
      ok
        ? toast.success(`Copied install command for ${toolName}`, { id: `copy-${toolName}` })
        : toast.error('Could not copy to clipboard'),
    );
  };

  // Run commands are copied VERBATIM — unlike install strings they carry no
  // "# or" alternative, and the output flags (-oX / -json / …) are exactly
  // what makes the result ingestible, so we must not strip anything.
  const copyRun = (cmd: string, toolName: string) => {
    copyToClipboard(cmd).then((ok) =>
      ok
        ? toast.success(`Copied run command for ${toolName}`, { id: `copyrun-${toolName}` })
        : toast.error('Could not copy to clipboard'),
    );
  };

  const groupedEntries = Object.entries(grouped) as Array<[Category, ToolEntry[]]>;

  return (
    <div className="p-md md:p-lg">
      <h1 className="text-page-title">Tool Reference</h1>
      <p className="mt-xxs mb-md text-metadata text-muted-foreground">
        Tools available as connection helpers on the host detail page. Each tool is suggested when
        a matching port or service is detected. Use the install commands below to set up any tools
        you are missing — and, where shown, the <span className="font-medium text-foreground">Run for
        BlueStick</span> command to produce output BlueStick can ingest.{' '}
        <Link to="/reference/tool-coverage" className="text-info underline-offset-2 hover:underline">
          What BlueStick reads
        </Link>{' '}
        lists, for each of those, what is kept, where it is shown and what is dropped.
      </p>
      <p className="mb-md text-metadata text-muted-foreground">
        This is a catalogue, read by you and by agents alike — it does not decide what an agent
        may run; you do, from the agent you drive.{' '}
        <span className="font-medium text-foreground">Suggested</span> tools were proposed by an
        agent and wait for an admin to add them to the catalogue or decline them.
      </p>

      {isAdmin && pending.length > 0 ? (
        <Alert variant="warning" className="mb-md">
          <AlertDescription>
            <span className="font-medium">
              {pending.length} tool{pending.length === 1 ? '' : 's'} awaiting review
            </span>{' '}
            — an agent asked for {pending.map((t) => t.name).join(', ')}. Use{' '}
            <span className="font-medium">Review</span> on the row to add it to the catalogue or
            decline it.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="relative mb-md max-w-md">
        <Search
          className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          type="search"
          placeholder="Filter by name, category, description, or port..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="pl-xl"
          aria-label="Filter tools"
        />
      </div>

      {loading ? (
        <p className="flex items-center gap-sm text-metadata text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Loading tool catalogue…
        </p>
      ) : error ? (
        <Alert variant="destructive">
          <AlertDescription className="break-words">
            {error}{' '}
            <button type="button" className="underline hover:no-underline" onClick={() => { void registry.refetch(); }}>
              Retry
            </button>
          </AlertDescription>
        </Alert>
      ) : groupedEntries.length === 0 ? (
        <p className="text-metadata text-muted-foreground">
          {filter ? `No tools match "${filter}".` : 'No tools are registered.'}
        </p>
      ) : (
        <>
        {/* One entry per category the filter leaves.  Always the picker, so
            the control does not change shape as the reader types. */}
        <SectionJumpBar
          label="Tool categories"
          presentation="picker"
          entries={groupedEntries.map(([category, items]) => ({
            id: sectionId(category, 'category'),
            label: category,
            count: String(items.length),
          }))}
        />
        <Accordion
          type="multiple"
          defaultValue={groupedEntries.map(([cat]) => cat)}
          className="flex flex-col gap-sm"
        >
          {groupedEntries.map(([category, tools]) => (
            <AccordionItem
              key={category}
              value={category}
              id={sectionId(category, 'category')}
              style={jumpTargetStyle}
              className="rounded-panel border border-border bg-card px-md"
            >
              <AccordionTrigger>
                <div className="flex items-center gap-sm">
                  <Badge variant={CATEGORY_TONE[category] || 'muted'}>{category}</Badge>
                  <span className="text-metadata font-medium text-muted-foreground">
                    {tools.length} tool{tools.length === 1 ? '' : 's'}
                  </span>
                </div>
              </AccordionTrigger>
              <AccordionContent className="pb-md">
                <div className="overflow-x-auto rounded-panel border border-border">
                  <Table className="min-w-[860px]">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[18%]">Tool</TableHead>
                        <TableHead className="w-[30%]">Description</TableHead>
                        <TableHead className="w-[10%]">Ports</TableHead>
                        <TableHead className="w-[34%]">Install / Run</TableHead>
                        <TableHead className="w-[8%] text-center">Kali</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tools.map((tool) => {
                        const statusBadge = STATUS_BADGE[tool.status];
                        return (
                        <TableRow key={tool.name} id={`tool-row-${tool.name}`}>
                          <TableCell>
                            <div className="flex min-w-0 flex-col items-start gap-xxs">
                              {safeHttpHref(tool.url) ? (
                                <a
                                  href={safeHttpHref(tool.url)}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex max-w-full items-center gap-xxs font-semibold text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                                >
                                  <span className="truncate">{tool.name}</span>
                                  <ExternalLink className="size-3 shrink-0" aria-hidden />
                                </a>
                              ) : (
                                <span className="truncate font-semibold text-foreground">
                                  {tool.name}
                                </span>
                              )}
                              {statusBadge && (
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Badge variant={statusBadge.tone} className="max-w-full truncate">
                                      {statusBadge.label}
                                    </Badge>
                                  </TooltipTrigger>
                                  <TooltipContent className="max-w-sm">
                                    {statusBadge.title}
                                  </TooltipContent>
                                </Tooltip>
                              )}
                              {/* v5.296.0 — what BlueStick keeps from this tool's output. */}
                              {tool.ingestible && (
                                <Link
                                  to={`/reference/tool-coverage?tool=${encodeURIComponent(tool.name)}`}
                                  className="text-caption text-info underline-offset-2 hover:underline"
                                >
                                  What BlueStick reads
                                </Link>
                              )}
                              {isAdmin && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-auto px-0 text-caption"
                                  onClick={() => setVetting(tool)}
                                >
                                  {tool.status === 'suggested' ? 'Review' : 'Edit'}
                                </Button>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <span className="line-clamp-2 text-metadata text-foreground">
                              {tool.description}
                            </span>
                            {tool.status === 'suggested' && tool.suggested_rationale && (
                              <span className="mt-xxs block line-clamp-2 text-caption text-muted-foreground break-words">
                                Agent rationale: {tool.suggested_rationale}
                              </span>
                            )}
                          </TableCell>
                          <TableCell>
                            <code className="font-mono text-caption text-foreground break-words">
                              {tool.ports || '—'}
                            </code>
                          </TableCell>
                          <TableCell>
                            <div className="space-y-xs">
                              {tool.install ? (
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      type="button"
                                      onClick={() => copyInstall(tool.install as string, tool.name)}
                                      className={cn(
                                        'block w-full rounded-control bg-muted px-xs py-xxs text-left font-mono text-caption text-foreground break-words',
                                        'transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                                      )}
                                    >
                                      {tool.install}
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent>Click to copy install command</TooltipContent>
                                </Tooltip>
                              ) : (
                                <span className="block text-caption text-muted-foreground">
                                  No install command recorded
                                </span>
                              )}
                              {tool.run_command && (
                                <div className="space-y-xxs">
                                  <span className="block text-caption font-medium uppercase tracking-wider text-muted-foreground">
                                    Run for BlueStick
                                  </span>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <button
                                        type="button"
                                        onClick={() => copyRun(tool.run_command as string, tool.name)}
                                        className={cn(
                                          'block w-full rounded-control border border-info/40 bg-info/10 px-xs py-xxs text-left font-mono text-caption text-foreground break-words',
                                          'transition-colors hover:bg-info/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                                        )}
                                      >
                                        {tool.run_command}
                                      </button>
                                    </TooltipTrigger>
                                    <TooltipContent>Click to copy — produces BlueStick-ingestible output</TooltipContent>
                                  </Tooltip>
                                  {tool.run_note && (
                                    <span className="block text-caption text-muted-foreground break-words">
                                      {tool.run_note}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="text-center">
                            {tool.kali ? (
                              <Badge variant="success">Yes</Badge>
                            ) : (
                              <Badge variant="outline">No</Badge>
                            )}
                          </TableCell>
                        </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
        </>
      )}

      <ToolVettingDialog
        tool={vetting}
        open={vetting !== null}
        onOpenChange={(open) => {
          if (!open) setVetting(null);
        }}
      />
    </div>
  );
};

export default ToolReference;
