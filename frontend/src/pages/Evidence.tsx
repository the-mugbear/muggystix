/**
 * Evidence — "can we trust the posture conclusions?"
 *
 * The fourth posture tab. Where Posture/Patterns/Segments report WHAT is wrong
 * and WHERE, this reports how much of the estate has actually been assessed, per
 * assessment domain: an eligibility denominator (hosts where the domain applies)
 * and an assessed numerator (hosts with evidence). A confident posture built on
 * a discovery-only scan is not trustworthy — the gaps here are blind spots in
 * the evidence, distinct from blind spots in the estate.
 *
 * UI-style-guide compliance: no page-level overflow; every state (loading /
 * error / empty) renders a safe fallback; external values (tool names) truncate.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, RefreshCw, Info, ShieldAlert, Wrench, AlertTriangle } from 'lucide-react';

import {
  getEvidenceCoverage,
  type EvidenceCoverageResponse,
  type EvidenceDomain,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';

const InfoTip: React.FC<{ text: string }> = ({ text }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button type="button" aria-label="How is this derived?"
        className="inline-flex shrink-0 rounded text-muted-foreground/70 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Info className="size-3.5" aria-hidden />
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs text-left text-caption leading-snug">{text}</TooltipContent>
  </Tooltip>
);

// Coverage → bar colour: red under a third, amber under two thirds, green above.
const coverageColor = (pct: number): string =>
  pct < 34 ? 'hsl(var(--destructive))' : pct < 67 ? 'hsl(var(--warning))' : 'hsl(var(--success))';

const DomainCard: React.FC<{ d: EvidenceDomain }> = ({ d }) => {
  const { numerator, denominator } = d.coverage;
  const pct = denominator > 0 ? Math.round((numerator / denominator) * 100) : null;
  return (
    <Card>
      <CardContent className="space-y-sm p-md">
        <div className="flex items-start justify-between gap-xs">
          <h3 className="min-w-0 truncate font-semibold text-foreground" title={d.label}>{d.label}</h3>
          {denominator === 0 ? (
            <Badge variant="muted">n/a</Badge>
          ) : (
            <span className="shrink-0 text-metadata font-bold tabular-nums text-foreground">{pct}%</span>
          )}
        </div>
        {denominator === 0 ? (
          <p className="text-caption text-muted-foreground">No eligible hosts for this domain yet.</p>
        ) : (
          <>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full transition-all"
                style={{ width: `${pct}%`, backgroundColor: coverageColor(pct ?? 0) }} />
            </div>
            <p className="text-caption tabular-nums text-muted-foreground">
              <span className="font-medium text-foreground">{numerator}</span> of {denominator} eligible hosts assessed
            </p>
          </>
        )}
        <p className="text-caption text-muted-foreground">{d.note}</p>
      </CardContent>
    </Card>
  );
};

const Evidence: React.FC = () => {
  const { currentProject } = useProject();
  const [data, setData] = useState<EvidenceCoverageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getEvidenceCoverage({ signal: controller.signal })
      .then((d) => { if (!controller.signal.aborted) { setData(d); setError(null); } })
      .catch((e) => { if (!controller.signal.aborted) setError(formatApiError(e, 'Could not load evidence coverage.')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [currentProject?.id, nonce]);

  return (
    <div className="space-y-md p-md">
      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <h1 className="text-page-title">Evidence</h1>
          <p className="mt-xs max-w-3xl text-caption text-muted-foreground">
            Whether the posture conclusions are trustworthy — how much of the estate has actually been
            assessed, per domain. Each bar is <strong className="text-foreground">assessed ÷ eligible</strong>{' '}
            hosts (only hosts where a domain applies count toward its denominator). A large gap is a
            blind spot in the <em>evidence</em>, not the estate — the next collection activity, not a
            finding.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={reload} disabled={loading}>
          <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden /> Refresh
        </Button>
      </div>

      {loading && !data ? (
        <div className="flex items-center gap-xs" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">Assessing evidence coverage…</p>
        </div>
      ) : error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load evidence</AlertTitle>
          <AlertDescription>
            <p className="break-words">{error}</p>
            <Button size="sm" variant="outline" className="mt-xs" onClick={reload}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : data ? (
        data.total_hosts === 0 ? (
          <Card>
            <CardContent className="p-lg text-center">
              <ShieldAlert className="mx-auto mb-sm size-8 text-muted-foreground" aria-hidden />
              <p className="text-subheading font-semibold text-foreground">No hosts yet</p>
              <p className="mx-auto mt-xs max-w-md text-metadata text-muted-foreground">
                Upload a scan or run recon, then return — evidence coverage is measured against the
                hosts in this project.
              </p>
              <Button asChild size="sm" className="mt-md"><Link to="/scans">Upload a scan</Link></Button>
            </CardContent>
          </Card>
        ) : (
          <>
            <p className="text-caption text-muted-foreground">
              Assessed against <span className="font-medium text-foreground">{data.total_hosts}</span> hosts in this project.
            </p>

            <div className="grid gap-sm md:grid-cols-2 xl:grid-cols-3">
              {data.domains.map((d) => <DomainCard key={d.key} d={d} />)}
            </div>

            <div className="grid gap-md lg:grid-cols-2">
              {/* Contributing tools */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-xs text-metadata">
                    <Wrench className="size-4 text-muted-foreground" aria-hidden /> Contributing tools
                    <InfoTip text="The scanners whose output has been ingested into this project, with how many scans each contributed. A domain with no matching tool can't be assessed." />
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {data.contributing_tools.length === 0 ? (
                    <p className="text-caption text-muted-foreground">No tools recorded yet.</p>
                  ) : (
                    <div className="flex flex-wrap gap-xs">
                      {data.contributing_tools.map((t) => (
                        <Badge key={t.tool} variant="muted" className="max-w-[16rem] truncate" title={`${t.tool} — ${t.scans} scan${t.scans === 1 ? '' : 's'}`}>
                          {t.tool} · {t.scans}
                        </Badge>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Data quality */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-xs text-metadata">
                    <AlertTriangle className="size-4 text-muted-foreground" aria-hidden /> Data quality
                    <InfoTip text="Signals that can undermine the evidence: files that failed to parse (their data never landed). Resolve or re-upload them to close the gap." />
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-xs">
                  <p className="text-caption text-muted-foreground">
                    <span className="font-medium text-foreground">{data.data_quality.scans}</span> scan{data.data_quality.scans === 1 ? '' : 's'} ingested
                  </p>
                  <p className="text-caption text-muted-foreground">
                    {data.data_quality.parse_errors_unresolved > 0 ? (
                      <Link to="/parse-errors" className="text-warning hover:underline">
                        {data.data_quality.parse_errors_unresolved} unresolved parse error{data.data_quality.parse_errors_unresolved === 1 ? '' : 's'} →
                      </Link>
                    ) : (
                      <span>No unresolved parse errors.</span>
                    )}
                  </p>
                </CardContent>
              </Card>
            </div>
          </>
        )
      ) : null}
    </div>
  );
};

export default Evidence;
