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
import { Loader2, RefreshCw, ShieldAlert, Wrench, AlertTriangle } from 'lucide-react';

import {
  getEvidenceCoverage,
  getEvidenceGaps,
  type EvidenceCoverageResponse,
  type EvidenceDomain,
  type EvidenceGapsResponse,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { copyToClipboard } from '../utils/clipboard';
import { stashPlanSelection } from '../utils/planSelection';
import { useNavigate } from 'react-router-dom';
import { formatApiError } from '../utils/apiErrors';
import { useProject } from '../contexts/ProjectContext';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { InfoTip } from '../components/ui/info-tip';


// Coverage → bar colour: red under a third, amber under two thirds, green above.
const coverageColor = (pct: number): string =>
  pct < 34 ? 'hsl(var(--destructive))' : pct < 67 ? 'hsl(var(--warning))' : 'hsl(var(--success))';

const GAP_PREVIEW = 12;

/**
 * v5.224.0 — a coverage gap the operator can act on (design review item 4):
 * "Show the N unassessed hosts" reveals the affected endpoints (with the
 * open ports that made them eligible) and offers the step that closes the
 * gap: copy the IPs for a scoped collection run, or hand the hosts to the
 * generate dialog as a fixed selection.
 */
const GapList: React.FC<{ domain: string; gapCount: number }> = ({ domain, gapCount }) => {
  const toast = useToast();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [gaps, setGaps] = useState<EvidenceGapsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!open || gaps) return;
    const controller = new AbortController();
    setLoading(true);
    getEvidenceGaps(domain, { signal: controller.signal })
      .then((g) => setGaps(g))
      .catch((e) => { if (!controller.signal.aborted) setError(formatApiError(e, 'Could not load the gap.')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open, gaps, domain]);

  const copyIps = async () => {
    if (!gaps) return;
    const ok = await copyToClipboard(gaps.items.map((h) => h.ip_address).join('\n'));
    if (ok) toast.success(`Copied ${gaps.items.length} IP${gaps.items.length === 1 ? '' : 's'}${gaps.total > gaps.items.length ? ` (first ${gaps.items.length} of ${gaps.total})` : ''}`, { autoHideMs: 2500 });
    else toast.error('Could not copy to clipboard.');
  };

  const planThese = () => {
    if (!gaps) return;
    stashPlanSelection({
      host_ids: gaps.items.map((h) => h.host_id),
      rationale: `${gaps.label}: ${gaps.action.text}`,
      summary: `${gaps.items.length} hosts with no ${gaps.label.toLowerCase()} evidence (Evidence page)`,
      taken_at: new Date().toISOString(),
    });
    navigate('/test-plans?generate=1&source=selection');
  };

  if (gapCount === 0) return null;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {open ? 'Hide' : 'Show'} the {gapCount.toLocaleString()} unassessed host{gapCount === 1 ? '' : 's'}
      </button>
      {open && (
        <div className="mt-xs rounded-panel border border-border p-sm">
          {loading && (
            <p className="inline-flex items-center gap-xs text-caption text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading…
            </p>
          )}
          {error && <p className="text-caption text-destructive break-words">{error}</p>}
          {gaps && (
            <>
              <p className="mb-xs break-words text-caption text-foreground">{gaps.action.text}</p>
              <ul className="flex flex-col gap-xxs" aria-label={`Hosts without ${gaps.label} evidence`}>
                {(showAll ? gaps.items : gaps.items.slice(0, GAP_PREVIEW)).map((h) => (
                  <li key={h.host_id} className="flex min-w-0 flex-wrap items-baseline gap-x-xs text-caption">
                    <Link to={`/hosts/${h.host_id}`} className="font-mono text-foreground hover:underline">{h.ip_address}</Link>
                    {h.hostname && <span className="min-w-0 truncate text-muted-foreground">{h.hostname}</span>}
                    {h.ports.length > 0 && (
                      <span className="font-mono text-muted-foreground">{h.ports.join(', ')}</span>
                    )}
                  </li>
                ))}
              </ul>
              {gaps.items.length > GAP_PREVIEW && (
                <button type="button" onClick={() => setShowAll((v) => !v)}
                  className="mt-xxs rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  {showAll ? 'Show fewer' : `Show ${gaps.items.length - GAP_PREVIEW} more`}
                </button>
              )}
              {gaps.total > gaps.items.length && (
                <p className="mt-xxs text-caption text-muted-foreground">Showing the first {gaps.items.length} of {gaps.total.toLocaleString()}.</p>
              )}
              <div className="mt-xs flex flex-wrap gap-xs">
                <Button size="sm" variant="outline" onClick={() => void copyIps()} title="Copy the IPs as a target list for the collection step">
                  Copy IPs
                </Button>
                <Button size="sm" variant={gaps.action.kind === 'plan' ? 'default' : 'outline'} onClick={planThese}
                  title="Hand these hosts to the generate dialog as a fixed selection">
                  Plan these
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

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
            <GapList domain={d.key} gapCount={Math.max(denominator - numerator, 0)} />
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
