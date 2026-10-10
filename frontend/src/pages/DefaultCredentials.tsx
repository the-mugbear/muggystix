import React, { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { useUrlPage } from '../hooks/useUrlPage';
import { useUrlSearchDraft } from '../hooks/useUrlSearchDraft';
import { copyToClipboard as copyText } from '../utils/clipboard';
import { parseCsv } from '../utils/csv';
import {
  Search,
  Copy,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Button } from '../components/ui/button';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Separator } from '../components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
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
import { useToast } from '../contexts/ToastContext';
import { cn } from '../utils/cn';

interface CredentialEntry {
  vendor: string;
  username: string;
  password: string;
}

const NO_CREDENTIALS: CredentialEntry[] = [];
const PAGE_SIZES = [10, 25, 50, 100];
const DEFAULT_PAGE_SIZE = 25;

/** The sheet shipped with the app (a file of the build, not an API route). */
const loadDefaultCredentials = async (signal: AbortSignal): Promise<CredentialEntry[]> => {
  const response = await fetch('/DefaultCreds-Cheat-Sheet.csv', { signal });
  if (!response.ok) throw new Error('Failed to load credentials data');
  const csvText = await response.text();
  // A real CSV read: a quoted password may hold a comma or a quote.
  const rows = parseCsv(csvText);
  const parsed: CredentialEntry[] = [];
  for (let i = 1; i < rows.length; i++) {
    const values = rows[i];
    if (values.length >= 3) {
      parsed.push({
        vendor: values[0].trim(),
        username: values[1].trim() || '<blank>',
        password: values[2].trim() || '<blank>',
      });
    }
  }
  return parsed;
};

