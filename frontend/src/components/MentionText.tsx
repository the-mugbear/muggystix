/**
 * A note or comment body with its @mentions of project members marked, so a
 * reader sees who was notified. Only real members are marked (the same rule
 * the server notifies by — utils/mentions.ts); any other `@word` stays text.
 *
 * `MentionText` is the body as plain text (an excerpt in a list);
 * `DiscussionText` is the whole body where it is read — Markdown, because an
 * agent's note is written in it and a person's may be, with the writer's
 * line breaks kept.
 */
import React from 'react';

import { useProjectMembers } from '../hooks/useProjectMembers';
import SafeMarkdown, { markMentions } from './SafeMarkdown';

export const MentionText: React.FC<{ text: string | null | undefined }> = ({ text }) => {
  const members = useProjectMembers();
  return <>{markMentions(text ?? '', members.map((m) => m.username))}</>;
};

export const DiscussionText: React.FC<{ text: string | null | undefined; className?: string }> = ({ text, className }) => {
  const members = useProjectMembers();
  return <SafeMarkdown text={text ?? ''} className={className} lineBreaks mentions={members.map((m) => m.username)} />;
};

export default MentionText;
