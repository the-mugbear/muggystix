import React, { useEffect, useState } from 'react';
import { Globe, Loader2, Plus, Trash2 } from 'lucide-react';

import {
  addScopeDomains,
  deleteScopeDomain,
  listScopeDomains,
  ScopeDomainRow,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Checkbox } from './ui/checkbox';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { InfoTip } from './ui/info-tip';

/**
 * ScopeDomainsCard — the domains declared in scope, alongside the subnet
 * table (v5.193.0).
 *
 * Exact-name membership and "include subdomains" are separate on purpose:
 * approving portal.example.com does not approve dev.portal.example.com.  Name
 * scope never confers subnet scope on the addresses names resolve to — a host
 * reached only through an in-scope name is reported as "reachable via
 * in-scope name", not as in scope.  `name_count` is the operator's check that
 * an entry actually covers something in the names inventory.
 */

interface ScopeDomainsCardProps {
  scopeId: number;
  /** Bump to force a reload — the scope-file upload can add domain rows
   *  from outside this card. */
  refreshKey?: number;
  /** Called after any change so the parent can refresh coverage numbers. */
  onChanged?: () => void;
}

// Plain-English help for the name-scope presentation (5.198.0).  Name scope
// is the newest and least self-evident part of the page — three coverage
// states, exact-vs-subdomain membership, and a count that is derived from the
// names inventory rather than from what the operator typed — so each is
// explained on an explicit (i).
const TIPS = {
  domains:
    'A name is in scope when an entry here covers it. This is independent of subnet scope: ' +
    'an in-scope name does not make the address it resolves to in scope, and an in-scope subnet ' +
    'does not make names in scope. A host reached only through an in-scope name is shown in ' +
    'Scope Coverage as "via in-scope name" — neither in nor out of subnet scope.',
  match:
    'Exact: only this one name. Name + subdomains: this name and every name under it ' +
    '(portal.example.com does not cover dev.portal.example.com unless subdomains are included). ' +
    'Re-adding an entry can widen it to include subdomains but never narrows it.',
  includeSub:
    'Also cover every subdomain of each name entered. Typing *.example.com does the same for ' +
    'that entry regardless of this box.',
  namesCovered:
    'How many names in this project\'s inventory the entry currently covers — not how many you ' +
    'uploaded. An exact entry covers at most one; a subdomains entry counts every descendant. ' +
    '0 means nothing imported or observed yet matches; names arrive from name imports, dnsx / ' +
    'httpx / amass uploads and certificates. Counts are per entry and overlap when entries nest.',
} as const;

