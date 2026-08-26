/**
 * "Connect via MCP" — the per-client setup block shown after a session mints
 * a key.
 *
 * v5.169.0 — extracted from StartAssistDialog, because MCP stopped being
 * assist-only: recon, plan generation and execution sessions emit the same
 * `mcp_clients` payload and had no way to show it. Each client wants a
 * different shape (VS Code writes a file, the other two run a command), which
 * is why the server sends the payload and this only renders it — the shapes
 * have diverged before, and inferring one here is how that happened.
 */
import React, { useState } from 'react';
import { CheckCircle2, Copy } from 'lucide-react';

import { Button } from './ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { copyToClipboard } from '../utils/clipboard';
import McpCertTrustNotice from './McpCertTrustNotice';

export interface McpClientSetup {
  id: string;
  label: string;
  /** 'file' → payload is JSON to save at `path`; 'command' → a shell command. */
  kind: string;
  path: string;
  payload: string;
  hint: string;
}

interface Props {
  clients: McpClientSetup[];
  /** One line above the tabs, describing what this session's tools are for. */
  blurb?: string;
  /** Surface the certificate-trust prerequisite above the recipes. On by
   *  default in the start dialogs (where the first connection happens and the
   *  cert wall is invisible until it refuses); off on the reference page,
   *  which already carries the full cert write-up. */
  withCertTrust?: boolean;
}

const McpConnectPanel: React.FC<Props> = ({ clients, blurb, withCertTrust = false }) => {
  const [selected, setSelected] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  if (!clients?.length) return null;

  const copy = (payload: string) => {
    copyToClipboard(payload).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div>
      <p className="mb-xxs text-metadata font-semibold">Connect via MCP</p>
      {withCertTrust ? <McpCertTrustNotice /> : null}
      {blurb ? (
        <p className="mb-xs text-caption text-muted-foreground">{blurb}</p>
      ) : null}
      <Tabs value={selected ?? clients[0].id} onValueChange={setSelected}>
        <TabsList className="mb-xs">
          {clients.map((c) => (
            <TabsTrigger key={c.id} value={c.id}>
              {c.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {clients.map((client) => (
          <TabsContent key={client.id} value={client.id}>
            <div className="mb-xxs flex items-center justify-between gap-sm">
              <p className="min-w-0 truncate text-caption text-muted-foreground">
                {client.kind === 'file' ? (
                  <>
                    Save as <span className="font-mono">{client.path}</span>
                  </>
                ) : (
                  'Run this command'
                )}
              </p>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => copy(client.payload)}
                    aria-label={`Copy ${client.label} MCP setup`}
                  >
                    {copied ? (
                      <CheckCircle2 className="size-4 text-success" aria-hidden />
                    ) : (
                      <Copy className="size-4" aria-hidden />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {copied ? 'Copied!' : `Copy ${client.label} setup`}
                </TooltipContent>
              </Tooltip>
            </div>
            <div className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-control border border-border bg-accent p-sm font-mono text-caption">
              {client.payload}
            </div>
            <p className="mt-xxs text-caption text-muted-foreground">{client.hint}</p>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
};

export default McpConnectPanel;
