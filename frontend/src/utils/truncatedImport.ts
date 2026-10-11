/**
 * A failed import whose file was cut short (owner decision 2026-10-11: "Ask
 * the user if it's a partial file whether they want to keep partial
 * results").
 *
 * The server says so with a KIND, never a sentence to match: `failure_kind`
 * is `truncated_file` when the file ends before its document does AND holds
 * at least one complete record.  Nothing from it was imported.  The reader
 * may then ask for what it holds (`components/scans/KeepTruncatedImport`).
 * A file cut before its first record ended fails as an ordinary parsing
 * error — there is nothing to keep, so nothing is offered.
 */
export const TRUNCATED_FILE_KIND = 'truncated_file';

export const isCutShort = (
  job: { status: string; failure_kind?: string | null },
): boolean => job.status === 'failed' && job.failure_kind === TRUNCATED_FILE_KIND;
