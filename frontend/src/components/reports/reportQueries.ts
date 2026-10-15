/**
 * The project's report defaults and its report templates, read by five
 * places (the Reports page twice, Report defaults, Templates, a draft) — one
 * definition of each read's key and request (review 2026-10-14, R11), spread
 * into `useQuery` with whatever the caller adds (`enabled`).
 */
import { queryOptions } from '@tanstack/react-query';

import { getReportProfile, listReportTemplates } from '../../services/api';

export const reportProfileQuery = (projectId: number) => queryOptions({
  queryKey: ['getReportProfile', projectId] as const,
  queryFn: ({ signal }) => getReportProfile(projectId, signal),
});

export const reportTemplatesQuery = (projectId: number) => queryOptions({
  queryKey: ['listReportTemplates', projectId] as const,
  queryFn: ({ signal }) => listReportTemplates(projectId, signal),
});
