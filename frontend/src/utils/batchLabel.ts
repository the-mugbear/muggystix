/**
 * An upload batch's display name (v5.288.0).
 *
 * An operator's multi-file upload is created with a GENERATED label the
 * moment the files are dropped: "31 files · 9/18/2026, 10:20:37 PM" (the old
 * format) or "31 files uploaded · Sep 23, 2026, 10:08 PM UTC".  Shown
 * as written, its date sat beside the When column in a different format, and
 * its count read as what was imported.  A generated label is shown as
 * "Upload batch · N files" (N = the files dropped); a name the operator gave,
 * or an agent's batch label, is shown exactly as written.
 */

const GENERATED = /^(\d+) files?(?: uploaded)? · /;

/** The number of files dropped, when `label` is a generated one; else null. */
export function generatedBatchCount(label: string | null | undefined): number | null {
  const m = GENERATED.exec((label ?? '').trim());
  return m ? Number(m[1]) : null;
}

export interface BatchDisplayName {
  /** What the row shows as its title. */
  title: string;
  /** True when the title was derived from a generated label. */
  generated: boolean;
  /** Files dropped, from a generated label; null for a named batch. */
  dropped: number | null;
}

export function batchDisplayName(label: string | null | undefined): BatchDisplayName {
  const dropped = generatedBatchCount(label);
  if (dropped == null) return { title: (label ?? '').trim() || 'Upload batch', generated: false, dropped: null };
  return {
    title: `Upload batch · ${dropped.toLocaleString()} file${dropped === 1 ? '' : 's'}`,
    generated: true,
    dropped,
  };
}
