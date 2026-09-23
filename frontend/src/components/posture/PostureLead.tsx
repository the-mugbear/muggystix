/**
 * The lead of a Posture page (v5.262.0): one plain sentence of fact — the
 * answer to the page's question — on a coloured rule, with what it rests on
 * underneath.  The Overview's conclusion is the same shape (§7 "Lead"); the
 * child pages use this so each one opens by saying what it found.
 */
import React from 'react';

import { cn } from '../../utils/cn';

export type LeadTone = 'critical' | 'warning' | 'clear' | 'neutral';

const TONE_BORDER: Record<LeadTone, string> = {
  // Same classes as the Overview's conclusion (postureTheme LABEL_TONE).
  critical: 'border-l-destructive',
  warning: 'border-l-warning',
  clear: 'border-l-success',
  neutral: 'border-l-muted-foreground',
};

export interface PostureLeadProps {
  tone?: LeadTone;
  children: React.ReactNode;
  /** One caption line: what the sentence rests on. */
  restsOn?: React.ReactNode;
  className?: string;
}

export const PostureLead: React.FC<PostureLeadProps> = ({ tone = 'neutral', children, restsOn, className }) => (
  <div className={cn('border-l-4 py-xs pl-md', TONE_BORDER[tone], className)}>
    <p className="break-words text-subheading font-semibold text-foreground">{children}</p>
    {restsOn && <p className="mt-xs break-words text-caption text-muted-foreground">{restsOn}</p>}
  </div>
);

export default PostureLead;
