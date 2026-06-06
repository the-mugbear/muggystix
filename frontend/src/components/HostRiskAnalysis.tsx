import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  Bug,
  ClipboardCheck,
  Info,
  Loader2,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import api, { getCurrentProjectId } from '../services/api';
import { asAxiosError } from '../utils/apiErrors';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from './ui/accordion';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Separator } from './ui/separator';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from '../utils/cn';

interface HostRiskData {
  host: {
    id: number;
    ip_address: string;
    hostname: string;
    os_name: string;
    os_family: string;
    state: string;
  };
  risk_assessment: {
    risk_score: number;
    risk_level: string;
    vulnerability_count: number;
    critical_vulnerabilities: number;
    high_vulnerabilities: number;
    exposed_services: number;
    dangerous_ports: number;
    attack_surface_score: number;
    patch_urgency_score: number;
    exposure_risk_score: number;
    configuration_risk_score: number;
    risk_summary: string;
    assessment_date: string;
    last_updated: string;
  };
  vulnerabilities: {
    Critical: Array<{
      cve_id: string;
      title: string;
      description: string;
      cvss_score: number;
      severity: string;
      exploitability: string;
      affected_software: string;
      patch_available: boolean;
      patch_url?: string;
    }>;
    High: Array<any>;
    Medium: Array<any>;
    Low: Array<any>;
  };
  security_findings: {
    Critical: Array<{
      finding_type: string;
      title: string;
      description: string;
      severity: string;
      risk_score: number;
      evidence: string;
      recommendation: string;
    }>;
    High: Array<any>;
    Medium: Array<any>;
    Low: Array<any>;
  };
  recommendations: string[];
  summary_stats: {
    total_vulnerabilities: number;
    critical_count: number;
    high_count: number;
    medium_count: number;
    low_count: number;
    total_findings: number;
    critical_findings: number;
    high_findings: number;
  };
}

interface HostRiskAnalysisProps {
  hostId: number;
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

const levelRing = (level: string): string => {
  switch (level.toLowerCase()) {
    case 'critical':
      return 'border-destructive text-destructive';
    case 'high':
      return 'border-warning text-warning';
    case 'medium':
      return 'border-info text-info';
    case 'low':
      return 'border-success text-success';
    default:
      return 'border-border text-muted-foreground';
  }
};

const LevelIcon: React.FC<{ level: string }> = ({ level }) => {
  switch (level.toLowerCase()) {
    case 'critical':
      return <ShieldAlert className="size-4 text-destructive" aria-hidden />;
    case 'high':
      return <AlertTriangle className="size-4 text-warning" aria-hidden />;
    case 'medium':
      return <Info className="size-4 text-info" aria-hidden />;
    case 'low':
      return <ShieldCheck className="size-4 text-success" aria-hidden />;
    default:
      return <Info className="size-4 text-muted-foreground" aria-hidden />;
  }
};

const HostRiskAnalysis: React.FC<HostRiskAnalysisProps> = ({ hostId }) => {
  const [riskData, setRiskData] = useState<HostRiskData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAssessing, setIsAssessing] = useState(false);
  const [assessmentProgress, setAssessmentProgress] = useState<string>('');
  const { token } = useAuth();

  const fetchRiskAssessment = async () => {
    if (!token) return;
    const projectId = getCurrentProjectId();
    if (!projectId) return;
    try {
      const { data } = await api.get(
        `/projects/${projectId}/risk/hosts/${hostId}/risk-assessment`,
      );
      setRiskData(data);
      setError(null);
    } catch (err: unknown) {
      if (asAxiosError(err).response?.status === 404) {
        setError('No risk assessment found for this host');
      } else {
        console.error('Error fetching risk assessment:', err);
        setError('Failed to load risk assessment data');
      }
    } finally {
      setLoading(false);
    }
  };

  const triggerRiskAssessment = async () => {
    if (!token) return;
    setIsAssessing(true);
    setError(null);
    const projectId = getCurrentProjectId();
    if (!projectId) {
      setError('No project selected');
      setIsAssessing(false);
      return;
    }
    try {
      setAssessmentProgress('Running security assessment...');
      await api.post(`/projects/${projectId}/risk/hosts/${hostId}/assess-risk`);
      setAssessmentProgress('Loading results...');
      await fetchRiskAssessment();
      setAssessmentProgress('Assessment completed successfully!');
      const clearTimer = setTimeout(() => setAssessmentProgress(''), 2000);
      void clearTimer;
    } catch (err) {
      console.error('Error triggering risk assessment:', err);
      setError(
        'Failed to perform risk assessment. The system analyzes your host data including open ports, service versions, and known vulnerabilities to generate a comprehensive security report.',
      );
      setAssessmentProgress('');
    } finally {
      setIsAssessing(false);
    }
  };

