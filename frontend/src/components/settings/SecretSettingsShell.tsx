/**
 * What the two "things with a stored secret" settings pages share — LLM
 * Providers (`pages/LLMSettings`) and Scanner Integrations
 * (`pages/IntegrationSettings`).  They were written as twins and drifted (the
 * provider's save kept the typed API key in the library's cache; the
 * integration's did not).  Only what is truly the same is here:
 *
 *  - `SettingsPage` — the frame: the title, ONE explaining paragraph, the add
 *    action, and a failed read said in place with Retry;
 *  - `useSettingsReads` — what "loading" and "could not be read" mean for a
 *    list with its type catalogue;
 *  - `useSecretSave` — a save that carries a secret as typed.
 *
 * What is NOT shared, on purpose: who may write (a provider is its user's
 * own; an integration is the installation's, written by a global
 * administrator), each page's fields, its list and its words.
 */
import React from 'react';
import { useMutation, type UseQueryResult } from '@tanstack/react-query';

import { useToast } from '../../contexts/ToastContext';
import { SECRET_MUTATION, queryErrorText } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';

export const SettingsPage: React.FC<{
  title: string;
  /** The page's one explanation (UI_STYLE_GUIDE §44). */
  lead: React.ReactNode;
  /** The add button — absent for a reader who cannot write (hidden, not disabled). */
  action?: React.ReactNode;
  /** A read that failed, said where the list would be — never a toast over "none configured". */
  error: string | null;
  onRetry: () => void;
  children: React.ReactNode;
}> = ({ title, lead, action, error, onRetry, children }) => (
  <div className="p-md md:p-lg">
    <div className="mb-md flex flex-col gap-xs sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h1 className="text-page-title">{title}</h1>
        <p className="mt-xxs text-metadata text-muted-foreground">{lead}</p>
      </div>
      {action}
    </div>

    {error && (
      <p role="alert" className="mb-md break-words text-metadata text-destructive">
        {error}{' '}
        <button type="button" className="text-info hover:underline" onClick={onRetry}>Retry</button>
      </p>
    )}

    {children}
  </div>
);

/**
 * The list and its type catalogue, read together.
 *
 * `loading` is the FIRST load only (nothing of the list read yet): a later
 * read — after a save, or Retry — keeps the rows on screen, and if it fails
 * they stay with the failure said above them.  `retry` asks again for what
 * failed; what was read stays beside it.
 */
export function useSettingsReads(
  list: UseQueryResult<unknown>,
  types: UseQueryResult<unknown>,
  failedText: string,
): { loading: boolean; error: string | null; retry: () => void } {
  const failed = [list, types].find((q) => q.isError && !q.isFetching);
  return {
    loading: list.data === undefined && list.isFetching,
    error: queryErrorText(failed?.error, failedText),
    retry: () => {
      if (list.isError) void list.refetch();
      if (types.isError) void types.refetch();
    },
  };
}

export type SaveOutcome = 'updated' | 'added';

/**
 * A save that carries a secret as it was typed (an API key, a scanner's
 * credentials): nothing of it is kept once it has settled — `SECRET_MUTATION`
 * drops it from the library's cache, and the `reset` here clears the
 * observer's copy (UI_STYLE_GUIDE §48).  What is saved is what was handed
 * over with the click, not whatever the form holds when the request is built.
 */
export function useSecretSave<TVariables>({ mutationFn, savedText, failedText, onSaved }: {
  mutationFn: (variables: TVariables) => Promise<SaveOutcome>;
  savedText: Record<SaveOutcome, string>;
  failedText: string;
  /** Close the form and read the list again; the save is busy until it has. */
  onSaved: () => Promise<unknown> | void;
}): { saving: boolean; save: (variables: TVariables) => void } {
  const toast = useToast();
  const saving = useMutation({
    ...SECRET_MUTATION,
    mutationFn,
    onSuccess: (outcome) => {
      toast.success(savedText[outcome]);
      return onSaved();
    },
    onError: (err) => toast.error(formatApiError(err, failedText)),
  });
  const { mutate, reset } = saving;
  return {
    saving: saving.isPending,
    save: (variables) => mutate(variables, { onSettled: () => reset() }),
  };
}
