/**
 * AgentSessionCredentials — v5.214.0; one choice, one copy (5.309.0)
 *
 * The "hand this session to an agent" panel, shared by the Start and Resume
 * dialogs so they cannot drift.
 *
 * 5.309.0 — it used to show the key, then two levels of tabs (MCP / prompt,
 * then a client), the certificate notice, the client's notes as paragraphs
 * and the verification: ~770 words, scrolling on a tall screen. Now the
 * operator picks their client once (remembered), sees that client's config
 * with ONE copy button — the key is inside it — and everything else folds:
 * setup notes, the bare key. "Other agent" is the
 * pasted prompt for anything without MCP.
 */
import React, { useState } from 'react';
import { CheckCircle2, Copy } from 'lucide-react';

import { copyToClipboard } from '../utils/clipboard';
import { Button } from './ui/button';
import { cn } from '../utils/cn';
import type { McpClientSetup } from '../services/api';
import { McpClientRecipe } from './McpConnectPanel';

const PROMPT = 'prompt';
const CHOICE_KEY = 'bluestick.agentClient';

const readChoice = (): string | null => {
  try {
    return window.localStorage.getItem(CHOICE_KEY);
  } catch {
    return null;
  }
};
const saveChoice = (id: string) => {
  try {
    window.localStorage.setItem(CHOICE_KEY, id);
  } catch {
    /* a per-viewer convenience: nothing to do without storage */
  }
};

interface Props {
  apiKey: string;
  instructions: string;
  mcpClients?: McpClientSetup[];
  /** Label of the bare-key disclosure. */
  keyLabel?: string;
  /** Something holding the key was copied (config, prompt or key) — the
   *  dialog then closes without asking. */
  onCopied?: () => void;
}

const CopyText: React.FC<{ text: string; label: string; onCopied?: () => void }> = ({ text, label, onCopied }) => {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      aria-label={label}
      onClick={async () => {
        if (await copyToClipboard(text)) {
          setCopied(true);
          onCopied?.();
          setTimeout(() => setCopied(false), 1500);
        }
      }}
    >
      {copied ? <CheckCircle2 className="size-4 text-success" aria-hidden /> : <Copy className="size-4" aria-hidden />}
      {copied ? 'Copied' : 'Copy'}
    </Button>
  );
};

const AgentSessionCredentials: React.FC<Props> = ({
  apiKey,
  instructions,
  mcpClients = [],
  keyLabel = 'The key on its own',
  onCopied,
}) => {
  const options = [
    ...mcpClients.map((c) => ({ id: c.id, label: c.label })),
    { id: PROMPT, label: mcpClients.length ? 'Other agent' : 'Paste the prompt' },
  ];
  const [choice, setChoice] = useState<string>(() => {
    const saved = readChoice();
    return saved && options.some((o) => o.id === saved) ? saved : options[0].id;
  });
  const choose = (id: string) => {
    setChoice(id);
    saveChoice(id);
  };
  const client = mcpClients.find((c) => c.id === choice) ?? null;
  // 5.314.1 — Codex's recipe keeps the key out of the command (`read -rs`
  // waits for it to be pasted), so for such a client the key is part of the
  // steps, not a folded extra.
  const keyIsSeparateStep = client !== null && !client.payload.includes(apiKey);

  const keyRow = (
    <div className="flex items-start gap-xs">
      <div className="min-w-0 flex-1 break-all rounded-control border border-border bg-accent p-sm font-mono text-caption" data-testid="agent-key">
        {apiKey}
      </div>
      <CopyText text={apiKey} label="Copy agent API key" onCopied={onCopied} />
    </div>
  );

  return (
    <div className="flex flex-col gap-sm">
      <div>
        <p className="mb-xxs text-metadata font-semibold">Your agent</p>
        <div role="radiogroup" aria-label="Your agent" className="flex flex-wrap gap-xxs">
          {options.map((o) => (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={choice === o.id}
              onClick={() => choose(o.id)}
              className={cn(
                'rounded-control border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                choice === o.id
                  ? 'border-primary bg-primary/10 font-medium text-foreground'
                  : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {client ? (
        <>
          <McpClientRecipe client={client} compact onCopied={onCopied} />
          {keyIsSeparateStep && (
            <div>
              <p className="mb-xxs text-caption text-muted-foreground">
                Paste this key when the first line waits for it:
              </p>
              {keyRow}
            </div>
          )}
        </>
      ) : (
        <div>
          <div className="mb-xxs flex items-center justify-between gap-sm">
            <p className="min-w-0 text-caption text-muted-foreground">
              Paste this into any terminal agent — it drives the session with curl.
            </p>
            <CopyText text={instructions} label="Copy the agent prompt" onCopied={onCopied} />
          </div>
          <div className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
            {instructions}
          </div>
        </div>
      )}

      {!keyIsSeparateStep && (
        <details className="text-caption">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            {keyLabel}
          </summary>
          <div className="mt-xs">{keyRow}</div>
        </details>
      )}
    </div>
  );
};

/**
 * The footer of a dialog showing a key (5.309.0). Replaces the "I copied the
 * key" checkbox: Done closes at once when something holding the key was
 * copied; otherwise the first press says what is lost and offers to close
 * anyway. The key really is shown once — but resuming the session issues a
 * new one, so losing it costs a click on Agent Sessions, not the session.
 */
export const KeyHandoffFooter: React.FC<{
  copied: boolean;
  onDone: () => void;
  note?: React.ReactNode;
}> = ({ copied, onDone, note }) => {
  const [warned, setWarned] = useState(false);
  return (
    <div className="flex w-full flex-wrap items-center justify-end gap-sm">
      {warned && !copied ? (
        <p role="alert" className="mr-auto min-w-0 flex-1 text-caption text-warning">
          Nothing was copied — the key will not be shown again. Resume the session from
          Agent Sessions for a new one.
        </p>
      ) : (
        note && <p className="mr-auto min-w-0 flex-1 text-caption text-muted-foreground">{note}</p>
      )}
      <Button
        variant={warned && !copied ? 'outline' : 'default'}
        onClick={() => {
          if (copied || warned) onDone();
          else setWarned(true);
        }}
      >
        {warned && !copied ? 'Close anyway' : 'Done'}
      </Button>
    </div>
  );
};

export default AgentSessionCredentials;
