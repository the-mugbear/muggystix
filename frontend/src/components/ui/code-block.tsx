/**
 * Shared code-block + copy-button primitives.
 *
 * These two patterns were hand-rolled in at least three places — a local
 * `CodeBlock` in McpReference, a local `CopyButton` in TestPlans, and the
 * inline copy-icon logic every start dialog carries — each with its own
 * copied-state timer and clipboard call. Consolidated here so the copy
 * affordance looks and behaves the same everywhere it appears.
 *
 * Neither toasts: the check-icon swap is the feedback, matching the dialogs.
 * `copyToClipboard` falls back to execCommand on non-secure origins, and the
 * text is on screen if even that fails, so a silent no-op is acceptable.
 */
import React, { useState } from 'react';
import { Check, Copy } from 'lucide-react';

import { Button } from './button';
import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip';
import { copyToClipboard } from '../../utils/clipboard';

interface CopyButtonProps {
  text: string;
  /** Accessible label + tooltip; defaults to a generic "Copy". */
  label?: string;
  className?: string;
}

/** Ghost icon button that copies `text` and flips to a check for ~1.5s. */
export const CopyButton: React.FC<CopyButtonProps> = ({ text, label, className }) => {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (await copyToClipboard(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={copy}
          aria-label={label ?? 'Copy to clipboard'}
          className={className}
        >
          {copied ? (
            <Check className="size-4 text-success" aria-hidden />
          ) : (
            <Copy className="size-4" aria-hidden />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{copied ? 'Copied!' : label ?? 'Copy'}</TooltipContent>
    </Tooltip>
  );
};

interface CodeBlockProps {
  text: string;
  /** Accessible label for the copy button, e.g. "certificate trust setup". */
  label: string;
  /** Extra classes on the <pre> (e.g. a different max-height). */
  className?: string;
}

/**
 * A scrollable monospace block with a copy button pinned top-right. Wraps its
 * own horizontal scroll so a long line never widens the page (UI style guide).
 */
export const CodeBlock: React.FC<CodeBlockProps> = ({ text, label, className }) => (
  <div className="relative">
    <pre
      className={
        'max-h-72 overflow-auto rounded-control border border-border bg-accent p-sm pr-xl font-mono text-caption ' +
        (className ?? '')
      }
    >
      {text}
    </pre>
    <CopyButton text={text} label={`Copy ${label}`} className="absolute right-xxs top-xxs" />
  </div>
);

export default CodeBlock;
