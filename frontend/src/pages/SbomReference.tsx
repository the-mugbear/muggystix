import React, { useEffect, useState, useMemo } from 'react';
import { Search, Download, ChevronLeft, ChevronRight } from 'lucide-react';
import { getSbom, SbomResponse, SbomComponent } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { CardListSkeleton } from '../components/PageSkeleton';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Switch } from '../components/ui/switch';
import { Card, CardContent } from '../components/ui/card';
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
import { cn } from '../utils/cn';

type LayerFilter = 'all' | 'backend' | 'frontend';

const SbomReference: React.FC = () => {
  const [data, setData] = useState<SbomResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [directOnly, setDirectOnly] = useState(false);
  const [layer, setLayer] = useState<LayerFilter>('all');

  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);

  useEffect(() => {
    let cancelled = false;
    getSbom()
      .then((r) => {
        if (!cancelled) {
          setData(r);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(formatApiError(err, 'Could not load the SBOM.'));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo<SbomComponent[]>(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.components.filter((c) => {
      if (layer !== 'all' && c.application_layer !== layer) return false;
      if (directOnly && !c.direct) return false;
      if (q && !c.name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, search, directOnly, layer]);

  // Drop back to page 0 whenever filters narrow the result set.
  useEffect(() => {
    setPage(0);
  }, [search, directOnly, layer]);

  const handleDownload = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `networkmapper-sbom-${data.app_version}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <div className="p-md md:p-lg">
        <CardListSkeleton count={3} cardHeight={120} />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="destructive">
          <AlertDescription>{error || 'Could not load the SBOM.'}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const totalPages = Math.max(1, Math.ceil(filtered.length / rowsPerPage));
  const pageRows = filtered.slice(page * rowsPerPage, (page + 1) * rowsPerPage);

  return (
    <div className="p-md md:p-lg">
      <h1 className="text-page-title">Software Bill of Materials</h1>
      <p className="mt-xxs mb-md text-metadata text-muted-foreground">
        Every backend Python and frontend npm component bundled with this build, tagged direct vs
        transitive. Use it to answer "is package X in the app?" when a new advisory is announced.
      </p>

      {/* Persistent caveat — load-bearing UX, NOT a dismissable footnote.
          Half the value of the page over a `pip freeze` dump is honest
          expectation-setting about what presence in the list does and
          does not assert. */}
      <Alert variant="info" className="mb-md">
        <AlertDescription>
          <strong>Presence in this list confirms a package is bundled with the app.</strong>{' '}
          It does <em>not</em> confirm a vulnerability is exploitable — that depends on which
          features use the package, runtime context, and exposure.
        </AlertDescription>
      </Alert>

      {/* Build identity + download. */}
      <div className="mb-md flex flex-wrap items-center gap-md">
        <p className="text-caption text-muted-foreground">
          App version <strong className="text-foreground">{data.app_version}</strong>{' '}
          · Generated{' '}
          <strong className="text-foreground">
            {new Date(data.generated_at).toLocaleString()}
          </strong>
        </p>
        <div className="ml-auto">
          <Button variant="outline" size="sm" onClick={handleDownload}>
            <Download className="size-4" aria-hidden /> Download JSON
          </Button>
        </div>
      </div>

      {/* Summary cards. */}
      <div className="mb-md grid grid-cols-2 gap-sm md:grid-cols-4">
        <SummaryCard label="Total" value={data.summary.total} />
        <SummaryCard label="Direct" value={data.summary.direct} hint="explicitly listed" />
        <SummaryCard
          label="Transitive"
          value={data.summary.transitive}
          hint="pulled in by direct deps"
        />
        <SummaryCard
          label="Backend / Frontend"
          value={`${data.summary.backend} / ${data.summary.frontend}`}
          hint="python / npm"
        />
      </div>

      {/* Filters. */}
      <div className="mb-md flex flex-wrap items-center gap-md">
        <div className="relative min-w-60 flex-1 sm:flex-initial">
          <Search
            className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="search"
            placeholder="Search by name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-xl"
            aria-label="Search SBOM by package name"
          />
        </div>

        <div
          role="group"
          aria-label="Filter by application layer"
          className="inline-flex rounded-control border border-border bg-card p-xxs"
        >
          {(['all', 'backend', 'frontend'] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setLayer(v)}
              aria-pressed={layer === v}
              className={cn(
                'rounded-control px-sm py-xxs text-metadata font-medium capitalize transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                layer === v
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              )}
            >
              {v}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-xs">
          <Switch
            id="sbom-direct-only"
            checked={directOnly}
            onCheckedChange={(v) => setDirectOnly(Boolean(v))}
          />
          <Label htmlFor="sbom-direct-only">Direct only</Label>
        </div>

        <p className="ml-auto text-caption text-muted-foreground">
          Showing {filtered.length.toLocaleString()} of {data.summary.total.toLocaleString()}
        </p>
      </div>

      {/* Components table. */}
      <div className="rounded-panel border border-border">
        <div className="overflow-x-auto">
          <Table className="min-w-[920px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[24%]">Name</TableHead>
                <TableHead className="w-[12%]">Version</TableHead>
                <TableHead className="w-[10%]">Ecosystem</TableHead>
                <TableHead className="w-[12%]">Layer</TableHead>
                <TableHead className="w-[10%]">Source</TableHead>
                <TableHead className="w-[12%]">License</TableHead>
                <TableHead className="w-[20%]">Provenance</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pageRows.map((c) => (
                <TableRow key={`${c.ecosystem}:${c.name}:${c.version}`}>
                  <TableCell>
                    <span className="font-mono text-caption text-foreground break-words">
                      {c.name}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-caption text-foreground">{c.version}</span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{c.ecosystem}</Badge>
                  </TableCell>
                  <TableCell className="truncate">{c.application_layer}</TableCell>
                  <TableCell>
                    {c.direct ? (
                      <Badge variant="default">direct</Badge>
                    ) : (
                      <Badge variant="outline">transitive</Badge>
                    )}
                  </TableCell>
                  <TableCell className="break-words">{c.license || '—'}</TableCell>
                  <TableCell>
                    <div className="space-y-xxs font-mono text-caption break-words">
                      <div className={c.declared_in ? 'text-foreground' : 'text-muted-foreground'}>
                        declared: {c.declared_in || '—'}
                      </div>
                      <div className="text-muted-foreground">resolved: {c.resolved_from}</div>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {pageRows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="py-xl text-center text-muted-foreground">
                    No components match your filters.{' '}
                    {search && <span>Try searching for the package name exactly as published.</span>}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>

        {/* Inline pagination — simple prev/next + rows-per-page + count. */}
        <div className="flex flex-wrap items-center justify-end gap-md border-t border-border px-md py-sm">
          <div className="flex items-center gap-xs">
            <Label htmlFor="sbom-rows-per-page" className="text-caption text-muted-foreground">
              Rows per page
            </Label>
            <Select
              value={String(rowsPerPage)}
              onValueChange={(v) => {
                setRowsPerPage(Number(v));
                setPage(0);
              }}
            >
              <SelectTrigger id="sbom-rows-per-page" className="w-20">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[25, 50, 100, 250].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <p className="text-caption text-muted-foreground">
            Page {page + 1} of {totalPages} · {filtered.length.toLocaleString()} component
            {filtered.length === 1 ? '' : 's'}
          </p>
          <div className="flex gap-xxs">
            <Button
              variant="outline"
              size="icon"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              aria-label="Previous page"
            >
              <ChevronLeft className="size-4" aria-hidden />
            </Button>
            <Button
              variant="outline"
              size="icon"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
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

const SummaryCard: React.FC<{ label: string; value: string | number; hint?: string }> = ({
  label,
  value,
  hint,
}) => (
  <Card>
    <CardContent className="p-md">
      <p className="text-micro font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="text-section-title font-semibold text-foreground">{value}</p>
      {hint && <p className="text-caption text-muted-foreground">{hint}</p>}
    </CardContent>
  </Card>
);

export default SbomReference;
