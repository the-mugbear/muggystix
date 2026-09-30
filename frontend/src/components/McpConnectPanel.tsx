/**
 * "Connect via MCP" — the per-client setup block shown after a session mints
 * a key.
 *
 * v5.169.0 — extracted from StartAssistDialog, because MCP stopped being
 * assist-only: plan generation and execution sessions emit the same
 * `mcp_clients` payload and had no way to show it. Each client wants a
 * different shape (VS Code writes a file, the other two run a command), which
 * is why the server sends the payload and this only renders it — the shapes
 * have diverged before, and inferring one here is how that happened.
 */
import React, { useState } from 'react';
import { CheckCircle2, Copy } from 'lucide-react';

import { Button } from './ui/button';
import { CodeBlock, CopyButton } from './ui/code-block';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { copyToClipboard } from '../utils/clipboard';

export interface McpClientSetup {
  id: string;
  label: string;
  /** 'file' → payload is JSON to save at `path`; 'command' → a shell command. */
  kind: string;
  path: string;
  payload: string;
  hint: string;
  /** v5.203.0 — the handoff the config used to stop short of. All optional:
   *  the reference page's sample recipes and older fixtures render the
   *  config alone. */
  verify_check?: string;
  verify_prompt?: string;
  verify_expected?: string;
}

interface Props {
  clients: McpClientSetup[];
  /** One line above the tabs, describing what this session's tools are for. */
  blurb?: string;
}

/** The server's hint is several separate notes — save step, key handling,
 *  sandbox — joined by blank lines, with commands in
 *  backticks. Rendered as one <p> it was a 12-line wall with the commands lost
 *  in the prose (and the backticks shown literally). */
const inlineCode = (text: string) =>
  text.split(/(`[^`]+`)/).map((part, j) =>
    part.length > 2 && part.startsWith('`') && part.endsWith('`') ? (
      <code key={j} className="break-all rounded-sm bg-accent px-1 font-mono text-foreground">
        {part.slice(1, -1)}
      </code>
    ) : (
      <React.Fragment key={j}>{part}</React.Fragment>
    ),
  );

const HintText: React.FC<{ text: string }> = ({ text }) => (
  <div className="mt-xxs space-y-xs text-caption text-muted-foreground">
    {text.split(/\n{2,}/).map((para) => para.trim()).filter(Boolean).map((para, i) => (
      <p key={i} className="break-words">{inlineCode(para)}</p>
    ))}
  </div>
);

/** Copy with a brief confirmation; `onCopied` tells a dialog the key left the screen. */
const useCopy = (onCopied?: () => void) => {
  const [copied, setCopied] = useState(false);
  const copy = (payload: string) => {
    copyToClipboard(payload).then((ok) => {
      if (!ok) return;
      setCopied(true);
      onCopied?.();
      window.setTimeout(() => setCopied(false), 2000);
    });
  };
  return [copied, copy] as const;
};

/**
 * One client's recipe: where the config goes (or "run this"), the config with
 * a copy button, the client's notes, and the verification handoff.
 *
 * `compact` (5.309.0, the Start / Resume agent-session dialogs) folds the
 * client's notes behind "Setup notes" and states the verification in two
 * lines — the full dialog was ~770 words and scrolled on a tall screen.
 */
export const McpClientRecipe: React.FC<{
  client: McpClientSetup;
  compact?: boolean;
  onCopied?: () => void;
}> = ({ client, compact = false, onCopied }) => {
  const [copied, copy] = useCopy(onCopied);
  return (
    <div>
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
              variant={compact ? 'outline' : 'ghost'}
              size={compact ? 'sm' : 'icon'}
              onClick={() => copy(client.payload)}
              aria-label={`Copy ${client.label} MCP setup`}
            >
              {copied ? (
                <CheckCircle2 className="size-4 text-success" aria-hidden />
              ) : (
                <Copy className="size-4" aria-hidden />
              )}
              {compact && (copied ? 'Copied' : 'Copy')}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {copied ? 'Copied!' : `Copy ${client.label} setup — the key is in it`}
          </TooltipContent>
        </Tooltip>
      </div>
      <div className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-control border border-border bg-accent p-sm font-mono text-caption">
        {client.payload}
      </div>
      {compact ? (
        client.hint ? (
          <details className="mt-xs text-caption">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Setup notes for {client.label}</summary>
            <HintText text={client.hint} />
          </details>
        ) : null
      ) : (
        <HintText text={client.hint ?? ''} />
      )}
      {/* v5.203.0 — the handoff. The config block used to be the end of
          the story, and the two signals a client offers both mislead:
          "registered" says nothing about the key, and the tool list
          appears without one by design. The only proof is an
          authenticated tool call, so hand the operator the prompt that
          makes one and what its answer should say. */}
      {client.verify_prompt && compact ? (
        <CompactVerify client={client} />
      ) : client.verify_prompt ? (
        <div className="mt-sm border-t border-border pt-sm">
          <p className="mb-xxs text-metadata font-semibold">Then verify it works</p>
          <p className="mb-xs text-caption text-muted-foreground">
            Your client makes the connection after you configure and relaunch it;
            you then ask the agent to use BlueStick’s tools.{' '}
            {inlineCode(client.verify_check ?? '')}
          </p>
          <p className="mb-xxs text-caption text-muted-foreground">
            Seeing the tools listed is not proof — the list is public. Ask this first;
            it is the one check that proves the key works end to end:
          </p>
          <CodeBlock
            text={client.verify_prompt}
            label="verification prompt"
            className="max-h-40 whitespace-pre-wrap break-words"
          />
          {client.verify_expected ? (
            <p className="mt-xxs text-caption text-muted-foreground">
              {client.verify_expected}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

/** The verification in two lines (5.309.0): what to do, what a working answer
 *  names, and a copy button. The prompt itself (~130 words, deliberately
 *  strict: a field report showed an unconnected agent faking the call with
 *  curl), the client's own check and the rest of the expectation fold away —
 *  the operator copies the prompt; they need not read it first. */
const CompactVerify: React.FC<{ client: McpClientSetup }> = ({ client }) => {
  const expected = client.verify_expected ?? '';
  // The first sentence names this session's facts; the rest is troubleshooting.
  const cut = expected.indexOf('. ');
  const headline = cut > 0 ? expected.slice(0, cut + 1) : expected;
  const rest = cut > 0 ? expected.slice(cut + 2) : '';
  return (
    <div className="mt-sm border-t border-border pt-sm">
      <div className="flex items-center justify-between gap-sm">
        <p className="text-metadata font-semibold">Then verify it works</p>
        <CopyButton text={client.verify_prompt ?? ''} label="Copy verification prompt" />
      </div>
      <p className="mt-xxs text-caption text-muted-foreground">
        Relaunch your client and paste the check into it. {headline}
      </p>
      <details className="mt-xxs text-caption">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">The check, and what to do if it fails</summary>
        <div className="mt-xs space-y-xs text-muted-foreground">
          <p className="whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-foreground">
            {client.verify_prompt}
          </p>
          <p>Seeing the tools listed is not proof — the list is public. {inlineCode(client.verify_check ?? '')}</p>
          {rest && <p>{rest}</p>}
        </div>
      </details>
    </div>
  );
};

const McpConnectPanel: React.FC<Props> =({ clients, blurb }) => {
  const [selected, setSelected] = useState<string | null>(null);

  if (!clients?.length) return null;

  return (
    <div>
      <p className="mb-xxs text-metadata font-semibold">Connect via MCP</p>
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
            <McpClientRecipe client={client} />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
};

export default McpConnectPanel;
