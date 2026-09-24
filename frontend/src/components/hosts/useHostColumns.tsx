import React, { useMemo } from 'react';
import { ColumnDef } from '@tanstack/react-table';
import { Link } from 'react-router-dom';
import {
  Bookmark,
  BookmarkPlus,
  Check,
  ChevronRight,
  Copy,
} from 'lucide-react';

import type { Host, FollowStatus, HostDiscovery, Port } from '../../services/api';
import { copyToClipboard } from '../../utils/clipboard';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { cn } from '../../utils/cn';
import { formatRelativeTime } from '../../utils/relativeTime';
import {
  exposureChips,
} from '../../utils/portsOfInterest';
import { matchedEndpoints, type EndpointMatchCriteria } from '../../utils/endpointMatch';

// Map a tag's palette key to a coloured dot.  Unknown / null colours
// fall back to a neutral dot — the backend stores whatever string the
// UI sends, so this must tolerate anything.
const TAG_DOT_CLASS: Record<string, string> = {
  red: 'bg-destructive',
  orange: 'bg-warning',
  amber: 'bg-warning',
  yellow: 'bg-warning',
  green: 'bg-success',
  teal: 'bg-success',
  blue: 'bg-info',
  info: 'bg-info',
  violet: 'bg-info',
  purple: 'bg-info',
  pink: 'bg-destructive',
};
const tagDotClass = (color?: string | null): string =>
  (color && TAG_DOT_CLASS[color.toLowerCase()]) || 'bg-muted-foreground/50';

/**
 * Per-row copy-IP control.  Feeding IPs to external tools is the core loop,
 * so it gets a one-click affordance instead of select-text-and-copy.  Stops
 * propagation so it never opens the row inspector; flips to a check for a
 * beat on success.  Co-located with the other host-table cell helpers.
 */
