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
import { useToast } from '../../contexts/ToastContext';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { useProjectId } from '../../hooks/useProjectId';
import { rememberFor } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';

const DEBOUNCE_MS = 350;
/** How long the DSL schema is kept after it was read (it changes only with
 *  the server's version). */
const SCHEMA_REMEMBER_MS = 30 * 60_000;
const NO_HISTORY: HostQueryHistoryEntry[] = [];

/**
 * Backs the Hosts command bar: loads the DSL schema (remembered), debounce-validates
 * the draft query (lint + live match count) against the backend, and owns the
 * recent-queries history (list / record / delete / clear).
 *
 * Validation runs server-side (single source of truth = the parser) but never
 * blocks typing — the request is keyed by the draft once typing pauses, a
 * request for an earlier draft is dropped, and an empty draft is a valid
 * no-op without a round-trip.
 */
export function useQueryAssist(draft: string) {
  const projectId = useProjectId();
  const queryClient = useQueryClient();
  const toast = useToast();
  // The query language's fields: the same for every visit to the page, so
  // the answer is REMEMBERED (lib/query's default asks at every mount) — a
  // return to Hosts within `SCHEMA_REMEMBER_MS` asks nothing.  Non-fatal when
  // it fails: the command bar degrades to free typing, and a failure is not
  // remembered — the next visit asks again (`retryOnMount`).
  const schema = useQuery({
    queryKey: ['getHostQuerySchema', projectId],
    queryFn: ({ signal }) => getHostQuerySchema(projectId, signal),
    ...rememberFor(SCHEMA_REMEMBER_MS),
    retryOnMount: true,
  }).data ?? null;
  // Best-effort: a failed read is an empty history.
  const history = useQuery({
    queryKey: ['listHostQueryHistory', projectId],
    queryFn: ({ signal }) => listHostQueryHistory(projectId, undefined, signal),
  }).data ?? NO_HISTORY;

  const trimmed = draft.trim();
  const settled = useDebouncedValue(trimmed, DEBOUNCE_MS);
  // The answer carries the draft it describes, so the previous one — kept on
  // screen while the next is asked for — is never taken for the current.
  const check = useQuery({
    queryKey: ['validateHostQuery', projectId, settled],
    queryFn: async ({ signal }) => ({ query: settled, validation: await validateHostQuery(projectId, settled, signal) }),
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

  // A history action the reader TOOK and that was refused is said (remove and
  // clear used to fail silently: a ✕ that did nothing).  Recording a query is
  // the page's doing, on every search: its failure is logged for whoever
  // looks, never put in front of a reader who only asked for a list.  None of
  // them stops the search itself.
  const { mutate: record } = useMutation({
    mutationFn: ({ q, resultCount }: { q: string; resultCount?: number | null }) => recordHostQuery(projectId, q, resultCount),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['listHostQueryHistory', projectId] }),
    onError: (err) => console.warn('recordHostQuery failed:', formatApiError(err, 'not recorded')),
  });
  const recordQuery = useCallback((q: string, resultCount?: number | null) => {
    const text = q.trim();
    if (text) record({ q: text, resultCount });
  }, [record]);

  const { mutate: removeHistory } = useMutation({
    mutationFn: (id: number) => deleteHostQuery(projectId, id),
    onSuccess: (_done, id) => {
      queryClient.setQueryData<HostQueryHistoryEntry[]>(
        ['listHostQueryHistory', projectId], (entries) => entries?.filter((h) => h.id !== id),
      );
    },
    onError: (err) => toast.error(formatApiError(err, 'That query could not be removed from your recent queries.')),
  });

  const { mutate: clearHistory } = useMutation({
    mutationFn: () => clearHostQueryHistory(projectId),
    onSuccess: () => { queryClient.setQueryData<HostQueryHistoryEntry[]>(['listHostQueryHistory', projectId], []); },
    onError: (err) => toast.error(formatApiError(err, 'Your recent queries could not be cleared.')),
  });

  return { schema, validation, validatedQuery, validating, validationError, retryValidation, history, recordQuery, removeHistory, clearHistory };
}
