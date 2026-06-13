import React, { useEffect, useState, useMemo } from 'react';
import {
  Search,
  Download,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ArrowUp,
  ArrowDown,
  ChevronsUpDown,
} from 'lucide-react';
import { getSbom, SbomResponse, SbomComponent } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { CardListSkeleton } from '../components/PageSkeleton';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
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
type SourceFilter = 'all' | 'direct' | 'transitive';

// Columns the user can sort by.  `source` sorts on the direct/transitive
// classification; everything else is the obvious field.
type SortKey = 'name' | 'version' | 'ecosystem' | 'layer' | 'source' | 'license';
type SortDir = 'asc' | 'desc';

const NO_LICENSE = '__none__';

const SbomReference: React.FC = () => {
  const [data, setData] = useState<SbomResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [layer, setLayer] = useState<LayerFilter>('all');
  const [source, setSource] = useState<SourceFilter>('all');
  const [license, setLicense] = useState<string>('all');

  const [sortKey, setSortKey] = useState<SortKey>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);
  const [showProvenance, setShowProvenance] = useState(false);

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

  // Distinct licenses present in the build, for the license filter dropdown.
  // Null/empty licenses collapse into a single "unspecified" bucket so a
  // user can filter for "what don't we have license data on?".
  const licenseOptions = useMemo<{ value: string; label: string; count: number }[]>(() => {
    if (!data) return [];
    const counts = new Map<string, number>();
    for (const c of data.components) {
      const key = c.license && c.license.trim() ? c.license.trim() : NO_LICENSE;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const entries = Array.from(counts.entries())
      .map(([value, count]) => ({
        value,
        label: value === NO_LICENSE ? 'Unspecified' : value,
        count,
      }))
      .sort((a, b) => {
        // Unspecified sinks to the bottom; otherwise alphabetical.
        if (a.value === NO_LICENSE) return 1;
        if (b.value === NO_LICENSE) return -1;
        return a.label.localeCompare(b.label);
      });
    return entries;
  }, [data]);

  const filtered = useMemo<SbomComponent[]>(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    const rows = data.components.filter((c) => {
      if (layer !== 'all' && c.application_layer !== layer) return false;
      if (source === 'direct' && !c.direct) return false;
      if (source === 'transitive' && c.direct) return false;
      if (license !== 'all') {
        const key = c.license && c.license.trim() ? c.license.trim() : NO_LICENSE;
        if (key !== license) return false;
      }
      if (q && !c.name.toLowerCase().includes(q)) return false;
      return true;
    });

    const dir = sortDir === 'asc' ? 1 : -1;
    const cmp = (a: SbomComponent, b: SbomComponent): number => {
      switch (sortKey) {
        case 'name':
          return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
        case 'version':
          return a.version.localeCompare(b.version, undefined, { numeric: true, sensitivity: 'base' });
        case 'ecosystem':
          return a.ecosystem.localeCompare(b.ecosystem);
        case 'layer':
          return a.application_layer.localeCompare(b.application_layer);
        case 'source':
          // direct < transitive in ascending order.
          return (a.direct ? 0 : 1) - (b.direct ? 0 : 1);
        case 'license': {
          // Unspecified always sorts last regardless of direction, so it
          // doesn't crowd the top when sorting descending.
          const al = a.license?.trim() ?? '';
          const bl = b.license?.trim() ?? '';
          if (!al && !bl) return 0;
          if (!al) return 1 * dir; // keep "last" intent stable below
          if (!bl) return -1 * dir;
          return al.localeCompare(bl, undefined, { sensitivity: 'base' });
        }
        default:
          return 0;
      }
    };

    // Stable sort with name as the tiebreaker so equal keys stay readable.
    return [...rows].sort((a, b) => {
      const primary = cmp(a, b) * dir;
      if (primary !== 0) return primary;
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });
  }, [data, search, layer, source, license, sortKey, sortDir]);

  // Drop back to page 0 whenever filters/sort narrow or reorder the set.
  useEffect(() => {
    setPage(0);
  }, [search, layer, source, license, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

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

      {/* Build identity + provenance disclosure + download. */}
      <div className="mb-sm flex flex-wrap items-center gap-md">
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

      {/* "How this is generated" — answers "can I trust this isn't stale?".
          Collapsed by default to keep the page calm; the build-identity line
          above already shows the live version + timestamp at a glance. */}
      <div className="mb-md rounded-panel border border-border bg-card">
        <button
          type="button"
          onClick={() => setShowProvenance((v) => !v)}
          aria-expanded={showProvenance}
          className={cn(
            'flex w-full items-center justify-between gap-sm px-md py-sm text-left',
            'text-metadata font-medium text-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-panel',
          )}
        >
          <span>How is this list generated? (and why it isn't stale)</span>
          <ChevronDown
            className={cn(
              'size-4 shrink-0 text-muted-foreground transition-transform duration-base ease-standard',
              showProvenance && 'rotate-180',
            )}
            aria-hidden
          />
        </button>
        {showProvenance && (
          <div className="space-y-sm border-t border-border px-md py-sm text-metadata text-muted-foreground">
            <p>
              This is a <strong className="text-foreground">live snapshot of the running build</strong>,
              not a checked-in file that can drift. The backend computes it on request from the
              resolved dependency trees actually installed in this deployment:
            </p>
            <ul className="ml-lg list-disc space-y-xxs marker:text-muted-foreground">
              <li>
                <strong className="text-foreground">Backend (Python)</strong> — walks the installed
                virtualenv via <code className="font-mono text-caption">importlib.metadata</code>, so
                every transitive dependency is visible at the exact version that's deployed. Each
                distribution is cross-referenced against{' '}
                <code className="font-mono text-caption">requirements.txt</code> to set the{' '}
                <em>direct</em> flag.
              </li>
              <li>
                <strong className="text-foreground">Frontend (npm)</strong> — read from the resolved{' '}
                <code className="font-mono text-caption">package-lock.json</code> lockfile (not the
                looser <code className="font-mono text-caption">package.json</code>). A package is{' '}
                <em>direct</em> iff it appears in the root manifest's dependency sets.
              </li>
            </ul>
            <p>
              The result is cached keyed by the two manifest files' modification times and the app
              version, so it <strong className="text-foreground">recomputes automatically</strong>{' '}
              whenever a dependency changes or a new version deploys — no restart, no manual refresh.
              The <strong className="text-foreground">Generated</strong> timestamp and{' '}
              <strong className="text-foreground">App version</strong> above are from that live
              computation; if they match the build you're running, the list is current.
            </p>
          </div>
        )}
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
      <div className="mb-md flex flex-wrap items-end gap-md">
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

        <FilterSegment
          label="Layer"
          ariaLabel="Filter by application layer"
          value={layer}
          options={['all', 'backend', 'frontend'] as const}
          onChange={setLayer}
        />

        <FilterSegment
          label="Source"
          ariaLabel="Filter by source (direct or transitive)"
          value={source}
          options={['all', 'direct', 'transitive'] as const}
          onChange={setSource}
        />

        <div className="flex flex-col gap-xxs">
          <Label htmlFor="sbom-license" className="text-caption text-muted-foreground">
            License
          </Label>
          <Select value={license} onValueChange={setLicense}>
            <SelectTrigger id="sbom-license" className="w-52">
              <SelectValue placeholder="All licenses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All licenses</SelectItem>
              {licenseOptions.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label} ({opt.count})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
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
                <SortableHead className="w-[24%]" label="Name" sortKey="name" active={sortKey} dir={sortDir} onSort={toggleSort} />
                <SortableHead className="w-[12%]" label="Version" sortKey="version" active={sortKey} dir={sortDir} onSort={toggleSort} />
                <SortableHead className="w-[10%]" label="Ecosystem" sortKey="ecosystem" active={sortKey} dir={sortDir} onSort={toggleSort} />
                <SortableHead className="w-[12%]" label="Layer" sortKey="layer" active={sortKey} dir={sortDir} onSort={toggleSort} />
                <SortableHead className="w-[10%]" label="Source" sortKey="source" active={sortKey} dir={sortDir} onSort={toggleSort} />
                <SortableHead className="w-[12%]" label="License" sortKey="license" active={sortKey} dir={sortDir} onSort={toggleSort} />
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

/** Segmented control for the small fixed-option filters (layer, source). */
function FilterSegment<T extends string>({
  label,
  ariaLabel,
  value,
  options,
  onChange,
}: {
  label: string;
  ariaLabel: string;
  value: T;
  options: readonly T[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-col gap-xxs">
      <span className="text-caption text-muted-foreground">{label}</span>
      <div
        role="group"
        aria-label={ariaLabel}
        className="inline-flex rounded-control border border-border bg-card p-xxs"
      >
        {options.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => onChange(v)}
            aria-pressed={value === v}
            className={cn(
              'rounded-control px-sm py-xxs text-metadata font-medium capitalize transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              value === v
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
            )}
          >
            {v}
          </button>
        ))}
      </div>
    </div>
  );
}

/** A clickable column header that toggles sort on its key. */
const SortableHead: React.FC<{
  label: string;
  sortKey: SortKey;
  active: SortKey;
  dir: SortDir;
  onSort: (key: SortKey) => void;
  className?: string;
}> = ({ label, sortKey, active, dir, onSort, className }) => {
  const isActive = active === sortKey;
  return (
    <TableHead className={className} aria-sort={isActive ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          'group inline-flex items-center gap-xxs rounded-control text-left',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
        )}
      >
        {label}
        {isActive ? (
          dir === 'asc' ? (
            <ArrowUp className="size-3" aria-hidden />
          ) : (
            <ArrowDown className="size-3" aria-hidden />
          )
        ) : (
          <ChevronsUpDown className="size-3 opacity-0 transition-opacity group-hover:opacity-60" aria-hidden />
        )}
      </button>
    </TableHead>
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
