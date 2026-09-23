/**
 * One message in a discussion (v5.264.0) — finding comments and host notes
 * read like a text-message conversation instead of a stack of boxes.
 *
 * The phone convention (v5.268.0): the viewer's own messages sit on the
 * RIGHT, everyone else's on the LEFT, each in a rounded bubble with a tint
 * that tells the two apart without reading the name.  The bubble's text is
 * always left-aligned; only its position changes.  Author, badges and time
 * sit above the bubble; actions (reply, edit, delete…) below it, on the same
 * side.  A reply quotes what it answers ("Replying to Ana: …") instead of
 * indenting — indentation and left/right alignment cannot both carry meaning.
 */
import React from 'react';
import { CornerDownRight } from 'lucide-react';

import { AgentAuthorBadge } from './AgentAuthorBadge';
import { cn } from '../utils/cn';

export interface MessageBubbleProps {
  /** The viewer wrote it — shown on the right, in the viewer's tint. */
  mine: boolean;
  author: string;
  actorType?: 'user' | 'agent' | null;
  createdAt: string;
  edited?: boolean;
  editedAt?: string | null;
  /** What this message answers, when it is a reply. */
  replyingTo?: { author: string; excerpt: string } | null;
  /** Extra chips beside the author (status, type, pinned…). */
  meta?: React.ReactNode;
  /** Controls on the meta line's far side (e.g. a thread's status select). */
  metaControls?: React.ReactNode;
  /** Reply / edit / delete…, under the bubble on the same side. */
  actions?: React.ReactNode;
  /** Anchor id for deep links (#note-17). */
  id?: string;
  className?: string;
  children: React.ReactNode;
}

/**
 * A message was edited when it changed well after it was written.  Creating a
 * note stamps its thread root in a second write, and a status change touches
 * the row too, so "has an updated_at" is not "was edited" (every message read
 * "· edited").  Five seconds absorbs the create path.
 */
export const wasEdited = (m: { created_at: string; updated_at?: string | null }): boolean =>
  !!m.updated_at && new Date(m.updated_at).getTime() - new Date(m.created_at).getTime() > 5000;

const excerptOf = (text: string, max = 90) => {
  const one = text.replace(/\s+/g, ' ').trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
};

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  mine, author, actorType, createdAt, edited, editedAt, replyingTo, meta, metaControls, actions, id, className, children,
}) => (
  <div id={id} data-side={mine ? 'mine' : 'theirs'}
    className={cn('flex min-w-0 scroll-mt-24 flex-col', mine ? 'items-end' : 'items-start', className)}>
    <div className={cn('mb-xxs flex w-full max-w-[85%] flex-wrap items-center gap-xs', mine ? 'justify-end' : 'justify-start')}>
      {/* The controls sit on the side away from the screen edge. */}
      {mine && metaControls}
      <div className={cn('flex min-w-0 flex-wrap items-center gap-xs', mine && 'justify-end')}>
        {meta}
        <span className="truncate text-metadata font-semibold text-foreground" title={author}>
          {mine ? 'You' : author}
        </span>
        <AgentAuthorBadge actorType={actorType ?? undefined} />
        <span className="text-caption text-muted-foreground">
          {new Date(createdAt).toLocaleString()}
          {edited && <span title={editedAt ? `Edited ${new Date(editedAt).toLocaleString()}` : undefined}> · edited</span>}
        </span>
      </div>
      {!mine && metaControls}
    </div>
    <div
      className={cn(
        'min-w-0 max-w-[85%] rounded-2xl px-sm py-xs text-left',
        mine
          // Two tints that stay visible in every theme: `bg-muted` is the
          // hover token, which is invisible on the dark palettes.
          ? 'rounded-tr-sm bg-primary/15 text-foreground'
          : 'rounded-tl-sm bg-foreground/[0.07] text-foreground',
      )}
    >
      {replyingTo && (
        <p className="mb-xxs flex min-w-0 items-start gap-xxs border-l-2 border-border pl-xs text-caption text-muted-foreground">
          <CornerDownRight className="mt-0.5 size-3 shrink-0" aria-hidden />
          <span className="min-w-0 break-words">
            Replying to <span className="font-medium">{replyingTo.author}</span>
            {replyingTo.excerpt && <>: {excerptOf(replyingTo.excerpt)}</>}
          </span>
        </p>
      )}
      {children}
    </div>
    {actions && (
      <div className={cn('mt-xxs flex max-w-[85%] flex-wrap items-center gap-xxs', mine ? 'justify-end' : 'justify-start')}>
        {actions}
      </div>
    )}
  </div>
);

export default MessageBubble;
