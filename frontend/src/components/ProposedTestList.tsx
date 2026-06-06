import React, { useState } from 'react';
import { Check, Copy, ExternalLink } from 'lucide-react';
import type { ProposedTestItem, ProposedTestObject } from '../services/api';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

function isSafeUrl(url: string): boolean {
  return /^https?:\/\//i.test(url.trim());
}

export function isStructuredTest(item: ProposedTestItem): item is ProposedTestObject {
  return typeof item === 'object' && item !== null && 'tool' in item;
}

export function getTestChipLabel(item: ProposedTestItem): string {
  return isStructuredTest(item) ? item.tool : item;
}

export function resolveCommand(command: string, hostIp: string | undefined): string {
  if (!hostIp) return command;
  return command.replace(/\{ip\}/g, hostIp);
}

function CopyCommandButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => {
        // clipboard denied — ignore gracefully
      },
    );
  };
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleCopy}
          aria-label="Copy command to clipboard"
          className="mr-xxs mt-xxs shrink-0"
        >
          {copied ? (
            <Check className="size-4 text-success" aria-hidden />
          ) : (
            <Copy className="size-4" aria-hidden />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{copied ? 'Copied' : 'Copy command'}</TooltipContent>
    </Tooltip>
  );
}

interface StructuredTestCardProps {
  test: ProposedTestObject;
  hostIp?: string;
}

export function StructuredTestCard({ test, hostIp }: StructuredTestCardProps) {
  const resolvedCmd = test.command ? resolveCommand(test.command, hostIp) : null;

  return (
    <Card>
      <CardContent className="p-sm">
        <div className="mb-xxs flex flex-wrap items-center gap-xs">
          <Badge>{test.tool}</Badge>
          <p className="min-w-0 flex-1 break-words font-semibold">{test.description}</p>
        </div>
        {resolvedCmd && (
          <div className="mt-xxs flex items-start rounded-control bg-accent">
            <pre className="m-0 flex-1 whitespace-pre-wrap break-all p-xs font-mono text-caption">
              {resolvedCmd}
            </pre>
            <CopyCommandButton text={resolvedCmd} />
          </div>
        )}
        {test.expected_result && (
          <p className="mt-xxs break-words text-metadata text-muted-foreground">
            <strong>Expected:</strong> {test.expected_result}
          </p>
        )}
        {test.references && test.references.length > 0 && (
          <div className="mt-xxs flex flex-wrap gap-xxs">
            {test.references.filter(isSafeUrl).map((ref, ri) => {
              // FRX·H10: prefer rendering the hostname (with an
              // external-link icon) instead of the raw URL — the raw
              // URL bloats the chip row and rarely tells the reader
              // anything they can't see in the link title.  The full
              // URL is restored on hover via Tooltip.  Falls back to
              // the previous raw-URL rendering when URL parsing
              // throws (malformed input).
              let host: string | null = null;
              try {
                host = new URL(ref).hostname;
              } catch {
                host = null;
              }
              if (host) {
                return (
                  <Tooltip key={ri}>
                    <TooltipTrigger asChild>
                      <a
                        href={ref}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <Badge variant="outline" className="max-w-[16rem] overflow-hidden cursor-pointer">
                          <span className="flex items-center gap-xxs truncate">
                            <span className="truncate">{host}</span>
                            <ExternalLink className="size-3 shrink-0" aria-hidden />
                          </span>
                        </Badge>
                      </a>
                    </TooltipTrigger>
                    <TooltipContent>
                      <span className="break-all">{ref}</span>
                    </TooltipContent>
                  </Tooltip>
                );
              }
              return (
                <a
                  key={ri}
                  href={ref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Badge variant="outline" className="max-w-full cursor-pointer break-all">
                    {ref}
                  </Badge>
                </a>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default StructuredTestCard;