const ScopeDomainsCard: React.FC<ScopeDomainsCardProps> = ({ scopeId, refreshKey = 0, onChanged }) => {
  const toast = useToast();
  const [confirmDialog, confirm] = useConfirm();
  const [rows, setRows] = useState<ScopeDomainRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [domainInput, setDomainInput] = useState('');
  const [includeSub, setIncludeSub] = useState(false);
  const [adding, setAdding] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const PAGE = 100;

  // Server-paged: a bulk import with declare-scope can create thousands of
  // entries, so the list loads a page at a time with a load-more affordance.
  const load = async () => {
    try {
      setError(null);
      const page = await listScopeDomains(scopeId, { skip: 0, limit: PAGE });
      setRows(page.items);
      setTotal(page.total);
    } catch (err: unknown) {
      setError(formatApiError(err, 'Failed to load scope domains.'));
    }
  };

  const loadMore = async () => {
    if (!rows) return;
    setLoadingMore(true);
    try {
      const page = await listScopeDomains(scopeId, { skip: rows.length, limit: PAGE });
      setRows((prev) => [...(prev ?? []), ...page.items]);
      setTotal(page.total);
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to load more domains.'));
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeId, refreshKey]);

  const handleAdd = async () => {
    // One per line or comma/whitespace-separated; "*.example.com" is accepted.
    const entries = domainInput
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (entries.length === 0) return;
    setAdding(true);
    try {
      const res = await addScopeDomains(
        scopeId,
        entries.map((domain) => ({ domain, include_subdomains: includeSub })),
      );
      setRows(res.domains);
      setTotal(res.total);
      setDomainInput('');
      const parts: string[] = [];
      if (res.added) parts.push(`${res.added} added`);
      if (res.updated) parts.push(`${res.updated} widened to include subdomains`);
      if (res.invalid.length) parts.push(`${res.invalid.length} rejected`);
      if (res.invalid.length) {
        toast.warning(`${parts.join(', ')}. Rejected: ${res.invalid.slice(0, 3).join('; ')}`);
      } else {
        toast.success(parts.length ? parts.join(', ') : 'Already in scope');
      }
      onChanged?.();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to add domains.'));
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (row: ScopeDomainRow) => {
    const ok = await confirm({
      title: 'Remove domain from scope',
      body: `${row.domain}${row.include_subdomains ? ' and all subdomains' : ''} will no longer be in scope. Names and hosts are not deleted.`,
      severity: 'warning',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    setDeletingId(row.id);
    try {
      await deleteScopeDomain(scopeId, row.id);
      setRows((prev) => (prev ? prev.filter((r) => r.id !== row.id) : prev));
      setTotal((t) => Math.max(0, t - 1));
      toast.success(`Removed ${row.domain} from scope`);
      onChanged?.();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to remove domain.'));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <Card className="mb-md">
      {confirmDialog}
      <CardContent className="p-0">
        <div className="flex flex-wrap items-center gap-xs border-b border-border p-sm">
          <Globe className="size-4 text-primary" aria-hidden />
          <span className="font-medium">Domains in scope</span>
          <InfoTip text={TIPS.domains} label="About domain scope" />
          {rows && <Badge variant="outline">{total.toLocaleString()}</Badge>}
          <span className="min-w-0 flex-1 truncate text-metadata text-muted-foreground">
            Names covered here are in scope; the addresses they resolve to are not made subnet-in-scope.
          </span>
        </div>

        <div className="flex flex-col gap-xs border-b border-border bg-accent/30 p-sm sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <Label htmlFor="new-scope-domain">Domain (one or more; *.example.com allowed)</Label>
            <Input
              id="new-scope-domain"
              value={domainInput}
              onChange={(e) => setDomainInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !adding) handleAdd();
              }}
              placeholder="portal.example.com, *.lab.example.com"
              className="font-mono"
            />
          </div>
          <span className="flex items-center gap-xs text-metadata">
            <label className="flex items-center gap-xs">
              <Checkbox checked={includeSub} onCheckedChange={(v) => setIncludeSub(v === true)} />
              Include subdomains
            </label>
            <InfoTip text={TIPS.includeSub} label="About include subdomains" />
          </span>
          <Button size="sm" onClick={handleAdd} disabled={adding || !domainInput.trim()}>
            {adding ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Plus className="size-4" aria-hidden />}
            Add
          </Button>
        </div>

        {error && (
          <Alert variant="destructive" className="m-sm">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {rows === null ? (
          <div className="p-md text-center text-metadata text-muted-foreground">Loading domains…</div>
        ) : rows.length === 0 ? (
          <div className="p-md text-center text-metadata text-muted-foreground">
            No domains declared. Subnet scope is unaffected; imported names stay out of scope until a domain covers them.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table style={{ tableLayout: 'fixed' }} className="min-w-[560px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[45%]">Domain</TableHead>
                  <TableHead className="w-[20%]">
                    <span className="inline-flex items-center gap-xxs">
                      Match
                      <InfoTip text={TIPS.match} label="About match" />
                    </span>
                  </TableHead>
                  <TableHead className="w-[20%] text-right">
                    <span className="inline-flex items-center gap-xxs">
                      Names covered
                      <InfoTip text={TIPS.namesCovered} label="About names covered" />
                    </span>
                  </TableHead>
                  <TableHead className="w-[15%]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="truncate font-mono" title={row.domain}>
                      {row.include_subdomains ? `*.${row.domain}` : row.domain}
                    </TableCell>
                    <TableCell>
                      <Badge variant={row.include_subdomains ? 'info-outline' : 'outline'}>
                        {row.include_subdomains ? 'name + subdomains' : 'exact name'}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.name_count.toLocaleString()}</TableCell>
                    <TableCell className="text-right">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Remove ${row.domain} from scope`}
                            disabled={deletingId === row.id}
                            onClick={() => handleDelete(row)}
                          >
                            <Trash2 className="size-4" aria-hidden />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Remove from scope</TooltipContent>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {rows.length < total && (
              <div className="flex items-center justify-between border-t border-border p-sm text-metadata text-muted-foreground">
                <span>
                  Showing {rows.length.toLocaleString()} of {total.toLocaleString()}
                </span>
                <Button size="sm" variant="outline" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
                  Load more
                </Button>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default ScopeDomainsCard;
