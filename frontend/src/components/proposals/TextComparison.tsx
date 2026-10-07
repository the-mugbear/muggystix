/**
 * A report-text proposal against the field's current text (5.334.0).
 *
 * The walkthrough of 2026-10-02: the current text sat in a closed "Current
 * text" toggle under each draft, in caption grey, read AFTER the draft — so
 * nobody compared.  Here it is always visible: "Now in the report" beside
 * "Proposed", or one pane with the words the draft adds and removes marked.
 * A proposal replaces the whole section, so when the field changed after the
 * draft was written (`changed_since_proposed`) that is said first, with the
 * text the draft was written against.
 *
 * An empty field keeps the same two panes, the left one saying it is empty
 * (5.334.5): shown alone, its proposal made written and empty sections of
 * one finding look laid out differently.
 */
import React, { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import type { EvidenceResolver } from '../../utils/reportImages';
import { cn } from '../../utils/cn';
import { diffStats, diffWords, DiffPart } from '../../utils/textDiff';
import SafeMarkdown from '../SafeMarkdown';

type View = 'side' | 'changes';

interface Props {
  /** The field's text now. */
  current: string | null | undefined;
  /** The proposal's text. */
  proposed: string;
  /** Monospace and no Markdown (a CVSS vector). */
  mono?: boolean;
  /** The text the proposal was written against, when the field has changed since. */
  staleBase?: { value: string | null } | null;
  /** The finding's placed images, where the page has them. */
  evidence?: EvidenceResolver;
}

const PaneLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="mb-xxs text-caption font-medium text-muted-foreground">{children}</p>
);

const Text: React.FC<{ text: string; mono?: boolean; evidence?: EvidenceResolver; muted?: boolean }> = ({
  text, mono, evidence, muted,
}) => (mono
  ? <p className={cn('break-all font-mono text-body', muted && 'text-muted-foreground')}>{text}</p>
  : <SafeMarkdown text={text} evidence={evidence} className={cn('text-body', muted && 'text-muted-foreground')} />);

export const ChangesText: React.FC<{ parts: DiffPart[]; mono?: boolean }> = ({ parts, mono }) => (
  <p className={cn('whitespace-pre-wrap break-words text-body', mono && 'break-all font-mono')} data-testid="text-changes">
    {parts.map((p, i) => (p.kind === 'same'
      ? <span key={i}>{p.text}</span>
      : p.kind === 'added'
        // A replacement starts clear of the struck text it replaces
        // ("~~accepts~~evaluates" read as one word).
        ? <ins key={i} className={cn('rounded-sm bg-success/20 text-foreground no-underline',
            parts[i - 1]?.kind === 'removed' && 'ml-[0.3em]')}>{p.text}</ins>
        : <del key={i} className="rounded-sm bg-destructive/15 text-muted-foreground line-through">{p.text}</del>))}
  </p>
);

const TextComparison: React.FC<Props> = ({ current, proposed, mono = false, staleBase = null, evidence }) => {
  const now = (current ?? '').trim();
  const [view, setView] = useState<View>('side');
  const parts = useMemo(() => (now ? diffWords(now, proposed.trim()) : null), [now, proposed]);
  const stats = parts ? diffStats(parts) : null;

  // An empty section keeps the two panes (5.334.5).  It used to show the
  // proposal alone, full width — so on one finding Description (written) read
  // side by side and Impact, Recommendation… (empty) did not, which looked
  // like a layout fault.  Every draft now sits in the same place; the left
  // pane says the section is empty.  There are no words to diff, so no
  // Side by side / Changes switch.
  if (!now) {
    return (
      <div className="grid min-w-0 grid-cols-2 gap-md">
        <div className="min-w-0 border-r border-border pr-md" data-testid="compare-current">
          <PaneLabel>Now in the report</PaneLabel>
          <p className="text-body text-muted-foreground">Nothing yet — accepting fills this section.</p>
        </div>
        <div className="min-w-0" data-testid="compare-proposed">
          <PaneLabel>Proposed</PaneLabel>
          <Text text={proposed} mono={mono} evidence={evidence} />
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-xs">
      {staleBase && (
        <div role="note" className="flex min-w-0 items-start gap-xs rounded-md border border-warning/40 bg-warning/10 p-xs text-caption">
          <AlertTriangle className="mt-[2px] size-4 shrink-0 text-warning" aria-hidden />
          <div className="min-w-0 space-y-xxs">
            <p className="break-words text-foreground">
              This section changed after the draft was written. Accepting replaces the whole section, the newer edit included.
            </p>
            <details>
              <summary className="cursor-pointer select-none text-muted-foreground">The text the draft was written against</summary>
              <div className="mt-xxs border-l-2 border-border pl-sm">
                {(staleBase.value ?? '').trim()
                  ? <Text text={staleBase.value as string} mono={mono} muted />
                  : <p className="text-muted-foreground">Empty.</p>}
              </div>
            </details>
          </div>
        </div>
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-xs" role="group" aria-label="How to compare">
        {(['side', 'changes'] as View[]).map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={view === v}
            disabled={v === 'changes' && !parts}
            onClick={() => setView(v)}
            className={cn(
              'rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
              view === v
                ? 'border-primary bg-primary/10 text-foreground'
                : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
          >
            {v === 'side' ? 'Side by side' : 'Changes'}
          </button>
        ))}
        <span className="text-caption text-muted-foreground">
          {stats
            ? (stats.added || stats.removed
              ? `${stats.added} word${stats.added === 1 ? '' : 's'} added, ${stats.removed} removed`
              : 'Same text')
            : 'Too long to compare word by word'}
        </span>
      </div>
      {view === 'changes' && parts ? (
        <ChangesText parts={parts} mono={mono} />
      ) : (
        <div className="grid min-w-0 grid-cols-2 gap-md">
          <div className="min-w-0 border-r border-border pr-md" data-testid="compare-current">
            <PaneLabel>Now in the report</PaneLabel>
            <Text text={now} mono={mono} evidence={evidence} muted />
          </div>
          <div className="min-w-0" data-testid="compare-proposed">
            <PaneLabel>Proposed</PaneLabel>
            <Text text={proposed} mono={mono} evidence={evidence} />
          </div>
        </div>
      )}
    </div>
  );
};

export default TextComparison;
