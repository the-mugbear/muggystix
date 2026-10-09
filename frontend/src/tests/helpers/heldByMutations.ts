/**
 * What the client still holds from its mutations — everything that was sent
 * (`variables`) and everything that came back (`data`), as one string.
 *
 * For the tests of a mutation that carries or returns a secret
 * (`SECRET_MUTATION`, lib/query): once the flow has ended, the password, the
 * one-time code or the recovery codes must not be in here.
 *
 *   const client = createQueryClient();
 *   render(<Page />, { wrapper: withClient(client) });
 *   …
 *   await waitFor(() => expect(heldByMutations(client)).not.toContain('the-password'));
 *
 * The client is passed in explicitly: `render` otherwise makes its own
 * (setupTests), which the test cannot look into.
 */
import { createElement, type ReactNode } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';

export const heldByMutations = (client: QueryClient): string => JSON.stringify(
  client.getMutationCache().getAll().map((mutation) => ({
    sent: mutation.state.variables ?? null,
    answered: mutation.state.data ?? null,
  })),
);

/** A `wrapper` that gives the rendered tree THIS client. */
export const withClient = (client: QueryClient) => ({ children }: { children: ReactNode }) => (
  createElement(QueryClientProvider, { client }, children)
);
