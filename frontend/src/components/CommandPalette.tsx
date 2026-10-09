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
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Command as CommandPrimitive } from 'cmdk';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import {
  Folder,
  LogOut,
  Palette,
  Search as SearchIcon,
} from 'lucide-react';
import {
  AlertHexIcon,
  ScanLinesIcon,
  ServerStackIcon,
} from './AppIcons';
import { useAppTheme, type AppThemeName } from '../contexts/ThemeContext';
import { useAuth } from '../contexts/AuthContext';
import { useRoleGate } from '../hooks/useProjectRole';
import { useProject } from '../contexts/ProjectContext';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useRemediationPolicy } from '../hooks/useRemediationPolicy';
import {
  getFinding,
  getHosts,
  getScans,
  listFindings,
  type Finding,
  type Host,
  type Scan,
} from '../services/api';
import { cn } from '../utils/cn';

// Page nav entries come from the navigation manifest
// (src/config/navigation.tsx) — the single source of truth shared with
// the Layout sidebar and App.tsx route gates.  Add/re-gate pages there.
import { NAV_COMMANDS } from '../config/navigation';

const NO_HOSTS: Host[] = [];
const NO_SCANS: Scan[] = [];
const NO_FINDINGS: Finding[] = [];

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({ open, onOpenChange }) => {
  const navigate = useNavigate();
  const { logout } = useAuth();
  // Project-scoped entries follow the PROJECT role, admin ones the account's.
  const allowed = useRoleGate();
  const { enabled: remediationEnabled } = useRemediationPolicy();
  const { projects, currentProject, selectProject } = useProject();
  const { themeName, setThemeName, availableThemes } = useAppTheme();
  const [search, setSearch] = useState('');

  // v2.43.0 — UX review #6.  Every resource search hits the
  // server with a `search=` query and a small `limit`; previously
  // scans were fetched unfiltered and client-side filtered (degraded
  // poorly at scale, hid failures behind "no results").  Per-group
  // error state surfaces backend failures inline instead of swallowing.
  const debouncedSearch = useDebouncedValue(search, 300);

  // Reset search on close so the next open starts clean.
  useEffect(() => {
    if (!open) setSearch('');
  }, [open]);

  const q = debouncedSearch.trim();
  // "#37" (or "37") is a finding number — searchable from one digit…
  const findingId = /^#?(\d+)$/.exec(q)?.[1];
  const findingNumber = findingId ? Number(findingId) : null;
  // …and a one-digit finding number searches findings only.
  const wide = open && q.length >= 2;
  const byNumber = open && findingNumber != null;

  // One query per group, so one group's failure is said in that group.  The
  // rows of the previous search stay while the next one is read.
  const hosts = useQuery({
    queryKey: ['getHosts', { search: q, limit: 5, include_total: false }],
    queryFn: ({ signal }) => getHosts({ search: q, limit: 5, include_total: false }, signal),
    enabled: wide,
    placeholderData: keepPreviousData,
  });
  const scans = useQuery({
    queryKey: ['getScans', 0, 5, { search: q }],
    queryFn: ({ signal }) => getScans(0, 5, { search: q, signal }),
    enabled: wide,
    placeholderData: keepPreviousData,
  });
  // v5.294.0 (UX review) — findings of the current project, by title, and
  // by number when the query is one ("#37" / "37"); the numbered one first.
  // A number nobody has is a 404, which is simply no match.
  const findingByNumber = useQuery({
    queryKey: ['getFinding', findingNumber],
    queryFn: ({ signal }) => getFinding(findingNumber as number, signal),
    enabled: byNumber,
    placeholderData: keepPreviousData,
  });
  const findingsByTitle = useQuery({
    queryKey: ['listFindings', { search: q, limit: 5 }],
    queryFn: ({ signal }) => listFindings({ search: q, limit: 5 }, signal),
    enabled: wide,
    placeholderData: keepPreviousData,
  });

  const unavailable = (label: string) => `${label} search unavailable — try refining your query or retry.`;
  const hostResults: Host[] = (wide && hosts.data?.items) || NO_HOSTS;
  const hostsError = wide && hosts.isError ? unavailable('Hosts') : null;
  const scanResults: Scan[] = (wide && scans.data) || NO_SCANS;
  const scansError = wide && scans.isError ? unavailable('Scans') : null;
  const findingsError = wide && findingsByTitle.isError ? unavailable('Findings') : null;
  const numbered = byNumber ? findingByNumber.data ?? null : null;
  const titled = wide ? findingsByTitle.data?.items : undefined;
  const findingResults = useMemo<Finding[]>(() => {
    const rows = titled ?? NO_FINDINGS;
    return (numbered ? [numbered, ...rows.filter((f) => f.id !== numbered.id)] : rows).slice(0, 6);
  }, [numbered, titled]);
  const resourcesLoading = hosts.isFetching || scans.isFetching
    || findingByNumber.isFetching || findingsByTitle.isFetching;

  const navItems = useMemo(
    () => NAV_COMMANDS.filter((entry) => allowed(entry.requiredRole) && (!entry.feature || remediationEnabled)),
    [allowed, remediationEnabled],
  );

  const showResourceGroups = debouncedSearch.trim().length >= 2;
  const showFindingGroup = showResourceGroups || /^#?\d+$/.test(debouncedSearch.trim());

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
            Type to search pages, hosts, findings (by title or number), scans, projects
            and quick actions. Use arrow keys to navigate, Enter to run, Escape to dismiss.
          </DialogPrimitive.Description>
          <CommandPrimitive loop shouldFilter>
            <div className="flex items-center gap-xs border-b border-border px-sm">
              <SearchIcon className="size-4 text-muted-foreground" aria-hidden />
              <CommandPrimitive.Input
                autoFocus
                value={search}
                onValueChange={setSearch}
                placeholder="Search pages, hosts, findings, scans, projects…"
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

              {showFindingGroup && (
                <CommandPrimitive.Group
                  heading="Findings"
                  className={cn(
                    '[&_[cmdk-group-heading]]:px-sm [&_[cmdk-group-heading]]:py-xxs',
                    '[&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold',
                    '[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider',
                    '[&_[cmdk-group-heading]]:text-muted-foreground',
                  )}
                >
                  {resourcesLoading && findingResults.length === 0 && !findingsError && (
                    <div className="px-sm py-xxs text-caption text-muted-foreground">
                      Searching…
                    </div>
                  )}
                  {findingsError && (
                    <div className="px-sm py-xxs text-caption text-destructive">
                      {findingsError}
                    </div>
                  )}
                  {findingResults.map((finding) => (
                    <CommandPrimitive.Item
                      key={`finding:${finding.id}`}
                      value={`finding:${finding.id}:${finding.title}`}
                      // The typed query must match for cmdk to keep the row:
                      // the title, and the number as "#37" and "37".
                      keywords={[finding.title, `#${finding.id}`, String(finding.id)]}
                      onSelect={() => run(() => navigate(`/findings/${finding.id}`))}
                      className={itemClass}
                    >
                      <AlertHexIcon className="size-4 text-muted-foreground" />
                      <span className="shrink-0 tabular-nums text-caption text-muted-foreground">#{finding.id}</span>
                      <span className="min-w-0 flex-1 truncate">{finding.title}</span>
                      <span className="shrink-0 text-caption capitalize text-muted-foreground">
                        {finding.severity}
                      </span>
                    </CommandPrimitive.Item>
                  ))}
                </CommandPrimitive.Group>
              )}

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
