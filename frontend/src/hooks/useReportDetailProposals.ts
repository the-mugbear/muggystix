/**
 * The pending proposals for the project's report defaults, by detail
 * (5.431.0) — read once for the Report defaults page, which reviews each
 * detail's drafts where the detail is (`FieldDraftsReview`), as the finding
 * page does for a finding's sections (`useFindingProposals`).  Re-read every
 * 30 s while visible, and whenever a write says `listProposals` is out of
 * date (a decision).
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { listProposals, type Proposal } from '../services/api';
import { pollEvery, queryErrorText } from '../lib/query';
import { useProjectId } from './useProjectId';

const QUERY = { kind: 'report_detail', status: 'pending', limit: 200 } as const;

const byCreated = (a: Proposal, b: Proposal) =>
  (a.created_at ?? '').localeCompare(b.created_at ?? '') || a.id - b.id;

/** Drafts by detail, oldest first (so their letters stay put). */
export const groupByDetail = (items: Proposal[]): Map<string, Proposal[]> => {
  const out = new Map<string, Proposal[]>();
  for (const pr of [...items].sort(byCreated)) {
    if (pr.kind === 'report_detail' && pr.field) out.set(pr.field, [...(out.get(pr.field) ?? []), pr]);
  }
  return out;
};

export const useReportDetailProposals = (): { byDetail: Map<string, Proposal[]>; error: string | null } => {
  const projectId = useProjectId();
  const query = useQuery({
    queryKey: ['listProposals', projectId, QUERY],
    queryFn: ({ signal }) => listProposals(projectId, QUERY, signal),
    ...pollEvery(30_000),
  });
  const items = query.data?.items;
  const byDetail = useMemo(() => groupByDetail(items ?? []), [items]);
  return { byDetail, error: queryErrorText(query.error, 'The proposals for these defaults could not be read.') };
};
