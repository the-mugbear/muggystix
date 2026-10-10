/**
 * The Tool reference's entry for a tool a test names, if it has one.
 *
 * A test's `tool` is free text ("nxc", "NetExec", "curl"); the catalogue is
 * the installation's list (`GET /references/tools`, the Tool reference page).
 * Where the two agree — the same name, whatever the case — the test row links
 * to the entry, so an analyst who has not used the tool finds its install and
 * run commands.  No match means no link: never a link to an empty page.
 *
 * One remembered read for every open row (the catalogue changes when an admin
 * vets a suggestion, not while a host is being worked).
 */
import { useQuery } from '@tanstack/react-query';

import { getToolRegistry } from '../services/api';
import { rememberFor } from '../lib/query';

const TTL_MS = 5 * 60_000;

/** The catalogue's own spelling of `tool`, or null (not listed, not known yet, or the read failed). */
export function useToolReferenceName(tool: string | null | undefined, { enabled = true }: { enabled?: boolean } = {}): string | null {
  const wanted = (tool ?? '').trim().toLowerCase();
  const registry = useQuery({
    queryKey: ['getToolRegistry'],
    queryFn: ({ signal }) => getToolRegistry(undefined, signal),
    enabled: enabled && wanted !== '',
    ...rememberFor(TTL_MS),
  });
  if (!wanted) return null;
  return registry.data?.tools.find((t) => t.name.trim().toLowerCase() === wanted)?.name ?? null;
}

/** Where the Tool reference shows that entry. */
export const toolReferencePath = (name: string): string => `/tool-reference?q=${encodeURIComponent(name)}`;
