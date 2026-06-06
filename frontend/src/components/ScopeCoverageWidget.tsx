import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { getScopeCoverage, ScopeCoverageSummary } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription } from './ui/alert';
import { Separator } from './ui/separator';

type BadgeTone = 'success' | 'warning' | 'destructive' | 'muted';

const ScopeCoverageWidget: React.FC = () => {
  const [coverage, setCoverage] = useState<ScopeCoverageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    getScopeCoverage()
      .then((d) => {
        if (!cancelled) {
          setCoverage(d);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(formatApiError(err, 'Failed to load scope coverage.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const coverageTone: BadgeTone = !coverage
    ? 'muted'
    : coverage.coverage_percentage >= 90
      ? 'success'
      : coverage.coverage_percentage >= 50
        ? 'warning'
        : coverage.coverage_percentage > 0
          ? 'destructive'
          : 'muted';

  if (loading) {
    return (
      <Card className="h-full">
        <CardContent className="flex min-h-48 items-center justify-center">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
        </CardContent>
      </Card>
    );
  }
  if (error || !coverage) {
    return (
      <Card className="h-full">
        <CardContent className="p-md">
          <Alert variant="destructive">
            <AlertDescription>{error || 'Scope coverage is unavailable.'}</AlertDescription>
          </Alert>
          <Button variant="outline" size="sm" onClick={() => navigate('/scopes')} className="mt-sm">
            Manage scopes
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex items-center justify-between gap-xs">
          <p className="text-subheading font-semibold">Scope Coverage</p>
          <Badge variant={coverageTone}>{coverage.coverage_percentage.toFixed(1)}% covered</Badge>
        </div>

        <div className="mb-sm grid grid-cols-2 gap-sm">
          <div className="text-center">
            <p className="text-page-title font-semibold text-primary">{coverage.total_hosts}</p>
            <p className="text-caption text-muted-foreground">Total Hosts</p>
          </div>
          <div className="text-center">
            <p
              className={`text-page-title font-semibold ${coverage.out_of_scope_hosts ? 'text-destructive' : 'text-success'}`}
            >
              {coverage.out_of_scope_hosts}
            </p>
            <p className="text-caption text-muted-foreground">Out of Scope</p>
          </div>
        </div>

        {coverage.out_of_scope_hosts > 0 ? (
          <div>
            <Separator className="mb-sm" />
            <p className="mb-xs text-caption font-semibold text-foreground">
              Recently seen outside configured scopes
            </p>
            <ul className="flex max-h-56 flex-col overflow-auto">
              {coverage.recent_out_of_scope_hosts.map((host) => {
                const lastSeen = host.last_seen ? new Date(host.last_seen).toLocaleString() : 'Unknown';
                return (
                  <li key={`dash-oos-${host.host_id}`}>
                    <button
                      type="button"
                      onClick={() => navigate(`/hosts/${host.host_id}`)}
                      className="flex w-full flex-col px-xs py-xs text-left hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control"
                    >
                      <p className="font-mono text-metadata font-medium text-foreground">{host.ip_address}</p>
                      <p className="text-caption text-muted-foreground">
                        {host.hostname || 'Unknown host'} · Last seen {lastSeen}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
            <Button
              variant="link"
              size="sm"
              className="mt-xs"
              onClick={() => navigate('/hosts?out_of_scope=true')}
            >
              View all out-of-scope hosts
            </Button>
          </div>
        ) : coverage.has_scope_configuration ? (
          <Alert variant="success">
            <AlertDescription>All discovered hosts map to your defined scopes.</AlertDescription>
          </Alert>
        ) : (
          <Alert variant="info">
            <AlertDescription>
              No subnet scopes configured yet. Upload a subnet file to track out-of-scope hosts.
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
};

export default ScopeCoverageWidget;
