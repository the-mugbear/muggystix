import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import api, { getCurrentProjectId } from '../services/api';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from '../utils/cn';

interface RiskSummary {
  has_data?: boolean;
  empty_state?: {
    title?: string;
    message: string;
    is_positive: boolean;
    action_text?: string;
    action_url?: string;
  };
  total_hosts: number;
  assessed_hosts: number;
  unassessed_hosts: number;
  risk_distribution: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
  };
  risk_percentages: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
  };
  top_risk_hosts: Array<{
    host_id: number;
    ip_address: string;
    hostname: string;
    risk_score: number;
    risk_level: string;
    vulnerability_count: number;
    last_assessment: string;
  }>;
}

type Tone = 'destructive' | 'warning' | 'info' | 'success' | 'muted';

const levelTone = (level: string): Tone => {
  switch (level.toLowerCase()) {
    case 'critical':
      return 'destructive';
    case 'high':
      return 'warning';
    case 'medium':
      return 'info';
    case 'low':
      return 'success';
    default:
      return 'muted';
  }
};

const levelBarClass = (level: string): string => {
  switch (level.toLowerCase()) {
    case 'critical':
      return 'bg-destructive';
    case 'high':
      return 'bg-warning';
    case 'medium':
      return 'bg-info';
    case 'low':
      return 'bg-success';
    default:
      return 'bg-muted-foreground';
  }
};

const RiskLevelIcon: React.FC<{ level: string }> = ({ level }) => {
  switch (level.toLowerCase()) {
    case 'critical':
      return <ShieldAlert className="size-4 text-destructive" aria-hidden />;
    case 'high':
      return <AlertTriangle className="size-4 text-warning" aria-hidden />;
    case 'medium':
      return <Info className="size-4 text-info" aria-hidden />;
    case 'low':
      return <CheckCircle2 className="size-4 text-success" aria-hidden />;
    default:
      return <ShieldCheck className="size-4 text-muted-foreground" aria-hidden />;
  }
};

const RiskSummaryWidget: React.FC = () => {
  const [riskData, setRiskData] = useState<RiskSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { token } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (token) fetchRiskSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const fetchRiskSummary = async () => {
    if (!token) {
      setError('Authentication required');
      setLoading(false);
      return;
    }
    try {
      const projectId = getCurrentProjectId();
      if (!projectId) {
        setError('No project selected');
        setLoading(false);
        return;
      }
      const { data } = await api.get(`/projects/${projectId}/risk/hosts/risk-summary`);
      setRiskData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load risk summary');
      console.error('Error fetching risk summary:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="flex min-h-48 items-center justify-center p-md">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="p-md">
          <Alert variant="destructive">
            <AlertDescription>
              <strong>Failed to Load Risk Data</strong>
              <br />
              {error.includes('404') || error.includes('Not Found')
                ? 'Risk assessment service is not available. Contact your administrator.'
                : 'Unable to connect to the risk assessment service. Please try again later.'}
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  if (!riskData) return null;

  if (!riskData.has_data && riskData.empty_state) {
    const { empty_state } = riskData;
    return (
      <Card>
        <CardContent className="p-md">
          <h3 className="mb-sm text-subheading font-semibold">Security Risk Overview</h3>
          <Alert variant={empty_state.is_positive ? 'success' : 'info'}>
            <AlertDescription className="flex flex-wrap items-start justify-between gap-sm">
              <span>
                <strong>{empty_state.title}</strong>
                <br />
                {empty_state.message}
              </span>
              {empty_state.action_text && empty_state.action_url && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(empty_state.action_url!)}
                    >
                      {empty_state.action_text}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Upload new scans to analyze more hosts.</TooltipContent>
                </Tooltip>
              )}
            </AlertDescription>
          </Alert>

          <div className="mt-md grid grid-cols-2 gap-sm">
            <div className="text-center">
              <p className="text-page-title font-semibold text-primary">{riskData.total_hosts || 0}</p>
              <p className="text-metadata text-muted-foreground">Total Hosts</p>
            </div>
            <div className="text-center">
              <p className="text-page-title font-semibold text-muted-foreground">
                {riskData.assessed_hosts || 0}
              </p>
              <p className="text-metadata text-muted-foreground">Assessed</p>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  const criticalAndHighCount =
    riskData.risk_distribution.critical + riskData.risk_distribution.high;

  return (
    <Card>
      <CardContent className="flex flex-col gap-md p-md">
        <h3 className="text-subheading font-semibold">Security Risk Overview</h3>

        <div className="grid grid-cols-2 gap-sm sm:grid-cols-4">
          <div className="text-center">
            <p className="text-page-title font-bold text-destructive">{criticalAndHighCount}</p>
            <p className="text-metadata text-muted-foreground">Immediate Attention</p>
          </div>
          <div className="text-center">
            <p className="text-page-title font-semibold text-primary">{riskData.total_hosts}</p>
            <p className="text-metadata text-muted-foreground">Total Hosts</p>
          </div>
          <div className="text-center">
            <p className="text-page-title font-semibold text-success">{riskData.assessed_hosts}</p>
            <p className="text-metadata text-muted-foreground">Assessed</p>
          </div>
          <div className="text-center">
            <p className="text-page-title font-semibold text-warning">
              {riskData.unassessed_hosts}
            </p>
            <p className="text-metadata text-muted-foreground">Unassessed</p>
          </div>
        </div>

        <div>
          <h4 className="mb-xs text-metadata font-semibold">Risk Distribution</h4>
          <div className="flex flex-col gap-xs">
            {Object.entries(riskData.risk_distribution).map(([level, count]) => {
              const percentage =
                riskData.risk_percentages[level as keyof typeof riskData.risk_percentages];
              return (
                <div key={level}>
                  <div className="mb-xxs flex items-center justify-between">
                    <div className="flex items-center gap-xs">
                      <RiskLevelIcon level={level} />
                      <span className="text-metadata capitalize">{level}</span>
                    </div>
                    <span className="text-metadata text-muted-foreground">
                      {count} hosts ({percentage}%)
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn('h-full transition-all', levelBarClass(level))}
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {riskData.top_risk_hosts.length > 0 && (
          <div>
            <h4 className="mb-xs text-metadata font-semibold">Highest Risk Hosts</h4>
            <div className="flex flex-col gap-xs">
              {riskData.top_risk_hosts.map((host) => (
                <button
                  key={host.host_id}
                  type="button"
                  onClick={() => navigate(`/hosts/${host.host_id}`)}
                  className="flex items-center justify-between rounded-control border border-border p-sm text-left hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div>
                    <p className="text-metadata font-medium">{host.ip_address}</p>
                    <p className="text-caption text-muted-foreground">
                      {host.hostname || 'Unknown hostname'}
                    </p>
                  </div>
                  <div className="text-right">
                    <Badge variant={levelTone(host.risk_level)}>
                      {host.risk_level.toUpperCase()}
                    </Badge>
                    <p className="mt-xxs text-caption text-muted-foreground">
                      Score: {host.risk_score.toFixed(1)} | {host.vulnerability_count} CVEs
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {riskData.unassessed_hosts > 0 && (
          <Alert variant="info">
            <AlertDescription>
              {riskData.unassessed_hosts} hosts have not been assessed for security risks. Run risk
              assessments for complete coverage.
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
};

export default RiskSummaryWidget;
