import { useCallback, useEffect, useRef, useState } from 'react';
import {
  clearHostQueryHistory,
  deleteHostQuery,
  getHostQuerySchema,
  listHostQueryHistory,
  recordHostQuery,
  validateHostQuery,
  type HostQueryHistoryEntry,
  type HostQuerySchema,
  type HostQueryValidation,
} from '../../services/api';

const DEBOUNCE_MS = 350;

/**
 * Backs the Hosts command bar: loads the DSL schema once, debounce-validates
 * the draft query (lint + live match count) against the backend, and owns the
 * recent-queries history (list / record / delete / clear).
 *
 * Validation runs server-side (single source of truth = the parser) but never
 * blocks typing — an in-flight request is aborted when the draft changes, and
 * an empty draft short-circuits to a valid no-op without a round-trip.
 */
export function useQueryAssist(draft: string) {
  const [schema, setSchema] = useState<HostQuerySchema | null>(null);
  const [validation, setValidation] = useState<HostQueryValidation | null>(null);
  // The exact trimmed draft `validation` describes. Callers compare it to the
  // current draft so they never act on a result for a previous draft (a fast
  // typist could otherwise commit `port:` while validation still reflects a
  // valid earlier draft).
  const [validatedQuery, setValidatedQuery] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);
  // True when the validate request itself failed (offline / endpoint down) —
  // distinct from "validated and invalid". Callers degrade gracefully (allow
  // explicit submit + show a Retry) instead of dead-ending the input.
  const [validationError, setValidationError] = useState(false);
  // Bumped by retryValidation() to re-run validation for the same draft.
  const [retryNonce, setRetryNonce] = useState(0);
  const [history, setHistory] = useState<HostQueryHistoryEntry[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let active = true;
    getHostQuerySchema()
      .then((s) => { if (active) setSchema(s); })
      .catch(() => { /* non-fatal: command bar degrades to free typing */ });
    return () => { active = false; };
  }, []);

  const refreshHistory = useCallback(() => {
    listHostQueryHistory()
      .then(setHistory)
      .catch(() => { /* history is best-effort */ });
  }, []);

  useEffect(() => { refreshHistory(); }, [refreshHistory]);

  // Debounced validation of the draft.
  useEffect(() => {
    abortRef.current?.abort();
    const trimmed = draft.trim();
    if (!trimmed) {
      setValidation(null);
      setValidatedQuery(null);
      setValidating(false);
      setValidationError(false);
      return;
    }
    setValidating(true);
    setValidationError(false);
    const controller = new AbortController();
    abortRef.current = controller;
    const timer = setTimeout(() => {
      validateHostQuery(trimmed, controller.signal)
        .then((v) => {
          if (!controller.signal.aborted) {
            setValidation(v);
            setValidatedQuery(trimmed);
            setValidationError(false);
          }
        })
        .catch(() => {
          // A real failure (not an abort) — surface it so the input can offer
          // Retry and still allow an explicit submit, rather than silently
          // blocking commit forever because validation never went "fresh".
          // Clear any prior result too, so a failed re-validation of an
          // already-valid draft can't show a stale success badge alongside
          // the validation-unavailable control.
          if (!controller.signal.aborted) {
            setValidation(null);
            setValidatedQuery(null);
            setValidationError(true);
          }
        })
        .finally(() => { if (!controller.signal.aborted) setValidating(false); });
    }, DEBOUNCE_MS);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [draft, retryNonce]);

  const retryValidation = useCallback(() => setRetryNonce((n) => n + 1), []);

  const recordQuery = useCallback(async (q: string, resultCount?: number | null) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    try {
      await recordHostQuery(trimmed, resultCount);
      refreshHistory();
    } catch { /* best-effort */ }
  }, [refreshHistory]);

  const removeHistory = useCallback(async (id: number) => {
    try {
      await deleteHostQuery(id);
      setHistory((prev) => prev.filter((h) => h.id !== id));
    } catch { /* best-effort */ }
  }, []);

  const clearHistory = useCallback(async () => {
    try {
      await clearHostQueryHistory();
      setHistory([]);
    } catch { /* best-effort */ }
  }, []);

  return { schema, validation, validatedQuery, validating, validationError, retryValidation, history, recordQuery, removeHistory, clearHistory };
}