const CopyIpButton: React.FC<{ ip: string }> = ({ ip }) => {
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-6 shrink-0 text-muted-foreground hover:text-foreground"
      aria-label={`Copy IP ${ip}`}
      title={`Copy ${ip}`}
      onClick={(e) => {
        e.stopPropagation();
        void copyToClipboard(ip).then((ok) => {
          if (!ok) return;
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
    </Button>
  );
};

// Compact liveness indicator for the IP cell (B3-1).  host.state was only
// visible in the inspector / expanded sub-row, so the table couldn't answer
// "is this thing even alive?" — and /operations itself notes masscan/naabu/DNS
// often leave it unknown.  Non-interactive (the IP cell is a button, so no
// nested interactive el): a coloured dot + a title.  Mirrors stateBadgeClass:
// up=success, down=destructive, unknown/absent=hollow muted ring.
const StateDot: React.FC<{ state: string | null | undefined }> = ({ state }) => {
  const cls =
    state === 'up'
      ? 'bg-success'
      : state === 'down'
        ? 'bg-destructive'
        : 'border border-muted-foreground/50';
  const title =
    state === 'up'
      ? 'State: up — a scanner confirmed this host responding'
      : state === 'down'
        ? 'State: down — a scanner reported this host not responding'
        : 'State: unknown — liveness not confirmed (e.g. masscan / naabu / DNS)';
  return (
    <span
      role="img"
      data-host-state={state === 'up' || state === 'down' ? state : 'unknown'}
      className={cn('mt-1 inline-block size-2 shrink-0 rounded-full', cls)}
      title={title}
      aria-label={title}
    />
  );
};

/**
 * Hosts-table column definitions extracted from Hosts.tsx
 * (v2.43.0 — MONO-1).  Pre-extraction this was a 166-line `useMemo`
 * inline in a 2098-LoC file.  Pulling it out lets the page focus on
 * orchestration (data fetch, filter state, dialogs) and isolates the
 * cell-level formatting that grows fastest as columns are tuned.
 *
 * Co-located helpers (FollowMenu, format functions, status constants)
 * are scoped to host-table rendering and have no callers outside this
 * file, so they live here rather than in a generic utils module.
 */

// --- Status / display constants -------------------------------------------

// Review lifecycle the operator drives: In Review → Reviewed.  The legacy
// "watching" follow state is retired (nobody followed hosts, they review
// them) — it stays in the FollowStatus type + display meta so any old row
// still renders, but it is no longer offered as a choice anywhere.
export const FOLLOW_STATUS_OPTIONS: Array<{
  value: FollowStatus;
  label: string;
  badgeClass: string;
}> = [
  { value: 'in_review', label: 'In Review', badgeClass: 'bg-warning text-warning-foreground' },
  { value: 'reviewed', label: 'Reviewed', badgeClass: 'bg-success text-success-foreground' },
];

// --- Pure helpers ---------------------------------------------------------

export const getLatestDiscovery = (discoveries?: HostDiscovery[]): HostDiscovery | null =>
  (discoveries ?? []).reduce<HostDiscovery | null>((latest, discovery) => {
    if (!latest) return discovery;
    const latestTime = new Date(latest.discovered_at || 0).getTime();
    const nextTime = new Date(discovery.discovered_at || 0).getTime();
    return nextTime > latestTime ? discovery : latest;
  }, null);

export const getTopServices = (hostPorts: Port[] = []): string[] =>
  hostPorts
    .filter((port) => port.state === 'open' && port.service_name)
    .slice(0, 3)
    .map((port) => port.service_name!)
    .filter(Boolean);

export const getScanLabel = (discovery: HostDiscovery): string =>
  discovery.scan_filename || `Scan #${discovery.scan_id}`;

export const formatRelativeLastViewed = (value?: string | null): string | null =>
  formatRelativeTime(value, { fallback: null });

// --- Redesign helpers: Host / Exposure / Attention columns ----------------

/** Compact relative age ("3d", "5h", "2mo") for a host's last_seen. */
export const relativeAge = (iso?: string | null): string | null => {
  if (!iso) return null;
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return null;
  const mins = Math.floor(Math.max(diff, 0) / 60000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.floor(days / 365)}y`;
};

/** A host first seen within the last 7 days reads as "new". */
export const isNewHost = (iso?: string | null): boolean => {
  if (!iso) return false;
  const diff = Date.now() - new Date(iso).getTime();
  return !Number.isNaN(diff) && diff >= 0 && diff < 7 * 86_400_000;
};

/**
 * What the Host column says under a host that no subnet contains.  Mirrors
 * the detail card's three states (v5.220.0) — before, every such host read
 * "out of scope", including one an approved name resolves to and every host
 * on a project that has declared no scope at all.
 */
export const scopeCoverageText = (
  host: Pick<Host, 'scope_coverage' | 'project_has_scope'>,
): { text: string; title: string; tone: 'info' | 'warning' | 'muted' } => {
  if (host.scope_coverage === 'name') {
    return {
      text: 'via in-scope name',
      title: 'No subnet entry contains this address, but an approved name currently resolves to it.',
      tone: 'info',
    };
  }
  if (host.project_has_scope === false) {
    return {
      text: 'no scope defined',
      title: 'This project has no subnet or domain entries yet, so there is nothing to check against.',
      tone: 'muted',
    };
  }
  return {
    text: 'out of scope',
    title: 'No scope entry covers this address or its names.',
    tone: 'warning',
  };
};

const ScopeCoverageLabel: React.FC<{ host: Host }> = ({ host }) => {
  const { text, title, tone } = scopeCoverageText(host);
  return (
    <span
      className={cn(
        'italic',
        tone === 'warning' && 'text-warning',
        tone === 'info' && 'text-info',
      )}
      title={title}
    >
      {text}
    </span>
  );
};

interface AttentionReason {
  label: string;
  tone: 'severity-critical' | 'severity-high' | 'destructive' | 'warning' | 'info' | 'muted';
  // Plain-language explanation shown on hover — the chip label is terse, so
  // the "why" (e.g. what "Changed" means) lives here.
  detail: string;
}

/**
 * The single most-important reason a host needs attention, plus any others.
 * Priority: critical (folding in exploitability) → exploit-only → data
 * conflict → changed-since-scan → high → stale.  Out-of-scope is surfaced in
 * the Host column next to the subnet, not here.
 */
export const computeAttention = (
  host: Host,
): { primary: AttentionReason | null; others: AttentionReason[] } => {
  const vs = host.vulnerability_summary;
  const crit = vs?.critical ?? 0;
  const high = vs?.high ?? 0;
  const exploit = host.exploitable_count ?? 0;
  // v5.220.0 — "critical · exploit" is only claimed when the backend joined
  // the two on the same vulnerability.  Before, a critical with no exploit
  // plus a low with one produced the same badge.
  const critExploit = host.critical_exploitable_count ?? 0;
  const conflicts = host.conflict_count ?? 0;
  const reasons: AttentionReason[] = [];
  if (crit > 0) {
    reasons.push({
      label: critExploit > 0 ? `${crit} critical · exploit` : `${crit} critical`,
      tone: 'severity-critical',
      detail:
        critExploit > 0
          ? `${crit} critical-severity vulnerability${crit === 1 ? '' : 'ies'}; ${critExploit} of them ${critExploit === 1 ? 'has' : 'have'} a known public exploit.`
          : exploit > 0
            ? `${crit} critical-severity vulnerability${crit === 1 ? '' : 'ies'}. A lower-severity vulnerability on this host has a known public exploit.`
            : `${crit} critical-severity vulnerability${crit === 1 ? '' : 'ies'}.`,
    });
  }
  if (exploit > 0 && critExploit === 0) {
    reasons.push({
      label: 'Exploit available',
      tone: 'destructive',
      detail: `${exploit} vulnerabilit${exploit === 1 ? 'y' : 'ies'} on this host ${exploit === 1 ? 'has' : 'have'} a known public exploit (none of them critical).`,
    });
  }
  if (conflicts > 0) {
    reasons.push({
      label: `${conflicts} conflict${conflicts === 1 ? '' : 's'}`,
      tone: 'warning',
      detail: 'Scans disagreed on this host’s data (e.g. OS or state). Open the host to reconcile.',
    });
  }
  if (host.changed_recently) {
    reasons.push({
      label: 'Changed',
      tone: 'info',
      detail: 'A port opened or closed, or the host’s up/down state changed, at the most recent scan vs the prior one.',
    });
  }
  if (high > 0) {
    reasons.push({ label: `${high} high`, tone: 'severity-high', detail: `${high} high-severity vulnerability${high === 1 ? '' : 'ies'}.` });
  }
  const findings = host.finding_count ?? 0;
  if (findings > 0) {
    reasons.push({
      label: `${findings} finding${findings === 1 ? '' : 's'}`,
      tone: 'info',
      detail: `${findings} promoted finding${findings === 1 ? '' : 's'} recorded on this host — triage has produced results here.`,
    });
  }
  // v5.270.0 — no "Stale" reason: a project is one assessment window, and a
  // scan's age is provenance, never an attention signal (CLAUDE.md /posture).
  return { primary: reasons[0] ?? null, others: reasons.slice(1) };
};

/** The dot before an Attention line — the tone of its reason. */
const ATTENTION_DOT: Record<AttentionReason['tone'], string> = {
  // Same tokens as the severity badges (ui/badge.tsx).
  'severity-critical': 'bg-destructive',
  'severity-high': 'bg-warning',
  destructive: 'bg-destructive',
  warning: 'bg-warning',
  info: 'bg-info',
  muted: 'bg-muted-foreground',
};

/** Where a host's team review stands, as the Review column states it. */
export const reviewStateText = (
  host: Pick<Host, 'follow' | 'other_reviewers' | 'reviewed_by'>,
): { kind: 'none' | 'in_review' | 'reviewed'; label: string } => {
  const status = host.follow?.status ?? null;
  if (status === 'in_review') return { kind: 'in_review', label: 'In review' };
  if (status === 'reviewed') return { kind: 'reviewed', label: 'Reviewed' };
  // Someone else's review counts: the column is the team's state.
  if ((host.other_reviewers?.length ?? 0) > 0) return { kind: 'in_review', label: 'In review' };
  if ((host.reviewed_by?.length ?? 0) > 0) return { kind: 'reviewed', label: 'Reviewed' };
  return { kind: 'none', label: 'Not started' };
};

/**
 * The test-workflow state of a host, as a word beside its IP (v5.270.0 — was
 * a coloured left border on the row, explained only by a hover title).
 * Executed wins over planned.
 */
export const testWorkState = (
  host: Pick<Host, 'test_execution_count' | 'test_plan_entry_count'>,
): { kind: 'tested' | 'planned'; label: string; title: string } | null => {
  const n = host.test_execution_count ?? 0;
  if (n > 0) {
    return { kind: 'tested', label: 'Tested', title: `${n} agentic test result${n === 1 ? '' : 's'} recorded` };
  }
  const p = host.test_plan_entry_count ?? 0;
  if (p > 0) {
    return { kind: 'planned', label: 'Planned', title: `${p} test${p === 1 ? '' : 's'} approved but not yet executed` };
  }
  return null;
};

// --- FollowMenu -----------------------------------------------------------

export interface FollowMenuProps {
  host: Host;
  updating: boolean;
  onChange: (status: FollowStatus | 'none') => void;
}

/**
 * Per-row follow status menu — composes a Badge trigger with a Radix
 * dropdown.  Stops click propagation so toggling status doesn't trigger
 * any parent row click handler.
 *
 * Exported so Hosts.tsx can reuse it from the mobile-card layout that
 * doesn't go through the table-columns pipeline.
 */
export const FollowMenu: React.FC<FollowMenuProps> = ({ host, updating, onChange }) => {
  const status = host.follow?.status ?? null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          onClick={(event) => event.stopPropagation()}
          disabled={updating}
          aria-label={`Change review for ${host.ip_address}`}
          className={cn(
            'inline-flex shrink-0 items-center gap-xxs rounded-control px-xxs text-caption text-info transition-opacity',
            'hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            // Revealed on row hover, on keyboard focus, and while its menu is
            // open (v5.270.0).
            'opacity-0 focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100',
            updating && 'opacity-60',
          )}
          aria-haspopup="menu"
        >
          {status ? <Bookmark className="size-3" aria-hidden /> : <BookmarkPlus className="size-3" aria-hidden />}
          {status ? 'Change' : 'Review'}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
        {FOLLOW_STATUS_OPTIONS.map((option) => (
          <DropdownMenuItem
            key={option.value}
            onSelect={() => onChange(option.value)}
            disabled={updating}
          >
            {option.label}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => onChange('none')}
          disabled={updating || !status}
        >
          Clear review
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

// --- The hook ------------------------------------------------------------

/** A pivotable cell value — clicking it narrows the list to hosts sharing it. */
export type HostFilterPivot =
  | { kind: 'tag'; value: string }
  | { kind: 'service'; value: string }
  | { kind: 'os'; value: string };

export interface UseHostColumnsOptions {
  /** Host id currently mid-flight on a follow toggle (disables that menu). */
  updatingHostId: number | null;
  /** Fires when the user picks a follow status from a row's menu. */
  onFollowChange: (hostId: number, status: FollowStatus | 'none') => void;
  /**
   * Fires when the user activates the primary cell (clicks the IP/hostname
   * button or presses Enter on it).  v2.44.1 (UX review #2): this is the
   * keyboard path for the row-level openInspector action.  The row itself
   * still has an onClick handler (DataTableShell's mouse convenience) but
   * is no longer focusable — keyboard users tab to this button.
   */
  onOpen?: (hostId: number) => void;
  /**
   * Fires when the user clicks a pivotable cell value (tag / service / OS)
   * to narrow the list to hosts sharing it.  When omitted, those values
   * render as plain non-interactive text.
   */
  onAddFilter?: (pivot: HostFilterPivot) => void;
  /**
   * The active port / service / version conditions (`endpointMatchCriteria`).
   * When set, the Exposure cell names the port(s) that made the row match —
   * the risk-ranked chips alone never said why a "service ftp" row was listed.
   */
  endpointMatch?: EndpointMatchCriteria | null;
}

/**
 * A cell value that pivots the list to hosts sharing it.  Degrades to a
 * plain span when no `onPivot` is wired, so callers without a filter setter
 * (or future non-Hosts callers of this hook) render unchanged.  Stops
 * propagation so a pivot click never also opens the row inspector.
 */
const PivotValue: React.FC<{
  onPivot?: () => void;
  title: string;
  className?: string;
  children: React.ReactNode;
}> = ({ onPivot, title, className, children }) => {
  if (!onPivot) return <span className={className}>{children}</span>;
  return (
    <button
      type="button"
      title={title}
      onClick={(e) => {
        e.stopPropagation();
        onPivot();
      }}
      className={cn(
        'rounded-control text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      {children}
    </button>
  );
};

export function useHostColumns({
  updatingHostId,
  onFollowChange,
  onOpen,
  onAddFilter,
  endpointMatch = null,
}: UseHostColumnsOptions): ColumnDef<Host>[] {
  return useMemo<ColumnDef<Host>[]>(
    () => [
      // v4.46.0 — the `__expand` chevron column was removed along with the
      // expandable sub-row.  A row previously carried three competing
      // affordances: this toggle, a decorative (aria-hidden, non-clickable)
      // ChevronRight in the Host cell, and the row's own open-inspector
      // click.  Only the last one survives, and it now announces itself.
      {
        id: 'ip',
        header: 'Host',
        cell: ({ row }) => {
          const host = row.original;
          // v2.44.1 (UX review #2): the IP/hostname is the keyboard-activation
          // target for the row-level "open host inspector" action — a real
          // <button> with a focus ring.  Only IP + hostname live inside it;
          // the metadata below (OS / tags) are pivot <button>s (click to
          // filter), which is why they're SIBLINGS of the opener, not nested
          // (button-in-button is invalid HTML).  The copy-IP control is a
          // sibling too.  All inner controls stopPropagation so they never
          // also fire the row's open-inspector onClick.
          const identity = (
            <div className="min-w-0">
              {/* Underlines whenever the row is hovered (not just this cell),
                  because the whole row opens the inspector.  Without it the
                  identity looked like inert text and operators didn't know
                  it was the way in. */}
              <div className="truncate font-medium text-foreground underline-offset-2 group-hover:underline" title={host.ip_address}>
                {host.ip_address}
              </div>
            </div>
          );
          // An <a> rather than a <button> so cmd/ctrl/middle-click open the
          // standalone /hosts/:id route in a new tab — the muscle memory for a
          // triage list — while a plain click keeps the in-page side sheet.
          // Still the keyboard-activation target for the row.
          const opener = onOpen ? (
            <Link
              to={`/hosts/${host.id}`}
              onClick={(e) => {
                // Let the browser handle modified clicks (new tab/window) and
                // anything that isn't a primary-button click.
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                e.preventDefault();
                e.stopPropagation();
                onOpen(host.id);
              }}
              aria-label={`Open host inspector for ${host.ip_address}${host.hostname ? ` (${host.hostname})` : ''}`}
              className="block min-w-0 rounded-control text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {identity}
            </Link>
          ) : (
            <div className="min-w-0">{identity}</div>
          );
          const work = testWorkState(host);
          return (
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-xs">
                <StateDot state={host.state} />
                {opener}
                {/* v5.270.0 — the test-workflow state is a word, not an
                    unexplained coloured left border on the row. */}
                {work && (
                  <span
                    className={cn(
                      'shrink-0 text-caption font-medium',
                      work.kind === 'tested' ? 'text-info' : 'text-warning',
                    )}
                    title={work.title}
                  >
                    {work.label}
                  </span>
                )}
                {/* Copy / open show on row hover or keyboard focus only — two
                    icons on every row were noise. */}
                <div className="ml-auto flex shrink-0 items-center gap-xxs opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                  <CopyIpButton ip={host.ip_address} />
                  <ChevronRight
                    className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
                    aria-hidden
                  />
                </div>
              </div>
              {/* Hostname and OS share the second line (was three lines). */}
              <div className="flex min-w-0 items-baseline gap-xs pl-sm text-caption">
                {host.hostname ? (
                  <span className="min-w-0 truncate text-foreground/80" title={host.hostname}>
                    {host.hostname}
                  </span>
                ) : (
                  <span className="shrink-0 text-muted-foreground">No hostname</span>
                )}
                {host.os_name && (
                  <PivotValue
                    onPivot={onAddFilter ? () => onAddFilter({ kind: 'os', value: host.os_name! }) : undefined}
                    title={
                      host.os_accuracy != null
                        ? `Filter to hosts running ${host.os_name} (OS confidence ${host.os_accuracy}%)`
                        : `Filter to hosts running ${host.os_name}`
                    }
                    className="min-w-0 max-w-[45%] shrink-0 truncate text-muted-foreground"
                  >
                    {host.os_name}
                  </PivotValue>
                )}
              </div>
              {host.tags && host.tags.length > 0 && (
                <div className="mt-xxs flex flex-wrap gap-xxs pl-sm">
                  {host.tags.slice(0, 3).map((tag) => (
                    <PivotValue
                      key={tag.id}
                      onPivot={onAddFilter ? () => onAddFilter({ kind: 'tag', value: tag.name }) : undefined}
                      title={`Filter to hosts tagged "${tag.name}"`}
                      className="inline-flex max-w-full items-center gap-xxs rounded-chip border border-border bg-muted/40 px-xs py-px text-caption hover:bg-muted/70 hover:no-underline"
                    >
                      <span
                        className={cn('inline-block size-1.5 shrink-0 rounded-full', tagDotClass(tag.color))}
                        aria-hidden
                      />
                      <span className="truncate">{tag.name}</span>
                    </PivotValue>
                  ))}
                  {host.tags.length > 3 && (
                    <span className="text-caption text-muted-foreground">+{host.tags.length - 3}</span>
                  )}
                </div>
              )}
            </div>
          );
        },
      },
      {
        // Where the host lives + how fresh it is.  Its own column (5.249.0):
        // stacked under the identity it made the Host cell the tallest in
        // every row while the cells beside it stood half empty, and subnet /
        // site could not be compared down the page.
        id: 'network',
        header: 'Network',
        // UX review 2026-09-24 — the four sized columns took 790px and the
        // table 1060px, wider than the page at a 1246px window; now 680px,
        // and the table fits a ~900px content column (Host gets the rest).
        size: 140,
        cell: ({ row }) => {
          const host = row.original;
          const lastSeenAge = relativeAge(host.last_seen);
          // v5.270.0 — "seen 1d ago" on every row was provenance repeated
          // down the page; it is on hover now.  NEW (first seen this week)
          // stays visible: that one sets a host apart.
          const seenTitle = lastSeenAge ? `Last seen ${lastSeenAge} ago (${host.last_seen})` : undefined;
          return (
            <div className="flex w-full min-w-0 flex-col gap-xxs text-caption text-muted-foreground" title={seenTitle}>
              {host.primary_subnet ? (
                <span className="truncate font-mono text-foreground" title={host.primary_subnet}>
                  {host.primary_subnet}
                </span>
              ) : (
                <span className="truncate">
                  <ScopeCoverageLabel host={host} />
                </span>
              )}
              {(host.primary_site || isNewHost(host.first_seen)) && (
                <span className="flex min-w-0 items-center gap-xs">
                  {host.primary_site && (
                    <span className="min-w-0 truncate" title={host.primary_site}>{host.primary_site}</span>
                  )}
                  {isNewHost(host.first_seen) && <Badge variant="info" className="shrink-0">New</Badge>}
                </span>
              )}
            </div>
          );
        },
      },
      {
        id: 'exposure',
        header: 'Exposure',
        size: 180,
        cell: ({ row }) => {
          // Open-port count + the host's risk-ranked high-value services
          // (ports of interest), replacing the arbitrary first-3-services
          // list.  Chips are non-interactive (the service filter matches the
          // raw service_name, not these labels) — they're a risk read, not a
          // pivot.
          const host = row.original;
          const openCount = host.ports?.filter((port) => port.state === 'open').length ?? 0;
          const chips = exposureChips(host.ports);
          const matched = matchedEndpoints(host.ports, endpointMatch);
          return (
            <div className="flex w-full min-w-0 flex-col gap-xxs">
              {/* Why this row is in the list when an endpoint condition is
                  applied: the port(s) that satisfy it, ahead of the
                  risk-ranked chips (which may not include it at all). */}
              {matched.length > 0 && (
                <div
                  className="flex min-w-0 flex-wrap items-center gap-xxs"
                  data-testid="endpoint-match"
                  title={`Matches the endpoint condition: ${matched.map((m) => (m.state ? `${m.label} (${m.state})` : m.label)).join(', ')}`}
                >
                  <span className="text-caption text-muted-foreground">Matched</span>
                  {matched.slice(0, 2).map((m) => (
                    <span
                      key={m.key}
                      className="inline-flex max-w-full items-center rounded-chip border border-info/40 bg-info/10 px-xs py-px font-mono text-caption text-info"
                    >
                      <span className="truncate">{m.state ? `${m.label} · ${m.state}` : m.label}</span>
                    </span>
                  ))}
                  {matched.length > 2 && (
                    <span className="text-caption text-muted-foreground">+{matched.length - 2}</span>
                  )}
                </div>
              )}
              {/* "40 open ports", not "40 open / 45": the total counted
                  closed and filtered sightings, which nobody reads here. */}
              <div className="text-caption text-muted-foreground">
                <strong className="text-foreground">{openCount}</strong> open port{openCount === 1 ? '' : 's'}
              </div>
              {chips.length > 0 ? (
                <div className="flex flex-wrap gap-xxs">
                  {chips.slice(0, 3).map((c) => (
                    <span
                      key={c.key}
                      title={
                        c.detected
                          ? `${c.label} — port ${c.port}, identified by service probe`
                          : `${c.label}? — port ${c.port}, guessed from the port number (no service probe)`
                      }
                      className={cn(
                        'inline-flex max-w-full items-center rounded-chip border px-xs py-px text-caption',
                        c.weight > 0
                          ? 'border-warning/40 bg-warning/10 text-warning'
                          : 'border-border bg-muted/40 text-foreground',
                        !c.detected && 'border-dashed',
                      )}
                    >
                      <span className="truncate">{c.detected ? c.label : `${c.label}?`}</span>
                    </span>
                  ))}
                  {chips.length > 3 && (
                    <span className="text-caption text-muted-foreground">+{chips.length - 3}</span>
                  )}
                </div>
              ) : openCount > 0 ? (
                <span className="text-caption text-muted-foreground">services not probed</span>
              ) : null}
            </div>
          );
        },
      },
      {
        id: 'attention',
        header: 'Attention',
        size: 170,
        cell: ({ row }) => {
          // v5.270.0 — one sentence-case line for the most important reason,
          // then the others spelled out in quiet text ("1 high · 1 finding"),
          // not a bright capital pill and an unexplained "+N".
          const { primary, others } = computeAttention(row.original);
          if (!primary) {
            return <span className="text-caption text-muted-foreground">—</span>;
          }
          return (
            <div className="flex w-full min-w-0 flex-col gap-xxs">
              <span className="flex min-w-0 items-center gap-xs text-metadata font-medium text-foreground" title={primary.detail}>
                <span className={cn('size-2 shrink-0 rounded-full', ATTENTION_DOT[primary.tone])} aria-hidden />
                <span className="truncate">{primary.label}</span>
              </span>
              {others.length > 0 && (
                <span
                  className="line-clamp-2 pl-md text-caption text-muted-foreground"
                  title={others.map((o) => `${o.label} — ${o.detail}`).join('\n')}
                >
                  {others.map((o) => o.label).join(' · ')}
                </span>
              )}
            </div>
          );
        },
      },
      {
        id: 'review',
        header: 'Review',
        size: 150,
        cell: ({ row }) => {
          // v5.270.0 — the column states where the review stands, in quiet
          // text; the action to change it appears on row hover or keyboard
          // focus.  A "REVIEW" button on every unreviewed row read like a
          // status and made the whole column shout.
          const host = row.original;
          const noteCount = host.note_count ?? host.notes?.length ?? 0;
          const otherReviewerCount = host.other_reviewers?.length ?? 0;
          const reviewedCount = host.reviewed_by?.length ?? 0;
          const owner = host.assignees?.[0]?.name;
          const state = reviewStateText(host);
          const detail = [
            owner ? `Assigned: ${owner}${(host.assignees?.length ?? 0) > 1 ? ` +${host.assignees!.length - 1}` : ''}` : null,
            otherReviewerCount > 0
              ? `${host.other_reviewers![0].name}${otherReviewerCount > 1 ? ` +${otherReviewerCount - 1}` : ''} reviewing`
              : null,
            reviewedCount > 0 && state.kind !== 'reviewed'
              ? `Reviewed by ${host.reviewed_by![0].name}${reviewedCount > 1 ? ` +${reviewedCount - 1}` : ''}`
              : null,
            // A count is shown when it says something; "0 notes" was noise.
            noteCount > 0 ? `${noteCount} note${noteCount === 1 ? '' : 's'}` : null,
          ].filter(Boolean).join(' · ');
          const titles = [
            host.assignees?.length ? `Assigned: ${host.assignees.map((a) => a.name).join(', ')}` : null,
            otherReviewerCount > 0 ? `Reviewing: ${host.other_reviewers!.map((r) => r.name).join(', ')}` : null,
            reviewedCount > 0 ? `Reviewed by: ${host.reviewed_by!.map((r) => r.name).join(', ')}` : null,
          ].filter(Boolean).join('\n');
          return (
            <div className="flex w-full min-w-0 flex-col gap-xxs">
              <div className="flex min-w-0 items-center gap-xs">
                <span
                  data-review-state={state.kind}
                  className={cn(
                    'min-w-0 truncate text-metadata',
                    state.kind === 'in_review' && 'font-medium text-warning',
                    state.kind === 'reviewed' && 'font-medium text-success',
                    state.kind === 'none' && 'text-muted-foreground',
                  )}
                >
                  {state.kind === 'reviewed' && <Check className="mr-xxs inline size-3" aria-hidden />}
                  {state.label}
                </span>
                <FollowMenu
                  host={host}
                  updating={updatingHostId === host.id}
                  onChange={(status) => onFollowChange(host.id, status)}
                />
              </div>
              {detail && (
                <span className="line-clamp-2 text-caption text-muted-foreground" title={titles || undefined}>
                  {detail}
                </span>
              )}
            </div>
          );
        },
      },
    ],
    // v4.7.5 — onOpen was missing from this dep list, so the IP-cell
    // button closed over a stale openInspector callback after the
    // parent's filters/followFilter/onlyWithNotes changed.  Result:
    // keyboard users tabbing into the cell and pressing Enter would
    // return to the inspector with stale list context that didn't
    // match the current filter set, while mouse row-click (which
    // uses the live callback) worked correctly.
    [updatingHostId, onFollowChange, onOpen, onAddFilter, endpointMatch],
  );
}