const DefaultCredentials: React.FC = () => {
  const toast = useToast();
  const sheet = useQuery({
    queryKey: ['defaultCredentialsSheet'],
    queryFn: ({ signal }) => loadDefaultCredentials(signal),
  });
  const credentials = sheet.data ?? NO_CREDENTIALS;
  const loading = sheet.isPending;
  const error = sheet.error ? 'Failed to load default credentials data' : null;

  const vendors = useMemo(() => {
    const set = new Set<string>();
    for (const c of credentials) {
      if (c.vendor && c.vendor !== 'productvendor') set.add(c.vendor);
    }
    return Array.from(set).sort();
  }, [credentials]);

  // The list's state lives in the address (`?vendor=`, `?search=`, `?per=`,
  // `?page=`; UI_STYLE_GUIDE §39, as on the SBOM page): a reload, a shared
  // link and Back show the same rows.  It is read from the address on every
  // render and changed by writing it; a value the address cannot mean is the
  // default.  Only the text being typed in the search box is state.
  const [params, setParams] = useSearchParams();
  const searchBox = useUrlSearchDraft('search');
  const searchTerm = searchBox.value;
  const vendorParam = params.get('vendor') ?? '';
  const selectedVendor = vendors.includes(vendorParam) ? vendorParam : '';
  const perParam = Number(params.get('per'));
  const rowsPerPage = PAGE_SIZES.includes(perParam) ? perParam : DEFAULT_PAGE_SIZE;
  const { page: askedPage, setPage } = useUrlPage();
  // One write per change, replacing the entry; a filter or another page size
  // changes which rows a page holds, so each starts from the first (`page` is
  // dropped) — there is no effect to reset it.  A default is left out.
  const writeParams = useCallback((updates: Record<string, string | null>) => {
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      Object.entries(updates).forEach(([k, v]) => (v == null ? out.delete(k) : out.set(k, v)));
      out.delete('page');
      return out;
    }, { replace: true });
  }, [setParams]);
  const setSelectedVendor = (v: string) => writeParams({ vendor: v || null });
  const setRowsPerPage = (n: number) => writeParams({ per: n === DEFAULT_PAGE_SIZE ? null : String(n) });
  // One write for both filters; the box then agrees with the address.
  const clearFilters = () => {
    writeParams({ vendor: null, search: null });
    searchBox.setDraft('');
  };

  const filtered = useMemo(() => {
    let out = credentials;
    if (selectedVendor) {
      out = out.filter((c) => c.vendor.toLowerCase() === selectedVendor.toLowerCase());
    }
    if (searchTerm) {
      const q = searchTerm.toLowerCase();
      out = out.filter(
        (c) =>
          c.vendor.toLowerCase().includes(q) ||
          c.username.toLowerCase().includes(q) ||
          c.password.toLowerCase().includes(q),
      );
    }
    return out;
  }, [credentials, selectedVendor, searchTerm]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / rowsPerPage));
  // A page the address names past the end shows the last one.
  const page = Math.min(askedPage, totalPages - 1);
  const pageRows = filtered.slice(page * rowsPerPage, (page + 1) * rowsPerPage);

  const copyToClipboard = async (text: string, label: string) => {
    if (await copyText(text)) {
      toast.success(`Copied ${label}`, { id: `copy-${label}` });
    } else {
      toast.error('Could not copy to clipboard');
    }
  };

  const fmtCred = (v: string) => (v === '<blank>' || v === '' ? '(blank)' : v);
  const isBlank = (v: string) => v === '<blank>' || v === '';

  if (loading) {
    return (
      <div className="flex min-h-96 flex-col items-center justify-center gap-sm">
        <Search className="size-6 animate-pulse text-muted-foreground" aria-hidden />
        <p className="text-metadata text-muted-foreground">Loading default credentials…</p>
      </div>
    );
  }

  return (
    <div className="p-md md:p-lg">
      <h1 className="text-page-title">Default credentials</h1>
      <p className="mt-xxs mb-md max-w-3xl text-metadata text-muted-foreground">
        Vendor default usernames and passwords, searchable. For authorised security testing only —
        confirm authorisation before trying any of them on a system.
      </p>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* v5.266.0 — one filter row closed by a rule (was a card, plus four
          stat cards that repeated the count and the selected vendor). */}
      <div className="mb-md border-b border-border pb-sm">
          <div className="grid grid-cols-1 items-end gap-md md:grid-cols-12">
            <div className="md:col-span-4">
              <Label htmlFor="dc-vendor">Vendor</Label>
              <Select
                value={selectedVendor || 'all'}
                onValueChange={(v) => setSelectedVendor(v === 'all' ? '' : v)}
              >
                <SelectTrigger id="dc-vendor">
                  <SelectValue placeholder="All vendors" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All vendors</SelectItem>
                  {vendors.map((v) => (
                    <SelectItem key={v} value={v}>
                      {v}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-6">
              <Label htmlFor="dc-search">Search</Label>
              <div className="relative">
                <Search
                  className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden
                />
                <Input
                  id="dc-search"
                  type="search"
                  value={searchBox.draft}
                  onChange={(e) => searchBox.setDraft(e.target.value)}
                  placeholder="Search vendor, username, or password…"
                  className="pl-xl"
                />
              </div>
            </div>
            <div className="flex items-center gap-sm md:col-span-2">
              <Button
                variant="ghost"
                onClick={clearFilters}
                disabled={!selectedVendor && !searchTerm && !searchBox.draft}
              >
                Clear
              </Button>
            </div>
          </div>
          <p className="mt-xs text-caption text-muted-foreground">
            {filtered.length.toLocaleString()} of {credentials.length.toLocaleString()} credentials · {vendors.length.toLocaleString()} vendors
          </p>
      </div>

      {/* Table */}
      <div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Vendor / Product</TableHead>
                  <TableHead>Username</TableHead>
                  <TableHead>Password</TableHead>
                  <TableHead className="text-center">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pageRows.map((c, i) => (
                  <TableRow key={`${c.vendor}-${i}`}>
                    <TableCell className="font-medium">{c.vendor}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-xs">
                        <code
                          className={cn(
                            'rounded-control bg-muted px-xs py-xxs font-mono text-caption',
                            isBlank(c.username) ? 'italic text-muted-foreground' : 'text-foreground',
                          )}
                        >
                          {fmtCred(c.username)}
                        </code>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => copyToClipboard(isBlank(c.username) ? '' : c.username, 'username')}
                              aria-label="Copy username"
                            >
                              <Copy className="size-3.5" aria-hidden />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Copy username</TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-xs">
                        <code
                          className={cn(
                            'rounded-control bg-muted px-xs py-xxs font-mono text-caption',
                            isBlank(c.password) ? 'italic text-muted-foreground' : 'text-foreground',
                          )}
                        >
                          {fmtCred(c.password)}
                        </code>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => copyToClipboard(isBlank(c.password) ? '' : c.password, 'password')}
                              aria-label="Copy password"
                            >
                              <Copy className="size-3.5" aria-hidden />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Copy password</TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                    <TableCell className="text-center">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() =>
                              copyToClipboard(
                                `${isBlank(c.username) ? '' : c.username}:${isBlank(c.password) ? '' : c.password}`,
                                'pair',
                              )
                            }
                            aria-label="Copy username and password"
                          >
                            <Copy className="size-4" aria-hidden />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Copy user:pass</TooltipContent>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Separator />
          <div className="flex flex-wrap items-center justify-end gap-md px-md py-sm">
            <div className="flex items-center gap-xs">
              <Label htmlFor="dc-rows" className="text-caption text-muted-foreground">
                Rows per page
              </Label>
              <Select
                value={String(rowsPerPage)}
                onValueChange={(v) => setRowsPerPage(Number(v))}
              >
                <SelectTrigger id="dc-rows" className="w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZES.map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <p className="text-caption text-muted-foreground">
              Page {page + 1} of {totalPages} · {filtered.length.toLocaleString()} entries
            </p>
            <div className="flex gap-xxs">
              <Button
                variant="outline"
                size="icon"
                disabled={page === 0}
                onClick={() => setPage(Math.max(0, page - 1))}
                aria-label="Previous page"
              >
                <ChevronLeft className="size-4" aria-hidden />
              </Button>
              <Button
                variant="outline"
                size="icon"
                disabled={page >= totalPages - 1}
                onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                aria-label="Next page"
              >
                <ChevronRight className="size-4" aria-hidden />
              </Button>
            </div>
          </div>
      </div>
    </div>
  );
};

export default DefaultCredentials;