  useEffect(() => {
    fetchRiskAssessment();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hostId, token]);

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
          {isAssessing && assessmentProgress && (
            <Alert variant="info" className="mb-sm">
              <AlertDescription className="flex items-start gap-xs">
                <Loader2 className="mt-xxs size-4 shrink-0 animate-spin" aria-hidden />
                <div>
                  <p className="font-medium">Risk Assessment in Progress</p>
                  <p className="text-caption text-muted-foreground">{assessmentProgress}</p>
                  <p className="mt-xxs text-caption">
                    Using: Port scan data • Service versions • Pattern matching • Configuration
                    analysis
                  </p>
                </div>
              </AlertDescription>
            </Alert>
          )}
          <Alert variant="warning">
            <AlertDescription className="flex flex-wrap items-start justify-between gap-sm">
              <span>{error}</span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={triggerRiskAssessment}
                    disabled={isAssessing}
                  >
                    {isAssessing ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <ClipboardCheck className="size-4" aria-hidden />
                    )}
                    {isAssessing ? 'Assessing…' : 'Run Assessment'}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  Analyze this host for security vulnerabilities, exposed services, and
                  configuration risks. This will scan open ports, detect software versions, and
                  check for known CVEs.
                </TooltipContent>
              </Tooltip>
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  if (!riskData) return null;

  const { risk_assessment, vulnerabilities, security_findings, recommendations, summary_stats } =
    riskData;

  return (
    <div>
      {isAssessing && assessmentProgress && (
        <Alert variant="info" className="mb-sm">
          <AlertDescription className="flex items-start gap-xs">
            <Loader2 className="mt-xxs size-4 shrink-0 animate-spin" aria-hidden />
            <div>
              <p className="font-medium">Risk Assessment in Progress</p>
              <p className="text-caption text-muted-foreground">{assessmentProgress}</p>
              <p className="mt-xxs text-caption">
                Data sources: Network scan results • Service fingerprints • Hardcoded vulnerability
                patterns • Configuration analysis
              </p>
            </div>
          </AlertDescription>
        </Alert>
      )}

      <Card className="mb-md">
        <CardContent className="p-md">
          <div className="mb-md flex flex-wrap items-center justify-between gap-sm">
            <div className="flex items-center gap-xs">
              <ShieldCheck className="size-5 text-primary" aria-hidden />
              <h3 className="text-subheading font-semibold">Security Risk Assessment</h3>
            </div>
            <div className="flex flex-wrap gap-xs">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={fetchRiskAssessment}
                    disabled={loading}
                  >
                    <RefreshCw className="size-4" aria-hidden /> Refresh
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Refresh Assessment</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button size="sm" onClick={triggerRiskAssessment} disabled={isAssessing}>
                    {isAssessing ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <ClipboardCheck className="size-4" aria-hidden />
                    )}
                    {isAssessing ? 'Assessing…' : 'Re-assess'}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  Run a fresh security assessment. This will re-scan for vulnerabilities, check
                  current service configurations, and update the risk score based on latest
                  findings.
                </TooltipContent>
              </Tooltip>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-md md:grid-cols-4">
            <div className="flex flex-col items-center md:col-span-1">
              <div
                className={cn(
                  'flex size-20 items-center justify-center rounded-full border-4 font-semibold',
                  levelRing(risk_assessment.risk_level),
                )}
              >
                <span className="text-section-title">{Math.round(risk_assessment.risk_score)}</span>
              </div>
              <Badge variant={levelTone(risk_assessment.risk_level)} className="mt-xs">
                <LevelIcon level={risk_assessment.risk_level} />
                {risk_assessment.risk_level.toUpperCase()}
              </Badge>
            </div>

            <div className="grid grid-cols-2 gap-sm md:col-span-3 md:grid-cols-4">
              {[
                { value: summary_stats.critical_count, label: 'Critical CVEs', tone: 'text-destructive' },
                { value: summary_stats.high_count, label: 'High CVEs', tone: 'text-warning' },
                {
                  value: risk_assessment.exposed_services,
                  label: 'Exposed Services',
                  tone: 'text-primary',
                },
                {
                  value: risk_assessment.dangerous_ports,
                  label: 'Dangerous Ports',
                  tone: 'text-destructive',
                },
              ].map((m) => (
                <div key={m.label} className="text-center">
                  <p className={cn('text-section-title font-semibold', m.tone)}>{m.value}</p>
                  <p className="text-metadata text-muted-foreground">{m.label}</p>
                </div>
              ))}
            </div>
          </div>

          {risk_assessment.risk_summary && (
            <Alert variant="info" className="mt-sm">
              <AlertDescription>{risk_assessment.risk_summary}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {vulnerabilities.Critical.length > 0 && (
        <Card className="mb-md">
          <Accordion type="single" collapsible defaultValue="cve">
            <AccordionItem value="cve" className="border-b-0">
              <AccordionTrigger className="px-md">
                <div className="flex items-center gap-xs">
                  <ShieldAlert className="size-5 text-destructive" aria-hidden />
                  <span>Critical Vulnerabilities ({vulnerabilities.Critical.length})</span>
                </div>
              </AccordionTrigger>
              <AccordionContent className="px-md">
                <ul className="flex flex-col gap-xs">
                  {vulnerabilities.Critical.map((v, i) => (
                    <React.Fragment key={i}>
                      <li className="flex items-start gap-xs">
                        <Bug className="mt-xxs size-4 shrink-0 text-destructive" aria-hidden />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-xs">
                            <span className="font-semibold">{v.cve_id}</span>
                            <Badge variant="destructive">CVSS: {v.cvss_score}</Badge>
                            {v.patch_available && (
                              <Badge variant="success">Patch Available</Badge>
                            )}
                          </div>
                          <p className="mt-xxs text-metadata">{v.title}</p>
                          <p className="text-caption text-muted-foreground">
                            Affected: {v.affected_software} | Exploitability: {v.exploitability}
                          </p>
                        </div>
                      </li>
                      {i < vulnerabilities.Critical.length - 1 && <Separator />}
                    </React.Fragment>
                  ))}
                </ul>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </Card>
      )}

      {security_findings.Critical.length > 0 && (
        <Card className="mb-md">
          <Accordion type="single" collapsible>
            <AccordionItem value="findings" className="border-b-0">
              <AccordionTrigger className="px-md">
                <div className="flex items-center gap-xs">
                  <AlertTriangle className="size-5 text-warning" aria-hidden />
                  <span>Critical Security Findings ({security_findings.Critical.length})</span>
                </div>
              </AccordionTrigger>
              <AccordionContent className="px-md">
                <ul className="flex flex-col gap-xs">
                  {security_findings.Critical.map((f, i) => (
                    <React.Fragment key={i}>
                      <li className="flex items-start gap-xs">
                        <AlertTriangle
                          className="mt-xxs size-4 shrink-0 text-warning"
                          aria-hidden
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-xs">
                            <span className="font-semibold">{f.title}</span>
                            <Badge variant="warning">Risk: {f.risk_score.toFixed(1)}</Badge>
                          </div>
                          <p className="mt-xxs text-metadata">{f.description}</p>
                          <p className="text-caption text-muted-foreground">Type: {f.finding_type}</p>
                          {f.recommendation && (
                            <Alert variant="info" className="mt-xs">
                              <AlertDescription>
                                <strong>Recommendation:</strong> {f.recommendation}
                              </AlertDescription>
                            </Alert>
                          )}
                        </div>
                      </li>
                      {i < security_findings.Critical.length - 1 && <Separator />}
                    </React.Fragment>
                  ))}
                </ul>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </Card>
      )}

      {recommendations.length > 0 && (
        <Card>
          <CardContent className="p-md">
            <div className="mb-xs flex items-center gap-xs">
              <TrendingUp className="size-5 text-primary" aria-hidden />
              <h3 className="text-subheading font-semibold">Security Recommendations</h3>
            </div>
            <ul className="flex flex-col gap-xs">
              {recommendations.map((r, i) => (
                <li key={i} className="text-metadata">
                  <strong>{i + 1}.</strong> {r}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <p className="mt-sm text-center text-caption text-muted-foreground">
        Last assessed: {new Date(risk_assessment.assessment_date).toLocaleString()}
      </p>
    </div>
  );
};

export default HostRiskAnalysis;
