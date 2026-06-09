/**
 * AttentionCard — the project "needs help" surface (site-metrics arc P1).
 *
 * Two axes the operator must see separately: Exposure (severity-weighted
 * active findings) and Neglect (stale/absent scans, untriaged backlog,
 * unreviewed hosts), led by a recommended next action. Explainable by
 * design — every number is a visible component, never an opaque score
 * (the lesson from the deleted risk-scoring system).
 */
import React, { useEffect, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';

import {
  getProjectAttention, getSiteAttention,
  type ProjectAttention, type SiteAttention,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';

type BadgeTone = 'destructive' | 'warning' | 'info' | 'muted' | 'secondary' | 'success';

const ACTION_TONE: Record<ProjectAttention['recommended_action']['kind'], BadgeTone> = {
  onboard: 'info',
  scan: 'warning',
  triage: 'warning',
  remediate: 'destructive',
  review: 'muted',
  ok: 'success',
};
const SEVERITY_VARIANT: Record<string, string> = {
  critical: 'severity-critical', high: 'severity-high', medium: 'severity-medium',
  low: 'severity-low', info: 'muted',
};

export const AttentionCard: React.FC = () => {
  const [data, setData] = useState<ProjectAttention | null>(null);
  const [sites, setSites] = useState<SiteAttention | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = React.useCallback(() => {
    setLoading(true);
    Promise.all([getProjectAttention(), getSiteAttention()])
      .then(([d, s]) => { setData(d); setSites(s); setError(null); })
      .catch((e) => setError(formatApiError(e, 'Could not load project attention.')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <p className="mb-sm text-subheading font-semibold text-foreground">Needs attention</p>

        {loading ? (
          <div className="flex items-center gap-xs" role="status" aria-live="polite">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Assessing…</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTitle>Couldn't assess</AlertTitle>
            <AlertDescription>
              <p className="break-words">{error}</p>
              <Button size="sm" variant="outline" className="mt-xs" onClick={load}>
                <RefreshCw className="size-3.5" aria-hidden /> Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : data ? (
          <div className="flex flex-col gap-sm">
            {/* Recommended action — the "what to do next". */}
            <div className="flex flex-wrap items-center gap-xs">
              <Badge variant={ACTION_TONE[data.recommended_action.kind]}>
                {data.recommended_action.kind === 'ok' ? 'On track' : 'Next'}
              </Badge>
              <span className="text-metadata text-foreground">{data.recommended_action.text}</span>
            </div>

            {/* Exposure — severity-weighted active findings (the breakdown). */}
            <div>
              <p className="mb-xxs text-caption text-muted-foreground">
                Exposure · {data.exposure.active_findings} active finding{data.exposure.active_findings === 1 ? '' : 's'}
              </p>
              <div className="flex flex-wrap gap-xxs">
                {(['critical', 'high', 'medium', 'low', 'info'] as const)
                  .filter((s) => data.exposure.by_severity[s] > 0)
                  .map((s) => (
                    <Badge key={s} variant={SEVERITY_VARIANT[s] as never}>
                      {data.exposure.by_severity[s]} {s}
                    </Badge>
                  ))}
                {data.exposure.active_findings === 0 && (
                  <span className="text-caption text-muted-foreground">None — but check coverage →</span>
                )}
              </div>
            </div>

            {/* Neglect — the under-served signals. */}
            <div>
              <p className="mb-xxs text-caption text-muted-foreground">Neglect</p>
              <div className="flex flex-wrap gap-x-md gap-y-xxs text-caption">
                <span>
                  Last scan:{' '}
                  <span className="font-medium text-foreground">
                    {data.neglect.scan_count === 0
                      ? 'never'
                      : data.neglect.scan_staleness_days === null
                        ? '—'
                        : `${data.neglect.scan_staleness_days}d ago`}
                  </span>
                </span>
                <span>
                  Unowned findings:{' '}
                  <span className="font-medium text-foreground">{data.neglect.unowned_active_findings}</span>
                </span>
                <span>
                  Unreviewed hosts:{' '}
                  <span className="font-medium text-foreground">
                    {data.neglect.unreviewed_hosts}/{data.neglect.total_hosts}
                  </span>
                </span>
              </div>
            </div>

            {/* Per-site breakdown — the same components grouped by site,
                worst-first. Only when the project has organised subnets into
                sites (otherwise the project-level view above is all there is). */}
            {sites?.adopted && sites.sites.length > 0 && (
              <div>
                <p className="mb-xxs text-caption text-muted-foreground">By site</p>
                <div className="flex flex-col gap-xxs">
                  {sites.sites.slice(0, 6).map((s) => (
                    <div
                      key={s.site ?? '__unassigned__'}
                      className="flex flex-wrap items-center gap-xs border-b border-border pb-xxs last:border-0"
                    >
                      <span className="min-w-0 flex-1 truncate text-metadata font-medium text-foreground">
                        {s.unassigned ? <span className="italic text-muted-foreground">Unassigned</span> : s.site}
                      </span>
                      <span className="text-caption text-muted-foreground">{s.host_count}h</span>
                      {(['critical', 'high'] as const)
                        .filter((sev) => s.exposure.by_severity[sev] > 0)
                        .map((sev) => (
                          <Badge key={sev} variant={SEVERITY_VARIANT[sev] as never}>
                            {s.exposure.by_severity[sev]} {sev}
                          </Badge>
                        ))}
                      <span className="text-caption text-muted-foreground">{s.recommended_action.text}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
};

export default AttentionCard;
