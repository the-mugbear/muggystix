/**
 * Host workflow lineage panel — v3 alpha.9 (v4 alpha.17 rewrite).
 *
 * The host-centric "what's been done to this host?" view.  Three
 * sections (recons / plans / executions) each linking to the
 * per-session detail pages.  One round trip to the alpha.9 backend
 * endpoint; self-contained so HostDetail just renders ``<HostLineagePanel
 * hostId={...} />``.
 *
 * Empty when the host has no workflow attribution — explicit
 * "nothing yet" rather than hiding the panel, so the user knows
 * what's available.
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ExternalLink, Loader2 } from 'lucide-react';

import { HostLineageResponse, getHostLineage } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { cn } from '../utils/cn';

export interface HostLineagePanelProps {
  hostId: number;
}

function fmtAgo(iso?: string | null): string {
  if (!iso) return '';
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 0) return 'just now';
    const sec = Math.floor(ms / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const day = Math.floor(hr / 24);
    return `${day}d ago`;
  } catch {
    return '';
  }
}

const SectionHeader: React.FC<{ title: string; count: number; hint: string }> = ({
  title,
  count,
  hint,
}) => (
  <div className="mb-xs flex flex-wrap items-center gap-xs">
    <h3 className="text-subheading">{title}</h3>
    <Badge variant="outline">{count}</Badge>
    <span className="text-caption text-muted-foreground">{hint}</span>
  </div>
);

const LineageRow: React.FC<{
  status: string;
  statusVariant: 'success' | 'outline' | 'warning';
  body: React.ReactNode;
  trailing?: React.ReactNode;
  onOpen: () => void;
}> = ({ status, statusVariant, body, trailing, onOpen }) => (
  <div className="flex flex-wrap items-center gap-xs">
    <Badge variant={statusVariant}>{status}</Badge>
    <div className={cn('min-w-0 flex-1 truncate text-metadata text-foreground')}>{body}</div>
    {trailing}
    <Button size="sm" variant="ghost" onClick={onOpen}>
      Open
      <ExternalLink className="size-3" aria-hidden />
    </Button>
  </div>
);

export const HostLineagePanel: React.FC<HostLineagePanelProps> = ({ hostId }) => {
  const navigate = useNavigate();
  const [lineage, setLineage] = useState<HostLineageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getHostLineage(hostId)
      .then((resp) => {
        if (!cancelled) setLineage(resp);
      })
      .catch((err) => {
        if (!cancelled) setError(formatApiError(err, 'Failed to load workflow lineage.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hostId]);

  return (
    <Card className="mb-md">
      <CardHeader>
        <CardTitle>Workflow lineage</CardTitle>
        <p className="text-caption text-muted-foreground">
          What agent workflows have touched this host: recon runs that discovered it, plans that
          include it, and execution sessions that have tested it.
        </p>
      </CardHeader>
      <CardContent className="space-y-md">
        {loading && (
          <div className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Loading lineage…
          </div>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {lineage && !error && (
          <>
            <div>
              <SectionHeader
                title="Recon sessions"
                count={lineage.recon_sessions.length}
                hint="Runs that discovered this host."
              />
              {lineage.recon_sessions.length === 0 ? (
                <p className="text-metadata text-muted-foreground">
                  No agent-attributed recon sessions discovered this host.
                </p>
              ) : (
                <div className="space-y-xxs">
                  {lineage.recon_sessions.map((r) => (
                    <LineageRow
                      key={r.session_id}
                      status={r.status}
                      statusVariant={r.status === 'active' ? 'success' : 'outline'}
                      body={
                        <>
                          <strong>#{r.session_id}</strong>
                          {r.scope_name && <> · scope {r.scope_name}</>}
                          {r.generated_by_model && <> · by {r.generated_by_model}</>}
                          {r.started_by_username && <> · started by {r.started_by_username}</>}
                          {r.started_at && <> · {fmtAgo(r.started_at)}</>}
                        </>
                      }
                      onOpen={() => navigate(`/recon/runs/${r.session_id}`)}
                    />
                  ))}
                </div>
              )}
            </div>

            <div>
              <SectionHeader
                title="Plan entries"
                count={lineage.plan_entries.length}
                hint="Plans that include this host."
              />
              {lineage.plan_entries.length === 0 ? (
                <p className="text-metadata text-muted-foreground">
                  No plan includes this host yet.
                </p>
              ) : (
                <div className="space-y-xxs">
                  {lineage.plan_entries.map((p) => (
                    <LineageRow
                      key={`${p.plan_id}-${p.entry_id}`}
                      status={p.status}
                      statusVariant="outline"
                      body={
                        <>
                          <strong>#{p.plan_id}</strong> v{p.version} · {p.title}
                          <span className="ml-xxs text-caption text-muted-foreground">
                            entry {p.entry_status}
                            {p.generated_by_model && ` · by ${p.generated_by_model}`}
                          </span>
                        </>
                      }
                      onOpen={() => navigate(`/test-plans/${p.plan_id}`)}
                    />
                  ))}
                </div>
              )}
            </div>

            <div>
              <SectionHeader
                title="Execution sessions"
                count={lineage.execution_sessions.length}
                hint="Runs that produced per-test results against this host."
              />
              {lineage.execution_sessions.length === 0 ? (
                <p className="text-metadata text-muted-foreground">
                  No execution session has tested this host yet.
                </p>
              ) : (
                <div className="space-y-xxs">
                  {lineage.execution_sessions.map((e) => (
                    <LineageRow
                      key={e.execution_session_id}
                      status={e.status}
                      statusVariant={e.status === 'active' ? 'success' : 'outline'}
                      body={
                        <>
                          <strong>#{e.execution_session_id}</strong> · plan #{e.plan_id}
                          <span className="ml-xxs text-caption text-muted-foreground">
                            · {e.test_count} test{e.test_count === 1 ? '' : 's'}
                            {e.finding_count > 0 &&
                              ` · ${e.finding_count} finding${e.finding_count === 1 ? '' : 's'}`}
                            {e.generated_by_model && ` · by ${e.generated_by_model}`}
                            {e.started_by_username && ` · started by ${e.started_by_username}`}
                          </span>
                        </>
                      }
                      trailing={
                        e.finding_count > 0 ? (
                          <Badge variant="warning">
                            {e.finding_count} finding{e.finding_count === 1 ? '' : 's'}
                          </Badge>
                        ) : undefined
                      }
                      onOpen={() => navigate(`/executions/${e.execution_session_id}`)}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
};

export default HostLineagePanel;
