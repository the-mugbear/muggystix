/**
 * Images in a finding's report text — the client-free rules, shared by the
 * editor, the preview (`SafeMarkdown`) and the finding page.
 *
 * A finding's image, ticked "In report", may be PLACED inside one of that
 * finding's written sections with an ordinary Markdown image whose target is
 * `evidence:<attachment id>`:
 *
 *     ![The relayed session](evidence:57)
 *
 * The alt text, when present, is the caption for that placement; left empty,
 * the image's stored caption prints.  A ticked image no section places prints
 * under Evidence.
 *
 * The server's twin is `backend/app/services/report_images.py` (`REFERENCE`)
 * — change both together.  The server decides what prints; this only shows
 * the author what it will decide.
 */

/** A placement in Markdown: `![alt](evidence:<id>)`, with an optional title. */
export const EVIDENCE_REFERENCE = /!\[((?:[^\]\\\n]|\\.)*)\]\(\s*evidence:(\d{1,12})(?:\s+"[^"\n]*")?\s*\)/g;

/** The target of an image that names one of the finding's images, else null. */
export const evidenceIdOf = (target: string): number | null => {
  const m = /^evidence:(\d{1,12})$/.exec(target.trim());
  return m ? Number(m[1]) : null;
};

/** The attachment ids a section's Markdown references, each once, in order. */
export const referencedImageIds = (text: string | null | undefined): number[] => {
  const seen: number[] = [];
  for (const m of (text ?? '').matchAll(EVIDENCE_REFERENCE)) {
    const id = Number(m[2]);
    if (!seen.includes(id)) seen.push(id);
  }
  return seen;
};

/** A caption made safe to sit inside `![…]`: one line, no brackets (they
 *  would end the alt text early), and short enough to read in the source. */
export const captionAsAlt = (caption: string | null | undefined, max = 120): string => {
  const text = (caption ?? '').replace(/[[\]\\]/g, '').replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
};

/** The Markdown that places image `id`. */
export const imageReference = (id: number, caption?: string | null): string =>
  `![${captionAsAlt(caption)}](evidence:${id})`;

export const REPORT_FIELD_LABELS: Record<string, string> = {
  description: 'Description',
  impact: 'Impact',
  recommendation: 'Recommendation',
  steps_to_reproduce: 'Steps to reproduce',
  references: 'References',
};

const fieldNames = (fields: readonly string[]): string =>
  fields.map((f) => REPORT_FIELD_LABELS[f] ?? f).join(', ');

export interface ImagePlacement {
  in_report: boolean;
  printable: boolean;
  placed_in: readonly string[];
}

/** One line saying where an image prints — or why it will not. */
export const placementLine = (image: ImagePlacement): { text: string; tone: 'plain' | 'warning' } => {
  const placed = image.placed_in ?? [];
  if (!image.printable) {
    return { text: 'WebP — the report cannot print it (attach a PNG or JPEG)', tone: 'warning' };
  }
  if (!image.in_report) {
    return placed.length
      ? { text: `Referenced in ${fieldNames(placed)}, but not ticked “In report” — it will not print`, tone: 'warning' }
      : { text: 'Not in the report', tone: 'plain' };
  }
  return placed.length
    ? { text: `In: ${fieldNames(placed)}`, tone: 'plain' }
    : { text: 'Not placed — prints under Evidence', tone: 'plain' };
};

/** An image the editor may insert: one of the finding's, ticked "In report". */
export interface InsertableImage {
  id: number;
  caption: string | null;
  filename: string;
}

/** What a report-text editor needs to place the finding's images
 *  (`useFindingImages` returns it). */
export interface MarkdownImages {
  placeable: InsertableImage[];
  /** Object URLs loaded so far, by attachment id (thumbnails). */
  urls: Record<number, string>;
  resolver: EvidenceResolver;
}

/** What the preview needs to show a referenced image. */
export interface EvidenceLookup {
  /** The stored caption, when the id is one of this finding's ticked,
   *  printable images; `null` when the reference places nothing. */
  caption: string | null;
  /** An object URL for the bytes once they are loaded. */
  src?: string;
}

/** Given to `SafeMarkdown` on a finding's page.  Without one, an `evidence:`
 *  reference is shown as a short text marker and nothing is loaded. */
export interface EvidenceResolver {
  /** `null`: not an image this section may show. */
  lookup: (id: number) => EvidenceLookup | null;
  /** Ask for the bytes of an image `lookup` knows (idempotent). */
  ensure: (id: number) => void;
}
