import React, { useEffect, useMemo, useState } from 'react';
import { copyToClipboard } from '../utils/clipboard';
import { Search, ChevronDown, ExternalLink, Copy, Loader2, RefreshCw } from 'lucide-react';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
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
import {
  getToolReadiness,
  getToolRegistry,
  ToolReadinessResponse,
  ToolReadinessStatus,
  ToolRegistryEntry,
} from '../services/api/references';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Tool catalogue
//
// v5.167.0 — served by the backend tool registry rather than hardcoded here.
// This page and the agent catalogue used to be separate lists in separate
// languages, and only the backend one could gate anything; they had already
// drifted. One registry, two views: this page renders everything, the agent
// catalogue is the `approved` subset. Approval status now shows per row, so
// "may an agent run this?" is answerable where operators already read about
// tools — and that is where vetted-in suggestions surface too.
// ---------------------------------------------------------------------------

type ToolEntry = ToolRegistryEntry;

const STATUS_BADGE: Record<string, { tone: CategoryTone; label: string; title: string }> = {
  approved: {
    tone: 'success',
    label: 'Agent-approved',
    title: 'An agent may run this tool against inventory hosts.',
  },
  reference: {
    tone: 'outline',
    label: 'Reference only',
    title: 'Documented for operators; not offered to agents.',
  },
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

/**
 * BlueStick-ingestible run commands, keyed by tool name.  Only tools with a
 * parser get an entry — the exact invocation (with the machine-readable output
 * flag) that produces a file BlueStick can upload.  `note` explains what to
 * upload / any gotcha.  Kept as a sibling map (not inlined on every TOOLS row)
 * so the ~200-char catalogue lines stay readable; grounded in AGENTS.md's
 * "Supported upload formats" table and documentation/UPLOAD_FORMATS.md.
 * `<target>` / list files are placeholders the operator fills.
 *
 * SOURCE OF TRUTH for the output-extension contract:
 *   backend/app/services/tool_output_contract.py
 * A backend test (backend/tests/test_tool_command_consistency.py) fails if any
 * run command here writes an extension that tool's parser can't ingest — so
 * this map, the backend recon catalog, and the two docs can't silently drift.
 */
interface RunCommand {
  run: string;
  note?: string;
}

const RUN_COMMANDS: Record<string, RunCommand> = {
  // Port scanning
  nmap: { run: 'nmap -sV -sC -O -oX scan.xml <target>', note: 'Upload scan.xml (-oX). Use -oG for a .gnmap instead.' },
  masscan: { run: 'sudo masscan -p1-65535 --rate=1000 -oX masscan.xml <target>', note: 'Upload the XML (-oX), JSON (-oJ), or list (-oL).' },
  rustscan: { run: 'rustscan -a <target> -- -sV -oX scan.xml', note: 'Pipes into nmap — upload the resulting nmap scan.xml.' },
  naabu: { run: 'naabu -host <target> -json -o naabu.json', note: 'Upload naabu.json (-json). Include "naabu" in the filename.' },
  // Web analysis
  httpx: { run: 'httpx -l targets.txt -sc -title -tech-detect -favicon -json -o httpx.jsonl', note: 'Upload httpx.jsonl. Call ProjectDiscovery\'s binary by path if the Python httpx CLI shadows it.' },
  whatweb: { run: 'whatweb -a 3 --input-file=targets.txt --log-json=whatweb.json --no-errors', note: 'Upload whatweb.json (--log-json).' },
  eyewitness: { run: 'eyewitness --web -f urls.txt -d eyewitness_report --no-prompt', note: 'Upload the JSON/CSV report (filename must contain "eyewitness" or "report").' },
  nikto: { run: 'nikto -h <target> -Format json -o nikto.json', note: 'Upload nikto.json (-Format json).' },
  nuclei: { run: 'nuclei -l targets.txt -je nuclei.json', note: 'Upload nuclei.json (-je writes the JSON export).' },
  // SMB / AD
  smbmap: { run: 'smbmap -H <target> | tee smbmap.txt', note: 'Upload smbmap.txt — keep the "[+] <ip>" host lines.' },
  netexec: { run: "netexec smb <target> -u '' -p '' --shares", note: 'Upload the --json output or the standard text report.' },
  'bloodhound-python': { run: 'bloodhound-python -d <domain> -u <user> -p <pass> -c All -ns <dc-ip>', note: 'Upload the extracted JSON files, not the ZIP bundle.' },
  // DNS / subdomains
  amass: { run: 'amass enum -d <domain> -json amass.json', note: 'Upload amass.json — best results include resolved IPs.' },
  subfinder: { run: 'subfinder -d <domain> -oJ -o subfinder.json', note: 'Upload subfinder.json (-oJ) with resolved IPs.' },
  dnsx: { run: 'dnsx -j -resp -l ips.txt -r resolvers.txt -ptr -a -aaaa -cname -mx -ns -txt -o dnsx-output.json', note: 'Upload dnsx-output.json (-j). PTR answers feed hostnames.' },
  // Content discovery (unified dirbuster parser — put the tool name in the filename)
  gobuster: { run: 'gobuster dir -u http://<target> -w wordlist.txt -o gobuster.txt', note: 'Upload gobuster.txt (.json/.csv/.txt all parse).' },
  feroxbuster: { run: 'feroxbuster -u http://<target> --json -o feroxbuster.json', note: 'Upload feroxbuster.json (--json).' },
  ffuf: { run: 'ffuf -u http://<target>/FUZZ -w wordlist.txt -of json -o ffuf.json', note: 'Upload ffuf.json (-of json).' },
  dirsearch: { run: 'dirsearch -u http://<target> --format json -o dirsearch.json', note: 'Upload dirsearch.json (--format json).' },
  dirb: { run: 'dirb http://<target> wordlist.txt -o dirb.txt', note: 'Upload dirb.txt.' },
  wfuzz: { run: 'wfuzz -w wordlist.txt -f wfuzz.json,json http://<target>/FUZZ', note: 'Upload wfuzz.json (-f … ,json).' },
};

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

const scrollToCatalogueEntry = (toolName: string): void => {
  const el = document.getElementById(`tool-row-${toolName}`);
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    // Brief highlight so the operator's eye lands on the right row —
    // CSS classes flicker via an inline style + setTimeout because we
    // don't want to thread a stateful "highlighted row" through the
    // catalogue render.
    el.classList.add('bg-accent');
    setTimeout(() => el.classList.remove('bg-accent'), 1200);
  }
};

