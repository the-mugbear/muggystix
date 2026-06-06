/**
 * Cmd/Ctrl+K command palette — global keyboard-driven navigation.
 *
 * Opens on `mod+k`, closes on Esc / outside-click.  Three groups:
 *   - Pages         (every sidebar destination the current role can reach)
 *   - Projects      (switch to another project without leaving the page)
 *   - Theme         (toggle the active theme without going to the menu)
 *   - Session       (sign out)
 *
 * Mounted once in Layout so the keyboard binding is global.  Uses
 * cmdk's built-in fuzzy match (already pulled in by the Combobox
 * primitive — no new dependency).
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Command as CommandPrimitive } from 'cmdk';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import {
  Bot,
  ClipboardList,
  Compass,
  FileWarning,
  Folder,
  KeyRound,
  LogOut,
  MessageSquareHeart,
  Network as NetworkIcon,
  Palette,
  Search as SearchIcon,
  Settings,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
} from 'lucide-react';
import {
  ActivityPulseIcon,
  AlertHexIcon,
  ScanLinesIcon,
  ScopeIcon,
  ServerStackIcon,
} from './AppIcons';
import { useAppTheme, type AppThemeName } from '../contexts/ThemeContext';
import { useAuth } from '../contexts/AuthContext';
import { useProject } from '../contexts/ProjectContext';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import {
  getHosts,
  getScans,
  getTestPlans,
  type Host,
  type Scan,
  type TestPlanSummary,
} from '../services/api';
import { cn } from '../utils/cn';

type IconComponent = React.FC<{ className?: string }>;

interface NavCommand {
  label: string;
  path: string;
  requiredRole: string;
  Icon: IconComponent;
  /** Extra keywords for cmdk's match haystack. */
  keywords?: string[];
}

