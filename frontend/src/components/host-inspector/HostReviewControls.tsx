/**
 * The reader's review of this host, in the inspector's title row: the state
 * badge and — for a writer — the one state-aware action plus the off-path
 * transitions (§6/§9).
 *
 * The review is the READER'S follow row on the host (`host.follow`).  The
 * server's answer goes back to the caller (`onSaved`), which puts it on the
 * cached host and tells a parent list; nothing is remembered here.
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { CheckCircle2, Eye, RotateCcw } from 'lucide-react';
import { followHost, unfollowHost } from '../../services/api';
import type { FollowStatus, Host } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';

const FOLLOW_STATUS_META: Record<
  FollowStatus,
  { label: string; description: string; badgeVariant: 'info' | 'warning' | 'success' }
> = {
  watching: {
    label: 'Watching',
    description: 'Track this host for future review or to share with teammates.',
    badgeVariant: 'info',
  },
  in_review: {
    label: 'In Review',
    description: 'You are actively investigating this host and its findings.',
    badgeVariant: 'warning',
  },
  reviewed: {
    label: 'Reviewed',
    description: 'Investigation completed — leave a note with outcomes if relevant.',
    badgeVariant: 'success',
  },
};

export interface HostReviewControlsProps {
  hostId: number;
  /** The reader's review status of this host ('' = none). */
  followStatus: FollowStatus | '';
  /** Setting a review status is a write (analyst and up). */
  canEdit: boolean;
  /** The server's answer (null = no longer followed). */
  onSaved: (follow: Host['follow']) => void;
  /** See `HostInspectorProps.onNextUnreviewed`. */
  onNextUnreviewed?: () => void;
}

type FollowChange = { status: FollowStatus | 'none' };

export const HostReviewControls: React.FC<HostReviewControlsProps> = ({
  hostId,
  followStatus,
  canEdit,
  onSaved,
  onNextUnreviewed,
}) => {
  const toast = useToast();
  const projectId = useProjectId();

  const follow = useMutation({
    mutationFn: ({ status }: FollowChange): Promise<Host['follow']> => (
      status === 'none' ? unfollowHost(projectId, hostId).then(() => null) : followHost(projectId, hostId, status)
    ),
    onSuccess: onSaved,
  });
  const followLoading = follow.isPending;
  const updateFollow = (status: FollowChange['status'], onDone?: () => void) => {
    // The toasts say "this host", so only while the reader is still on it.
    follow.mutate({ status }, {
      onSuccess: () => {
        if (status === 'none') toast.info('Removed from your follow list', { autoHideMs: 2000 });
        else toast.success(`Marked as ${FOLLOW_STATUS_META[status].label}`, { autoHideMs: 2000 });
        onDone?.();
      },
      onError: () => toast.error('Failed to update follow status. Please try again.'),
    });
  };

  // Finishing a review is ONE click, like starting one (owner decision
  // 2026-10-10): no dialog, no conclusion — a host that needs more evidence
  // stays In Review.  `advance`: then move to the next host nobody has
  // started.  Only after the save succeeded: a failed save must not carry the
  // reader away from the host that was not marked.
  const markReviewed = (advance = false) => {
    updateFollow('reviewed', advance ? onNextUnreviewed : undefined);
  };

  const followHelperText = followStatus
    ? FOLLOW_STATUS_META[followStatus].description
    : 'Select a review status to keep track of this host.';

  return (
    // v5.240.0 — the review control lives in the title row. It used to own
    // 5/12 of the overview card, which for an unreviewed host was one button
    // over ~200px of nothing while the OS beside it truncated.
    <div className="ml-auto flex flex-wrap items-center gap-xs">
      {/* 5.303.0 — the bookmark icon that stood here looked like a button
          and did nothing; the badge says the state. */}
      <Badge
        variant={followStatus ? FOLLOW_STATUS_META[followStatus].badgeVariant : 'outline'}
        title={followHelperText}
      >
        {followStatus ? FOLLOW_STATUS_META[followStatus].label : 'Not reviewed'}
      </Badge>
      {/* §6/§9 — one state-aware review control: primary action for the
          common path + an overflow for the off-path transitions
          (mark-reviewed-direct, clear status).  Setting a review status
          is a write (analyst and up): below that the badge above is the
          whole of it — the controls are hidden, not disabled. */}
      {canEdit && (<>
      {followStatus === 'reviewed' ? (
        <Button size="sm" variant="outline" disabled={followLoading}
          onClick={() => updateFollow('in_review')}>
          <RotateCcw className="size-3.5" aria-hidden /> Re-open review
        </Button>
      ) : followStatus === 'in_review' ? (
        <Button size="sm" disabled={followLoading} onClick={() => markReviewed()}>
          <CheckCircle2 className="size-3.5" aria-hidden /> Mark reviewed
        </Button>
      ) : (
        <Button size="sm" disabled={followLoading}
          onClick={() => updateFollow('in_review')}>
          <Eye className="size-3.5" aria-hidden /> Start review
        </Button>
      )}
      {/* 5.303.0 — the off-path transitions as quiet buttons: the "⋯"
          menu they lived in held one item on almost every host. */}
      {followStatus !== 'in_review' && followStatus !== 'reviewed' && (
        <Button size="sm" variant="ghost" disabled={followLoading} onClick={() => markReviewed()}>
          Mark reviewed
        </Button>
      )}
      {/* Only inside a queue (the Hosts side sheet passes the step): mark
          it Reviewed and go to the next host nobody has started. */}
      {followStatus !== 'reviewed' && onNextUnreviewed && (
        <Button size="sm" variant="ghost" disabled={followLoading} onClick={() => markReviewed(true)}
          title="Mark this host Reviewed, then open the next host nobody has started">
          Reviewed, next unreviewed
        </Button>
      )}
      {followStatus && (
        <Button size="sm" variant="ghost" disabled={followLoading} onClick={() => updateFollow('none')}
          title="Clear this host's review status">
          Clear status
        </Button>
      )}
      </>)}
    </div>
  );
};

export default HostReviewControls;
