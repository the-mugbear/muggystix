import React, { useMemo } from 'react';
import { ColumnDef } from '@tanstack/react-table';
import {
  Bookmark,
  BookmarkPlus,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Copy,
  Users,
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
import {
  PORTS_OF_INTEREST_BY_PORT,
  type PortOfInterestDefinition,
} from '../../utils/portsOfInterest';

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

export const formatRelativeLastViewed = (value?: string | null): string | null => {
  if (!value) return null;
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
};

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

/** A host whose latest observation is > 30 days old reads as "stale". */
const isStaleHost = (iso?: string | null): boolean => {
  if (!iso) return false;
  const diff = Date.now() - new Date(iso).getTime();
  return !Number.isNaN(diff) && diff > 30 * 86_400_000;
};

/** This host's OPEN ports-of-interest, de-duplicated and risk-ranked. */
export const exposurePortsOfInterest = (ports?: Port[]): PortOfInterestDefinition[] => {
  const seen = new Set<number>();
  const out: PortOfInterestDefinition[] = [];
  for (const p of ports ?? []) {
    if (p.state !== 'open') continue;
    const def = PORTS_OF_INTEREST_BY_PORT.get(p.port_number);
    if (def && !seen.has(def.port)) {
      seen.add(def.port);
      out.push(def);
    }
  }
  return out.sort((a, b) => b.weight - a.weight);
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
  const conflicts = host.conflict_count ?? 0;
  const reasons: AttentionReason[] = [];
  if (crit > 0) {
    reasons.push({
      label: exploit > 0 ? `${crit} critical · exploit` : `${crit} critical`,
      tone: 'severity-critical',
      detail:
        exploit > 0
          ? `${crit} critical-severity vulnerability${crit === 1 ? '' : 'ies'}, at least one with a known public exploit.`
          : `${crit} critical-severity vulnerability${crit === 1 ? '' : 'ies'}.`,
    });
  } else if (exploit > 0) {
    reasons.push({
      label: 'Exploit available',
      tone: 'destructive',
      detail: 'A vulnerability on this host has a known public exploit.',
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
  if (isStaleHost(host.last_seen)) {
    reasons.push({ label: 'Stale', tone: 'muted', detail: 'Not seen in a scan for over 30 days — data may be out of date.' });
  }
  return { primary: reasons[0] ?? null, others: reasons.slice(1) };
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
  const followOption = status
    ? FOLLOW_STATUS_OPTIONS.find((option) => option.value === status) ?? null
    : null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          onClick={(event) => event.stopPropagation()}
          disabled={updating}
          className={cn(
            'inline-flex items-center gap-xxs rounded-chip border px-xs py-px text-micro font-semibold uppercase tracking-wider transition-colors',
            'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
            followOption
              ? followOption.badgeClass + ' border-transparent'
              : 'border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground',
            updating && 'opacity-60',
          )}
          aria-haspopup="menu"
        >
          {followOption ? (
            <Bookmark className="size-3" aria-hidden />
          ) : (
            <BookmarkPlus className="size-3" aria-hidden />
          )}
          {followOption?.label ?? 'Review'}
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
}: UseHostColumnsOptions): ColumnDef<Host>[] {
  return useMemo<ColumnDef<Host>[]>(
    () => [
      {
        id: '__expand',
        size: 36,
        header: () => <span className="sr-only">Expand</span>,
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="icon"
            aria-label={row.getIsExpanded() ? 'Collapse host details' : 'Expand host details'}
            aria-expanded={row.getIsExpanded()}
            onClick={(event) => {
              event.stopPropagation();
              row.toggleExpanded();
            }}
          >
            {row.getIsExpanded() ? (
              <ChevronUp className="size-4" aria-hidden />
            ) : (
              <ChevronDown className="size-4" aria-hidden />
            )}
          </Button>
        ),
        enableSorting: false,
      },
      {
        id: 'ip',
        header: 'Host',
        cell: ({ row }) => {
          const host = row.original;
          const lastSeenAge = relativeAge(host.last_seen);
          const newHost = isNewHost(host.first_seen);
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
              <div className="break-words font-medium text-foreground">{host.ip_address}</div>
              {host.hostname ? (
                <div className="line-clamp-2 text-caption text-foreground/80">
                  {host.hostname}
                </div>
              ) : (
                <div className="text-caption text-muted-foreground">No hostname</div>
              )}
            </div>
          );
          const opener = onOpen ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onOpen(host.id);
              }}
              aria-label={`Open host inspector for ${host.ip_address}${host.hostname ? ` (${host.hostname})` : ''}`}
              className="block min-w-0 flex-1 rounded-control text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {identity}
            </button>
          ) : (
            <div className="min-w-0 flex-1">{identity}</div>
          );
          return (
            <div className="min-w-0">
              <div className="flex min-w-0 items-start gap-xs">
                {opener}
                <div className="flex shrink-0 items-center gap-xxs pt-px">
                  <CopyIpButton ip={host.ip_address} />
                  <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
                </div>
              </div>
              {host.os_name && (
                <PivotValue
                  onPivot={onAddFilter ? () => onAddFilter({ kind: 'os', value: host.os_name! }) : undefined}
                  title={
                    host.os_accuracy != null
                      ? `Filter to hosts running ${host.os_name} (OS confidence ${host.os_accuracy}%)`
                      : `Filter to hosts running ${host.os_name}`
                  }
                  className="mt-xxs block max-w-full truncate text-caption text-muted-foreground"
                >
                  {host.os_name}
                </PivotValue>
              )}
              {/* Where the host lives + how fresh it is.  Subnet · site, then
                  a relative last-seen age, then a "New" badge for hosts first
                  discovered in the last week. */}
              <div className="mt-xxs flex flex-wrap items-center gap-x-sm gap-y-xxs text-caption text-muted-foreground">
                {host.primary_subnet ? (
                  <span className="truncate font-mono" title={host.primary_subnet}>
                    {host.primary_subnet}
                  </span>
                ) : (
                  <span className="italic" title="Not mapped to any configured scope">out of scope</span>
                )}
                {host.primary_site && (
                  <span className="truncate" title={host.primary_site}>· {host.primary_site}</span>
                )}
                {lastSeenAge && <span title={host.last_seen ?? undefined}>seen {lastSeenAge} ago</span>}
                {newHost && <Badge variant="info">New</Badge>}
              </div>
              {host.tags && host.tags.length > 0 && (
                <div className="mt-xxs flex flex-wrap gap-xxs">
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
        id: 'exposure',
        header: 'Exposure',
        size: 210,
        cell: ({ row }) => {
          // Open-port count + the host's risk-ranked high-value services
          // (ports of interest), replacing the arbitrary first-3-services
          // list.  Chips are non-interactive (the service filter matches the
          // raw service_name, not these labels) — they're a risk read, not a
          // pivot.
          const host = row.original;
          const openCount = host.ports?.filter((port) => port.state === 'open').length ?? 0;
          const poi = exposurePortsOfInterest(host.ports);
          return (
            <div className="flex w-full min-w-0 flex-col gap-xxs">
              <div className="text-caption text-muted-foreground">
                <strong className="text-foreground">{openCount}</strong> open
                {host.ports ? ` / ${host.ports.length}` : ''}
              </div>
              {poi.length > 0 ? (
                <div className="flex flex-wrap gap-xxs">
                  {poi.slice(0, 3).map((d) => (
                    <span
                      key={d.port}
                      title={`${d.label} — port ${d.port} (high-value service)`}
                      className="inline-flex items-center rounded-chip border border-warning/40 bg-warning/10 px-xs py-px text-caption text-warning"
                    >
                      {d.label}
                    </span>
                  ))}
                  {poi.length > 3 && (
                    <span className="text-caption text-muted-foreground">+{poi.length - 3}</span>
                  )}
                </div>
              ) : openCount > 0 ? (
                <span className="text-caption text-muted-foreground">no high-value services</span>
              ) : (
                <span className="text-caption text-muted-foreground">no open ports</span>
              )}
            </div>
          );
        },
      },
      {
        id: 'attention',
        header: 'Attention',
        size: 180,
        cell: ({ row }) => {
          // The single most-important reason this host needs a human, with a
          // "+N" for any others (full list in the tooltip).  Replaces the
          // badge pile: one prioritized signal answers "why this host?".
          const { primary, others } = computeAttention(row.original);
          if (!primary) {
            return <span className="text-caption text-muted-foreground">—</span>;
          }
          return (
            <div className="flex w-full min-w-0 flex-wrap items-center gap-xxs">
              <Badge variant={primary.tone as never} className="max-w-full overflow-hidden" title={primary.detail}>
                <span className="truncate">{primary.label}</span>
              </Badge>
              {others.length > 0 && (
                <span
                  className="text-caption text-muted-foreground"
                  title={others.map((o) => `${o.label} — ${o.detail}`).join('\n')}
                >
                  +{others.length}
                </span>
              )}
            </div>
          );
        },
      },
      {
        id: 'review',
        header: 'Review',
        size: 190,
        cell: ({ row }) => {
          // Team review state + owner + a compact action, with notes as plain
          // muted text.  Plan / web / execution counts and the conflict badge
          // moved out of this column (to the inspector / the Attention column)
          // so it stops being an overloaded badge pile.
          const host = row.original;
          const noteCount = host.note_count ?? host.notes?.length ?? 0;
          const otherReviewerCount = host.other_reviewers?.length ?? 0;
          const reviewedCount = host.reviewed_by?.length ?? 0;
          const owner = host.assignees?.[0]?.name;
          return (
            <div className="flex w-full min-w-0 flex-col items-start gap-xxs">
              <FollowMenu
                host={host}
                updating={updatingHostId === host.id}
                onChange={(status) => onFollowChange(host.id, status)}
              />
              {otherReviewerCount > 0 && (
                <Badge
                  variant="warning"
                  className="max-w-full overflow-hidden"
                  title={`Reviewing: ${host.other_reviewers!.map((r) => r.name).join(', ')}`}
                >
                  <Users className="size-3 shrink-0" aria-hidden />
                  <span className="truncate">
                    {host.other_reviewers![0].name}
                    {otherReviewerCount > 1 && ` +${otherReviewerCount - 1}`} reviewing
                  </span>
                </Badge>
              )}
              {reviewedCount > 0 && (
                <Badge
                  variant="success"
                  className="max-w-full overflow-hidden"
                  title={`Reviewed by: ${host.reviewed_by!.map((r) => r.name).join(', ')}`}
                >
                  <Check className="size-3 shrink-0" aria-hidden />
                  <span className="truncate">
                    Reviewed · {host.reviewed_by![0].name}
                    {reviewedCount > 1 && ` +${reviewedCount - 1}`}
                  </span>
                </Badge>
              )}
              {owner && (
                <span
                  className="line-clamp-1 max-w-full text-caption text-muted-foreground"
                  title={`Assigned: ${host.assignees!.map((a) => a.name).join(', ')}`}
                >
                  Owner: <span className="text-foreground">{owner}</span>
                  {(host.assignees?.length ?? 0) > 1 ? ` +${host.assignees!.length - 1}` : ''}
                </span>
              )}
              <span className="text-caption text-muted-foreground">
                {noteCount} note{noteCount === 1 ? '' : 's'}
              </span>
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
    [updatingHostId, onFollowChange, onOpen, onAddFilter],
  );
}
