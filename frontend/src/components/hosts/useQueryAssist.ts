import { useCallback } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  clearHostQueryHistory,
  deleteHostQuery,
  getHostQuerySchema,
  listHostQueryHistory,
  recordHostQuery,
  validateHostQuery,
  type HostQueryHistoryEntry,
} from '../../services/api';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';

const DEBOUNCE_MS = 350;
const NO_HISTORY: HostQueryHistoryEntry[] = [];

/**
 * Backs the Hosts command bar: loads the DSL schema once, debounce-validates
 * the draft query (lint + live match count) against the backend, and owns the
 * recent-queries history (list / record / delete / clear).
 *
 * Validation runs server-side (single source of truth = the parser) but never
 * blocks typing — the request is keyed by the draft once typing pauses, a
 * request for an earlier draft is dropped, and an empty draft is a valid
 * no-op without a round-trip.
 */
export function useQueryAssist(draft: string) {
  const queryClient = useQueryClient();
  // Non-fatal when it fails: the command bar degrades to free typing.
  const schema = useQuery({
    queryKey: ['getHostQuerySchema'],
    queryFn: ({ signal }) => getHostQuerySchema(signal),
  }).data ?? null;
  // Best-effort: a failed read is an empty history.
  const history = useQuery({
    queryKey: ['listHostQueryHistory'],
    queryFn: ({ signal }) => listHostQueryHistory(undefined, signal),
  }).data ?? NO_HISTORY;

  const trimmed = draft.trim();
  const settled = useDebouncedValue(trimmed, DEBOUNCE_MS);
  // The answer carries the draft it describes, so the previous one — kept on
  // screen while the next is asked for — is never taken for the current.
  const check = useQuery({
    queryKey: ['validateHostQuery', settled],
    queryFn: async ({ signal }) => ({ query: settled, validation: await validateHostQuery(settled, signal) }),
    enabled: settled !== '',
    placeholderData: keepPreviousData,
  });
  // A failed check clears any prior result too, so a failed re-validation of
  // an already-valid draft cannot show a stale success badge beside the
  // validation-unavailable control.
  const answered = trimmed !== '' && !check.isError ? check.data ?? null : null;
  const validation = answered?.validation ?? null;
  // The exact trimmed draft `validation` describes. Callers compare it to the
  // current draft so they never act on a result for a previous draft (a fast
  // typist could otherwise commit `port:` while validation still reflects a
  // valid earlier draft).
  const validatedQuery = answered?.query ?? null;
  const validating = trimmed !== '' && (settled !== trimmed || check.isFetching);
  // True when the validate request itself failed (offline / endpoint down) —
  // distinct from "validated and invalid". Callers degrade gracefully (allow
  // explicit submit + show a Retry) instead of dead-ending the input.
  const validationError = trimmed !== '' && settled === trimmed && check.isError && !check.isFetching;
  const { refetch } = check;
  const retryValidation = useCallback(() => { void refetch(); }, [refetch]);

  // The history's writes are best-effort: a failure is not said.
  const { mutate: record } = useMutation({
    mutationFn: ({ q, resultCount }: { q: string; resultCount?: number | null }) => recordHostQuery(q, resultCount),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['listHostQueryHistory'] }),
  });
  const recordQuery = useCallback((q: string, resultCount?: number | null) => {
    const text = q.trim();
    if (text) record({ q: text, resultCount });
  }, [record]);

  const { mutate: removeHistory } = useMutation({
    mutationFn: (id: number) => deleteHostQuery(id),
    onSuccess: (_done, id) => {
      queryClient.setQueryData<HostQueryHistoryEntry[]>(
        ['listHostQueryHistory'], (entries) => entries?.filter((h) => h.id !== id),
      );
    },
  });

  const { mutate: clearHistory } = useMutation({
    mutationFn: () => clearHostQueryHistory(),
    onSuccess: () => { queryClient.setQueryData<HostQueryHistoryEntry[]>(['listHostQueryHistory'], []); },
  });

  return { schema, validation, validatedQuery, validating, validationError, retryValidation, history, recordQuery, removeHistory, clearHistory };
}