// ---------------------------------------------------------------------------
// Host readiness — probe-driven view
// ---------------------------------------------------------------------------

const READINESS_BADGE: Record<ToolReadinessStatus, CategoryTone> = {
  installed: 'success',
  missing: 'destructive',
  warn: 'warning',
  unknown: 'outline',
};

const READINESS_LABEL: Record<ToolReadinessStatus, string> = {
  installed: 'Installed',
  missing: 'Missing',
  warn: 'Warning',
  unknown: 'Unknown',
};

/** Pick the most appropriate install command for a tool, preferring the
 *  probe's OS-derived provider, then a sensible fallback order. */
const pickInstallHint = (
  hints: Record<string, string>,
  preferred?: string | null,
): string | null => {
  if (preferred && hints[preferred]) return hints[preferred];
  return (
    hints.apt ||
    hints.brew ||
    hints.pipx ||
    hints.go ||
    hints.cargo ||
    hints.binary ||
    hints.docker ||
    null
  );
};

/**
 * Probe-driven host readiness — the agent tool catalog cross-referenced
 * against the operator's most recent environment probe.  Sits above the
 * static catalogue below; this panel reflects *this* host specifically.
 */
const HostReadinessPanel: React.FC<{ documentedNames: Set<string> }> = ({
  documentedNames,
}) => {
  const toast = useToast();
  const [data, setData] = useState<ToolReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toolFilter, setToolFilter] = useState('');

  const load = React.useCallback(() => {
    setLoading(true);
    setError(null);
    getToolReadiness()
      .then(setData)
      .catch(() => setError('Could not load host readiness.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const copyInstallScript = () => {
    if (!data) return;
    const missing = data.tools.filter((t) => t.status === 'missing');
    const header =
      `# Install commands for ${missing.length} missing tool` +
      `${missing.length === 1 ? '' : 's'}` +
      `${data.os_family ? ` — ${data.os_family} host` : ''}\n` +
      `# Generated by BlueStick from your environment probe` +
      `${data.probed_at ? ` (${new Date(data.probed_at).toLocaleString()})` : ''}.\n` +
      `# Review each line before running — package names and providers vary by host.\n`;
    const blocks = missing.map((t) => {
      const cmd = pickInstallHint(t.install_hints || {}, data.preferred_provider);
      return cmd
        ? `# ${t.tool}\n${cmd}`
        : `# ${t.tool} — no catalog install hint; see the catalogue below`;
    });
    copyToClipboard([header, ...blocks].join('\n')).then((ok) =>
      ok
        ? toast.success('Install commands copied to clipboard')
        : toast.error('Could not copy to clipboard'),
    );
  };

  // Readiness-table filter — scoped to this panel only (the search box
  // below the panel filters the static catalogue, not this table).
  const query = toolFilter.trim().toLowerCase();
  const visibleTools =
    data && data.has_probe
      ? query
        ? data.tools.filter((t) => t.tool.toLowerCase().includes(query))
        : data.tools
      : [];

  return (
    <Card className="mb-md" aria-busy={loading}>
      <CardContent className="p-md">
        <div className="mb-sm flex flex-wrap items-start justify-between gap-sm">
          <div className="min-w-0">
            <h2 className="text-subheading font-semibold">Host Readiness</h2>
            <p className="text-metadata text-muted-foreground">
              The agent tool catalog checked against your most recent environment probe —
              what this host already has and what it still needs for agentic workflows.
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            {loading ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="size-4" aria-hidden />
            )}
            Refresh
          </Button>
        </div>

        {/* First load only — a refresh keeps the prior data on screen
            (aria-busy on the Card + the spinning Refresh button signal
            the in-flight fetch) to avoid blanking the panel. */}
        {loading && !data && (
          <p className="text-metadata text-muted-foreground">Loading host readiness…</p>
        )}

        {error && (
          <Alert variant="destructive" className={data ? 'mb-sm' : undefined}>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {data && !data.has_probe && (
          <Alert variant="info">
            <AlertDescription>
              No environment probe recorded yet. Start an agentic <strong>recon</strong> or{' '}
              <strong>execution</strong> workflow — the agent probes your host at startup, and
              this panel will then show which catalog tools are installed and which are missing.
            </AlertDescription>
          </Alert>
        )}

        {data && data.has_probe && (
          <div className="flex flex-col gap-sm">
            <p className="text-caption text-muted-foreground">
              {data.os_family && (
                <>
                  Host: <strong>{data.os_release || data.os_family}</strong>
                </>
              )}
              {data.shell && <> · shell {data.shell}</>}
              {data.probed_at && <> · probed {new Date(data.probed_at).toLocaleString()}</>}
            </p>
            <div className="flex flex-wrap gap-xs">
              <Badge variant="success">{data.summary.installed} installed</Badge>
              <Badge variant="destructive">{data.summary.missing} missing</Badge>
              <Badge variant="warning">{data.summary.warn} warning</Badge>
              <Badge variant="outline">{data.summary.unknown} unknown</Badge>
            </div>
            {data.summary.missing > 0 && (
              <div>
                <Button size="sm" onClick={copyInstallScript}>
                  <Copy className="size-4" aria-hidden /> Copy install commands (
                  {data.summary.missing} missing)
                </Button>
              </div>
            )}
            <div className="relative max-w-xs">
              <Search
                className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="search"
                placeholder="Filter readiness by tool…"
                value={toolFilter}
                onChange={(e) => setToolFilter(e.target.value)}
                className="pl-xl"
                aria-label="Filter host readiness by tool name"
              />
            </div>
            {visibleTools.length === 0 ? (
              <p className="text-metadata text-muted-foreground">
                No tools match "{toolFilter}".
              </p>
            ) : (
              <div className="overflow-x-auto rounded-panel border border-border">
                <Table className="min-w-[720px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[20%]">Tool</TableHead>
                      <TableHead className="w-[14%]">Status</TableHead>
                      <TableHead className="w-[32%]">Details</TableHead>
                      <TableHead className="w-[34%]">Install</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {visibleTools.map((t) => {
                      const hint = pickInstallHint(
                        t.install_hints || {},
                        data.preferred_provider,
                      );
                      const detail =
                        t.issue ||
                        t.path ||
                        (t.status === 'unknown'
                          ? 'Not reported by the probe'
                          : t.status === 'installed'
                            ? 'On PATH'
                            : '—');
                      return (
                        <TableRow key={t.tool}>
                          <TableCell>
                            <span className="font-semibold text-foreground break-words">
                              {t.tool}
                            </span>
                            {t.intrusive && (
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Badge
                                    variant="warning"
                                    className="ml-xs cursor-help"
                                  >
                                    intrusive
                                  </Badge>
                                </TooltipTrigger>
                                <TooltipContent className="max-w-sm">
                                  Generates active scanning traffic or runs
                                  potentially-impactful checks (vulnerability
                                  scans, exploit templates, brute force). The
                                  agent requests per-command approval before
                                  running these — they do not batch under
                                  plan-level approval.
                                </TooltipContent>
                              </Tooltip>
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant={READINESS_BADGE[t.status]}>
                              {READINESS_LABEL[t.status]}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="line-clamp-2 cursor-help text-caption text-muted-foreground break-words">
                                  {detail}
                                </span>
                              </TooltipTrigger>
                              <TooltipContent className="max-w-sm">{detail}</TooltipContent>
                            </Tooltip>
                          </TableCell>
                          <TableCell>
                            {t.status === 'installed' ? (
                              <span className="text-caption text-muted-foreground">—</span>
                            ) : hint ? (
                              <code className="block break-words font-mono text-caption text-foreground">
                                {hint}
                              </code>
                            ) : documentedNames.has(t.tool) ? (
                              // No install hint in the readiness payload, but
                              // the catalogue below documents this tool — link
                              // there directly instead of leaving the operator
                              // to scroll-and-search.
                              <button
                                type="button"
                                onClick={() => scrollToCatalogueEntry(t.tool)}
                                className="text-caption text-primary underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
                              >
                                View install command in catalogue ↓
                              </button>
                            ) : (
                              <span className="text-caption text-muted-foreground">
                                No install hint available
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

const ToolReference: React.FC = () => {
  const toast = useToast();
  const [filter, setFilter] = useState('');
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [vetting, setVetting] = useState<ToolEntry | null>(null);
  const { hasRole } = useAuth();

  useEffect(() => {
    let cancelled = false;
    getToolRegistry()
      .then((res) => {
        if (cancelled) return;
        setTools(res.tools);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError('Could not load the tool catalogue.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const documentedNames = useMemo(() => new Set(tools.map((t) => t.name)), [tools]);

  // Vetting is admin-only and deployment-wide (approving a tool approves it on
  // every project), so the affordance only exists for admins — the read view is
  // unchanged for everyone else.
  const isAdmin = hasRole('admin');
  const pending = useMemo(() => tools.filter((t) => t.status === 'suggested'), [tools]);

  const applyUpdate = (updated: ToolEntry) => {
    setTools((prev) => prev.map((t) => (t.name === updated.name ? { ...t, ...updated } : t)));
  };

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
        BlueStick</span> command to produce output BlueStick can ingest.
      </p>
      <p className="mb-md text-metadata text-muted-foreground">
        The badge under each tool name is its agent policy:{' '}
        <span className="font-medium text-foreground">Agent-approved</span> tools may be run by an
        agent against hosts in your inventory,{' '}
        <span className="font-medium text-foreground">Reference only</span> tools are documented for
        you to run yourself, and <span className="font-medium text-foreground">Suggested</span> tools
        were proposed by an agent and are waiting on review.
      </p>

      {isAdmin && pending.length > 0 ? (
        <Alert variant="warning" className="mb-md">
          <AlertDescription>
            <span className="font-medium">
              {pending.length} tool{pending.length === 1 ? '' : 's'} awaiting review
            </span>{' '}
            — an agent asked for {pending.map((t) => t.name).join(', ')}. Until one is
            approved, agents are told not to run it. Use{' '}
            <span className="font-medium">Review</span> on the row to decide.
          </AlertDescription>
        </Alert>
      ) : null}

      <HostReadinessPanel documentedNames={documentedNames} />

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
        <Card>
          <CardContent className="flex items-center justify-center gap-sm p-lg text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Loading tool catalogue…
          </CardContent>
        </Card>
      ) : error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : groupedEntries.length === 0 ? (
        <Card>
          <CardContent className="p-lg text-center text-metadata text-muted-foreground">
            {filter ? `No tools match "${filter}"` : 'No tools are registered.'}
          </CardContent>
        </Card>
      ) : (
        <Accordion
          type="multiple"
          defaultValue={groupedEntries.map(([cat]) => cat)}
          className="flex flex-col gap-sm"
        >
          {groupedEntries.map(([category, tools]) => (
            <AccordionItem
              key={category}
              value={category}
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
                        // id lets the Host Readiness panel above scroll
                        // to a specific catalogue row when the operator
                        // clicks "View in catalogue" from a row whose
                        // readiness payload lacks an install_hints entry.
                        <TableRow key={tool.name} id={`tool-row-${tool.name}`}>
                          <TableCell>
                            <div className="flex min-w-0 flex-col items-start gap-xxs">
                              {tool.url ? (
                                <a
                                  href={tool.url}
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
                              {RUN_COMMANDS[tool.name] && (
                                <div className="space-y-xxs">
                                  <span className="block text-caption font-medium uppercase tracking-wider text-muted-foreground">
                                    Run for BlueStick
                                  </span>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <button
                                        type="button"
                                        onClick={() => copyRun(RUN_COMMANDS[tool.name].run, tool.name)}
                                        className={cn(
                                          'block w-full rounded-control border border-info/40 bg-info/10 px-xs py-xxs text-left font-mono text-caption text-foreground break-words',
                                          'transition-colors hover:bg-info/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                                        )}
                                      >
                                        {RUN_COMMANDS[tool.name].run}
                                      </button>
                                    </TooltipTrigger>
                                    <TooltipContent>Click to copy — produces BlueStick-ingestible output</TooltipContent>
                                  </Tooltip>
                                  {RUN_COMMANDS[tool.name].note && (
                                    <span className="block text-caption text-muted-foreground break-words">
                                      {RUN_COMMANDS[tool.name].note}
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
      )}

      <ToolVettingDialog
        tool={vetting}
        open={vetting !== null}
        onOpenChange={(open) => {
          if (!open) setVetting(null);
        }}
        onSaved={applyUpdate}
      />
    </div>
  );
};

export default ToolReference;
