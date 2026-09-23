/**
 * A note or comment body with its @mentions of project members marked, so a
 * reader sees who was notified. Only real members are marked (the same rule
 * the server notifies by — utils/mentions.ts); any other `@word` stays text.
 */
import React from 'react';

import { useProjectMembers } from '../hooks/useProjectMembers';
import { findMentionSpans } from '../utils/mentions';

export const MentionText: React.FC<{ text: string | null | undefined }> = ({ text }) => {
  const members = useProjectMembers();
  const body = text ?? '';
  const spans = findMentionSpans(body, members.map((m) => m.username));
  if (spans.length === 0) return <>{body}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const s of spans) {
    if (s.start > cursor) parts.push(body.slice(cursor, s.start));
    parts.push(
      <span key={s.start} className="rounded-sm bg-primary/10 px-0.5 font-medium text-primary" title={`Mentions ${s.username}`}>
        {body.slice(s.start, s.end)}
      </span>,
    );
    cursor = s.end;
  }
  if (cursor < body.length) parts.push(body.slice(cursor));
  return <>{parts}</>;
};

export default MentionText;
