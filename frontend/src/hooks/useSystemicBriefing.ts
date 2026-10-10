import { useCallback } from 'react';
import { useMutation } from '@tanstack/react-query';

import { downloadSystemicReport } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { useProjectId } from './useProjectId';

/**
 * "Create briefing" — the executive systemic report (`/reports/systemic.html`,
 * AUDITOR on the server; the caller hides the button below that), saved as a
 * file.  One mutation for the three pages that offer it: estate-wide on
 * Posture and Patterns, one site's on Segments.
 *
 *   const briefing = useSystemicBriefing();
 *   briefing.create();          // the whole estate
 *   briefing.create('HQ');      // one site
 *   briefing.pending            // one is being made — offer no second
 *   briefing.pendingSite        // …and for which site (null: the estate, or none)
 *
 * A failure is a toast with the server's reason, naming the site.
 */
export interface SystemicBriefing {
  create: (site?: string) => void;
  pending: boolean;
  pendingSite: string | null;
}

export function useSystemicBriefing(): SystemicBriefing {
  const projectId = useProjectId();
  const toast = useToast();
  const briefing = useMutation({
    mutationFn: (site: string | null) => (
      site == null ? downloadSystemicReport(projectId) : downloadSystemicReport(projectId, site)
    ),
    onError: (e, site) => toast.error(formatApiError(
      e, site == null ? 'Could not create the briefing.' : `Could not create the briefing for ${site}.`,
    )),
  });
  const { mutate } = briefing;
  const create = useCallback((site?: string) => mutate(site ?? null), [mutate]);
  return {
    create,
    pending: briefing.isPending,
    pendingSite: briefing.isPending ? briefing.variables ?? null : null,
  };
}

export default useSystemicBriefing;
