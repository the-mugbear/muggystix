import React, { useState, useEffect } from 'react';
import { AlertOctagon, Code, Info, Loader2, ShieldAlert, Terminal, TriangleAlert } from 'lucide-react';
import { getScanCommandExplanation, CommandExplanation } from '../services/api';
import { Alert, AlertDescription } from './ui/alert';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from './ui/accordion';
import { Badge } from './ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

interface CommandExplanationProps {
  scanId: number;
}

const riskBadgeVariant = (
  riskLevel: string,
): 'destructive' | 'warning' | 'success' => {
  switch (riskLevel.toLowerCase()) {
    case 'high':
      return 'destructive';
    case 'medium':
      return 'warning';
    default:
      return 'success';
  }
};

const RiskIcon: React.FC<{ level: string; className?: string }> = ({ level, className }) => {
  switch (level.toLowerCase()) {
    case 'high':
      return <AlertOctagon className={className} aria-hidden />;
    case 'medium':
      return <TriangleAlert className={className} aria-hidden />;
    default:
      return <Info className={className} aria-hidden />;
  }
};

const categoryBadgeVariant = (
  category: string,
): 'default' | 'secondary' | 'destructive' | 'warning' | 'success' | 'outline' => {
  switch (category.toLowerCase()) {
    case 'scan techniques':
      return 'default';
    case 'service detection':
    case 'os detection':
      return 'secondary';
    case 'script scanning':
    case 'aggressive':
      return 'destructive';
    case 'timing':
    case 'performance':
      return 'warning';
    case 'stealth':
      return 'success';
    default:
      return 'outline';
  }
};

const overallRiskVariant = (
  risk: string | undefined,
): 'destructive' | 'warning' | 'success' => {
  if (!risk) return 'success';
  if (risk.includes('High')) return 'destructive';
  if (risk.includes('Medium')) return 'warning';
  return 'success';
};

const CommandExplanationComponent: React.FC<CommandExplanationProps> = ({ scanId }) => {
  const [explanation, setExplanation] = useState<CommandExplanation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await getScanCommandExplanation(scanId);
        setExplanation(data);
      } catch (err) {
        console.error('Error loading command explanation:', err);
        setError('Failed to load command explanation');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [scanId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-md">
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!explanation) return null;

  if (!explanation.has_command) {
    return (
      <Alert variant="info">
        <AlertDescription>
          <p>
            <strong>Tool:</strong> {explanation.tool}
          </p>
          <p className="mt-xxs">{explanation.message}</p>
        </AlertDescription>
      </Alert>
    );
  }

  if (explanation.message && !explanation.arguments) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-xs">
            <Terminal className="size-5" aria-hidden />
            <CardTitle>Command Information</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-sm">
          <p className="text-metadata">
            <strong>Tool:</strong> {explanation.tool}
          </p>
          <pre className="overflow-auto rounded-control bg-muted/50 p-sm font-mono text-caption break-all">
            {explanation.command}
          </pre>
          <Alert variant="warning">
            <AlertDescription>{explanation.message}</AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  // Group arguments by category.
  const argumentsByCategory: Record<string, typeof explanation.arguments> = {};
  if (explanation.arguments) {
    explanation.arguments.forEach((arg) => {
      if (!argumentsByCategory[arg.category]) argumentsByCategory[arg.category] = [];
      argumentsByCategory[arg.category]!.push(arg);
    });
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-xs">
          <Terminal className="size-5" aria-hidden />
          <CardTitle>Command Analysis</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-md">
        <div className="grid gap-xs sm:grid-cols-2">
          <p className="text-metadata text-muted-foreground">
            <strong>Tool:</strong> {explanation.tool}
          </p>
          <p className="text-metadata text-muted-foreground">
            <strong>Scan type:</strong> {explanation.scan_type}
          </p>
          <p className="text-metadata text-muted-foreground sm:col-span-2">
            <strong>Target:</strong> {explanation.target}
          </p>
        </div>

        <div className="space-y-xxs">
          <h4 className="text-subheading">Command line</h4>
          <pre className="overflow-auto rounded-control bg-muted/50 p-sm font-mono text-caption break-all whitespace-pre-wrap">
            {explanation.command}
          </pre>
        </div>

        <Alert variant="info">
          <AlertDescription>
            <strong>Summary:</strong> {explanation.summary}
          </AlertDescription>
        </Alert>
        <Alert variant={overallRiskVariant(explanation.risk_assessment)}>
          <AlertDescription>
            <strong>Risk assessment:</strong> {explanation.risk_assessment}
          </AlertDescription>
        </Alert>

        <div className="h-px bg-border" />

        <div>
          <h3 className="mb-sm flex items-center gap-xs text-subheading">
            <Code className="size-5" aria-hidden />
            Command arguments
          </h3>
          <Accordion type="multiple">
            {Object.entries(argumentsByCategory).map(([category, args]) => (
              <AccordionItem key={category} value={category}>
                <AccordionTrigger>
                  <div className="flex w-full items-center gap-xs pr-sm">
                    <ShieldAlert className="size-4 text-muted-foreground" aria-hidden />
                    <span className="flex-1 text-left">{category}</span>
                    <Badge variant={categoryBadgeVariant(category)}>
                      {args?.length || 0} argument{args?.length !== 1 ? 's' : ''}
                    </Badge>
                  </div>
                </AccordionTrigger>
                <AccordionContent>
                  <ul className="space-y-xs">
                    {args?.map((arg, index) => (
                      <li key={index} className="flex items-start gap-xs">
                        <span
                          title={`Risk Level: ${arg.risk_level}`}
                          className="mt-xxs shrink-0"
                        >
                          <Badge variant={riskBadgeVariant(arg.risk_level)}>
                            <RiskIcon level={arg.risk_level} className="size-3" />
                          </Badge>
                        </span>
                        <div className="min-w-0 flex-1 space-y-xxs">
                          <code className="inline-block rounded-control bg-muted/50 px-xs py-xxs font-mono text-caption">
                            {arg.arg}
                          </code>
                          <p className="text-metadata">{arg.description}</p>
                          {arg.examples && arg.examples.length > 0 && (
                            <p className="text-caption text-muted-foreground">
                              Examples: {arg.examples.join(', ')}
                            </p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      </CardContent>
    </Card>
  );
};

export default CommandExplanationComponent;
