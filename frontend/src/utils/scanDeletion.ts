/**
 * Deleting a scan removes the hosts only it saw, together with the work people
 * did on them, and is refused while an import runs in the project.  These
 * helpers read the server's account of both; they are free of the API client
 * so the dialog's block and its tests can import them.
 */
import { asAxiosError } from './apiErrors';

/** A removed host that carries work, as `GET /scans/{id}/deletion-impact` lists it. */
export interface HostWithWork {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  /** Kind of work → how many; the server sends only kinds with a count above 0. */
  work: Record<string, number>;
}

/** The kinds the server sends, in the order they are read out: [key, one, many]. */
const WORK_KINDS: ReadonlyArray<readonly [string, string, string]> = [
  ['findings', 'finding', 'findings'],
  ['evidence', 'evidence record', 'evidence records'],
  ['tests', 'test', 'tests'],
  ['notes', 'note', 'notes'],
  ['proposals', 'proposal', 'proposals'],
  ['remediation_entries', 'remediation entry', 'remediation entries'],
  ['reviews', 'review', 'reviews'],
  ['tags', 'tag', 'tags'],
  // A host name someone typed in place of the scanner's.
  ['corrections', 'corrected name', 'corrected names'],
];

/**
 * "2 notes · 1 test · 1 evidence record".  A kind this build does not know is
 * printed under the server's own name rather than dropped: work that is about
 * to be deleted must never go unmentioned.
 */
export function workPhrase(work: Record<string, number> | null | undefined): string {
  const counts = work ?? {};
  const present = (key: string) => Number(counts[key]) > 0;
  const parts: string[] = [];
  for (const [key, one, many] of WORK_KINDS) {
    if (present(key)) parts.push(`${Number(counts[key]).toLocaleString()} ${counts[key] === 1 ? one : many}`);
  }
  const known = new Set(WORK_KINDS.map(([key]) => key));
  for (const key of Object.keys(counts).filter((k) => !known.has(k)).sort()) {
    if (present(key)) parts.push(`${Number(counts[key]).toLocaleString()} ${key}`);
  }
  return parts.join(' · ');
}

/**
 * How many of the hosts a delete removes carry work.  A server that predates
 * the field sends none, which reads as 0: the dialog then behaves as it did.
 */
export function hostsWithWorkCount(
  impact: { hosts_with_work?: number | null } | null | undefined,
): number {
  const n = Number(impact?.hosts_with_work);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/**
 * What the dialog says before the click when the preview reports an import
 * running in the project (the delete would be refused), else null.
 */
export function importRunningNotice(
  impact: { import_running?: boolean | null; import_running_filename?: string | null } | null | undefined,
): string | null {
  if (!impact?.import_running) return null;
  const file = impact.import_running_filename?.trim();
  return file
    ? `An import of "${file}" is running in this project. Delete this scan when it finishes.`
    : 'An import is running in this project. Delete this scan when it finishes.';
}

/**
 * The server's words for a 409 `import_running` refusal (an import is running
 * in the project, or holds its lock), or null for any other failure.
 */
export function importRunningRefusal(err: unknown): string | null {
  const response = asAxiosError(err).response;
  if (response?.status !== 409) return null;
  const detail = response.data?.detail as { error?: unknown; message?: unknown } | null | undefined;
  if (!detail || typeof detail !== 'object' || detail.error !== 'import_running') return null;
  return typeof detail.message === 'string' && detail.message.trim()
    ? detail.message
    : 'An import is running in this project. Nothing was changed; try again when it finishes.';
}

/** The refusal `DELETE /scans/{id}` answers while such hosts are unconfirmed. */
export interface HostsWithWorkRefusal {
  hosts_with_work: number;
  message: string;
}

/** The 409 `hosts_with_work` refusal, or null for any other failure. */
export function hostsWithWorkRefusal(err: unknown): HostsWithWorkRefusal | null {
  const response = asAxiosError(err).response;
  if (response?.status !== 409) return null;
  const detail = response.data?.detail as
    | { error?: unknown; hosts_with_work?: unknown; message?: unknown }
    | null
    | undefined;
  if (!detail || typeof detail !== 'object' || detail.error !== 'hosts_with_work') return null;
  const n = Number(detail.hosts_with_work);
  return {
    hosts_with_work: Number.isFinite(n) && n > 0 ? n : 0,
    message:
      typeof detail.message === 'string' && detail.message.trim()
        ? detail.message
        : 'Hosts this scan removes have work on them. Review them before deleting.',
  };
}
