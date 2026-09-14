/**
 * AgentSessionCredentials — v5.214.0
 *
 * The "hand this session to an agent" panel: the key (shown once) plus the
 * two ways to connect — MCP client setup or the pasted prompt — as tabs.
 *
 * Lifted out of StartAssistDialog so the resume dialog on Agent Activity
 * renders exactly what the start dialog renders. Two copies would drift the
 * way the two MCP recipes once did (one silently didn't work).
 */
import React, { useState } from 'react';
import { CheckCircle2, Copy } from 'lucide-react';
import { copyToClipboard } from '../utils/clipboard';
import { Button } from './ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import type { McpClientSetup } from '../services/api';
import McpConnectPanel from './McpConnectPanel';

interface Props {
  apiKey: string;
  instructions: string;
  mcpClients?: McpClientSetup[];
  /** Label above the key. Defaults to the start-dialog wording. */
  keyLabel?: string;
}

const AgentSessionCredentials: React.FC<Props> = ({
  apiKey,
  instructions,
  mcpClients = [],
  keyLabel = 'Agent API Key (shown once)',
}) => {
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedInstr, setCopiedInstr] = useState(false);

  // A backend that predates the per-client MCP setup returns none; the prompt
  // tab then stands alone rather than opening on an empty tab.
  const hasMcp = mcpClients.length > 0;

  const copyKey = async () => {
    // copyToClipboard falls back to execCommand on http:// / non-secure
    // contexts where navigator.clipboard is unavailable; the value is also
    // visible on screen if even that fails.
    if (await copyToClipboard(apiKey)) {
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 1500);
    }
  };
  const copyInstructions = async () => {
    if (await copyToClipboard(instructions)) {
      setCopiedInstr(true);
      setTimeout(() => setCopiedInstr(false), 1500);
    }
  };

  return (
    <div className="flex flex-col gap-sm">
      <div>
        <div className="mb-xxs flex items-center justify-between">
          <p className="text-metadata font-semibold">{keyLabel}</p>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={copyKey}
                aria-label="Copy agent API key"
              >
                {copiedKey ? (
                  <CheckCircle2 className="size-4 text-success" aria-hidden />
                ) : (
                  <Copy className="size-4" aria-hidden />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{copiedKey ? 'Copied!' : 'Copy key'}</TooltipContent>
          </Tooltip>
        </div>
        <div className="break-all rounded-control border border-border bg-accent p-sm font-mono text-caption">
          {apiKey}
        </div>
      </div>
      {/* Two ways to hand this session to an agent, as tabs rather than
          stacked (v5.172.0). Stacked, the multi-KB prompt sat between the
          operator and the MCP config, so the path we recommend was the one
          they had to scroll past the other to find. */}
      <Tabs defaultValue={hasMcp ? 'mcp' : 'prompt'}>
        <TabsList className="mb-xs">
          {hasMcp && <TabsTrigger value="mcp">Connect via MCP</TabsTrigger>}
          <TabsTrigger value="prompt">Paste the prompt</TabsTrigger>
        </TabsList>
        {hasMcp && (
          <TabsContent value="mcp">
            <McpConnectPanel
              clients={mcpClients}
              withCertTrust
              blurb={
                'The tools appear natively in your client, and the read tools can be ' +
                'marked “always allow” so queries run without a prompt. Each client ' +
                'wants a different shape, so pick yours:'
              }
            />
          </TabsContent>
        )}
        <TabsContent value="prompt">
          <div className="mb-xxs flex items-center justify-between">
            <p className="text-metadata text-muted-foreground">
              Paste this into a terminal agent — it drives the same session with curl.
            </p>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={copyInstructions}
                  aria-label="Copy assist instructions"
                >
                  {copiedInstr ? (
                    <CheckCircle2 className="size-4 text-success" aria-hidden />
                  ) : (
                    <Copy className="size-4" aria-hidden />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {copiedInstr ? 'Copied!' : 'Copy instructions'}
              </TooltipContent>
            </Tooltip>
          </div>
          <div className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
            {instructions}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default AgentSessionCredentials;
