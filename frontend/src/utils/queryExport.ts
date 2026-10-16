/**
 * What a downloaded Query CSV holds when it is not the whole answer (external
 * review 2026-10-16, U1): read from the export's headers — every match
 * (`X-Total-Count`), the rows written (`X-Row-Count`), the file's cap
 * (`X-Cap`) and whether they are rows or groups (`X-Answer-Kind`).
 */
export const exportNote = (headers: Record<string, unknown>): string | undefined => {
  const total = Number(headers['x-total-count']);
  const rows = Number(headers['x-row-count']);
  const cap = Number(headers['x-cap']);
  if (!Number.isFinite(total) || !Number.isFinite(rows) || rows >= total) return undefined;
  const noun = headers['x-answer-kind'] === 'stats' ? 'groups' : 'rows';
  const why = Number.isFinite(cap) && rows >= cap
    ? ` — a file holds at most ${cap.toLocaleString()} ${noun}`
    : ' — as the query’s head asked';
  return `It holds the first ${rows.toLocaleString()} of ${total.toLocaleString()} ${noun}${why}.`;
};
