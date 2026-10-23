/**
 * Shared code-block + copy-button primitives.
 *
 * These two patterns were hand-rolled in at least three places — a local
 * `CodeBlock` in McpReference, a local `CopyButton` in TestPlans, and the
 * inline copy-icon logic every start dialog carries — each with its own
 * copied-state timer and clipboard call. Consolidated here so the copy
 * affordance looks and behaves the same everywhere it appears.
 *
 * Neither toasts: the icon swap is the feedback, matching the dialogs — a
 * check when the text was copied, a cross (and "Could not copy" on the
 * tooltip) when even `copyToClipboard`'s execCommand fallback failed; the text
 * is on screen to select by hand.
 *
 * `CopyButton` is the ONE copy icon button (review 2026-10-23b, FEC-7: the
 * host tests had their own).
 */
import React, { useState } from 'react';
import { Check, Copy, X } from 'lucide-react';

import { Button } from './button';
import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip';
import { cn } from '../../utils/cn';
import { copyToClipboard } from '../../utils/clipboard';

interface CopyButtonProps {
  text: string;
  /** Accessible label + tooltip; defaults to a generic "Copy". */
  label?: string;
  /** `compact`: the smaller button that sits on a row's line (a test's
   *  command). */
  size?: 'default' | 'compact';
  className?: string;
}

/** Ghost icon button that copies `text` and flips to a check (or, failing, a
 *  cross) for ~1.5s.  The click goes no further: a copy on a row never also
 *  opens the row. */
export const CopyButton: React.FC<CopyButtonProps> = ({ text, label, size = 'default', className }) => {
  const [outcome, setOutcome] = useState<'copied' | 'failed' | null>(null);
  const copy = async (event: React.MouseEvent) => {
    event.stopPropagation();
    const ok = await copyToClipboard(text);
    setOutcome(ok ? 'copied' : 'failed');
    setTimeout(() => setOutcome(null), 1500);
  };
  const icon = size === 'compact' ? 'size-3.5' : 'size-4';
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={copy}
          aria-label={label ?? 'Copy to clipboard'}
          className={cn(size === 'compact' && 'size-7 shrink-0', className)}
        >
          {outcome === 'copied' ? (
            <Check className={cn(icon, 'text-success')} aria-hidden />
          ) : outcome === 'failed' ? (
            <X className={cn(icon, 'text-destructive')} aria-hidden />
          ) : (
            <Copy className={icon} aria-hidden />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {outcome === 'copied' ? 'Copied!' : outcome === 'failed' ? 'Could not copy — select the text instead' : label ?? 'Copy'}
      </TooltipContent>
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
