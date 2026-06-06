import React from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Info,
  ShieldCheck,
  TrendingUp,
} from 'lucide-react';
import { SubnetStats } from '../services/api';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Card, CardContent } from './ui/card';
import { Separator } from './ui/separator';
import { cn } from '../utils/cn';

interface RiskAssessmentWidgetProps {
  subnetStats: SubnetStats[];
}

interface RiskMetrics {
  totalSubnets: number;
  highRiskSubnets: number;
  averageUtilization: number;
  topRisks: Array<{
    subnet: string;
    risk: string;
    utilization: number;
    hosts: number;
  }>;
  recommendations: string[];
}

type Tone = 'destructive' | 'warning' | 'info' | 'success' | 'muted';

const utilizationTone = (u: number): Tone => {
  if (u >= 80) return 'destructive';
  if (u >= 60) return 'warning';
  if (u >= 40) return 'info';
  return 'success';
};

const riskTone = (level: string): Tone => {
  switch (level) {
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

const RiskIcon: React.FC<{ level: string; className?: string }> = ({ level, className }) => {
  const cls = cn('size-5', className);
  switch (level) {
    case 'critical':
      return <AlertTriangle className={cn(cls, 'text-destructive')} aria-hidden />;
    case 'high':
      return <AlertTriangle className={cn(cls, 'text-warning')} aria-hidden />;
    case 'medium':
      return <Info className={cn(cls, 'text-info')} aria-hidden />;
    case 'low':
      return <CheckCircle2 className={cn(cls, 'text-success')} aria-hidden />;
    default:
      return <ShieldCheck className={cn(cls, 'text-muted-foreground')} aria-hidden />;
  }
};

const RiskAssessmentWidget: React.FC<RiskAssessmentWidgetProps> = ({ subnetStats }) => {
  if (subnetStats.length === 0) {
    return (
      <Card>
        <CardContent className="p-md">
          <div className="mb-xs flex items-center gap-xs">
            <ShieldCheck className="size-5 text-muted-foreground" aria-hidden />
            <h3 className="text-subheading font-semibold">Risk Assessment</h3>
          </div>
          <p className="text-metadata text-muted-foreground">
            No subnet data available for risk assessment.
          </p>
        </CardContent>
      </Card>
    );
  }

  const metrics: RiskMetrics = (() => {
    const totalSubnets = subnetStats.length;
    const highRiskSubnets = subnetStats.filter(
      (s) => s.risk_level === 'high' || s.risk_level === 'critical',
    ).length;
    const totalUtilization = subnetStats.reduce(
      (sum, s) => sum + (s.utilization_percentage || 0),
      0,
    );
    const averageUtilization = totalSubnets > 0 ? totalUtilization / totalSubnets : 0;

    const topRisks = subnetStats
      .filter((s) => s.utilization_percentage && s.utilization_percentage > 0)
      .sort((a, b) => (b.utilization_percentage || 0) - (a.utilization_percentage || 0))
      .slice(0, 3)
      .map((s) => ({
        subnet: s.cidr,
        risk: s.risk_level || 'unknown',
        utilization: s.utilization_percentage || 0,
        hosts: s.host_count,
      }));

    const recommendations: string[] = [];
    if (averageUtilization > 50) {
      recommendations.push('Consider network segmentation for high-utilization subnets');
    }
    if (highRiskSubnets > 0) {
      recommendations.push(`Review security controls for ${highRiskSubnets} high-risk subnet(s)`);
    }
    const publicSubnets = subnetStats.filter((s) => !s.is_private).length;
    if (publicSubnets > 0) {
      recommendations.push(`Audit ${publicSubnets} public subnet(s) for proper access controls`);
    }
    if (recommendations.length === 0) {
      recommendations.push('Network risk profile appears acceptable');
    }

    return {
      totalSubnets,
      highRiskSubnets,
      averageUtilization,
      topRisks,
      recommendations,
    };
  })();

  const avgTone = utilizationTone(metrics.averageUtilization);

  return (
    <Card>
      <CardContent className="flex flex-col gap-md p-md">
        <div className="flex items-center gap-xs">
          <ClipboardCheck className="size-5 text-primary" aria-hidden />
          <h3 className="text-subheading font-semibold">Network Risk Assessment</h3>
        </div>

        <div>
          <div className="mb-xxs flex items-center justify-between">
            <p className="text-metadata text-muted-foreground">Average Network Utilization</p>
            <p
              className={cn(
                'text-subheading font-semibold',
                avgTone === 'destructive' && 'text-destructive',
                avgTone === 'warning' && 'text-warning',
                avgTone === 'info' && 'text-info',
                avgTone === 'success' && 'text-success',
              )}
            >
              {metrics.averageUtilization.toFixed(1)}%
            </p>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                'h-full transition-all',
                avgTone === 'destructive' && 'bg-destructive',
                avgTone === 'warning' && 'bg-warning',
                avgTone === 'info' && 'bg-info',
                avgTone === 'success' && 'bg-success',
              )}
              style={{ width: `${Math.min(metrics.averageUtilization, 100)}%` }}
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-xs">
          <Badge variant="outline">
            <ShieldCheck className="size-3" aria-hidden /> {metrics.totalSubnets} Total Subnets
          </Badge>
          <Badge variant={metrics.highRiskSubnets > 0 ? 'warning' : 'outline'}>
            <AlertTriangle className="size-3" aria-hidden /> {metrics.highRiskSubnets} High Risk
          </Badge>
          <Badge variant={avgTone === 'muted' ? 'outline' : avgTone}>
            <TrendingUp className="size-3" aria-hidden /> {metrics.averageUtilization.toFixed(0)}%
            Avg Utilization
          </Badge>
        </div>

        {metrics.topRisks.length > 0 && (
          <>
            <div>
              <h4 className="mb-xs text-metadata font-semibold">Top Risk Subnets</h4>
              <ul className="flex flex-col gap-xs">
                {metrics.topRisks.map((risk, i) => (
                  <li key={i} className="flex items-start gap-xs">
                    <RiskIcon level={risk.risk} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-xs">
                        <span className="font-mono text-metadata">{risk.subnet}</span>
                        <Badge
                          variant={utilizationTone(risk.utilization) === 'muted' ? 'outline' : utilizationTone(risk.utilization)}
                        >
                          {risk.utilization.toFixed(1)}%
                        </Badge>
                      </div>
                      <p className="text-caption text-muted-foreground">
                        {risk.hosts} discovered hosts
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
            <Separator />
          </>
        )}

        <div>
          <h4 className="mb-xs text-metadata font-semibold">Security Recommendations</h4>
          <div className="flex flex-col gap-xs">
            {metrics.recommendations.map((rec, i) => (
              <Alert
                key={i}
                variant={i === 0 && metrics.highRiskSubnets > 0 ? 'warning' : 'info'}
              >
                <AlertDescription>{rec}</AlertDescription>
              </Alert>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default RiskAssessmentWidget;
