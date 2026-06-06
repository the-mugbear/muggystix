import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Bug,
  ChevronDown,
  ChevronUp,
  Loader2,
  RefreshCw,
  ShieldAlert,
  SquareArrowOutUpRight,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useProject } from '../contexts/ProjectContext';
import api, { getCurrentProjectId } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Separator } from './ui/separator';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from '../utils/cn';

interface HighRiskHost {
  host_id: number;
  ip_address: string;
  hostname: string;
  os_name: string;
  risk_score: number;
  risk_level: string;
  vulnerability_count: number;
  critical_vulnerabilities: number;
  high_vulnerabilities: number;
  risk_summary: string;
  top_vulnerabilities: Array<{
    cve_id: string;
    title: string;
    severity: string;
    cvss_score: number;
    exploitability: string;
  }>;
  critical_findings: Array<{
    finding_type: string;
    title: string;
    severity: string;
    risk_score: number;
  }>;
  recommendations: string[];
}

type Tone = 'destructive' | 'warning' | 'info' | 'success' | 'muted';

const severityTone = (s: string): Tone => {
  switch (s.toLowerCase()) {
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

const CriticalFindingsWidget: React.FC = () => {
  const [highRiskHosts, setHighRiskHosts] = useState<HighRiskHost[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Distinguish "endpoint missing" (404) from generic failure so the
  // copy can guide the operator differently — pre-fix the routing was
  // a brittle `error.includes('404')` substring match on the raw
  // err.message (audit CRIT-9).
  const [errorIs404, setErrorIs404] = useState(false);
  const [expandedHost, setExpandedHost] = useState<number | null>(null);
  const [emptyState, setEmptyState] = useState<any>(null);
  const { token } = useAuth();
  // PRF·M2 — re-fetch on project switch, not just on token change.
  const { currentProject } = useProject();
  const navigate = useNavigate();

  const fetchHighRiskHosts = React.useCallback(async () => {
    if (!token) {
      setError('Authentication required');
      setErrorIs404(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    setErrorIs404(false);
    try {
      const projectId = getCurrentProjectId();
      if (!projectId) {
        setError('No project selected');
        setLoading(false);
        return;
      }
      const { data } = await api.get(`/projects/${projectId}/risk/hosts/high-risk`, {
        params: { limit: 10, min_risk_score: 70 },
      });
      if (data.hosts !== undefined) {
        setHighRiskHosts(data.hosts);
        setEmptyState(data.empty_state);
      } else {
        setHighRiskHosts(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      setErrorIs404(status === 404);
      setError(formatApiError(err, 'Failed to load critical findings.'));
      console.error('Error fetching high-risk hosts:', err);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (token && currentProject) fetchHighRiskHosts();
  }, [token, currentProject?.id, fetchHighRiskHosts]);

  if (loading) {
    return (
      <Card>
        <CardContent className="p-md">
          <h3 className="mb-sm text-subheading font-semibold">Critical Security Findings</h3>
          <div className="flex min-h-48 items-center justify-center">
            <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="p-md">
          <h3 className="mb-sm text-subheading font-semibold">Critical Security Findings</h3>
          <Alert variant="destructive">
            <AlertDescription>
              <strong>Failed to Load Critical Findings</strong>
              <p className="mt-xxs break-words">
                {errorIs404
                  ? 'Risk assessment service is not available. Contact your administrator.'
                  : error}
              </p>
              {!errorIs404 && (
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-xs"
                  onClick={fetchHighRiskHosts}
                >
                  <RefreshCw className="size-3.5" aria-hidden />
                  Retry
                </Button>
              )}
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  const criticalHosts = highRiskHosts.filter((h) => h.risk_level === 'critical');
  const highRiskHostsFiltered = highRiskHosts.filter((h) => h.risk_level === 'high');

  return (
    <Card>
      <CardContent className="p-md">
        <div className="mb-sm flex items-center justify-between gap-sm">
          <h3 className="text-subheading font-semibold">Critical Security Findings</h3>
          {highRiskHosts.length > 0 && (
            <Badge variant="destructive">
              {criticalHosts.length + highRiskHostsFiltered.length} hosts need attention
            </Badge>
          )}
        </div>

        {highRiskHosts.length === 0 ? (
          <Alert variant={emptyState?.is_positive ? 'success' : 'info'}>
            <AlertDescription className="flex flex-wrap items-start justify-between gap-sm">
              <span>
                <strong>{emptyState?.title || 'No Critical Security Findings'}</strong>
                <br />
                {emptyState?.message ||
                  'No critical security findings detected. All hosts appear to be secure.'}
              </span>
              {emptyState?.action_text && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(emptyState.action_url)}
                    >
                      {emptyState.action_text}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    Go to the hosts page where you can select individual hosts and run security
                    assessments to analyze vulnerabilities, exposed services, and configuration
                    risks.
                  </TooltipContent>
                </Tooltip>
              )}
            </AlertDescription>
          </Alert>
        ) : (
          <ul className="flex flex-col gap-xs">
            {highRiskHosts.map((host) => {
              const expanded = expandedHost === host.host_id;
              const critical = host.risk_level === 'critical';
              return (
                <li key={host.host_id}>
                  {/* v2.43.0 — UX review #2: dropped role="link"/tabIndex/
                      onClick on the wrapping <div>.  The host title now
                      carries an explicit <Link>; the expand + open-detail
                      buttons inside stay as independent <Button>s. */}
                  <div
                    className={cn(
                      'flex items-start gap-xs rounded-control border p-sm transition-colors',
                      critical
                        ? 'border-destructive/40 bg-destructive/10 hover:bg-destructive/20'
                        : 'border-warning/40 bg-warning/10 hover:bg-warning/20',
                    )}
                  >
                    {critical ? (
                      <ShieldAlert className="mt-xxs size-5 shrink-0 text-destructive" aria-hidden />
                    ) : (
                      <AlertTriangle className="mt-xxs size-5 shrink-0 text-warning" aria-hidden />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="mb-xxs flex flex-wrap items-center gap-xs">
                        <Link
                          to={`/hosts/${host.host_id}`}
                          className="font-semibold text-inherit no-underline hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                          aria-label={`Open host ${host.ip_address}${host.hostname ? ` (${host.hostname})` : ''}`}
                        >
                          {host.ip_address}
                        </Link>
                        <span className="text-metadata text-muted-foreground">
                          {host.hostname || 'Unknown'}
                        </span>
                        <Badge variant={severityTone(host.risk_level)}>
                          {host.risk_level.toUpperCase()}
                        </Badge>
                      </div>
                      <p className="text-metadata">
                        Risk Score: {host.risk_score.toFixed(1)} | {host.vulnerability_count}{' '}
                        vulnerabilities
                      </p>
                      <p className="text-caption text-muted-foreground">
                        {host.os_name} • {host.critical_vulnerabilities} critical,{' '}
                        {host.high_vulnerabilities} high
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-xxs">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={(e) => {
                          e.stopPropagation();
                          setExpandedHost(expanded ? null : host.host_id);
                        }}
                        aria-label={expanded ? 'Collapse host details' : 'Expand host details'}
                        aria-expanded={expanded}
                      >
                        {expanded ? (
                          <ChevronUp className="size-4" aria-hidden />
                        ) : (
                          <ChevronDown className="size-4" aria-hidden />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/hosts/${host.host_id}`);
                        }}
                        aria-label="Open host details"
                      >
                        <SquareArrowOutUpRight className="size-4" aria-hidden />
                      </Button>
                    </div>
                  </div>
                  {expanded && (
                    <div className="ml-lg mr-sm mb-sm mt-xxs rounded-control border border-border bg-card p-sm">
                      <p className="mb-sm text-metadata italic">{host.risk_summary}</p>

                      {host.top_vulnerabilities.length > 0 && (
                        <div className="mb-sm">
                          <p className="mb-xxs text-metadata font-semibold">Top Vulnerabilities:</p>
                          {host.top_vulnerabilities.slice(0, 3).map((v, i) => (
                            <div key={i} className="mb-xxs flex items-center gap-xs">
                              <Bug
                                className={cn(
                                  'size-4',
                                  v.exploitability?.toLowerCase().includes('high')
                                    ? 'text-destructive'
                                    : 'text-warning',
                                )}
                                aria-hidden
                              />
                              <div className="min-w-0 flex-1">
                                <p className="text-metadata font-medium">{v.cve_id}</p>
                                <p className="text-caption text-muted-foreground">
                                  CVSS: {v.cvss_score} | {v.exploitability} exploitability
                                </p>
                              </div>
                              <Badge variant={severityTone(v.severity)}>{v.severity}</Badge>
                            </div>
                          ))}
                        </div>
                      )}

                      {host.critical_findings.length > 0 && (
                        <div className="mb-sm">
                          <p className="mb-xxs text-metadata font-semibold">Security Issues:</p>
                          {host.critical_findings.slice(0, 2).map((f, i) => (
                            <div key={i} className="mb-xxs flex items-start gap-xs">
                              <AlertTriangle className="mt-xxs size-4 shrink-0 text-warning" aria-hidden />
                              <div className="min-w-0 flex-1">
                                <p className="text-metadata font-medium">{f.title}</p>
                                <p className="text-caption text-muted-foreground">
                                  {f.finding_type} • Risk: {f.risk_score.toFixed(1)}
                                </p>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}

                      {host.recommendations && host.recommendations.length > 0 && (
                        <div>
                          <p className="mb-xxs text-metadata font-semibold">Immediate Actions:</p>
                          {host.recommendations.slice(0, 2).map((r, i) => (
                            <p key={i} className="mb-xxs pl-md text-metadata">
                              <span className="text-primary">→ </span>
                              {r}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  <Separator className="my-xxs last:hidden" />
                </li>
              );
            })}
          </ul>
        )}

        {highRiskHosts.length > 0 && (
          <p className="mt-sm text-center text-caption text-muted-foreground">
            Click any host for detailed security analysis
          </p>
        )}
      </CardContent>
    </Card>
  );
};

export default CriticalFindingsWidget;
