/**
 * Said beside a member picker whose roster could not be read: a failed load
 * is never shown as "No members" (hooks/useProjectMembers `useProjectRoster`).
 */
import React from 'react';

import { cn } from '../utils/cn';

export const MEMBERS_LOAD_ERROR = 'The project’s members could not be loaded.';

const MembersLoadError: React.FC<{ onRetry: () => void; className?: string }> = ({ onRetry, className }) => (
  <span role="alert" className={cn('inline-flex min-w-0 flex-wrap items-center gap-xs text-caption text-warning', className)}>
    <span className="min-w-0 break-words">{MEMBERS_LOAD_ERROR}</span>
    <button
      type="button"
      className="rounded text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={onRetry}
    >
      Retry
    </button>
  </span>
);

export default MembersLoadError;
