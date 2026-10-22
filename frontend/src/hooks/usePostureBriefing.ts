import { useMutation } from '@tanstack/react-query';

import { downloadPostureBriefing } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { useProjectId } from './useProjectId';

/**
 * "Create briefing" — the Posture briefing (`/reports/posture.html`, AUDITOR
 * on the server; the caller hides the button below that): the Posture
 * overview's argument as one file to hand out.  One mutation for the two pages
 * that offer it, Posture and Patterns.
 *
 *   const briefing = usePostureBriefing();
 *   briefing.create();
 *   briefing.pending            // one is being made — offer no second
 *
 * A failure is a toast with the server's reason.
 */
export interface PostureBriefing {
  create: () => void;
  pending: boolean;
}

export function usePostureBriefing(): PostureBriefing {
  const projectId = useProjectId();
  const toast = useToast();
  const briefing = useMutation({
    mutationFn: () => downloadPostureBriefing(projectId),
    onError: (e) => toast.error(formatApiError(e, 'Could not create the briefing.')),
  });
  const { mutate } = briefing;
  return { create: () => mutate(), pending: briefing.isPending };
}

export default usePostureBriefing;
