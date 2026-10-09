/**
 * "Scanner observations and scope" — a Posture section (5.330.0).
 *
 * It was the "Exposure" block at the bottom of Operations.  Operations is the
 * reader's own page now, and these are project status: what the scanners
 * reported by severity (Posture's measures and Findings count FINDINGS by
 * severity; the raw, not-yet-judged rows by severity are shown nowhere else),
 * and the three scope-coverage states, which add up to every host.
 *
 * It loads for itself (`GET /dashboard/stats`, `GET /coverage`), each half on
 * its own: a count that failed says "could not be counted", never an empty
 * bar or a zero — which would read as a clean project.  Every number opens the
 * Hosts list it counted.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import { getDashboardStats, getProjectCoverage } from '../../services/api';
import { queryErrorText } from '../../lib/query';
import { buildHostsUrl } from '../../utils/drilldownLinks';
import { InfoTip } from '../ui/info-tip';
import SeverityBar from '../ui/SeverityBar';
import { UnavailableLine } from '../operations/QueueParts';
import PostureSection from './PostureSection';

/** One coverage state: its count opens exactly its hosts (`scope:` DSL). */
const ScopeStateLink: React.FC<{ n: number | undefined; q: string; label: string }> = ({ n, q, label }) => {
  const count = n ?? 0;
  return count > 0 ? (
    <Link to={buildHostsUrl({ q })} className="text-info hover:underline">
      <strong className="tabular-nums">{count.toLocaleString()}</strong> {label}
    </Link>
  ) : (
    <span className="text-muted-foreground"><span className="tabular-nums">0</span> {label}</span>
  );
};

const ExposureSection: React.FC = () => {
  // Two reads, each half on its own.  The page's Refresh reaches them by
  // name (`getDashboardStats`, `getProjectCoverage`).
  const statsQuery = useQuery({ queryKey: ['getDashboardStats'], queryFn: () => getDashboardStats() });
  const coverageQuery = useQuery({ queryKey: ['getProjectCoverage'], queryFn: () => getProjectCoverage() });
  // A failed count is said BEFORE anything else below (the error branch comes
  // first), so an old bar is never shown under it: it would read as current.
  const stats = statsQuery.data ?? null;
  const statsLoading = statsQuery.isFetching;
  const statsError = queryErrorText(statsQuery.error, 'Could not load project statistics.');
  const coverage = coverageQuery.data ?? null;
  const coverageLoading = coverageQuery.isFetching;
  const coverageError = queryErrorText(coverageQuery.error, 'Could not load scope coverage.');

  // Retry reads both halves again, as one.
  const retry = () => {
    void statsQuery.refetch();
    void coverageQuery.refetch();
  };
  const vuln = stats?.vulnerability_stats;
  // Informational is excluded from the bar (it dwarfs real severities); the
  // bar's denominator is the non-info total so its segments fill the rail.
  const actionableTotal = vuln ? vuln.critical + vuln.high + vuln.medium + vuln.low : 0;

  return (
    <PostureSection
      title={<span>Scanner observations and scope</span>}
      description={<>
        What the scanners reported, not yet judged, and where the hosts stand against the declared
        scope. The judged record is on{' '}
        <Link to="/findings" className="text-info hover:underline">Findings</Link>.
      </>}
    >
      <div className="flex min-w-0 flex-col gap-md">
        {statsError ? (
          <UnavailableLine onRetry={retry}>
            Scanner observations could not be counted — this is not a clean project. {statsError}
          </UnavailableLine>
        ) : statsLoading && !stats ? (
          <p role="status" aria-live="polite" className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Counting scanner observations…
          </p>
        ) : vuln && actionableTotal > 0 ? (
          <div className="max-w-3xl" aria-busy={statsLoading || undefined}>
            <div className="mb-xs flex flex-wrap items-baseline justify-between gap-x-md gap-y-xxs">
              <h3 className="inline-flex items-center gap-xxs text-metadata font-semibold text-foreground">
                Scanner observations by severity
                <InfoTip text="One per scanner check per port, as imported — not yet judged, and a host usually carries several. Each severity opens the hosts carrying at least one observation of it; that host count is under the number." />
              </h3>
              <Link
                to={buildHostsUrl({ q: 'kind:vulnerability,misconfiguration,informational' })}
                className="text-caption tabular-nums text-muted-foreground hover:text-info hover:underline"
              >
                {actionableTotal.toLocaleString()} observations, informational excluded
                {' · '}{(vuln.hosts_with_vulnerabilities ?? 0).toLocaleString()} hosts carry one
              </Link>
            </div>
            <SeverityBar
              variant="summary"
              counts={vuln}
              total={actionableTotal}
              ariaLabel="Scanner observations by severity"
              // SeverityBar never renders info, but the callback is typed
              // over all severities — guard so the type narrows to HostSeverity.
              segmentHref={(sev) => (sev === 'info' ? null : buildHostsUrl({ severity: sev }))}
              // The counts are observations, the links hosts: the link says so.
              linkLabel={(sev) => {
                const n = vuln.hosts_by_severity?.[sev];
                return n == null ? null : `${n.toLocaleString()} host${n === 1 ? '' : 's'}`;
              }}
            />
          </div>
        ) : stats ? (
          <p className="text-metadata text-muted-foreground">
            No critical, high, medium or low scanner observation is recorded — upload a Nessus or
            OpenVAS scan to populate this.
          </p>
        ) : null}

        {/* The three coverage states, adding up to every host; each number
            opens its hosts.  Scope names are not shown: they are a relic. */}
        {coverageError ? (
          <UnavailableLine onRetry={retry}>
            Scope coverage could not be counted — this is not “no host outside scope”. {coverageError}
          </UnavailableLine>
        ) : coverageLoading && !coverage ? (
          <p role="status" aria-live="polite" className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Counting hosts by scope…
          </p>
        ) : coverage && coverage.total_scopes > 0 ? (
          <p className="flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs text-metadata">
            <span className="font-semibold text-foreground">Scope</span>
            <InfoTip text="Every host is in exactly one of these: inside a scope subnet; in no subnet but reached through an in-scope name (the name was approved, not the address); or outside scope — discovered, but nobody approved testing it, so confirm it is in scope before acting on it." />
            <ScopeStateLink n={coverage.hosts_in_subnet_scope} q="scope:subnet" label="in scope subnets" />
            <span className="text-muted-foreground" aria-hidden>·</span>
            <ScopeStateLink n={coverage.hosts_name_scope_only} q="scope:name" label="reached only through an in-scope name" />
            <span className="text-muted-foreground" aria-hidden>·</span>
            <ScopeStateLink n={coverage.hosts_outside_scope} q="scope:none" label="outside scope" />
          </p>
        ) : coverage ? (
          <p className="text-metadata text-muted-foreground">
            No scope is declared, so every host is outside scope.{' '}
            <Link to="/scopes" className="text-info hover:underline">Register a scope</Link>
          </p>
        ) : null}
      </div>
    </PostureSection>
  );
};

export default ExposureSection;
