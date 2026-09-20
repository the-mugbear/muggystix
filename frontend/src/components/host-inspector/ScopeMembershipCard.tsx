/**
 * Which scope entries cover this host — one line in the inspector's header
 * (v5.240.0; it was a card of its own, ~95px to say "no scope defined"). The
 * status and the closest subnet are always visible; the full entry list opens
 * on demand. Two things are never behind the click: "Out of scope", and the
 * sentence limiting what an in-scope NAME authorises.
 *
 * The Hosts list shows only the most-specific subnet; an operator opening a
 * host wants the full answer: every subnet entry the address falls in, every
 * in-scope name that currently resolves here, and — when nothing does — a
 * clear statement rather than an empty space.
 *
 * Three coverage states, mirroring the backend's scope_coverage:
 *
 *  - subnet: the address is inside at least one scope subnet.
 *  - name:   no subnet contains it, but an in-scope name resolves to it.
 *            Reachable, not subnet-scoped — approving a name never approves
 *            the address's other names or services, and the card says so.
 *  - none:   nothing covers it. Distinguishes "out of scope" (the project
 *            has a scope and this host is outside it — worth acting on) from
 *            "this project has declared no scope yet" (nothing to check).
 */
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Crosshair } from 'lucide-react';

import type { HostScopeMembership } from '../../services/api';
import { Badge } from '../ui/badge';

export interface ScopeMembershipCardProps {
  membership?: HostScopeMembership | null;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export const ScopeMembershipCard: React.FC<ScopeMembershipCardProps> = ({ membership }) => {
  const [open, setOpen] = useState(false);
  // The detail endpoint always sends the block; its absence means an older
  // backend, and a card claiming "out of scope" on no evidence would mislead.
  if (!membership) return null;

  const { coverage, project_has_scope: hasScope, subnets, names } = membership;
  const hasDetail = subnets.length > 0 || names.length > 0;
  // The entry an analyst means by "its subnet": the longest prefix.
  const prefix = (cidr: string) => Number(cidr.split('/')[1] ?? 0);
  const closest = [...subnets].sort((a, b) => prefix(b.cidr) - prefix(a.cidr))[0];

  let status: React.ReactNode;
  if (coverage === 'subnet') {
    status = (
      <>
        <Badge variant="success-outline">In scope</Badge>
        <span className="text-caption text-muted-foreground">
          {plural(subnets.length, 'subnet entry', 'subnet entries')}
          {names.length > 0 ? ` · ${plural(names.length, 'in-scope name', 'in-scope names')}` : ''}
        </span>
      </>
    );
  } else if (coverage === 'name') {
    status = (
      <>
        <Badge variant="info-outline">Reachable via in-scope name</Badge>
        <span className="text-caption text-muted-foreground">no subnet entry contains this address</span>
      </>
    );
  } else if (hasScope) {
    status = (
      <>
        <Badge variant="warning-outline">Out of scope</Badge>
        <span className="text-caption text-muted-foreground">no scope entry covers this address or its names</span>
      </>
    );
  } else {
    status = (
      <>
        <Badge variant="muted">No scope defined</Badge>
        <span className="text-caption text-muted-foreground">
          this project has no subnet or domain entries yet ·{' '}
          <Link to="/scopes" className="underline-offset-2 hover:underline">define scope</Link>
        </span>
      </>
    );
  }

  return (
    <div id="host-detail-scope" className="space-y-xs">
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        <Crosshair className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        {status}
        {closest && !open && (
          <Link
            to={`/scopes/${closest.scope_id}`}
            className="font-mono text-caption text-foreground underline-offset-2 hover:underline"
            title="The most specific scope entry containing this address — open its scope"
          >
            {closest.cidr}
          </Link>
        )}
        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls="host-detail-scope-entries"
            className="rounded text-caption text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {open ? 'hide entries' : 'show entries'}
          </button>
        )}
      </div>

      {/* Never behind a click: this is the limit of what may be tested. */}
      {coverage === 'name' && (
        <p className="text-caption text-muted-foreground">
          An approved name resolving here does not put the address&apos;s other names or
          services in scope. Test what the name serves, not the host.
        </p>
      )}

      {open && hasDetail && (
      <div id="host-detail-scope-entries" className="space-y-sm rounded-control border border-border p-sm">
        {subnets.length > 0 && (
          <ul className="space-y-xxs" aria-label="Scope subnet entries">
            {subnets.map((s) => (
              <li key={s.id} className="flex min-w-0 flex-wrap items-center gap-xs">
                <Link
                  to={`/scopes/${s.scope_id}`}
                  className="font-mono text-metadata text-foreground underline-offset-2 hover:underline"
                  title="Open this scope"
                >
                  {s.cidr}
                </Link>
                {s.description && (
                  <span className="min-w-0 max-w-[24rem] truncate text-caption text-muted-foreground" title={s.description}>
                    {s.description}
                  </span>
                )}
                {s.site && (
                  <span className="max-w-[12rem] truncate rounded-chip border border-border px-xs text-caption text-muted-foreground" title={s.site}>
                    {s.site}
                  </span>
                )}
                {s.labels.map((l) => (
                  <span
                    key={l.id}
                    className="inline-flex max-w-[10rem] items-center gap-xxs truncate rounded-chip border border-border px-xs text-caption text-foreground"
                    title={l.name}
                  >
                    {l.color && (
                      <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: l.color }} aria-hidden />
                    )}
                    <span className="truncate">{l.name}</span>
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}

        {names.length > 0 && (
          <ul
            className={`space-y-xxs ${subnets.length > 0 ? 'border-t border-border pt-sm' : ''}`}
            aria-label="In-scope names resolving here"
          >
            {names.map((n) => (
              <li key={`${n.fqdn}|${n.domain}`} className="flex min-w-0 flex-wrap items-center gap-xs">
                <span className="min-w-0 break-all font-mono text-metadata text-foreground">{n.fqdn}</span>
                <span className="min-w-0 break-all text-caption text-muted-foreground">
                  via {n.domain}
                  {n.include_subdomains ? ' (includes subdomains)' : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      )}
    </div>
  );
};

export default ScopeMembershipCard;