const NAV_COMMANDS: NavCommand[] = [
  { label: 'Portfolio', path: '/portfolio', requiredRole: 'viewer', Icon: Folder },
  { label: 'Operations', path: '/operations', requiredRole: 'viewer', Icon: Sparkles },
  { label: 'Scans', path: '/scans', requiredRole: 'viewer', Icon: ScanLinesIcon },
  { label: 'Hosts', path: '/hosts', requiredRole: 'viewer', Icon: ServerStackIcon },
  {
    label: 'Collaboration',
    path: '/activity',
    requiredRole: 'viewer',
    Icon: ActivityPulseIcon,
    keywords: ['notes', 'team', 'comments'],
  },
  {
    label: 'Agent Runs',
    path: '/agent-activity',
    requiredRole: 'viewer',
    Icon: Bot,
    keywords: ['agent', 'sessions', 'llm'],
  },
  { label: 'Scopes', path: '/scopes', requiredRole: 'analyst', Icon: ScopeIcon },
  {
    label: 'Recon Runs',
    path: '/recon/runs',
    requiredRole: 'viewer',
    Icon: Compass,
    keywords: ['discovery'],
  },
  { label: 'Test Plans', path: '/test-plans', requiredRole: 'viewer', Icon: ShieldCheck },
  {
    label: 'Executions',
    path: '/executions',
    requiredRole: 'viewer',
    Icon: TerminalSquare,
    keywords: ['runs'],
  },
  {
    label: 'Ingestion Results',
    path: '/parse-errors',
    requiredRole: 'analyst',
    Icon: AlertHexIcon,
    keywords: ['errors', 'parse'],
  },
  {
    label: 'Agent Feedback',
    path: '/feedback',
    requiredRole: 'admin',
    Icon: MessageSquareHeart,
  },
  {
    label: 'LLM Providers',
    path: '/llm-settings',
    requiredRole: 'viewer',
    Icon: Sparkles,
    keywords: ['ai', 'openai', 'anthropic', 'gemini'],
  },
  {
    label: 'Scanner Integrations',
    path: '/integrations',
    requiredRole: 'analyst',
    Icon: KeyRound,
    keywords: ['nessus', 'shodan', 'api'],
  },
  {
    label: 'Project Settings',
    path: '/project-settings',
    requiredRole: 'analyst',
    Icon: Settings,
    keywords: ['members'],
  },
  {
    label: 'Reference',
    path: '/reference',
    requiredRole: 'viewer',
    Icon: NetworkIcon,
    keywords: ['docs', 'help', 'guide'],
  },
  {
    label: 'Profile',
    path: '/profile',
    requiredRole: 'viewer',
    Icon: Settings,
    keywords: ['account', 'password'],
  },
  {
    label: 'System Settings',
    path: '/system-settings',
    requiredRole: 'admin',
    Icon: Settings,
    keywords: ['users', 'admin'],
  },
];

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({ open, onOpenChange }) => {
  const navigate = useNavigate();
  const { hasPermission, logout } = useAuth();
  const { projects, currentProject, selectProject } = useProject();
  const { themeName, setThemeName, availableThemes } = useAppTheme();
  const [search, setSearch] = useState('');

  // v2.43.0 — UX review #6.  All three resource searches now hit the
  // server with a `search=` query and a small `limit`; previously plans
  // and scans were fetched unfiltered and client-side filtered (degraded
  // poorly at scale, hid failures behind "no results").  Per-group
  // error state surfaces backend failures inline instead of swallowing.
  const debouncedSearch = useDebouncedValue(search, 300);
  const [hostResults, setHostResults] = useState<Host[]>([]);
  const [planResults, setPlanResults] = useState<TestPlanSummary[]>([]);
  const [scanResults, setScanResults] = useState<Scan[]>([]);
  const [resourcesLoading, setResourcesLoading] = useState(false);
  const [hostsError, setHostsError] = useState<string | null>(null);
  const [plansError, setPlansError] = useState<string | null>(null);
  const [scansError, setScansError] = useState<string | null>(null);

  // Reset search on close so the next open starts clean.
  useEffect(() => {
    if (!open) {
      setSearch('');
      setHostResults([]);
      setPlanResults([]);
      setScanResults([]);
      setHostsError(null);
      setPlansError(null);
      setScansError(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const q = debouncedSearch.trim();
    if (q.length < 2) {
      setHostResults([]);
      setPlanResults([]);
      setScanResults([]);
      setHostsError(null);
      setPlansError(null);
      setScansError(null);
      setResourcesLoading(false);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setResourcesLoading(true);
    setHostsError(null);
    setPlansError(null);
    setScansError(null);

    // Helper that converts a fetch failure to a per-group banner unless
    // the request was aborted (debounce or unmount), in which case it
    // stays silent.
    const groupFail = (
      setError: (msg: string | null) => void,
      label: string,
    ) => (err: unknown) => {
      if (cancelled) return;
      const e = err as { code?: string; name?: string };
      if (e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError') return;
      setError(`${label} search unavailable — try refining your query or retry.`);
    };

    const hostsPromise = getHosts(
      { search: q, limit: 5, include_total: false },
      controller.signal,
    )
      .then((response) => {
        if (!cancelled) setHostResults(response.items ?? []);
      })
      .catch(groupFail(setHostsError, 'Hosts'));

    const plansPromise = getTestPlans({ search: q, limit: 5, signal: controller.signal })
      .then((plans) => {
        if (!cancelled) setPlanResults(plans);
      })
      .catch(groupFail(setPlansError, 'Test plans'));

    const scansPromise = getScans(0, 5, { search: q, signal: controller.signal })
      .then((scans) => {
        if (!cancelled) setScanResults(scans);
      })
      .catch(groupFail(setScansError, 'Scans'));

    Promise.allSettled([hostsPromise, plansPromise, scansPromise]).then(() => {
      if (!cancelled) setResourcesLoading(false);
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [debouncedSearch, open]);

  const navItems = useMemo(
    () => NAV_COMMANDS.filter((entry) => hasPermission(entry.requiredRole)),
    [hasPermission],
  );

  const showResourceGroups = debouncedSearch.trim().length >= 2;

  const run = (fn: () => void) => {
    onOpenChange(false);
    // Defer so the dialog close animation doesn't fight the
    // navigation / theme change happening on the same frame.
    setTimeout(fn, 0);
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            'fixed inset-0 z-50 bg-black/60 backdrop-blur-sm',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
          )}
        />
        <DialogPrimitive.Content
          aria-label="Command palette"
          className={cn(
            'fixed left-1/2 top-[15vh] z-50 w-full max-w-xl -translate-x-1/2 overflow-hidden',
            'rounded-panel border border-border bg-popover text-popover-foreground shadow-overlay',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
            'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
            'focus:outline-none',
          )}
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            Type to search pages, projects, and quick actions. Use arrow keys to navigate, Enter to
            run, Escape to dismiss.
          </DialogPrimitive.Description>
          <CommandPrimitive loop shouldFilter>
            <div className="flex items-center gap-xs border-b border-border px-sm">
              <SearchIcon className="size-4 text-muted-foreground" aria-hidden />
              <CommandPrimitive.Input
                autoFocus
                value={search}
                onValueChange={setSearch}
                placeholder="Search pages, projects, themes…"
                className="flex h-10 w-full bg-transparent text-body text-foreground placeholder:text-muted-foreground focus:outline-none"
              />
              <kbd className="hidden text-caption text-muted-foreground sm:inline">esc</kbd>
            </div>
            <CommandPrimitive.List className="max-h-[24rem] overflow-y-auto py-xxs">
              <CommandPrimitive.Empty className="px-sm py-md text-center text-metadata text-muted-foreground">
                No matches.
              </CommandPrimitive.Empty>

              <CommandPrimitive.Group
                heading="Pages"
                className={cn(
                  '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                  '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                  '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                  '[&_[cmdk-group-heading]]:text-muted-foreground',
                )}
              >
                {navItems.map((item) => (
                  <CommandPrimitive.Item
                    key={item.path}
                    value={`page:${item.path}`}
                    keywords={[item.label, ...(item.keywords ?? [])]}
                    onSelect={() => run(() => navigate(item.path))}
                    className={itemClass}
                  >
                    <item.Icon className="size-4 text-muted-foreground" />
                    <span className="flex-1">{item.label}</span>
                    <span className="text-caption text-muted-foreground">{item.path}</span>
                  </CommandPrimitive.Item>
                ))}
              </CommandPrimitive.Group>

              {showResourceGroups && (
                <>
                  <CommandPrimitive.Group
                    heading="Hosts"
                    className={cn(
                      '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                      '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                      '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                      '[&_[cmdk-group-heading]]:text-muted-foreground',
                    )}
                  >
                    {resourcesLoading && hostResults.length === 0 && !hostsError && (
                      <div className="px-sm py-xxs text-caption text-muted-foreground">
                        Searching…
                      </div>
                    )}
                    {hostsError && (
                      <div className="px-sm py-xxs text-caption text-destructive">
                        {hostsError}
                      </div>
                    )}
                    {hostResults.map((host) => {
                      const label = host.hostname || host.ip_address;
                      return (
                        <CommandPrimitive.Item
                          key={`host:${host.id}`}
                          value={`host:${host.id}:${host.ip_address}:${host.hostname ?? ''}`}
                          keywords={[host.ip_address, host.hostname ?? '']}
                          onSelect={() => run(() => navigate(`/hosts/${host.id}`))}
                          className={itemClass}
                        >
                          <ServerStackIcon className="size-4 text-muted-foreground" />
                          <span className="min-w-0 flex-1 truncate">{label}</span>
                          {host.hostname && (
                            <span className="text-caption text-muted-foreground">
                              {host.ip_address}
                            </span>
                          )}
                        </CommandPrimitive.Item>
                      );
                    })}
                  </CommandPrimitive.Group>

                  <CommandPrimitive.Group
                    heading="Test Plans"
                    className={cn(
                      '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                      '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                      '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                      '[&_[cmdk-group-heading]]:text-muted-foreground',
                    )}
                  >
                    {resourcesLoading && planResults.length === 0 && !plansError && (
                      <div className="px-sm py-xxs text-caption text-muted-foreground">
                        Searching…
                      </div>
                    )}
                    {plansError && (
                      <div className="px-sm py-xxs text-caption text-destructive">
                        {plansError}
                      </div>
                    )}
                    {planResults.map((plan) => (
                      <CommandPrimitive.Item
                        key={`plan:${plan.id}`}
                        value={`plan:${plan.id}:${plan.title}`}
                        keywords={[plan.title, plan.status]}
                        onSelect={() => run(() => navigate(`/test-plans/${plan.id}`))}
                        className={itemClass}
                      >
                        <ShieldCheck className="size-4 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate">{plan.title}</span>
                        <span className="text-caption text-muted-foreground">{plan.status}</span>
                      </CommandPrimitive.Item>
                    ))}
                  </CommandPrimitive.Group>

                  <CommandPrimitive.Group
                    heading="Scans"
                    className={cn(
                      '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                      '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                      '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                      '[&_[cmdk-group-heading]]:text-muted-foreground',
                    )}
                  >
                    {resourcesLoading && scanResults.length === 0 && !scansError && (
                      <div className="px-sm py-xxs text-caption text-muted-foreground">
                        Searching…
                      </div>
                    )}
                    {scansError && (
                      <div className="px-sm py-xxs text-caption text-destructive">
                        {scansError}
                      </div>
                    )}
                    {scanResults.map((scan) => (
                      <CommandPrimitive.Item
                        key={`scan:${scan.id}`}
                        value={`scan:${scan.id}:${scan.filename}`}
                        keywords={[
                          scan.filename,
                          scan.scan_type ?? '',
                          scan.tool_name ?? '',
                        ]}
                        onSelect={() => run(() => navigate(`/scans/${scan.id}`))}
                        className={itemClass}
                      >
                        <ScanLinesIcon className="size-4 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate">{scan.filename}</span>
                        {scan.scan_type && (
                          <span className="text-caption text-muted-foreground">
                            {scan.scan_type}
                          </span>
                        )}
                      </CommandPrimitive.Item>
                    ))}
                  </CommandPrimitive.Group>
                </>
              )}

              {projects.length > 1 && (
                <CommandPrimitive.Group
                  heading="Switch project"
                  className={cn(
                    '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                    '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                    '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                    '[&_[cmdk-group-heading]]:text-muted-foreground',
                  )}
                >
                  {projects.map((project) => {
                    const isCurrent = project.id === currentProject?.id;
                    return (
                      <CommandPrimitive.Item
                        key={project.id}
                        value={`project:${project.id}`}
                        keywords={[project.name]}
                        onSelect={() =>
                          run(() => {
                            if (!isCurrent) selectProject(project);
                          })
                        }
                        className={itemClass}
                      >
                        <Folder className="size-4 text-muted-foreground" />
                        <span className="flex-1 truncate">{project.name}</span>
                        {isCurrent && (
                          <span className="text-caption text-muted-foreground">current</span>
                        )}
                      </CommandPrimitive.Item>
                    );
                  })}
                </CommandPrimitive.Group>
              )}

              <CommandPrimitive.Group
                heading="Theme"
                className={cn(
                  '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                  '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                  '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                  '[&_[cmdk-group-heading]]:text-muted-foreground',
                )}
              >
                {availableThemes.map((option) => {
                  const isActive = option.value === themeName;
                  return (
                    <CommandPrimitive.Item
                      key={option.value}
                      value={`theme:${option.value}`}
                      keywords={[option.label, 'theme', 'color']}
                      onSelect={() => run(() => setThemeName(option.value as AppThemeName))}
                      className={itemClass}
                    >
                      <Palette className="size-4 text-muted-foreground" />
                      <span className="flex-1">{option.label}</span>
                      {isActive && (
                        <span className="text-caption text-muted-foreground">active</span>
                      )}
                    </CommandPrimitive.Item>
                  );
                })}
              </CommandPrimitive.Group>

              <CommandPrimitive.Group
                heading="Session"
                className={cn(
                  '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                  '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                  '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                  '[&_[cmdk-group-heading]]:text-muted-foreground',
                )}
              >
                <CommandPrimitive.Item
                  value="session:logout"
                  keywords={['sign out', 'logout', 'exit', 'quit']}
                  onSelect={() => run(() => logout())}
                  className={itemClass}
                >
                  <LogOut className="size-4 text-destructive" />
                  <span className="flex-1 text-destructive">Sign out</span>
                </CommandPrimitive.Item>
              </CommandPrimitive.Group>
            </CommandPrimitive.List>

            <div className="flex items-center justify-between border-t border-border px-sm py-xxs text-caption text-muted-foreground">
              <span className="flex items-center gap-xs">
                <kbd className="rounded border border-border bg-muted px-xxs">↑</kbd>
                <kbd className="rounded border border-border bg-muted px-xxs">↓</kbd>
                navigate
              </span>
              <span className="flex items-center gap-xs">
                <kbd className="rounded border border-border bg-muted px-xxs">↵</kbd>
                run
              </span>
              <span className="hidden sm:inline">
                <kbd className="rounded border border-border bg-muted px-xxs">esc</kbd> close
              </span>
            </div>
          </CommandPrimitive>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

const itemClass = cn(
  'flex cursor-pointer select-none items-center gap-xs rounded-control px-sm py-xs text-metadata text-foreground',
  'data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground',
  'data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50',
);

export default CommandPalette;
