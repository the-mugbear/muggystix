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
  Eye,
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

export const FOLLOW_STATUS_OPTIONS: Array<{
  value: FollowStatus;
  label: string;
  badgeClass: string;
}> = [
  { value: 'watching', label: 'Watching', badgeClass: 'bg-info text-info-foreground' },
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
          {followOption?.label ?? 'Follow'}
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
          Stop following
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

// --- The hook ------------------------------------------------------------

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
}

export function useHostColumns({
  updatingHostId,
  onFollowChange,
  onOpen,
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
        header: 'IP / Hostname',
        cell: ({ row }) => {
          const host = row.original;
          const latestDiscovery = getLatestDiscovery(host.discoveries);
          const info = (
            <div className="min-w-0">
              <div className="break-words font-medium text-foreground">{host.ip_address}</div>
              {host.hostname ? (
                <div className="line-clamp-2 text-caption text-foreground/80">
                  {host.hostname}
                </div>
              ) : (
                <div className="text-caption text-muted-foreground">No hostname</div>
              )}
              {host.os_name && (
                <div className="line-clamp-1 text-caption text-muted-foreground">
                  {host.os_name}
                </div>
              )}
              {latestDiscovery && (
                <div className="line-clamp-1 text-caption text-muted-foreground">
                  Last seen in {getScanLabel(latestDiscovery)}
                </div>
              )}
              {host.tags && host.tags.length > 0 && (
                <div className="mt-xxs flex flex-wrap gap-xxs">
                  {host.tags.slice(0, 4).map((tag) => (
                    <span
                      key={tag.id}
                      className="inline-flex max-w-full items-center gap-xxs rounded-chip border border-border bg-muted/40 px-xs py-px text-caption"
                      title={tag.name}
                    >
                      <span
                        className={cn('inline-block size-1.5 shrink-0 rounded-full', tagDotClass(tag.color))}
                        aria-hidden
                      />
                      <span className="truncate">{tag.name}</span>
                    </span>
                  ))}
                  {host.tags.length > 4 && (
                    <span className="text-caption text-muted-foreground">+{host.tags.length - 4}</span>
                  )}
                </div>
              )}
              {host.assignees && host.assignees.length > 0 && (
                <div className="line-clamp-1 text-caption text-muted-foreground">
                  Assigned: {host.assignees.map((a) => a.name).join(', ')}
                </div>
              )}
            </div>
          );
          // v2.44.1 (UX review #2): the IP cell is the keyboard-activation
          // target for the row-level "open host inspector" action — a real
          // <button> with a focus ring.  The copy-IP control is a SIBLING of
          // that button (nesting would be invalid button-in-button HTML) and
          // stops propagation so it never opens the inspector.  If onOpen
          // wasn't passed, the text falls back to a non-interactive div.
          const opener = onOpen ? (
            <button
              type="button"
              onClick={(e) => {
                // The row's onClick will also fire; stop propagation so
                // openInspector isn't called twice.
                e.stopPropagation();
                onOpen(host.id);
              }}
              aria-label={`Open host inspector for ${host.ip_address}${host.hostname ? ` (${host.hostname})` : ''}`}
              className="block min-w-0 flex-1 rounded-control text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {info}
            </button>
          ) : (
            <div className="min-w-0 flex-1">{info}</div>
          );
          return (
            <div className="flex min-w-0 items-start gap-xs">
              {opener}
              <div className="flex shrink-0 items-center gap-xxs pt-px">
                <CopyIpButton ip={host.ip_address} />
                <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
              </div>
            </div>
          );
        },
      },
      {
        id: 'open_ports',
        header: () => <span className="block text-right">Open Ports</span>,
        size: 110,
        cell: ({ row }) => {
          const host = row.original;
          const openPorts = host.ports?.filter((port) => port.state === 'open') || [];
          return (
            <div className="text-right">
              <div className="font-semibold text-foreground">{openPorts.length}</div>
              <div className="text-caption text-muted-foreground">
                of {host.ports?.length || 0}
              </div>
            </div>
          );
        },
      },
      {
        id: 'services',
        header: 'Top Services',
        cell: ({ row }) => {
          // Service names are identifiers, not state — chips here added
          // visual weight without aiding scanning.  Comma-joined text
          // with a two-line clamp keeps the same content at a fraction
          // of the row's chromatic load.
          const services = getTopServices(row.original.ports || []);
          if (services.length === 0) {
            return <span className="text-caption text-muted-foreground">No named services</span>;
          }
          return (
            <span className="line-clamp-2 text-caption text-foreground break-words">
              {services.join(', ')}
            </span>
          );
        },
      },
      {
        id: 'findings',
        size: 140,
        header: () => <span className="block text-right">Findings</span>,
        cell: ({ row }) => {
          // Reserve the chip for the genuine alert (critical > 0) —
          // showing "0 critical" as a chip on every row was chip-noise
          // diluting the rows where there's actually something to flag.
          const summary = row.original.vulnerability_summary;
          const critical = summary?.critical ?? 0;
          const high = summary?.high ?? 0;
          const total = summary?.total_vulnerabilities ?? 0;
          return (
            <div className="flex flex-col items-end gap-xxs">
              {critical > 0 && (
                <Badge variant="severity-critical">{critical} critical</Badge>
              )}
              <span className="text-caption text-muted-foreground">
                {critical === 0 && <>0 critical · </>}
                {high} high · {total} total
              </span>
            </div>
          );
        },
      },
      {
        id: 'notes',
        size: 200,
        header: 'Notes / Follow',
        cell: ({ row }) => {
          const host = row.original;
          const noteCount = host.note_count ?? host.notes?.length ?? 0;
          const relativeViewed = formatRelativeLastViewed(host.follow?.last_viewed_at);
          const otherReviewerCount = host.other_reviewers?.length ?? 0;
          return (
            // v4.25.1 — `w-full min-w-0` lets the column's fixed 200px
            // width propagate to children so the In-review badge can
            // truncate.  Without `min-w-0` the flex item refuses to
            // shrink below its intrinsic content width, and a long
            // reviewer name overflows the cell into the next column.
            <div className="flex w-full min-w-0 flex-col items-start gap-xxs">
              <FollowMenu
                host={host}
                updating={updatingHostId === host.id}
                onChange={(status) => onFollowChange(host.id, status)}
              />
              {/* v4.25.0 — review indicator at table-row level.  Mobile
                  card view (Hosts.tsx) already renders this; the table
                  column was missing it, so on the desktop layout the
                  operator had no signal that a host was already under
                  review (by a teammate or themselves) until they opened
                  the inspector.  Ordered second so it
                  sits directly under the FollowMenu — high-salience
                  state belongs next to the action that would change
                  ownership.

                  v4.25.1 — `max-w-full overflow-hidden` clamps the
                  badge to the cell width (Badge.tsx documents this
                  as the call-site opt-in for truncation); `shrink-0`
                  on the icon keeps it intact while the name span
                  truncates.  The `title` attribute carries the full
                  reviewer list for hover discoverability. */}
              {otherReviewerCount > 0 && (
                <Badge
                  variant="warning"
                  className="max-w-full overflow-hidden"
                  title={`In review by ${host.other_reviewers!.map((r) => r.name).join(', ')}`}
                >
                  <Users className="size-3 shrink-0" aria-hidden />
                  <span className="truncate">
                    In review · {host.other_reviewers![0].name}
                    {otherReviewerCount > 1 && ` +${otherReviewerCount - 1}`}
                  </span>
                </Badge>
              )}
              {relativeViewed && (
                <Badge variant="success">
                  <Eye className="size-3" aria-hidden />
                  Viewed
                </Badge>
              )}
              <span className="line-clamp-2 text-caption text-muted-foreground">
                {noteCount} note{noteCount === 1 ? '' : 's'}
                {relativeViewed ? ` • Viewed ${relativeViewed}` : ''}
              </span>
              {(host.test_plan_entry_count ?? 0) > 0 && (
                <Badge variant="outline" className="border-primary/40 text-primary">
                  {host.test_plan_entry_count} plan entr
                  {host.test_plan_entry_count === 1 ? 'y' : 'ies'}
                </Badge>
              )}
              {(host.web_interface_count ?? 0) > 0 && (
                <Badge variant="outline" className="border-info/40 text-info">
                  {host.web_interface_count} web
                </Badge>
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
    [updatingHostId, onFollowChange, onOpen],
  );
}
